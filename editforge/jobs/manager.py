"""Runs jobs one after another in a background thread and records progress in the job store."""

from __future__ import annotations

import json
import queue
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..api import prepare, read_script
from ..core.cache import home_dir
from ..core.errors import Cancelled, EditForgeError
from ..render.engine import Progress, RenderOptions, render
from .store import FINAL_STATES, JobStore


def default_memory_mb() -> float:
    """Memory the page lets one job use: a quarter of the RAM that is free, between 300 MB and 4 GB.

    The command line keeps its fixed 300 MB default; in the page nobody types a number, so a
    computer with plenty of memory would otherwise render much slower than it can.
    """
    try:
        import psutil
        free_mb = psutil.virtual_memory().available / (1024 * 1024)
    except Exception:
        return 300.0
    return float(max(300, min(4000, int(free_mb * 0.25))))


class JobManager:
    """Queue plus one worker thread. Renders are heavy, so they run one at a time."""

    def __init__(self, store: Optional[JobStore] = None, allowed_roots: Optional[List[str]] = None, base_dir: Optional[str] = None):
        self.store = store or JobStore()
        self.allowed_roots = allowed_roots
        self.base_dir = base_dir
        self._q: "queue.Queue[Optional[str]]" = queue.Queue()
        self._cancels: Dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._loop, name="editforge-jobs", daemon=True)
        self._stopping = False
        self.store.mark_interrupted()
        self._thread.start()

    # ---- public --------------------------------------------------------------------------------
    def submit(self, script: str, input_path: str, output_path: str, options: Optional[Dict[str, Any]] = None) -> str:
        job_id = self.store.create(script, input_path, output_path, options or {})
        with self._lock:
            self._cancels[job_id] = threading.Event()
        self._q.put(job_id)
        return job_id

    def cancel(self, job_id: str) -> bool:
        job = self.store.get(job_id)
        if not job or job["status"] in FINAL_STATES:
            return False
        with self._lock:
            ev = self._cancels.get(job_id)
        if ev is not None:
            ev.set()
        if job["status"] in ("queued", "interrupted"):
            self.store.update(job_id, status="cancelled", message="Cancelled before it started.")
        return True

    def resume(self, job_id: str) -> bool:
        job = self.store.get(job_id)
        if not job or job["status"] not in ("interrupted", "failed", "cancelled"):
            return False
        with self._lock:
            self._cancels[job_id] = threading.Event()
        self.store.update(job_id, status="queued", progress=0.0, message="Waiting to start (finished pieces will be reused)",
                          error=None, error_fix=None, error_details=None)
        self._q.put(job_id)
        return True

    def wait(self, job_id: str, timeout: float = 300.0) -> Dict[str, Any]:
        end = time.time() + timeout
        while time.time() < end:
            job = self.store.get(job_id)
            if job and job["status"] in FINAL_STATES:
                return job
            time.sleep(0.05)
        raise TimeoutError(f"job {job_id} did not finish in {timeout}s")

    def shutdown(self) -> None:
        self._stopping = True
        with self._lock:
            for ev in self._cancels.values():
                ev.set()
        self._q.put(None)

    # ---- worker --------------------------------------------------------------------------------
    def _loop(self) -> None:
        while True:
            job_id = self._q.get()
            if job_id is None or self._stopping:
                return
            try:
                self._run(job_id)
            except Exception:  # never let the worker die
                self.store.update(job_id, status="failed", message="An unexpected error happened.",
                                  error="An unexpected error happened inside EditForge.",
                                  error_fix="Please try again. If it keeps happening, report the details below.",
                                  error_details=traceback.format_exc()[-3000:])

    def _run(self, job_id: str) -> None:
        job = self.store.get(job_id)
        if not job or job["status"] != "queued":
            return
        cancel = self._cancels.setdefault(job_id, threading.Event())
        opts: Dict[str, Any] = job.get("options") or {}
        self.store.update(job_id, status="running", message="Reading your script", progress=0.0)
        last = [0.0]

        def on_progress(p: Progress) -> None:
            now = time.time()
            if now - last[0] < 0.25 and p.fraction < 0.999:
                return
            last[0] = now
            self.store.update(job_id, progress=p.fraction, phase=p.phase, message=p.message, eta=p.eta_seconds)

        try:
            if opts.get("kind") == "combine":
                self._run_combine(job_id, job, opts, cancel)
                return
            if opts.get("kind") == "project":
                from ..project import plan_project
                self.store.update(job_id, message="Reading your project")
                plan = plan_project(json.loads(job["script"]), job["input_path"], music_path=opts.get("music_path"),
                                    captions_path=opts.get("captions_path"),
                                    preview=bool(opts.get("preview")), output_path=job["output_path"] or None, cancel=cancel,
                                    log=lambda t: self.store.update(job_id, message=t))
            else:
                script = read_script(job["script"])
                plan = prepare(script, input_path=job["input_path"], output_path=job["output_path"] or None, preset=opts.get("preset"),
                               size=opts.get("size"), fps=opts.get("fps"), quality=opts.get("quality"), fast_cuts=opts.get("fast_cuts"),
                               preview=bool(opts.get("preview")), base_dir=self.base_dir, allowed_roots=self.allowed_roots,
                               transcript=opts.get("transcript"), cancel=cancel,
                               log=lambda t: self.store.update(job_id, message=t))
            if not job["output_path"]:
                out_dir = Path(opts.get("output_dir") or (home_dir() / "outputs")) / job_id
                out_dir.mkdir(parents=True, exist_ok=True)
                made = Path(plan.output_path)
                name = (str(opts["output_name"]) + made.suffix) if opts.get("output_name") else made.name
                plan.output_path = str(out_dir / name)
                self.store.update(job_id, output_path=plan.output_path)
            self.store.update(job_id, plan={"duration": plan.timeline.duration, "segments": len(plan.timeline.segments),
                                            "summary": plan.summary(), "warnings": plan.warnings})
            ropts = RenderOptions(overwrite=True, encoder=opts.get("encoder", "auto"), workers=opts.get("workers"),
                                  max_memory_mb=float(opts.get("max_memory_mb") or default_memory_mb()), resume=True, cancel=cancel,
                                  on_progress=on_progress, log=lambda t: self.store.update(job_id, message=t))
            result = render(plan, ropts)
            self.store.update(job_id, status="done", progress=1.0, phase="done", message="Finished", eta=0.0, result=result.to_dict(),
                              output_path=result.output_path)
        except Cancelled:
            self.store.update(job_id, status="cancelled", message="Cancelled. Finished pieces are kept, so Resume continues where it stopped.")
        except EditForgeError as exc:
            self.store.update(job_id, status="failed", message=exc.message, error=exc.message, error_fix=exc.fix,
                              error_details=(exc.details or "")[-3000:])
        finally:
            with self._lock:
                self._cancels.pop(job_id, None)

    def _run_combine(self, job_id: str, job: Dict[str, Any], opts: Dict[str, Any], cancel: threading.Event) -> None:
        """A job that joins two videos (see :mod:`editforge.combine`). The exceptions are handled by ``_run``."""
        from ..combine import combine
        out_dir = Path(opts.get("output_dir") or (home_dir() / "outputs")) / job_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = str(out_dir / (str(opts.get("output_name") or "combined") + ".mp4"))
        self.store.update(job_id, output_path=out_path, message="Combining the two videos")
        last = [0.0]

        def on_progress(fraction: float) -> None:
            now = time.time()
            if now - last[0] >= 0.25:
                last[0] = now
                self.store.update(job_id, progress=fraction, phase="combine", message="Combining the two videos")

        result = combine(job["input_path"], str(opts["second_path"]), out_path, json.loads(job["script"]), cancel=cancel,
                         on_progress=on_progress)
        self.store.update(job_id, status="done", progress=1.0, phase="done", message="Finished", eta=0.0, result=result,
                          output_path=out_path)
