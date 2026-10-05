"""Command-line interface: edit, plan, validate, probe, presets, encoders, proxy, web (and a few helpers)."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import textwrap
import threading
import time
from pathlib import Path
from typing import Any, List, Optional

from ..api import Plan, prepare, read_script, validate_script
from ..core.cache import AnalysisCache, home_dir
from ..core.errors import Cancelled, EditForgeError
from ..core.ffmpeg import get_tools, run_ffmpeg, raise_for_result
from ..core.media import probe_media
from ..core.resources import DEFAULT_MEMORY_LIMIT_MB
from ..core.timecode import format_time
from ..dsl.registry import SPECS
from ..presets.presets import PRESETS
from ..render.encoders import detect_encoders
from ..render.engine import Progress, RenderOptions, cleanup_old_workdirs, render, workdir_root
from ..version import __version__


def _err(text: str = "") -> None:
    print(text, file=sys.stderr)


def print_error(exc: EditForgeError, verbose: bool = False) -> None:
    _err("")
    _err("Problem: " + (f"line {exc.line}: " if exc.line else "") + exc.message)
    if exc.fix:
        _err("How to fix: " + exc.fix)
    if verbose and exc.details:
        _err("\nTechnical details:\n" + exc.details)
    elif exc.details:
        _err("(Run again with --verbose to see the technical details.)")


class ProgressPrinter:
    """A one-line progress bar with a time-left estimate (plain lines when output is not a terminal)."""

    def __init__(self) -> None:
        self.tty = sys.stderr.isatty()
        self.last_print = 0.0
        self.last_pct = -1

    def __call__(self, p: Progress) -> None:
        pct = int(p.fraction * 100)
        now = time.time()
        if self.tty:
            if now - self.last_print < 0.1 and pct < 100:
                return
            self.last_print = now
            bar = "#" * (pct // 4) + "-" * (25 - pct // 4)
            eta = f"  about {format_time(p.eta_seconds, False)[3:]} left" if p.eta_seconds else ""
            sys.stderr.write(f"\r[{bar}] {pct:3d}%  {p.message[:38]:38s}{eta}   ")
            sys.stderr.flush()
        elif pct // 10 != self.last_pct // 10:
            self.last_pct = pct
            _err(f"{pct}%  {p.message}")

    def done(self) -> None:
        if self.tty:
            sys.stderr.write("\r" + " " * 90 + "\r")
            sys.stderr.flush()


def _load_script(args: argparse.Namespace):
    if getattr(args, "inline", None):
        return read_script(text=args.inline.replace("\\n", "\n")), os.getcwd()
    if args.script == "-":
        return read_script(text=sys.stdin.read()), os.getcwd()
    path = Path(args.script)
    if not path.exists():
        raise EditForgeError(f"I can't find the script file '{args.script}'.", "Check the name, or write the script inline with -e \"keep 0-10\".")
    return read_script(path=str(path)), str(path.resolve().parent)


def _abs(p: Optional[str]) -> Optional[str]:
    return os.path.abspath(os.path.expanduser(p)) if p else None


def _prepare(args: argparse.Namespace, log: Any = None) -> Plan:
    script, base = _load_script(args)
    for w in script.warnings:
        _err("Note: " + w.plain())
    script.raise_if_errors()
    return prepare(script, input_path=_abs(args.input), output_path=_abs(args.output), preset=args.preset, size=args.size, fps=args.fps,
                   quality=args.quality, fast_cuts=True if args.fast_cuts else None, preview=args.preview, base_dir=base,
                   transcript=args.transcript, log=log)


def print_plan(plan: Plan, table: bool = True) -> None:
    print(plan.summary())
    for n in plan.notes + plan.analysis_notes:
        print("  -", n)
    for w in plan.warnings:
        print("  ! Warning:", w)
    if table and plan.timeline.segments:
        print("\n  #   source                       output                       speed  notes")
        segs = plan.timeline.to_dict()["segments"]
        shown = segs if len(segs) <= 40 else segs[:20] + [None] + segs[-19:]
        for s in shown:
            if s is None:
                print(f"  ... {len(segs) - 39} more segments ...")
                continue
            notes = ", ".join(filter(None, [s["kind"] if s["kind"] != "clip" else "", "reverse" if s["reverse"] else "",
                                            ",".join(s["effects"]), "zoom" if s["zoom"] else "", "crop" if s["crop"] or s["reframe"] else "",
                                            f"fade-in {s['transition_in']}" if s["transition_in"] else "",
                                            f"fade-out {s['transition_out']}" if s["transition_out"] else ""]))
            print(f"  {s['index']:<3d} {format_time(s['source_start'])}-{format_time(s['source_end'])}  "
                  f"{format_time(s['output_start'])}-{format_time(s['output_end'])}  x{s['speed']:<5g} {notes}")


def cmd_edit(args: argparse.Namespace) -> int:
    printer = ProgressPrinter()
    plan = _prepare(args, log=lambda t: _err(t) if getattr(args, "verbose", False) else None)
    print_plan(plan, table=args.dry_run)
    if args.dry_run:
        print("\nDry run only: nothing was written.")
        return 0
    cancel = threading.Event()
    opts = RenderOptions(overwrite=args.overwrite, encoder=args.encoder, workers=args.workers, max_memory_mb=args.max_memory,
                         resume=not args.no_resume, keep_temp=args.keep_temp, cancel=cancel, on_progress=printer,
                         log=lambda t: _err(t) if getattr(args, "verbose", False) else None)
    try:
        result = render(plan, opts)
    except Cancelled:
        printer.done()
        _err("Cancelled. Finished pieces are kept, so running the same command again continues where it stopped.")
        return 130
    finally:
        printer.done()
    print(f"\nDone: {result.output_path}")
    if result.cache_hit:
        print("  (This exact edit was already made, so the finished file was reused.)")
    else:
        print(f"  Length {result.duration:.2f}s, made in {result.seconds:.1f}s"
              + (f", peak memory {result.peak_memory_mb:.0f} MB" if result.peak_memory_mb else "")
              + f", encoder {result.encoder}, {result.workers} worker(s)")
    for n in result.notes:
        print("  -", n)
    for s in result.sidecars:
        print("  Caption file:", s)
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    args.dry_run = True
    plan = _prepare(args, log=lambda t: _err(t))
    if args.json:
        print(json.dumps(plan.to_dict(), indent=1, default=str))
    else:
        print_plan(plan, table=True)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    if getattr(args, "inline", None):
        text = args.inline.replace("\\n", "\n")
    elif args.script == "-":
        text = sys.stdin.read()
    else:
        if not Path(args.script).exists():
            raise EditForgeError(f"I can't find the script file '{args.script}'.", "Check the name and folder.")
        text = Path(args.script).read_text(encoding="utf-8-sig")
    base = str(Path(args.script).resolve().parent) if args.script not in ("-", None) and Path(args.script).exists() else os.getcwd()
    report = validate_script(text, _abs(args.input), base_dir=base)
    for p in report["problems"]:
        head = "ERROR  " if p["level"] == "error" else "warning"
        line = f"line {p['line']}: " if p["line"] else ""
        print(f"{head} {line}{p['message']}" + (f"\n         How to fix: {p['fix']}" if p["fix"] else ""))
    if report["ok"]:
        print(f"OK: the script is valid ({report['info']['steps']} instruction(s), {report['info']['format']} format).")
        return 0
    return 2


def cmd_probe(args: argparse.Namespace) -> int:
    info = probe_media(args.file)
    if args.json:
        print(json.dumps(info.to_dict(), indent=1))
        return 0
    print(f"File:       {info.path}\nLength:     {format_time(info.duration)}  ({info.duration:.3f}s)\nSize:       {info.size_bytes / 1e6:.1f} MB")
    if info.has_video:
        print(f"Picture:    {info.width}x{info.height}, {float(info.fps):.3f} fps{' (variable)' if info.vfr else ''}, "
              f"{info.video_codec} {info.pix_fmt}" + (f", rotated {info.rotation} degrees" if info.rotation else ""))
    else:
        print("Picture:    none (audio only)")
    if info.has_audio:
        print(f"Sound:      {info.audio_codec}, {info.sample_rate} Hz, {info.channels} channel(s)")
    else:
        print("Sound:      none")
    for w in info.warnings:
        print("Note:", w)
    return 0


def cmd_presets(args: argparse.Namespace) -> int:
    print(f"{'name':18s} {'output':22s} description")
    for p in PRESETS:
        size = "audio only" if not p.has_video else (f"{p.width or 'auto'}x{p.height or 'auto'} {p.container}")
        print(f"{p.name:18s} {size:22s} {p.description}")
        if p.aliases:
            print(f"{'':18s} {'':22s} also: {', '.join(p.aliases)}")
    return 0


def cmd_encoders(args: argparse.Namespace) -> int:
    tools = get_tools()
    print(f"FFmpeg: {tools.version}\n")
    for e in detect_encoders(force=args.retest):
        state = "works" if e.works else ("built in but does NOT work here" if e.in_build else "not in this FFmpeg")
        print(f"{e.name:20s} {e.label:40s} {state}" + (f"  ({e.error})" if e.error and e.in_build and not e.works else ""))
    print("\n'--encoder auto' uses the first hardware encoder that works, otherwise the software encoder (libx264).")
    return 0


def cmd_proxy(args: argparse.Namespace) -> int:
    info = probe_media(args.file)
    if not info.has_video:
        raise EditForgeError("This file has no picture, so a picture proxy makes no sense.", "Use the file as it is.")
    out = _abs(args.output) or str(Path(args.file).with_name(Path(args.file).stem + ".proxy.mp4"))
    if os.path.exists(out) and not args.overwrite:
        raise EditForgeError(f"'{out}' already exists.", "Use --overwrite or choose another name with -o.")
    _err(f"Making a small proxy copy ({args.height}p) for quick previews...")
    a = ["-y", "-i", info.path, "-map", "0:v:0", "-vf", f"scale=-2:{args.height}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
         "-g", "12", "-pix_fmt", "yuv420p"]
    a += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "96k", "-ac", "2"] if info.has_audio else ["-an"]
    part = out + ".part"
    a += ["-movflags", "+faststart", "-f", "mp4", part]
    printer = ProgressPrinter()

    def prog(d: dict) -> None:
        try:
            t = float(d.get("out_time_us", "0")) / 1e6
        except ValueError:
            return
        printer(Progress(min(0.99, t / info.duration), "proxy", "Making the proxy", None, 0))

    res = run_ffmpeg(a, on_progress=prog)
    printer.done()
    raise_for_result(res, "making the proxy")
    os.replace(part, out)
    print(f"Proxy written: {out}\nEdit with it for quick tests; render from the original for the final result.")
    return 0


def cmd_beats(args: argparse.Namespace) -> int:
    from ..analysis.beats import analyze_beats
    info = probe_media(args.file)
    if not info.has_audio:
        raise EditForgeError("This file has no sound to listen to.", "Choose a music or video file with sound.")
    res = analyze_beats(args.file, duration=info.duration)
    if args.json:
        print(json.dumps(res.to_dict(), indent=1))
        return 0
    print(f"Tempo: {res.tempo:g} BPM   beats: {len(res.beats)}   beats per bar: {res.meter or 'unknown'}   downbeats: {len(res.downbeats)}")
    for n in res.notes:
        print(" -", n)
    print("First beats (s):", ", ".join(f"{b:.2f}" for b in res.beats[:16]))
    return 0


def cmd_jobs(args: argparse.Namespace) -> int:
    from ..jobs.store import JobStore
    rows = JobStore().list(args.limit)
    if not rows:
        print("No jobs yet.")
    for r in rows:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["created"]))
        print(f"{r['id']}  {when}  {r['status']:11s} {int(r['progress'] * 100):3d}%  {os.path.basename(r['output_path'] or '')}  {r['message'] or ''}")
    return 0


def cmd_commands(args: argparse.Namespace) -> int:
    group = None
    for s in SPECS:
        if s.group != group:
            group = s.group
            print(f"\n== {group} ==")
        print(f"  {s.name:16s} {s.summary}")
        print(f"  {'':16s} e.g.  {s.example}")
    return 0


def cmd_features(args: argparse.Namespace) -> int:
    from ..analysis.faces import opencv_available
    from ..analysis.speech import whisper_available
    from ..core.resources import memory_measurement_available
    tools = get_tools()
    rows = [
        ("FFmpeg", True, tools.version.split(" Copyright")[0]),
        ("Cuts, speed, effects, transitions, audio tools", True, "built in"),
        ("Beat and downbeat detection", True, "built in (uses FFmpeg only)"),
        ("Scene detection", True, "built in (FFmpeg's scene score)"),
        ("Automatic captions (Whisper)", whisper_available(), "pip install faster-whisper"),
        ("Face tracking and face-following re-frame", opencv_available(), "pip install opencv-python-headless"),
        ("Memory measurement", memory_measurement_available(), "pip install psutil"),
        ("Text on screen (drawtext)", tools.has_filter("drawtext"), "needs an FFmpeg build with freetype"),
        ("Burned-in captions (ass)", tools.has_filter("ass"), "needs an FFmpeg build with libass"),
        ("Transitions (xfade)", tools.has_filter("xfade"), "needs FFmpeg 4.3 or newer"),
    ]
    for name, on, hint in rows:
        print(f"[{'on ' if on else 'OFF'}] {name:48s} {'' if on else 'to switch on: ' + hint}")
    return 0


def cmd_clean(args: argparse.Namespace) -> int:
    n = AnalysisCache().clear()
    root = workdir_root()
    removed = cleanup_old_workdirs(root, 0.0 if args.all else 1.0)
    print(f"Removed {n} saved analysis file(s) and {removed} temporary work folder(s).")
    return 0


def cmd_web(args: argparse.Namespace) -> int:
    from ..web.server import serve
    return serve(host=args.host, port=args.port, open_browser=not args.no_open, media_dir=args.media_dir)


def cmd_version(args: argparse.Namespace) -> int:
    print(f"EditForge {__version__}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="editforge", description="EditForge: a free video editor that runs on your own computer.",
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog=textwrap.dedent("""\
                                    Quick start:
                                      editforge web                                   open the web page
                                      editforge edit my_script.txt -i video.mp4      make an edit
                                      editforge plan my_script.txt -i video.mp4      see what would happen
                                      editforge commands                              list every instruction
                                    """))
    p.add_argument("--verbose", "-v", action="store_true", help="show technical details")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--verbose", "-v", action="store_true", default=argparse.SUPPRESS, help="show technical details")
    sub = p.add_subparsers(dest="command", metavar="command")
    _orig_add = sub.add_parser
    sub.add_parser = lambda name, **kw: _orig_add(name, parents=[common], **kw)  # type: ignore[method-assign]

    def add_script_args(sp: argparse.ArgumentParser, edit: bool = False) -> None:
        sp.add_argument("script", nargs="?", default="-", help="script file (- reads from the keyboard/pipe)")
        sp.add_argument("-e", "--inline", metavar="TEXT", help='write the script right here, e.g. -e "keep 0-10" (use \\n for new lines)')
        sp.add_argument("-i", "--input", help="the video or audio file to edit")
        sp.add_argument("-o", "--output", help="the name of the finished file")
        sp.add_argument("--preset", help="output style, see 'editforge presets'")
        sp.add_argument("--size", help="picture size, e.g. 1280x720 or 720p")
        sp.add_argument("--fps", type=float, help="frame rate of the finished video")
        sp.add_argument("--quality", help="low, medium, high, max, or a CRF number (lower is better)")
        sp.add_argument("--preview", action="store_true", help="fast small 480p version to check the edit")
        sp.add_argument("--fast-cuts", action="store_true",
                        help="cut on keyframes without re-encoding: very fast, but cuts are NOT frame-exact (each cut moves to the nearest keyframe)")
        sp.add_argument("--transcript", help="use this .srt/.vtt/.json transcript for captions instead of Whisper")

    e = sub.add_parser("edit", help="make the edited video", description="Apply a script to a video and write the result.")
    add_script_args(e, True)
    e.add_argument("--dry-run", action="store_true", help="only show the plan, do not write anything")
    e.add_argument("--overwrite", action="store_true", help="replace the output file if it exists")
    e.add_argument("--encoder", default="auto", help="auto (default), software, nvenc, qsv, amf or videotoolbox")
    e.add_argument("--workers", type=int, help="how many FFmpeg programs to run at the same time (default: chosen from your CPU and memory)")
    e.add_argument("--max-memory", type=float, default=DEFAULT_MEMORY_LIMIT_MB, metavar="MB", help="memory budget for the whole job (default 300)")
    e.add_argument("--no-resume", action="store_true", help="start from scratch instead of reusing finished pieces")
    e.add_argument("--keep-temp", action="store_true", help="keep temporary files (for debugging)")
    e.set_defaults(func=cmd_edit)

    pl = sub.add_parser("plan", help="show what an edit would do, without making it")
    add_script_args(pl)
    pl.add_argument("--json", action="store_true", help="print the plan as JSON")
    pl.set_defaults(func=cmd_plan)

    v = sub.add_parser("validate", help="check a script for mistakes")
    v.add_argument("script", nargs="?", default="-")
    v.add_argument("-e", "--inline", metavar="TEXT")
    v.add_argument("-i", "--input", help="also check the script against this video")
    v.set_defaults(func=cmd_validate)

    pr = sub.add_parser("probe", help="show facts about a video or audio file")
    pr.add_argument("file")
    pr.add_argument("--json", action="store_true")
    pr.set_defaults(func=cmd_probe)

    sub.add_parser("presets", help="list output presets").set_defaults(func=cmd_presets)
    en = sub.add_parser("encoders", help="list video encoders and whether they work on this computer")
    en.add_argument("--retest", action="store_true", help="test them again")
    en.set_defaults(func=cmd_encoders)

    px = sub.add_parser("proxy", help="make a small, fast copy of a video for quick previews")
    px.add_argument("file")
    px.add_argument("-o", "--output")
    px.add_argument("--height", type=int, default=480)
    px.add_argument("--overwrite", action="store_true")
    px.set_defaults(func=cmd_proxy)

    w = sub.add_parser("web", help="open the web page")
    w.add_argument("--host", default="127.0.0.1", help="address to listen on (default 127.0.0.1 = this computer only)")
    w.add_argument("--port", type=int, default=8765)
    w.add_argument("--no-open", action="store_true", help="do not open the browser automatically")
    w.add_argument("--media-dir", help="a folder of videos to offer in the web page")
    w.set_defaults(func=cmd_web)

    b = sub.add_parser("beats", help="find the tempo, beats and bars of a song or video")
    b.add_argument("file")
    b.add_argument("--json", action="store_true")
    b.set_defaults(func=cmd_beats)
    j = sub.add_parser("jobs", help="list earlier jobs")
    j.add_argument("--limit", type=int, default=20)
    j.set_defaults(func=cmd_jobs)
    sub.add_parser("commands", help="list every script instruction").set_defaults(func=cmd_commands)
    sub.add_parser("features", help="show what is switched on or off").set_defaults(func=cmd_features)
    c = sub.add_parser("clean", help="delete saved analysis and temporary files")
    c.add_argument("--all", action="store_true")
    c.set_defaults(func=cmd_clean)
    sub.add_parser("version", help="show the version").set_defaults(func=cmd_version)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return int(args.func(args) or 0)
    except EditForgeError as exc:
        print_error(exc, bool(getattr(args, "verbose", False)))
        return 1
    except KeyboardInterrupt:
        _err("\nCancelled.")
        return 130
    except BrokenPipeError:  # pragma: no cover
        return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
