"""CPU and memory budgeting, plus measurement of a whole process tree.

The renderer never loads video into RAM. It keeps memory flat by running a small,
bounded number of FFmpeg workers, each handling a bounded batch of segments. This
module decides those bounds from the machine's CPU count and free RAM.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional

try:  # psutil is a small, free dependency; without it we fall back to /proc on Linux.
    import psutil  # type: ignore
except ImportError:  # pragma: no cover - depends on the machine
    psutil = None

DEFAULT_MEMORY_LIMIT_MB = 300
PYTHON_BASE_MB = 62.0
FFMPEG_BASE_MB = 60.0


def cpu_count() -> int:
    """Number of CPUs this process may use."""
    try:
        return max(1, len(os.sched_getaffinity(0)))  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return max(1, os.cpu_count() or 1)


def available_memory_mb() -> float:
    """Free RAM in MB (best effort; returns 2048 if it cannot be measured)."""
    if psutil is not None:
        try:
            return psutil.virtual_memory().available / 1e6
        except Exception:  # pragma: no cover
            pass
    try:
        with open("/proc/meminfo", "r") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    return 2048.0


def _tree_rss_proc(pid: int) -> Optional[float]:
    """Linux fallback: sum RSS of a process and all its descendants using /proc."""
    try:
        parents = {}
        for name in os.listdir("/proc"):
            if not name.isdigit():
                continue
            try:
                with open(f"/proc/{name}/stat", "rb") as fh:
                    data = fh.read().decode("utf-8", "replace")
                rest = data[data.rfind(")") + 2:].split()
                parents[int(name)] = int(rest[1])
            except (OSError, ValueError, IndexError):
                continue
        family = {pid}
        changed = True
        while changed:
            changed = False
            for child, parent in parents.items():
                if parent in family and child not in family:
                    family.add(child)
                    changed = True
        total = 0
        for p in family:
            try:
                with open(f"/proc/{p}/statm", "r") as fh:
                    total += int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
            except (OSError, ValueError, IndexError):
                continue
        return total / 1e6
    except OSError:
        return None


def process_tree_rss_mb(pid: Optional[int] = None) -> Optional[float]:
    """Total resident memory (MB) of a process and every child it started."""
    pid = pid or os.getpid()
    if psutil is not None:
        try:
            root = psutil.Process(pid)
            total = 0
            for proc in [root] + root.children(recursive=True):
                try:
                    total += proc.memory_info().rss
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            return total / 1e6
        except Exception:
            return None
    if os.path.isdir("/proc"):
        return _tree_rss_proc(pid)
    return None


def memory_measurement_available() -> bool:
    """True if this machine can measure process-tree memory."""
    return process_tree_rss_mb() is not None


class PeakMemoryMonitor:
    """Samples the memory of a process tree in a background thread and keeps the peak."""

    def __init__(self, pid: Optional[int] = None, interval: float = 0.05):
        self.pid = pid or os.getpid()
        self.interval = interval
        self.peak_mb = 0.0
        self.samples = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _run(self) -> None:
        while not self._stop.is_set():
            value = process_tree_rss_mb(self.pid)
            if value is not None:
                self.samples += 1
                if value > self.peak_mb:
                    self.peak_mb = value
            self._stop.wait(self.interval)

    def start(self) -> "PeakMemoryMonitor":
        self._thread = threading.Thread(target=self._run, name="memory-monitor", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> float:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        value = process_tree_rss_mb(self.pid)
        if value is not None and value > self.peak_mb:
            self.peak_mb = value
        return self.peak_mb

    def __enter__(self) -> "PeakMemoryMonitor":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()


@dataclass
class Resources:
    """How much parallelism the renderer will use."""

    workers: int
    threads: int
    batch_inputs: int
    est_worker_mb: float
    budget_mb: float
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _frame_mb(w: int, h: int) -> float:
    return max(1, w) * max(1, h) * 1.5 / 1e6


def x264_tuning(width: int, height: int, preset: str) -> "tuple[int, int, int]":
    """(reference frames, B-frames, lookahead frames) chosen so memory stays low at large picture sizes."""
    px = max(1, width) * max(1, height)
    if preset in ("ultrafast", "superfast"):
        return 1, 0, 0
    if px <= 1280 * 720 * 1.05:
        return 3, 2, 15
    if px <= 1920 * 1080 * 1.05:
        return 2, 2, 8
    return 1, 1, 3


def estimate_worker_mb(out_w: int, out_h: int, src_w: int, src_h: int, threads: int, inputs: int,
                       hardware: bool = False, preset: str = "veryfast") -> float:
    """Rough resident memory (MB) of one FFmpeg worker. Constants were fitted to real measurements
    (see BENCHMARKS.md): x264 keeps about 3 bytes per pixel for every frame it holds; each source
    decoder costs about 10 MB plus a few frames."""
    px = max(1, out_w) * max(1, out_h)
    if hardware:
        enc = 20.0 + px * 1.5e-6 * 6
    else:
        refs, bf, la = x264_tuning(out_w, out_h, preset)
        enc = px * 4.0e-6 * (refs + bf + la + threads + 1)
    dec = (2.7 + max(1, src_w) * max(1, src_h) * 7.4e-6) * inputs
    filt = _frame_mb(out_w, out_h) * 6
    return FFMPEG_BASE_MB + enc + dec + filt


def plan_resources(out_w: int, out_h: int, src_w: int, src_h: int, *, cpu: Optional[int] = None,
                   avail_mb: Optional[float] = None, limit_mb: float = DEFAULT_MEMORY_LIMIT_MB,
                   hardware: bool = False, requested_workers: Optional[int] = None, preset: str = "veryfast") -> Resources:
    """Choose worker count, threads per worker and inputs per batch.

    The total (Python + all workers) is kept under ``limit_mb`` and under 60% of the
    free RAM. Memory therefore stays flat no matter how many cuts the edit has.
    """
    cpu = cpu or cpu_count()
    avail = available_memory_mb() if avail_mb is None else avail_mb
    budget = max(120.0, min(float(limit_mb) * 0.95, avail * 0.6))
    notes: List[str] = []
    max_workers = min(cpu, 8, 2 if hardware else 8)
    if requested_workers:
        max_workers = max(1, min(requested_workers, 16))
    workers = max_workers
    while workers > 1:
        threads = max(1, min(6, cpu // workers))
        if PYTHON_BASE_MB + workers * estimate_worker_mb(out_w, out_h, src_w, src_h, threads, 1, hardware, preset) <= budget:
            break
        workers -= 1
    threads = max(1, min(6, cpu // workers))
    while threads > 1 and PYTHON_BASE_MB + workers * estimate_worker_mb(out_w, out_h, src_w, src_h, threads, 1, hardware, preset) > budget:
        threads -= 1       # still too big with one worker: use fewer encoder threads (less memory, a little slower)
    inputs = 1
    for b in range(1, 9):
        if PYTHON_BASE_MB + workers * estimate_worker_mb(out_w, out_h, src_w, src_h, threads, b, hardware, preset) <= budget:
            inputs = b
        else:
            break
    est = estimate_worker_mb(out_w, out_h, src_w, src_h, threads, inputs, hardware, preset)
    if PYTHON_BASE_MB + workers * est > budget:
        notes.append(
            f"This picture size needs about {PYTHON_BASE_MB + workers * est:.0f} MB even with one worker; "
            f"the memory limit is {budget:.0f} MB. EditForge will still run, using the smallest settings."
        )
    if requested_workers and workers < requested_workers:
        notes.append(f"Using {workers} worker(s) instead of {requested_workers} to stay inside the memory limit.")
    return Resources(workers=workers, threads=threads, batch_inputs=inputs, est_worker_mb=round(est, 1),
                     budget_mb=round(budget, 1), notes=notes)


def wait_for(predicate, timeout: float, interval: float = 0.05) -> bool:
    """Small helper used by tests and the job runner."""
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()
