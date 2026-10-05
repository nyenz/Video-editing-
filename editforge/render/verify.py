"""Checking the finished file with ffprobe."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..core.errors import EditForgeError
from ..core.media import probe_media
from ..core.model import OutputSpec


@dataclass
class VerifyReport:
    ok: bool
    problems: List[str] = field(default_factory=list)
    duration: float = 0.0
    video_duration: float = 0.0
    audio_duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    has_video: bool = False
    has_audio: bool = False
    size_bytes: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def verify_output(path: str, expected_seconds: float, spec: OutputSpec, expect_audio: bool, fps: float,
                  check_size: bool = True) -> VerifyReport:
    """Read the finished file back and compare it to the plan (length, size, streams)."""
    rep = VerifyReport(ok=False)
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        rep.problems.append("The finished file is missing or empty.")
        return rep
    rep.size_bytes = os.path.getsize(path)
    try:
        info = probe_media(path)
    except EditForgeError as exc:
        rep.problems.append("The finished file could not be read back: " + exc.message)
        return rep
    rep.duration, rep.has_video, rep.has_audio = info.duration, info.has_video, info.has_audio
    rep.video_duration, rep.audio_duration = info.video_duration, info.audio_duration
    rep.width, rep.height, rep.fps = info.width, info.height, float(info.fps)
    tol = max(1.0 / max(fps, 1.0), 0.04) + (0.03 if info.has_audio else 0.0)
    if abs(info.duration - expected_seconds) > tol + 0.001:
        rep.problems.append(f"The length is {info.duration:.3f}s but the plan said {expected_seconds:.3f}s.")
    if spec.has_video and not info.has_video:
        rep.problems.append("The finished file has no picture.")
    if spec.has_video and check_size and info.has_video and (info.width, info.height) != (spec.width, spec.height):
        rep.problems.append(f"The picture size is {info.width}x{info.height} but {spec.width}x{spec.height} was expected.")
    if not spec.has_video and info.has_video and spec.container not in ("gif",):
        rep.problems.append("The finished file has a picture but only sound was expected.")
    if expect_audio and not info.has_audio and spec.container != "gif":
        rep.problems.append("The finished file has no sound.")
    rep.ok = not rep.problems
    return rep
