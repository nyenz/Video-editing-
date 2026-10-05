"""Named output presets and helpers to choose the final size, frame rate and quality."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Dict, List, Optional, Tuple

from ..core.errors import ScriptError
from ..core.media import MediaInfo, snap_fps
from ..core.model import OutputSpec
from ..dsl.registry import norm, suggest


@dataclass(frozen=True)
class Preset:
    name: str
    label: str
    description: str
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None
    crf: int = 20
    x264_preset: str = "veryfast"
    audio_bitrate: str = "192k"
    container: str = "mp4"
    fit: str = "pad"
    has_video: bool = True
    channels: Optional[int] = None
    sample_rate: int = 48000
    aliases: Tuple[str, ...] = ()
    max_fps: Optional[float] = None


PRESETS: List[Preset] = [
    Preset("original", "Same as the source", "Keeps the picture size and frame rate of your file. Good default.",
           aliases=("same", "source", "default", "auto", "keep")),
    Preset("youtube_1080p", "YouTube 1080p", "1920x1080, good quality, plays everywhere.", 1920, 1080, None, 19, "medium",
           "192k", aliases=("youtube", "1080p", "hd", "full_hd"), max_fps=60),
    Preset("youtube_4k", "YouTube 4K", "3840x2160, high quality (slow to make).", 3840, 2160, None, 18, "medium", "256k",
           aliases=("4k", "uhd", "2160p"), max_fps=60),
    Preset("shorts", "Shorts / Reels / TikTok (9:16)", "1080x1920 vertical video; a blurred copy fills the empty space.",
           1080, 1920, None, 20, "veryfast", "160k", fit="blur",
           aliases=("reels", "tiktok", "youtube_shorts", "vertical", "instagram_reels", "story"), max_fps=60),
    Preset("instagram_square", "Instagram square (1:1)", "1080x1080 square video with a blurred background.", 1080, 1080,
           None, 20, "veryfast", "160k", fit="blur", aliases=("square", "instagram", "1x1"), max_fps=60),
    Preset("preview_480p", "480p preview", "Small and fast to make. Use it to check an edit quickly.", None, 480, None, 30,
           "ultrafast", "96k", aliases=("preview", "draft", "480p", "proxy_preview"), max_fps=30),
    Preset("podcast_audio", "Podcast audio (M4A)", "Sound only: mono AAC at 96 kbps, 44.1 kHz.", crf=0, audio_bitrate="96k",
           container="m4a", has_video=False, channels=1, sample_rate=44100, aliases=("podcast", "audio", "audio_only", "m4a")),
    Preset("podcast_mp3", "Podcast audio (MP3)", "Sound only: mono MP3 at 96 kbps, 44.1 kHz.", crf=0, audio_bitrate="96k",
           container="mp3", has_video=False, channels=1, sample_rate=44100, aliases=("mp3",)),
    Preset("gif", "Animated GIF", "480 pixels wide, 12 frames per second, no sound.", 480, None, 12, 23, "veryfast", "96k",
           container="gif", aliases=("animated_gif",)),
]

_BY_NAME: Dict[str, Preset] = {}
for _p in PRESETS:
    _BY_NAME[_p.name] = _p
    for _a in _p.aliases:
        _BY_NAME.setdefault(norm(_a), _p)


def get_preset(name: str) -> Preset:
    """Find a preset by name or alias, or raise a friendly error with a suggestion."""
    key = norm(name)
    if key in _BY_NAME:
        return _BY_NAME[key]
    close = suggest(key, list(_BY_NAME))
    hint = f" Did you mean '{_BY_NAME[close[0]].name}'?" if close else ""
    raise ScriptError(f"I don't know the preset '{name}'.{hint}",
                      "Run 'editforge presets' to see the list. Names: " + ", ".join(p.name for p in PRESETS) + ".")


def _even(v: float) -> int:
    v = int(round(v))
    return max(2, v - v % 2)


def resolve_output(media: MediaInfo, *, preset: str = "original", size: Optional[str] = None, fit: Optional[str] = None,
                   fps: Optional[float] = None, quality: Optional[str] = None, output_ext: Optional[str] = None,
                   preview: bool = False, notes: Optional[List[str]] = None) -> OutputSpec:
    """Decide the exact output settings from a preset plus the user's overrides."""
    notes = notes if notes is not None else []
    p = get_preset("preview_480p" if preview else preset)
    ext = (output_ext or "").lower().lstrip(".")
    video_exts = {"mp4": "mp4", "m4v": "mp4", "mov": "mov", "mkv": "mkv"}
    audio_exts = {"m4a": "m4a", "aac": "m4a", "mp3": "mp3", "wav": "wav", "flac": "flac"}
    container = p.container
    has_video = p.has_video and media.has_video
    if p.has_video and not media.has_video:
        notes.append("Your file has no picture, so the result is an audio file.")
        container = "m4a"
    if ext:
        if ext == "gif":
            container = "gif" if media.has_video else container
        elif ext in audio_exts and (not p.has_video or not media.has_video or True):
            if media.has_video and p.has_video and ext in audio_exts:
                has_video = False
                notes.append(f"You chose a .{ext} file name, so only the sound is saved.")
            container = audio_exts[ext]
        elif ext in video_exts and has_video:
            container = video_exts[ext]
        elif ext in video_exts and not has_video:
            notes.append(f"'.{ext}' is a video name but there is no picture; a .m4a audio file is made instead.")
            container = "m4a"
    if container in ("gif",) and not media.has_video:
        container = "m4a"
    if container in ("m4a", "mp3", "wav", "flac"):
        has_video = False
    w = h = 0
    if has_video:
        sw, sh = media.width, media.height
        if size:
            w, h = (int(x) for x in size.split("x"))
        elif p.width and p.height:
            w, h = p.width, p.height
        elif p.height and not p.width:
            h = p.height
            w = _even(h * sw / sh)
        elif p.width and not p.height:
            w = p.width
            h = _even(w * sh / sw)
        else:
            w, h = sw, sh
        w, h = _even(w), _even(h)
        if p.name == "original" and not size and (sw % 2 or sh % 2):
            w, h = _even(sw), _even(sh)
    out_fps = Fraction(fps).limit_denominator(1001) if fps else None
    if out_fps is None:
        if p.fps:
            out_fps = Fraction(p.fps).limit_denominator(1001)
        elif media.has_video:
            out_fps = media.fps
            if p.max_fps and float(out_fps) > p.max_fps:
                out_fps = snap_fps(Fraction(p.max_fps).limit_denominator(1001))
                notes.append(f"The frame rate was lowered to {float(out_fps):.3g} fps for this preset.")
        else:
            out_fps = Fraction(25)
    if float(out_fps) > 240:
        out_fps = Fraction(240)
    crf = p.crf
    if quality:
        crf = int(quality.split(":")[1]) if quality.startswith("crf:") else crf
    chosen_fit = fit or p.fit
    channels = p.channels or min(2, max(1, media.channels or 2))
    if not media.has_audio:
        channels = p.channels or 2
    return OutputSpec(preset=p.name, container=container, width=w, height=h, fps=out_fps, fit=chosen_fit, crf=crf,
                      x264_preset=p.x264_preset, audio_bitrate=p.audio_bitrate, sample_rate=p.sample_rate if p.channels else 48000,
                      channels=channels, has_video=has_video, has_audio=True, faststart=True)


def default_extension(spec: OutputSpec) -> str:
    return {"mp4": "mp4", "mov": "mov", "mkv": "mkv", "gif": "gif", "m4a": "m4a", "mp3": "mp3", "wav": "wav",
            "flac": "flac"}.get(spec.container, "mp4")
