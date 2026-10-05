"""Scene-change detection using FFmpeg's built-in scene score (no extra software needed)."""

from __future__ import annotations

import re
import threading
from typing import List, Optional, Tuple

from ..core.errors import Cancelled
from ..core.ffmpeg import raise_for_result, run_ffmpeg

_PTS = re.compile(r"pts_time:\s*(-?[\d.]+)")
_SCORE = re.compile(r"lavfi\.scene_score=([\d.]+)")


def detect_scene_scores(path: str, floor: float = 0.08, *, cancel: Optional[threading.Event] = None) -> List[Tuple[float, float]]:
    """Return every frame whose scene-change score is above ``floor`` as (time, score)."""
    out: List[Tuple[float, float]] = []
    last_t = [0.0]

    def on_line(line: str) -> None:
        m = _PTS.search(line)
        if m:
            last_t[0] = float(m.group(1))
            return
        m = _SCORE.search(line)
        if m:
            out.append((last_t[0], float(m.group(1))))

    graph = f"scale=160:-2:flags=fast_bilinear,select='gt(scene,{floor:g})',metadata=mode=print:file=-"
    result = run_ffmpeg(["-i", path, "-map", "0:v:0", "-an", "-vf", graph, "-f", "null", "-"], on_stdout_line=on_line, cancel=cancel)
    if result.cancelled:
        raise Cancelled()
    raise_for_result(result, "looking for scene changes")
    return out


def scenes_from_scores(scores: List[Tuple[float, float]], threshold: float, min_scene: float, duration: float) -> List[float]:
    """Pick scene boundaries: scores above the threshold that are at least ``min_scene`` apart."""
    bounds: List[float] = []
    last = 0.0
    for t, s in sorted(scores):
        if s >= threshold and t - last >= min_scene and duration - t >= min_scene * 0.5 and t > 0:
            bounds.append(round(t, 6))
            last = t
    return bounds
