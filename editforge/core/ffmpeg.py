"""Finding, describing and running FFmpeg / ffprobe."""

from __future__ import annotations

import atexit
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Deque, Dict, FrozenSet, List, Optional, Sequence, Tuple

from .errors import Cancelled, MissingToolError, RenderError

_LOCK = threading.RLock()
_TOOLS: Optional["Tools"] = None


def install_help() -> str:
    """Plain-English steps for installing FFmpeg on this computer."""
    system = platform.system()
    if system == "Windows":
        return ("Open the Start menu, type 'cmd', and run:  winget install Gyan.FFmpeg   "
                "(or download it from https://www.gyan.dev/ffmpeg/builds/ and add its 'bin' folder to PATH). "
                "Then close and reopen this window.")
    if system == "Darwin":
        return "Install Homebrew from https://brew.sh, then run:  brew install ffmpeg"
    return "Run:  sudo apt install ffmpeg   (Ubuntu/Debian)  or  sudo dnf install ffmpeg   (Fedora)."


def _candidates(name: str) -> List[str]:
    env = os.environ.get("EDITFORGE_" + name.upper())
    out = [env] if env else []
    found = shutil.which(name)
    if found:
        out.append(found)
    exe = name + (".exe" if os.name == "nt" else "")
    for folder in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "C:\\ffmpeg\\bin",
                   "C:\\Program Files\\ffmpeg\\bin", str(Path.home() / "ffmpeg" / "bin")):
        out.append(str(Path(folder) / exe))
    return out


def find_executable(name: str) -> Optional[str]:
    """Return the full path of ``ffmpeg`` or ``ffprobe`` or None."""
    for cand in _candidates(name):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


@dataclass
class Tools:
    """Paths and capabilities of the installed FFmpeg."""

    ffmpeg: str
    ffprobe: str
    version: str
    version_tuple: Tuple[int, int, int]
    filters: FrozenSet[str]
    encoders: FrozenSet[str]
    amix_normalize: bool = False

    def has_filter(self, name: str) -> bool:
        return name in self.filters

    def has_encoder(self, name: str) -> bool:
        return name in self.encoders

    def at_least(self, major: int, minor: int = 0) -> bool:
        return self.version_tuple[:2] >= (major, minor)


def _run_simple(cmd: Sequence[str], timeout: float = 30) -> Tuple[int, str, str]:
    proc = subprocess.run(list(cmd), capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
    return proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")


def get_tools(refresh: bool = False) -> Tools:
    """Locate FFmpeg once and learn what it can do.

    Raises:
        MissingToolError: if FFmpeg or ffprobe is not installed.
    """
    global _TOOLS
    with _LOCK:
        if _TOOLS is not None and not refresh:
            return _TOOLS
        ffmpeg, ffprobe = find_executable("ffmpeg"), find_executable("ffprobe")
        if not ffmpeg or not ffprobe:
            missing = "FFmpeg" if not ffmpeg else "ffprobe"
            raise MissingToolError(f"{missing} is not installed (or EditForge can't find it).", install_help())
        try:
            _, out, err = _run_simple([ffmpeg, "-hide_banner", "-version"])
            text = out or err
            m = re.search(r"version\s+n?(\d+)\.(\d+)(?:\.(\d+))?", text)
            if m:
                vt = (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))
            else:
                vt = (99, 0, 0)  # development builds are newer than any release
            version = text.splitlines()[0] if text else "unknown"
            _, fout, _ = _run_simple([ffmpeg, "-hide_banner", "-filters"])
            filters = frozenset(mm.group(1) for mm in re.finditer(r"^\s*[A-Z.]{2,4}\s+(\S+)\s", fout, re.M))
            _, eout, _ = _run_simple([ffmpeg, "-hide_banner", "-encoders"])
            encoders = frozenset(mm.group(1) for mm in re.finditer(r"^\s*[VAS][A-Z.]{5}\s+(\S+)\s", eout, re.M))
            _, aout, aerr = _run_simple([ffmpeg, "-hide_banner", "-h", "filter=amix"])
            amix_norm = "normalize" in (aout + aerr)
        except (OSError, subprocess.SubprocessError) as exc:
            raise MissingToolError(f"FFmpeg was found but could not be started ({exc}).", install_help())
        _TOOLS = Tools(ffmpeg, ffprobe, version, vt, filters, encoders, amix_norm)
        return _TOOLS


def vsync_args(mode: str = "passthrough") -> List[str]:
    """Return the right 'frame timing' option for this FFmpeg version."""
    tools = get_tools()
    if tools.at_least(5, 1):
        return ["-fps_mode", mode]
    return ["-vsync", {"passthrough": "0", "cfr": "1", "vfr": "2"}.get(mode, "0")]


# ----------------------------------------------------------------------------
# Running processes safely
# ----------------------------------------------------------------------------

_CHILDREN: "set[subprocess.Popen]" = set()
_CHILD_LOCK = threading.Lock()


def _kill_all_children() -> None:
    with _CHILD_LOCK:
        procs = list(_CHILDREN)
    for proc in procs:
        try:
            proc.kill()
        except OSError:
            pass


atexit.register(_kill_all_children)


@dataclass
class RunResult:
    """Outcome of one FFmpeg run."""

    returncode: int
    stderr_tail: str
    seconds: float
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.cancelled


def run_ffmpeg(args: Sequence[str], *, cwd: Optional[str] = None, cancel: Optional[threading.Event] = None,
               on_progress: Optional[Callable[[Dict[str, str]], None]] = None,
               on_stdout_line: Optional[Callable[[str], None]] = None,
               on_stderr_line: Optional[Callable[[str], None]] = None,
               loglevel: str = "error", tail_bytes: int = 6000, timeout: Optional[float] = None,
               threads: Optional[int] = None) -> RunResult:
    """Run FFmpeg, streaming its output so memory use stays tiny.

    Only the last ``tail_bytes`` of error text are kept. The process is killed if
    ``cancel`` is set or ``timeout`` seconds pass.
    """
    tools = get_tools()
    cmd = [tools.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", loglevel]
    if on_progress:
        cmd += ["-progress", "pipe:1", "-nostats"]
    cmd += list(args)
    popen_kwargs = {}
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
    elif os.name == "nt":  # pragma: no cover - Windows only
        popen_kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    start = time.time()
    proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, bufsize=0, **popen_kwargs)
    with _CHILD_LOCK:
        _CHILDREN.add(proc)
    tail: Deque[bytes] = deque()
    tail_size = [0]

    def read_stdout() -> None:
        block: Dict[str, str] = {}
        assert proc.stdout is not None
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if on_progress and "=" in line and not on_stdout_line:
                key, _, val = line.partition("=")
                block[key.strip()] = val.strip()
                if key.strip() == "progress":
                    try:
                        on_progress(dict(block))
                    except Exception:
                        pass
                    block = {}
            elif on_stdout_line:
                try:
                    on_stdout_line(line)
                except Exception:
                    pass

    def read_stderr() -> None:
        assert proc.stderr is not None
        for raw in iter(proc.stderr.readline, b""):
            if on_stderr_line:
                try:
                    on_stderr_line(raw.decode("utf-8", "replace").rstrip("\r\n"))
                except Exception:
                    pass
            tail.append(raw)
            tail_size[0] += len(raw)
            while tail_size[0] > tail_bytes and len(tail) > 1:
                tail_size[0] -= len(tail.popleft())

    t_out = threading.Thread(target=read_stdout, daemon=True)
    t_err = threading.Thread(target=read_stderr, daemon=True)
    t_out.start()
    t_err.start()
    cancelled = False
    try:
        while proc.poll() is None:
            if cancel is not None and cancel.is_set():
                cancelled = True
                _terminate(proc)
                break
            if timeout is not None and time.time() - start > timeout:
                _terminate(proc)
                break
            time.sleep(0.02)
        proc.wait()
    finally:
        t_out.join(timeout=2)
        t_err.join(timeout=2)
        with _CHILD_LOCK:
            _CHILDREN.discard(proc)
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream:
                    stream.close()
            except OSError:
                pass
    return RunResult(proc.returncode, b"".join(tail).decode("utf-8", "replace"), time.time() - start, cancelled)


def _terminate(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
        for _ in range(50):
            if proc.poll() is not None:
                return
            time.sleep(0.02)
        proc.kill()
    except OSError:
        pass


def run_ffprobe_json(path: str, extra: Sequence[str] = ()) -> dict:
    """Run ffprobe on a file and return its JSON answer."""
    tools = get_tools()
    cmd = [tools.ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", *extra, path]
    proc = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, timeout=120)
    if proc.returncode != 0:
        raise RenderError("ffprobe could not read the file.", "Check that the file is a real video or audio file.",
                          details=proc.stderr.decode("utf-8", "replace")[-800:])
    return json.loads(proc.stdout.decode("utf-8", "replace") or "{}")


def friendly_ffmpeg_error(stderr: str, returncode: int = 1) -> Tuple[str, str]:
    """Turn FFmpeg's technical error text into (message, how to fix)."""
    low = stderr.lower()
    if returncode in (-9, 137) or "cannot allocate memory" in low or "out of memory" in low:
        return ("The computer ran out of memory while making the video.",
                "Close other programs, or run again with a smaller size (for example --preset preview_480p) "
                "or a lower --max-memory.")
    if "no space left" in low:
        return ("The disk is full.", "Free some space on the drive (the video needs room for temporary files) and try again.")
    if "permission denied" in low:
        return ("EditForge is not allowed to read or write one of the files.",
                "Choose an output folder you can write to, and make sure the input file is not open in another program.")
    if "no such filter" in low or "filter not found" in low:
        m = re.search(r"no such filter: '?([\w]+)", low)
        name = m.group(1) if m else "a needed"
        return (f"Your FFmpeg does not include the '{name}' filter.",
                "Install a full FFmpeg build (see the README, 'Install FFmpeg'), then try again.")
    if "unknown encoder" in low or "encoder not found" in low or "unknown decoder" in low:
        return ("Your FFmpeg does not include a video or audio encoder that is needed.",
                "Install a full FFmpeg build that includes libx264 (see the README).")
    if "invalid data found" in low or "moov atom not found" in low or "could not find codec parameters" in low:
        return ("This file looks damaged or is not a video/audio file that FFmpeg can read.",
                "Try opening it in a media player. If it plays there, re-save or re-export it and try again.")
    if "no such file or directory" in low:
        return ("FFmpeg could not find one of the files it needs.",
                "Check the file names in your script, and that the files have not been moved or deleted.")
    if "fontconfig" in low or "cannot find a valid font" in low or "could not load font" in low:
        return ("No usable font was found for the on-screen text.",
                "Install a common font (for example DejaVu Sans or Arial) or give font=<path to a .ttf file>.")
    if "error while opening encoder" in low or "cannot open encoder" in low:
        return ("The video encoder could not start.",
                "Try again with --encoder software, or use a smaller picture size.")
    return ("FFmpeg stopped with an error while making the video.",
            "Look at the technical details below. Running the same edit with --preset preview_480p often helps.")


def raise_for_result(result: RunResult, what: str = "making the video") -> None:
    """Raise a plain-English RenderError if the run failed."""
    if result.cancelled:
        raise Cancelled()
    if result.returncode != 0:
        message, fix = friendly_ffmpeg_error(result.stderr_tail, result.returncode)
        raise RenderError(f"{message} (while {what})", fix, details=result.stderr_tail[-1500:])


def escape_concat_path(path: str) -> str:
    """Escape a path for use inside an FFmpeg concat list (single quotes)."""
    return path.replace("\\", "/").replace("'", "'\\''")
