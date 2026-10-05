"""The internal timeline model. The planner builds it; the renderer only reads it."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from fractions import Fraction
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class OutputSpec:
    """Everything about the finished file."""

    preset: str = "original"
    container: str = "mp4"          # mp4 | mkv | mov | gif | m4a | mp3 | wav | flac
    width: int = 0
    height: int = 0
    fps: Fraction = field(default_factory=lambda: Fraction(25))
    fit: str = "pad"
    crf: int = 20
    x264_preset: str = "veryfast"
    audio_bitrate: str = "192k"
    sample_rate: int = 48000
    channels: int = 2
    has_video: bool = True
    has_audio: bool = True
    faststart: bool = True
    gif_colors: int = 256

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["fps"] = f"{self.fps.numerator}/{self.fps.denominator}"
        return d


@dataclass
class EffectSpec:
    """A look or a short pulse applied to a segment."""

    name: str
    strength: float = 0.5
    kind: str = "look"  # "look" (whole segment) or "pulse" (a beat pulse: strongest at the start, fading out)


@dataclass
class CameraSpec:
    """Zoom and pan, defined over a range of source time so it stays smooth across cuts."""

    z0: float = 1.0
    z1: float = 1.0
    x0: float = 0.5
    x1: float = 0.5
    y0: float = 0.5
    y1: float = 0.5
    easing: str = "linear"
    r0: float = 0.0
    r1: float = 1.0
    pulse: bool = False  # a beat pulse: zoom in then relax within the segment


@dataclass
class ColorSpec:
    brightness: float = 0.0
    contrast: float = 1.0
    saturation: float = 1.0
    gamma: float = 1.0
    hue: float = 0.0


@dataclass
class CropSpec:
    aspect: Optional[str] = None
    box: Optional[List[str]] = None
    position: str = "center"


@dataclass
class ReframeSpec:
    """Auto re-frame. ``path`` holds (source time, centre fraction 0..1) keyframes when faces were tracked."""

    aspect: str = "9:16"
    mode: str = "center"
    smooth: float = 0.7
    path: Optional[List[Tuple[float, float]]] = None


@dataclass
class JoinSpec:
    """A transition shared by two neighbouring segments."""

    type: str = "fade"
    frames: int = 12


@dataclass
class Segment:
    """One stretch of the output made from one stretch of the source."""

    kind: str                     # "clip" or "freeze"
    src_start_f: int              # first source frame, counted on the output frame grid
    src_len_f: int                # source frames used (1 for a freeze)
    frames: int                   # output frames
    reverse: bool = False
    mute: bool = False
    pitch: bool = True
    volume: float = 1.0
    effects: List[EffectSpec] = field(default_factory=list)
    camera: Optional[CameraSpec] = None
    crop: Optional[CropSpec] = None
    reframe: Optional[ReframeSpec] = None
    color: Optional[ColorSpec] = None
    join_in: Optional[JoinSpec] = None
    join_out: Optional[JoinSpec] = None
    out_start: int = 0
    lines: List[int] = field(default_factory=list)
    rev_group: int = -1

    @property
    def speed(self) -> float:
        """Effective playback speed (source frames per output frame)."""
        return self.src_len_f / self.frames if self.frames else 1.0

    @property
    def head(self) -> int:
        return self.join_in.frames if self.join_in else 0

    @property
    def tail(self) -> int:
        return self.join_out.frames if self.join_out else 0

    @property
    def out_end(self) -> int:
        return self.out_start + self.frames


@dataclass
class TextOverlay:
    text: str
    start_f: int
    end_f: int
    position: str = "bottom"
    x: Optional[str] = None
    y: Optional[str] = None
    size: float = 48.0
    color: str = "#ffffff"
    font: Optional[str] = None
    box: bool = False
    box_color: str = "#000000@0.5"
    box_padding: float = 12.0
    border: float = 0.0
    border_color: str = "#000000"
    shadow: bool = False
    fade: float = 0.0
    line: int = 0


@dataclass
class ImageOverlay:
    file: str
    start_f: int
    end_f: int
    position: str = "top_right"
    x: Optional[str] = None
    y: Optional[str] = None
    scale: float = 0.15
    width: Optional[float] = None
    opacity: float = 1.0
    margin: float = 24.0
    line: int = 0


@dataclass
class MusicSpec:
    file: str
    volume: float = 0.25
    duck: bool = True
    start_s: float = 0.0
    end_s: Optional[float] = None
    loop: bool = True
    fade_in: float = 1.0
    fade_out: float = 2.0
    line: int = 0


@dataclass
class AudioSpec:
    loudnorm: Optional[Dict[str, float]] = None
    denoise: Optional[Dict[str, Any]] = None
    music: List[MusicSpec] = field(default_factory=list)
    fade_in: float = 0.0
    fade_out: float = 0.0
    fade_audio: bool = True
    fade_video: bool = True
    fade_color: str = "black"


@dataclass
class CaptionLine:
    """One caption line: words with output-time start/end in seconds."""

    words: List[Tuple[str, float, float]]

    @property
    def start(self) -> float:
        return self.words[0][1]

    @property
    def end(self) -> float:
        return self.words[-1][2]

    @property
    def text(self) -> str:
        return " ".join(w for w, _, _ in self.words)


@dataclass
class CaptionSpec:
    mode: str = "burn"
    style: str = "karaoke"
    font_size: float = 56.0
    color: str = "#ffffff"
    highlight: str = "#ffff00"
    outline: str = "#000000"
    position: str = "bottom"
    margin: float = 60.0
    font: Optional[str] = None
    write_files: bool = True
    lines: List[CaptionLine] = field(default_factory=list)
    language: str = ""
    source: str = ""


@dataclass
class Timeline:
    """The complete edit."""

    segments: List[Segment]
    output: OutputSpec
    source_path: str
    source_duration: float
    source_fps: Fraction
    total_frames: int = 0
    texts: List[TextOverlay] = field(default_factory=list)
    images: List[ImageOverlay] = field(default_factory=list)
    audio: AudioSpec = field(default_factory=AudioSpec)
    captions: Optional[CaptionSpec] = None
    video_mode: str = "encode"      # "encode" | "copy_all" | "copy_cuts"
    warnings: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def fps(self) -> Fraction:
        return self.output.fps

    @property
    def duration(self) -> float:
        return float(self.total_frames / self.output.fps) if self.total_frames else 0.0

    def frame_to_sec(self, f: int) -> float:
        return float(f / self.output.fps)

    def to_dict(self) -> Dict[str, Any]:
        fps = self.output.fps
        segs = []
        for i, s in enumerate(self.segments):
            segs.append({
                "index": i + 1, "kind": s.kind,
                "source_start": round(float(s.src_start_f / fps), 3),
                "source_end": round(float((s.src_start_f + s.src_len_f) / fps), 3),
                "output_start": round(float(s.out_start / fps), 3), "output_end": round(float(s.out_end / fps), 3),
                "frames": s.frames, "speed": round(s.speed, 3), "reverse": s.reverse, "mute": s.mute,
                "effects": [e.name for e in s.effects], "zoom": bool(s.camera and not s.camera.pulse),
                "crop": bool(s.crop), "reframe": s.reframe.mode if s.reframe else None, "color": bool(s.color),
                "transition_in": s.join_in.type if s.join_in else None,
                "transition_out": s.join_out.type if s.join_out else None, "lines": s.lines,
            })
        return {
            "source": self.source_path, "source_duration": round(self.source_duration, 3),
            "output": self.output.to_dict(), "video_mode": self.video_mode,
            "duration": round(self.duration, 3), "total_frames": self.total_frames, "segments": segs,
            "texts": [asdict(t) for t in self.texts], "images": [asdict(i) for i in self.images],
            "audio": asdict(self.audio),
            "captions": None if not self.captions else {
                "mode": self.captions.mode, "style": self.captions.style, "lines": len(self.captions.lines),
                "source": self.captions.source},
            "warnings": self.warnings, "notes": self.notes,
        }

    def digest_data(self) -> Dict[str, Any]:
        """Everything that changes the rendered result (used in cache keys).

        Script line numbers are left out on purpose: the same edit written in another
        language, or with its lines moved, must give the same result and the same key.
        """
        d = self.to_dict()
        for k in ("warnings", "notes", "source"):
            d.pop(k, None)
        d["segments"] = [{k: v for k, v in s.items() if k != "lines"} for s in d["segments"]]
        d["captions_full"] = None if not self.captions else asdict(self.captions)
        d["segments_full"] = [_strip_lines(asdict(s)) for s in self.segments]
        d["texts"] = [_strip_lines(t) for t in d["texts"]]
        d["images"] = [_strip_lines(i) for i in d["images"]]
        d["audio"] = _strip_lines(d["audio"])
        d["source_fps"] = str(self.source_fps)
        return d


def _strip_lines(obj: Any) -> Any:
    """Remove script line numbers (and the temporary reverse-group marker) from nested data."""
    if isinstance(obj, dict):
        return {k: _strip_lines(v) for k, v in obj.items() if k not in ("line", "lines", "rev_group")}
    if isinstance(obj, list):
        return [_strip_lines(v) for v in obj]
    return obj
