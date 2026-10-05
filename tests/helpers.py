"""Helpers for the tests: make media, render edits, and inspect the output files."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from editforge.api import Plan, prepare, read_script  # noqa: E402
from editforge.core.ffmpeg import get_tools  # noqa: E402
from editforge.core.media import MediaInfo, probe_media  # noqa: E402
from editforge.render.engine import RenderOptions, RenderResult, render  # noqa: E402


def ff(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run([get_tools().ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args], capture_output=True, timeout=timeout)


def edit(script: str, src: str, out: str, *, base_dir: Optional[str] = None, overwrite: bool = True, render_opts: Optional[dict] = None,
         **kw) -> Tuple[Plan, RenderResult]:
    """Plan and render a script; returns (plan, result)."""
    plan = prepare(read_script(script), input_path=src, output_path=out, base_dir=base_dir or os.path.dirname(src), **kw)
    opts = RenderOptions(overwrite=overwrite, **(render_opts or {}))
    return plan, render(plan, opts)


def plan_only(script: str, src: str, **kw) -> Plan:
    return prepare(read_script(script), input_path=src, base_dir=os.path.dirname(src), **kw)


def count_frames(path: str) -> int:
    r = subprocess.run([get_tools().ffprobe, "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                        "stream=nb_read_frames", "-of", "csv=p=0", path], capture_output=True, text=True)
    return int(r.stdout.strip().split(",")[0])


def stream_info(path: str, kind: str = "v") -> dict:
    import json
    r = subprocess.run([get_tools().ffprobe, "-v", "error", "-select_streams", f"{kind}:0", "-show_streams", "-of", "json", path],
                       capture_output=True, text=True)
    streams = json.loads(r.stdout or "{}").get("streams", [])
    return streams[0] if streams else {}


def frame_gray(path: str, t: float, w: int = 64, h: int = 36) -> bytes:
    r = subprocess.run([get_tools().ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.4f}", "-i", path, "-frames:v", "1",
                        "-vf", f"scale={w}:{h}:flags=area,format=gray", "-f", "rawvideo", "-"], capture_output=True)
    return r.stdout


def frame_rgb(path: str, t: float, w: int = 1, h: int = 1) -> bytes:
    r = subprocess.run([get_tools().ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.4f}", "-i", path, "-frames:v", "1",
                        "-vf", f"scale={w}:{h}:flags=area,format=rgb24", "-f", "rawvideo", "-"], capture_output=True)
    return r.stdout


def mean_luma(path: str, t: float) -> float:
    g = frame_gray(path, t, 16, 9)
    return sum(g) / max(1, len(g))


def color_at(path: str, t: float) -> Tuple[int, int, int]:
    d = frame_rgb(path, t)
    return d[0], d[1], d[2]


def diff_score(a: bytes, b: bytes) -> float:
    n = min(len(a), len(b))
    return sum(abs(a[i] - b[i]) for i in range(n)) / max(1, n)


def audio_level(path: str, start: float, length: float) -> Tuple[float, float]:
    """(mean dB, max dB) of a stretch of the sound."""
    r = subprocess.run([get_tools().ffmpeg, "-nostdin", "-hide_banner", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", path, "-vn",
                        "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True)
    m = re.search(r"mean_volume:\s*(-?[\d.]+|-inf) dB", r.stderr)
    x = re.search(r"max_volume:\s*(-?[\d.]+|-inf) dB", r.stderr)
    conv = lambda s: -99.0 if s is None or s == "-inf" else float(s)
    return conv(m.group(1) if m else None), conv(x.group(1) if x else None)


def loudness(path: str) -> Tuple[float, float]:
    """(integrated LUFS, true-peak dBTP) measured with the ebur128 filter."""
    r = subprocess.run([get_tools().ffmpeg, "-nostdin", "-hide_banner", "-i", path, "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True)
    tail = r.stderr[r.stderr.rfind("Summary:"):]
    i = re.search(r"I:\s+(-?[\d.]+) LUFS", tail)
    p = re.search(r"Peak:\s+(-?[\d.]+) dBFS", tail)
    return (float(i.group(1)) if i else -99.0, float(p.group(1)) if p else -99.0)


def band_level(path: str, start: float, length: float, freq: int) -> float:
    """Level (dB) of a narrow band around ``freq`` Hz."""
    r = subprocess.run([get_tools().ffmpeg, "-nostdin", "-hide_banner", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", path, "-vn", "-af",
                        f"bandpass=f={freq}:width_type=h:width=60,volumedetect", "-f", "null", "-"], capture_output=True, text=True)
    m = re.search(r"mean_volume:\s*(-?[\d.]+|-inf) dB", r.stderr)
    return -99.0 if not m or m.group(1) == "-inf" else float(m.group(1))


def hf_noise_level(path: str, start: float = 0.0, length: float = 2.0) -> float:
    r = subprocess.run([get_tools().ffmpeg, "-nostdin", "-hide_banner", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", path, "-vn", "-af",
                        "highpass=f=4000,volumedetect", "-f", "null", "-"], capture_output=True, text=True)
    m = re.search(r"mean_volume:\s*(-?[\d.]+|-inf) dB", r.stderr)
    return -99.0 if not m or m.group(1) == "-inf" else float(m.group(1))


def frame_times(path: str) -> List[float]:
    r = subprocess.run([get_tools().ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "frame=pts_time", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    return [float(x.split(",")[0]) for x in r.stdout.split() if x.strip()]
