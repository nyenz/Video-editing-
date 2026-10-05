"""The render engine: workers, batches, progress, cancel, resume and the final check."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..api import Plan
from ..core.cache import atomic_write_json, atomic_write_text, home_dir
from ..core.errors import Cancelled, EditForgeError, RenderError
from ..core.ffmpeg import get_tools, raise_for_result, run_ffmpeg
from ..core.fingerprint import stable_hash
from ..core.model import Segment, Timeline
from ..core.resources import DEFAULT_MEMORY_LIMIT_MB, PeakMemoryMonitor, Resources, plan_resources
from ..planner.captions import ass_events, ass_header, to_ass, to_srt
from ..planner.files import find_font
from ..version import __version__
from . import assemble as asm
from .encoders import SOFTWARE, choose_encoder
from .filters import Item, samples_at
from .pieces import (BuiltCommand, PieceJob, RenderContext, assign_names, build_copy_audio_command, build_copy_command,
                     build_piece_command, build_pieces)
from .verify import VerifyReport, verify_output


@dataclass
class RenderOptions:
    """Options for one render."""

    overwrite: bool = False
    encoder: str = "auto"
    workers: Optional[int] = None
    max_memory_mb: float = DEFAULT_MEMORY_LIMIT_MB
    resume: bool = True
    keep_temp: bool = False
    workdir_root: Optional[str] = None
    cancel: Optional[threading.Event] = None
    on_progress: Optional[Callable[["Progress"], None]] = None
    log: Optional[Callable[[str], None]] = None
    measure_memory: bool = True


@dataclass
class Progress:
    fraction: float
    phase: str
    message: str
    eta_seconds: Optional[float]
    elapsed: float
    pieces_done: int = 0
    pieces_total: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class RenderResult:
    output_path: str
    duration: float
    frames: int
    seconds: float
    peak_memory_mb: Optional[float]
    pieces_total: int
    pieces_reused: int
    encoder: str
    workers: int
    batch_inputs: int
    verify: Optional[VerifyReport]
    cache_hit: bool = False
    sidecars: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = dict(self.__dict__)
        d["verify"] = self.verify.to_dict() if self.verify else None
        return d


def workdir_root(opts_root: Optional[str] = None) -> Path:
    root = Path(opts_root) if opts_root else home_dir() / "work"
    root.mkdir(parents=True, exist_ok=True)
    return root


def cleanup_old_workdirs(root: Path, max_age_days: float = 7.0, keep: Optional[Path] = None) -> int:
    """Delete leftover temporary folders that have not been touched for a while."""
    removed = 0
    limit = time.time() - max_age_days * 86400
    try:
        for d in root.iterdir():
            if d.is_dir() and d != keep and d.stat().st_mtime < limit:
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
    except OSError:
        pass
    return removed


def render_job_key(plan: Plan, encoder: str) -> str:
    """The key for the whole render. It changes if ANY of these change: source content, the edit
    script (normalised), the resolved plan (which includes output settings), the encoder, or the app version."""
    return stable_hash({"v": __version__, "src": plan.identity["fp"], "script": plan.script.digest(), "plan": plan.digest,
                        "enc": encoder, "container": plan.timeline.output.container}, 40)


class Renderer:
    """Renders one :class:`Plan` to a file."""

    def __init__(self, plan: Plan, opts: Optional[RenderOptions] = None):
        self.plan = plan
        self.tl: Timeline = plan.timeline
        self.opts = opts or RenderOptions()
        self.stop = threading.Event()
        self.t0 = time.time()
        self._lock = threading.Lock()
        self._piece_frames: Dict[int, float] = {}
        self._weights = (0.0, 0.0, 0.0)  # (pieces, measure, final)
        self._phase_base = 0.0
        self.notes: List[str] = []
        self.src_fp = plan.identity["fp"]

    # ------------------------------------------------------------------ progress and logging
    def log(self, text: str) -> None:
        if self.opts.log:
            self.opts.log(text)

    def emit(self, phase: str, frac: float, message: str, done: int = 0, total: int = 0) -> None:
        if not self.opts.on_progress:
            return
        w = self._weights
        base = {"pieces": 0.0, "measure": w[0], "final": w[0] + w[1]}[phase]
        weight = {"pieces": w[0], "measure": w[1], "final": w[2]}[phase]
        overall = max(0.0, min(0.999, base + weight * max(0.0, min(1.0, frac))))
        elapsed = time.time() - self.t0
        eta = elapsed * (1 - overall) / overall if overall > 0.03 else None
        try:
            self.opts.on_progress(Progress(overall, phase, message, eta, elapsed, done, total))
        except Exception:
            pass

    def _check_cancel(self) -> None:
        if self.opts.cancel is not None and self.opts.cancel.is_set():
            self.stop.set()
        if self.stop.is_set():
            raise Cancelled()

    # ------------------------------------------------------------------ public entry
    def run(self) -> RenderResult:
        plan, tl, opts = self.plan, self.tl, self.opts
        out_path = plan.output_path
        self._check_output_path(out_path)
        encoder = choose_encoder(opts.encoder) if (tl.video_mode == "encode" and tl.output.has_video) else SOFTWARE
        key = render_job_key(plan, encoder)
        if opts.resume:
            hit = self._cache_hit(key, out_path)
            if hit:
                return hit
        monitor = PeakMemoryMonitor().start() if opts.measure_memory else None
        try:
            try:
                result = self._render(encoder, key)
            except RenderError as exc:
                user_cancelled = self.opts.cancel is not None and self.opts.cancel.is_set()
                if encoder == SOFTWARE or user_cancelled:
                    raise
                self.notes.append(f"The {encoder} encoder failed ({exc.message}); switched to the software encoder.")
                self.log(self.notes[-1])
                self.stop.clear()
                encoder = SOFTWARE
                key = render_job_key(plan, encoder)
                result = self._render(encoder, key)
        finally:
            peak = monitor.stop() if monitor else None
        result.peak_memory_mb = round(peak, 1) if peak else None
        result.notes = self.notes
        return result

    def _check_output_path(self, out_path: str) -> None:
        p = Path(out_path)
        if os.path.exists(out_path) and os.path.exists(self.plan.media.path) and os.path.samefile(out_path, self.plan.media.path):
            raise EditForgeError("The output file would replace your original video.", "Choose a different output name with -o.")
        if p.exists() and not self.opts.overwrite:
            raise EditForgeError(f"The file '{out_path}' already exists.", "Use --overwrite to replace it, or choose another name with -o.")
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            probe = p.parent / f".editforge_write_test_{os.getpid()}"
            probe.write_text("x")
            probe.unlink()
        except OSError as exc:
            raise EditForgeError(f"I can't write to the folder '{p.parent}' ({exc.strerror or exc}).",
                                 "Choose an output folder you can write to, with -o.")

    def _cache_hit(self, key: str, out_path: str) -> Optional[RenderResult]:
        rec = home_dir() / "renders" / f"{key}.json"
        try:
            import json
            data = json.loads(rec.read_text())
            st = os.stat(out_path)
            if data.get("output") != out_path or data.get("size") != st.st_size or data.get("mtime_ns") != st.st_mtime_ns:
                return None
        except (OSError, ValueError):
            return None
        rep = verify_output(out_path, self.tl.total_frames / float(self.tl.fps), self.tl.output, self._expect_audio(),
                            float(self.tl.fps), self.tl.video_mode == "encode")
        if not rep.ok:
            return None
        self.notes.append("This exact edit of this exact file was already rendered, so the finished file was reused.")
        return RenderResult(out_path, rep.duration, self.tl.total_frames, time.time() - self.t0, None, 0, 0, "cached", 0, 0, rep, True,
                            notes=list(self.notes))

    def _expect_audio(self) -> bool:
        return (self.plan.media.has_audio or bool(self.tl.audio.music)) and self.tl.output.container != "gif"

    # ------------------------------------------------------------------ the render itself
    def _render(self, encoder: str, key: str) -> RenderResult:
        plan, tl, opts, media = self.plan, self.tl, self.opts, self.plan.media
        out = tl.output
        root = workdir_root(opts.workdir_root)
        cleanup_old_workdirs(root)
        wd = root / key[:28]
        wd.mkdir(parents=True, exist_ok=True)
        os.utime(wd, None)
        atomic_write_json(wd / "job.json", {"key": key, "source": media.path, "output": plan.output_path, "version": __version__,
                                            "started": time.time(), "encoder": encoder})
        self._purge_parts(wd)
        res = plan_resources(out.width or 640, out.height or 360, media.width or 640, media.height or 360,
                             limit_mb=opts.max_memory_mb, hardware=encoder != SOFTWARE, requested_workers=opts.workers,
                             preset=out.x264_preset)
        for n in res.notes:
            self.notes.append(n)
        sr = out.sample_rate
        layout = "mono" if out.channels == 1 else "stereo"
        has_audio = self._expect_audio()
        mode = tl.video_mode
        has_video_pieces = out.has_video and mode == "encode"
        font_local = None
        if tl.texts:
            font = next((t.font for t in tl.texts if t.font), None) or find_font(None)
            if font and os.path.exists(font):
                font_local = "font_" + stable_hash(font, 8) + os.path.splitext(font)[1].lower()
                if not (wd / font_local).exists():
                    shutil.copy(font, wd / font_local)
            else:
                self.notes.append("No system font was found; on-screen text may fail. Install a font such as DejaVu Sans.")
        ctx = RenderContext(tl, media, media.path, self.src_fp, str(wd), encoder, res.threads, sr, layout, has_audio, has_video_pieces,
                            font_local)
        if mode == "encode":
            batch = res.batch_inputs if out.has_video else 16
            jobs = build_pieces(tl, batch)
        elif mode == "copy_all":
            seg = tl.segments[0]
            jobs = [PieceJob(0, "clips", [Item(seg, 0, seg.frames, 0)], 0, tl.total_frames)]
        else:
            jobs = []
            for i, seg in enumerate(tl.segments):
                job = PieceJob(i, "copy", [Item(seg, 0, seg.frames, seg.out_start)], seg.out_start, seg.out_end,
                               copy_range=(seg.src_start_f / float(tl.fps), (seg.src_start_f + seg.src_len_f) / float(tl.fps)))
                jobs.append(job)
        assign_names(jobs, ctx)
        piece_frames = sum(j.frames for j in jobs)
        loud = tl.audio.loudnorm is not None and has_audio
        self._weights = (0.80 if loud else 0.85, 0.05 if loud else 0.0, 0.15)
        reused = self._run_pieces(jobs, ctx, wd, res, piece_frames, mode)
        self._check_cancel()
        total_frames = tl.total_frames
        if mode == "copy_cuts":
            total_frames = sum(j.frames for j in jobs)
        return self._assemble(jobs, ctx, wd, key, encoder, res, reused, total_frames, mode, has_audio)

    def _purge_parts(self, wd: Path) -> None:
        for p in wd.glob("*.part"):
            try:
                p.unlink()
            except OSError:
                pass

    # -- pieces ----------------------------------------------------------------------------------
    def _piece_done(self, job: PieceJob, wd: Path, ctx: RenderContext) -> bool:
        need = []
        if job.kind == "copy":
            need = [job.video_name]
        else:
            if ctx.has_video_out:
                need.append(job.video_name)
            if ctx.has_audio_out:
                need.append(job.audio_name)
        return all((wd / n).exists() and (wd / n).stat().st_size > 0 for n in need)

    def _run_pieces(self, jobs: List[PieceJob], ctx: RenderContext, wd: Path, res: Resources, total_frames: int, mode: str) -> int:
        todo = [j for j in jobs if not (self.opts.resume and self._piece_done(j, wd, ctx))]
        reused = len(jobs) - len(todo)
        if reused:
            self.log(f"Resuming: {reused} of {len(jobs)} pieces were already done.")
        self._piece_frames = {j.index: float(j.frames) for j in jobs if j not in todo}
        self._pieces_total = len(jobs)
        self._pieces_done = reused
        self._total_piece_frames = max(1, total_frames)
        self.emit("pieces", sum(self._piece_frames.values()) / self._total_piece_frames, "Making the video pieces", reused, len(jobs))
        self.log(f"Making {len(todo)} piece(s) with {res.workers} worker(s), up to {res.batch_inputs} segment(s) per piece.")
        failure: List[BaseException] = []
        with ThreadPoolExecutor(max_workers=max(1, res.workers)) as pool:
            futures: Dict[Future, PieceJob] = {pool.submit(self._run_piece, j, ctx, wd): j for j in todo}
            pending = set(futures)
            while pending:
                done, pending = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
                if self.opts.cancel is not None and self.opts.cancel.is_set():
                    self.stop.set()
                for f in done:
                    exc = f.exception()
                    if exc is not None:
                        failure.append(exc)
                        self.stop.set()
                    else:
                        with self._lock:
                            self._pieces_done += 1
                self._emit_pieces()
        if failure:
            cancelled = [e for e in failure if isinstance(e, Cancelled)]
            others = [e for e in failure if not isinstance(e, Cancelled)]
            if others:
                raise others[0]
            raise cancelled[0]
        if mode == "copy_cuts":
            self._finish_copy_cuts(jobs, ctx, wd)
        return reused

    def _emit_pieces(self) -> None:
        with self._lock:
            done = sum(self._piece_frames.values())
        self.emit("pieces", done / self._total_piece_frames, "Making the video pieces", self._pieces_done, self._pieces_total)

    def _write_files(self, built: BuiltCommand, wd: Path) -> None:
        for name, text in built.files.items():
            atomic_write_text(wd / name, text)

    def _run_piece(self, job: PieceJob, ctx: RenderContext, wd: Path) -> None:
        if self.stop.is_set():
            raise Cancelled()
        built = build_copy_command(job, ctx) if job.kind == "copy" else build_piece_command(job, ctx)
        self._write_files(built, wd)

        def on_progress(d: Dict[str, str]) -> None:
            try:
                if ctx.has_video_out or job.kind == "copy":
                    fr = float(d.get("frame", "0"))
                else:
                    fr = float(d.get("out_time_us", d.get("out_time_ms", "0"))) / 1e6 * float(self.tl.fps)
            except ValueError:
                return
            with self._lock:
                self._piece_frames[job.index] = min(float(job.frames), max(self._piece_frames.get(job.index, 0.0), fr))

        result = run_ffmpeg(built.args, cwd=str(wd), cancel=self.stop, on_progress=on_progress)
        if result.cancelled:
            self._drop_parts(built, wd)
            raise Cancelled()
        if not result.ok:
            self._drop_parts(built, wd)
            try:
                raise_for_result(result, f"making piece {job.index + 1} (video time {job.g0 / float(self.tl.fps):.2f}s to {job.g1 / float(self.tl.fps):.2f}s)")
            except RenderError as exc:
                exc.details = (exc.details or "") + f"\n[piece {job.index + 1} of {self._pieces_total}, {job.kind}]"
                raise
        for part, final in built.outputs:
            os.replace(wd / part, wd / final)
        with self._lock:
            self._piece_frames[job.index] = float(job.frames)

    def _drop_parts(self, built: BuiltCommand, wd: Path) -> None:
        for part, _ in built.outputs:
            try:
                (wd / part).unlink()
            except OSError:
                pass

    def _finish_copy_cuts(self, jobs: List[PieceJob], ctx: RenderContext, wd: Path) -> None:
        """Fast cuts: measure how long each copied piece really is, then make the sound match it exactly."""
        tools = get_tools()
        F = self.tl.fps
        g = 0
        for j in jobs:
            proc = subprocess.run([tools.ffprobe, "-v", "error", "-count_packets", "-select_streams", "v:0", "-show_entries",
                                   "stream=nb_read_packets", "-of", "csv=p=0", str(wd / j.video_name)],
                                  capture_output=True, stdin=subprocess.DEVNULL)
            import re as _re
            m = _re.search(r"\d+", proc.stdout.decode())
            try:
                n = int(m.group(0)) if m else -1
                if n <= 0:
                    raise ValueError
            except ValueError:
                raise RenderError("A copied piece could not be measured.", "Turn off --fast-cuts and try again.")
            j.g0, j.g1 = g, g + n
            g += n
        if not ctx.has_audio_out:
            return
        for j in jobs:
            self._check_cancel()
            j.a_samples = samples_at(j.g1, F, ctx.sr) - samples_at(j.g0, F, ctx.sr)
            if (wd / j.audio_name).exists():
                continue
            built = build_copy_audio_command(j, ctx, j.a_samples)
            self._write_files(built, wd)
            result = run_ffmpeg(built.args, cwd=str(wd), cancel=self.stop)
            if result.cancelled:
                raise Cancelled()
            raise_for_result(result, "making the sound for a fast-cut piece")
            for part, final in built.outputs:
                os.replace(wd / part, wd / final)

    # -- assembling ------------------------------------------------------------------------------
    def _assemble(self, jobs: List[PieceJob], ctx: RenderContext, wd: Path, key: str, encoder: str, res: Resources, reused: int,
                  total_frames: int, mode: str, has_audio: bool) -> RenderResult:
        tl, plan, out = self.tl, self.plan, self.tl.output
        F = float(tl.fps)
        total_s = total_frames / F
        if out.has_video and mode != "copy_all":
            atomic_write_text(wd / "vlist.txt", asm.concat_list([j.video_name for j in jobs if j.video_name]))
        if has_audio:
            atomic_write_text(wd / "alist.txt", asm.concat_list([j.audio_name for j in jobs]))
        sidecars: List[str] = []
        soft = None
        cap = tl.captions
        if cap and cap.lines:
            srt_text = to_srt(cap.lines)
            atomic_write_text(wd / "captions.srt", srt_text)
            if cap.mode in ("soft", "both") and out.container in ("mp4", "mov", "mkv") and out.has_video:
                soft = "captions.srt"
        elif cap and cap.mode in ("soft", "both"):
            self.notes.append("No captions were made (no speech found), so nothing was added.")
        self._nf = -50.0
        if tl.audio.denoise and has_audio:
            self._nf = self._estimate_noise_floor(wd)
        ln_filter = None
        if tl.audio.loudnorm and has_audio:
            ln_filter = self._measure_loudness(wd, ctx, total_s)
        self._check_cancel()
        out_path = Path(plan.output_path)
        part = out_path.with_name(out_path.name + ".editforge.part")
        try:
            if out.container == "gif":
                self._make_gif(wd, part)
            else:
                args, files = asm.final_command(tl, src=plan.media.path, video_mode=mode, has_video_pieces=ctx.has_video_out,
                                                has_audio=has_audio, sr=ctx.sr, layout=ctx.layout, total_s=total_s, loudnorm=ln_filter,
                                                soft_subs=soft, out_name=str(part), denoise_nf=self._nf)
                for n, t in files.items():
                    atomic_write_text(wd / n, t)
                self._run_final(args, wd, total_s)
            self._check_cancel()
            rep = verify_output(str(part), total_s, out, has_audio, F, mode != "copy_cuts")
            if not rep.ok:
                raise RenderError("The finished file did not pass the final check: " + " ".join(rep.problems),
                                  "Try again with --no-resume (starts from scratch). If it keeps happening, report it with the details.",
                                  details=str(rep.to_dict()))
            os.replace(part, out_path)
        except BaseException:
            try:
                part.unlink()
            except OSError:
                pass
            raise
        if cap and cap.write_files and cap.lines:
            sidecars = self._write_sidecars(out_path, cap)
        st = os.stat(out_path)
        rec = home_dir() / "renders" / f"{key}.json"
        atomic_write_json(rec, {"output": str(out_path), "size": st.st_size, "mtime_ns": st.st_mtime_ns, "duration": rep.duration,
                                "when": time.time(), "version": __version__})
        if not self.opts.keep_temp:
            shutil.rmtree(wd, ignore_errors=True)
        self.emit("final", 1.0, "Done")
        return RenderResult(str(out_path), rep.duration, total_frames, time.time() - self.t0, None, len(jobs), reused, encoder,
                            res.workers, res.batch_inputs, rep, False, sidecars)

    def _run_final(self, args: List[str], wd: Path, total_s: float) -> None:
        def on_progress(d: Dict[str, str]) -> None:
            try:
                t = float(d.get("out_time_us", d.get("out_time_ms", "0"))) / 1e6
            except ValueError:
                return
            self.emit("final", t / max(total_s, 0.001), "Joining the pieces")

        result = run_ffmpeg(args, cwd=str(wd), cancel=self.stop, on_progress=on_progress)
        if result.cancelled:
            raise Cancelled()
        raise_for_result(result, "joining the pieces into the finished file")

    def _estimate_noise_floor(self, wd: Path) -> float:
        """Listen to the sound once to guess how loud the background noise is (used to tune the noise reducer)."""
        import re
        levels: List[float] = []
        pat = re.compile(r"lavfi\.astats\.Overall\.RMS_level=(\S+)")

        def on_line(line: str) -> None:
            m = pat.search(line)
            if m:
                try:
                    levels.append(float(m.group(1)))
                except ValueError:
                    levels.append(-90.0)

        graph = ("aformat=channel_layouts=mono,asetnsamples=n=9600:p=0,astats=metadata=1:reset=1:measure_perchannel=none:"
                 "measure_overall=RMS_level,ametadata=mode=print:key=lavfi.astats.Overall.RMS_level:file=-")
        res = run_ffmpeg(["-f", "concat", "-safe", "0", "-i", "alist.txt", "-af", graph, "-f", "null", "-"], cwd=str(wd),
                         cancel=self.stop, on_stdout_line=on_line)
        if res.cancelled:
            raise Cancelled()
        nf = asm.noise_floor_from_levels(levels)
        self.notes.append(f"Background noise level estimated at {nf:.0f} dB for the noise reducer.")
        return nf

    def _measure_loudness(self, wd: Path, ctx: RenderContext, total_s: float) -> Optional[str]:
        target = self.tl.audio.loudnorm
        assert target is not None
        args, files = asm.measure_command(self.tl, has_audio=True, sr=ctx.sr, layout=ctx.layout, total_s=total_s,
                                          loudnorm=asm.loudnorm_filter(target), denoise_nf=self._nf)
        for n, t in files.items():
            atomic_write_text(wd / n, t)

        def on_progress(d: Dict[str, str]) -> None:
            pass

        result = run_ffmpeg(args, cwd=str(wd), cancel=self.stop, loglevel="info", tail_bytes=16000)
        if result.cancelled:
            raise Cancelled()
        raise_for_result(result, "measuring the loudness")
        self.emit("measure", 1.0, "Measured the loudness")
        measured = asm.parse_loudnorm_json(result.stderr_tail)
        if measured is None:
            self.notes.append("The sound is silent or too quiet to measure, so the loudness was left as it is.")
            return None
        self.notes.append(f"Loudness measured {measured['input_i']:.1f} LUFS; normalised to {target['target']:g} LUFS.")
        return asm.loudnorm_filter(target, measured)

    def _make_gif(self, wd: Path, part: Path) -> None:
        p1, p2 = asm.gif_commands(self.tl)
        for args in (p1, p2):
            result = run_ffmpeg(args, cwd=str(wd), cancel=self.stop)
            if result.cancelled:
                raise Cancelled()
            raise_for_result(result, "making the GIF")
        os.replace(wd / "final.gif.part", part)

    def _write_sidecars(self, out_path: Path, cap: Any) -> List[str]:
        made: List[str] = []
        srt = out_path.with_suffix(".srt")
        atomic_write_text(srt, to_srt(cap.lines))
        made.append(str(srt))
        ass = out_path.with_suffix(".ass")
        atomic_write_text(ass, to_ass(cap.lines, cap, self.tl.output.width or 1920, self.tl.output.height or 1080))
        made.append(str(ass))
        return made


def render(plan: Plan, opts: Optional[RenderOptions] = None) -> RenderResult:
    """Render a plan to its output file."""
    return Renderer(plan, opts).run()
