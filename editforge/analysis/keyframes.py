"""Keyframe times (read from packet headers without decoding, so it is fast)."""

from __future__ import annotations

import subprocess
from typing import List

from ..core.ffmpeg import get_tools


def keyframe_times(path: str) -> List[float]:
    """Return the time of every keyframe of the first video stream."""
    tools = get_tools()
    cmd = [tools.ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time,flags",
           "-of", "csv=p=0", path]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    times: List[float] = []
    assert proc.stdout is not None
    for raw in proc.stdout:
        parts = raw.decode("utf-8", "replace").strip().split(",")
        if len(parts) >= 2 and "K" in parts[1]:
            try:
                times.append(float(parts[0]))
            except ValueError:
                continue
    proc.wait()
    return sorted(set(times))
