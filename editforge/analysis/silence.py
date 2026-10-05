"""Silence detection using FFmpeg's silencedetect filter (streamed, so memory stays flat)."""

from __future__ import annotations

import re
import threading
from typing import List, Optional, Tuple

from ..core.errors import Cancelled
from ..core.ffmpeg import raise_for_result, run_ffmpeg

_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_END = re.compile(r"silence_end:\s*(-?[\d.]+)")


def detect_silences(path: str, noise_db: float = -35.0, min_duration: float = 0.3, *, duration: Optional[float] = None,
                    cancel: Optional[threading.Event] = None) -> List[Tuple[float, float]]:
    """Return silent stretches as (start, end) seconds.

    The audio is turned into 16 kHz mono first, which is much faster than reading it at full quality.
    """
    found: List[Tuple[float, float]] = []
    pending: List[Optional[float]] = [None]

    def on_line(line: str) -> None:
        m = _START.search(line)
        if m:
            pending[0] = max(0.0, float(m.group(1)))
            return
        m = _END.search(line)
        if m and pending[0] is not None:
            end = float(m.group(1))
            if end > pending[0]:
                found.append((pending[0], end))
            pending[0] = None

    graph = f"aresample=16000,aformat=channel_layouts=mono,silencedetect=noise={noise_db:g}dB:d={min_duration:g}"
    result = run_ffmpeg(["-i", path, "-map", "0:a:0", "-vn", "-af", graph, "-f", "null", "-"], loglevel="info",
                        on_stderr_line=on_line, cancel=cancel)
    if result.cancelled:
        raise Cancelled()
    raise_for_result(result, "listening for silence")
    if pending[0] is not None and duration is not None and duration > pending[0]:
        found.append((pending[0], duration))
    return found
