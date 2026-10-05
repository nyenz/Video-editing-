"""Reading facts about a media file (size, length, streams, rotation, frame rate)."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from typing import Any, Dict, List, Optional

from .errors import MediaError
from .ffmpeg import run_ffprobe_json

STANDARD_FPS = [Fraction(24000, 1001), Fraction(24), Fraction(25), Fraction(30000, 1001), Fraction(30),
                Fraction(50), Fraction(60000, 1001), Fraction(60)]


def parse_fraction(text: Any) -> Fraction:
    """Parse ffprobe's '30000/1001' style numbers. Returns 0 for '0/0'."""
    try:
        if isinstance(text, (int, float)):
            return Fraction(text).limit_denominator(100000)
        num, _, den = str(text).partition("/")
        if not den:
            return Fraction(num).limit_denominator(100000)
        return Fraction(int(num), int(den)) if int(den) else Fraction(0)
    except (ValueError, ZeroDivisionError):
        return Fraction(0)


def snap_fps(value: Fraction) -> Fraction:
    """Snap an average frame rate to a standard one when it is within 2%."""
    if value <= 0:
        return Fraction(25)
    best = min(STANDARD_FPS, key=lambda s: abs(float(s) - float(value)))
    if abs(float(best) - float(value)) / float(best) <= 0.02:
        return best
    return Fraction(round(float(value))).limit_denominator(1) if value >= 1 else Fraction(1)


@dataclass
class MediaInfo:
    """Everything EditForge needs to know about one media file."""

    path: str
    duration: float
    size_bytes: int
    format_name: str
    has_video: bool
    has_audio: bool
    width: int = 0
    height: int = 0
    rotation: int = 0
    fps: Fraction = field(default_factory=lambda: Fraction(25))
    avg_fps: Fraction = field(default_factory=lambda: Fraction(0))
    vfr: bool = False
    video_codec: str = ""
    pix_fmt: str = ""
    video_duration: float = 0.0
    nb_frames: Optional[int] = None
    audio_codec: str = ""
    sample_rate: int = 0
    channels: int = 0
    audio_duration: float = 0.0
    audio_streams: int = 0
    bit_rate: int = 0
    warnings: List[str] = field(default_factory=list)

    @property
    def is_audio_only(self) -> bool:
        return self.has_audio and not self.has_video

    @property
    def is_video_only(self) -> bool:
        return self.has_video and not self.has_audio

    @property
    def total_frames(self) -> int:
        return int(round(self.duration * float(self.fps)))

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["fps"] = f"{self.fps.numerator}/{self.fps.denominator}"
        d["avg_fps"] = f"{self.avg_fps.numerator}/{self.avg_fps.denominator}"
        d["fps_float"] = round(float(self.fps), 3)
        return d


def _rotation_of(stream: Dict[str, Any]) -> int:
    for side in stream.get("side_data_list", []) or []:
        if "rotation" in side:
            try:
                return int(round(float(side["rotation"]))) % 360
            except (TypeError, ValueError):
                pass
    tag = (stream.get("tags") or {}).get("rotate")
    if tag:
        try:
            return int(tag) % 360
        except ValueError:
            pass
    return 0


def probe_media(path: str) -> MediaInfo:
    """Read a media file with ffprobe.

    Raises:
        MediaError: with a plain-English message if the file is missing or unreadable.
    """
    path = str(path)
    if not os.path.exists(path):
        raise MediaError(f"I can't find the file '{path}'.", "Check the spelling and the folder, or drag the file into the window.")
    if os.path.isdir(path):
        raise MediaError(f"'{path}' is a folder, not a video or audio file.", "Choose the video file itself.")
    try:
        data = run_ffprobe_json(path)
    except MediaError:
        raise
    except Exception as exc:
        raise MediaError(f"'{os.path.basename(path)}' does not look like a video or audio file (or it is damaged).",
                         "Try playing it in a media player. If it plays there, re-export it and try again.",
                         details=str(exc))
    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    vids = [s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")]
    auds = [s for s in streams if s.get("codec_type") == "audio"]
    if not vids and not auds:
        raise MediaError(f"'{os.path.basename(path)}' has no video or audio that FFmpeg can read.",
                         "Choose a real video or audio file (mp4, mov, mkv, webm, mp3, wav, m4a ...).")
    try:
        duration = float(fmt.get("duration") or 0)
    except ValueError:
        duration = 0.0
    def sdur(s: Dict[str, Any]) -> float:
        try:
            return float(s.get("duration") or 0)
        except ValueError:
            return 0.0
    v_dur = sdur(vids[0]) if vids else 0.0
    a_dur = sdur(auds[0]) if auds else 0.0
    if duration <= 0:
        duration = max(v_dur, a_dur)
    if duration <= 0:
        raise MediaError(f"I can't tell how long '{os.path.basename(path)}' is.",
                         "The file may be incomplete. Re-export it, or remux it: ffmpeg -i in.ext -c copy fixed.mp4")
    info = MediaInfo(path=path, duration=duration, size_bytes=int(fmt.get("size") or os.path.getsize(path)),
                     format_name=str(fmt.get("format_name", "")), has_video=bool(vids), has_audio=bool(auds),
                     bit_rate=int(fmt.get("bit_rate") or 0) if str(fmt.get("bit_rate") or "0").isdigit() else 0)
    if vids:
        v = vids[0]
        w, h = int(v.get("width") or 0), int(v.get("height") or 0)
        rot = _rotation_of(v)
        if rot in (90, 270):
            w, h = h, w
            info.warnings.append(f"This video is rotated ({rot} degrees, typical for phone video); EditForge turns it upright.")
        info.width, info.height, info.rotation = w, h, rot
        r_fps, avg = parse_fraction(v.get("r_frame_rate")), parse_fraction(v.get("avg_frame_rate"))
        info.avg_fps = avg
        base = avg if avg > 0 else r_fps
        info.vfr = bool(avg > 0 and r_fps > 0 and abs(float(r_fps) - float(avg)) / float(avg) > 0.02)
        info.fps = snap_fps(base) if info.vfr or base <= 0 else (r_fps if r_fps > 0 else snap_fps(base))
        if info.vfr:
            info.warnings.append(
                f"This video has a variable frame rate; EditForge converts it to a steady {float(info.fps):.3f} frames per second.")
        info.video_codec = str(v.get("codec_name", ""))
        info.pix_fmt = str(v.get("pix_fmt", ""))
        info.video_duration = v_dur or duration
        try:
            info.nb_frames = int(v["nb_frames"]) if v.get("nb_frames") else None
        except (ValueError, TypeError):
            info.nb_frames = None
        if w <= 0 or h <= 0:
            raise MediaError(f"'{os.path.basename(path)}' reports a picture size of {w}x{h}, which can't be used.",
                             "Re-export the video and try again.")
    if auds:
        a = auds[0]
        info.audio_codec = str(a.get("codec_name", ""))
        info.sample_rate = int(a.get("sample_rate") or 0)
        info.channels = int(a.get("channels") or 0)
        info.audio_duration = a_dur or duration
        info.audio_streams = len(auds)
        if len(auds) > 1:
            info.warnings.append(f"This file has {len(auds)} audio tracks; EditForge uses the first one.")
    if not info.has_video:
        info.fps = Fraction(25)
    return info
