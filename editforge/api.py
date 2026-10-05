"""High-level functions used by the command line, the web page and the tests."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from .analysis.manager import Analyzer
from .core.cache import AnalysisCache
from .core.errors import EditForgeError, Problem, ScriptError
from .core.fingerprint import source_identity, stable_hash
from .core.media import MediaInfo, probe_media
from .core.model import Timeline
from .dsl.loader import load_script, load_script_file
from .dsl.script import Script
from .planner.files import FileResolver
from .planner.planner import Planner
from .presets.presets import default_extension, get_preset, resolve_output
from .version import __version__


@dataclass
class Plan:
    """Everything decided before rendering."""

    timeline: Timeline
    media: MediaInfo
    script: Script
    output_path: str
    warnings: List[str]
    notes: List[str]
    analysis_notes: List[str]
    identity: Dict[str, Any]
    analyzer: Optional[Analyzer] = None
    options: Dict[str, Any] = field(default_factory=dict)

    @property
    def digest(self) -> str:
        """Hash of the resolved edit. Any change to what the result looks like changes this."""
        return stable_hash({"v": __version__, "timeline": self.timeline.digest_data()})

    def to_dict(self) -> Dict[str, Any]:
        d = self.timeline.to_dict()
        d.update({"output_path": self.output_path, "digest": self.digest, "warnings": self.warnings,
                  "notes": self.notes + self.analysis_notes, "source_info": self.media.to_dict()})
        return d

    def summary(self) -> str:
        t = self.timeline
        lines = [f"Source:   {self.media.path}  ({self.media.duration:.2f}s)",
                 f"Output:   {self.output_path}",
                 f"Format:   {t.output.container.upper()}"
                 + (f", {t.output.width}x{t.output.height} at {float(t.output.fps):.3f} fps" if t.output.has_video else ", audio only"),
                 f"Length:   {t.duration:.3f}s  ({t.total_frames} frames on a {float(t.output.fps):.3f} fps grid)",
                 f"Segments: {len(t.segments)}   Video: {t.video_mode.replace('_', ' ')}"]
        if t.audio.music:
            lines.append(f"Music:    {len(t.audio.music)} track(s)" + (" with ducking" if any(m.duck for m in t.audio.music) else ""))
        if t.audio.loudnorm:
            lines.append(f"Loudness: normalise to {t.audio.loudnorm['target']:g} LUFS (two passes)")
        if t.texts or t.images:
            lines.append(f"Overlays: {len(t.texts)} text, {len(t.images)} image")
        if t.captions:
            lines.append(f"Captions: {len(t.captions.lines)} line(s), mode {t.captions.mode}, style {t.captions.style}")
        return "\n".join(lines)


def read_script(text: Optional[str] = None, path: Optional[str] = None, fmt: Optional[str] = None) -> Script:
    """Load a script from text or a file (problems are collected in ``script.problems``)."""
    if path:
        return load_script_file(path)
    return load_script(text or "", fmt)


def default_output_path(source: str, spec_container: str) -> str:
    src = Path(source)
    ext = {"gif": "gif", "m4a": "m4a", "mp3": "mp3", "wav": "wav", "flac": "flac", "mkv": "mkv", "mov": "mov"}.get(spec_container, "mp4")
    return str(src.with_name(f"{src.stem}_edited.{ext}"))


def prepare(script: Script, *, input_path: Optional[str] = None, output_path: Optional[str] = None,
            preset: Optional[str] = None, size: Optional[str] = None, fps: Optional[float] = None,
            quality: Optional[str] = None, fast_cuts: Optional[bool] = None, preview: bool = False,
            base_dir: Optional[str] = None, allowed_roots: Optional[Sequence[str]] = None,
            transcript: Optional[str] = None, cancel: Optional[threading.Event] = None,
            log: Optional[Callable[[str], None]] = None, cache: Optional[AnalysisCache] = None) -> Plan:
    """Turn a script into a :class:`Plan` (runs any analysis that is needed and caches it).

    Command-line style options override what the script says.
    """
    from .dsl.coerce import _coerce_quality, _coerce_size
    from .dsl.registry import spec_for

    script.raise_if_errors()
    resolver = FileResolver(base_dir, allowed_roots)
    src = input_path or script.settings.get("input")
    if not src:
        raise ScriptError("No input file was chosen.", "Give the video with  -i my_video.mp4  or add a line to the script:  input my_video.mp4")
    src_path = resolver.resolve(src, "input file", script.setting_lines.get("input"))
    media = probe_media(src_path)
    notes: List[str] = list(media.warnings)
    s = script.settings
    size_v = size or s.get("size")
    if size and "x" not in size:
        size_v = _coerce_size(spec_for("size"), spec_for("size").params[0], size, None)
    q = quality or s.get("quality")
    if q and not str(q).startswith("crf:"):
        q = _coerce_quality(spec_for("quality"), spec_for("quality").params[0], q, None)
    out_ext = Path(output_path).suffix if output_path else (Path(s["output"]).suffix if s.get("output") else "")
    p_name = preset or s.get("preset") or "original"
    get_preset(p_name)
    out = resolve_output(media, preset=p_name, size=size_v, fit=s.get("fit") if size_v else None,
                         fps=fps or s.get("fps"), quality=q, output_ext=out_ext, preview=preview, notes=notes)
    if not out.has_video:
        from fractions import Fraction
        out.fps = Fraction(100)
    fc = fast_cuts if fast_cuts is not None else bool(s.get("fast_cuts"))
    analyzer = Analyzer(media, cache, cancel, log)
    from .analysis.faces import make_face_provider
    planner = Planner(script, media, analyzer, out, resolver, fast_cuts=fc, transcript_override=transcript,
                      face_provider=make_face_provider(analyzer))
    tl = planner.build()
    out_path = output_path or s.get("output")
    if out_path:
        out_path = str(Path(resolver.base_dir, out_path).resolve()) if not os.path.isabs(out_path) else out_path
    else:
        out_path = default_output_path(src_path, tl.output.container)
    warnings = list(script.warnings and [p.plain() for p in script.warnings]) + list(tl.warnings)
    return Plan(tl, media, script, out_path, warnings, notes + list(tl.notes), list(analyzer.notes),
                source_identity(src_path), analyzer, {"fast_cuts": fc, "preview": preview})


def plan_text(text: str, input_path: str, **kwargs: Any) -> Plan:
    """Convenience: parse text and prepare it."""
    return prepare(read_script(text), input_path=input_path, **kwargs)


def validate_script(text: str, input_path: Optional[str] = None, base_dir: Optional[str] = None,
                    allowed_roots: Optional[Sequence[str]] = None, fmt: Optional[str] = None) -> Dict[str, Any]:
    """Check a script and return every problem found (used for live validation).

    Without an input file only the script itself is checked. With one, the plan is also built
    (analysis that is not cached yet is skipped in the web page's quick mode by passing no input).
    """
    script = load_script(text, fmt)
    problems: List[Problem] = list(script.problems)
    info: Dict[str, Any] = {"format": script.format, "steps": len(script.steps)}
    if not script.errors and input_path:
        try:
            plan = prepare(script, input_path=input_path, base_dir=base_dir, allowed_roots=allowed_roots)
            info.update({"duration": plan.timeline.duration, "segments": len(plan.timeline.segments)})
            problems += [Problem("warning", w) for w in plan.warnings]
        except EditForgeError as exc:
            problems.append(Problem("error", exc.message, exc.fix, exc.line))
    return {"ok": not any(p.level == "error" for p in problems), "problems": [p.to_dict() for p in problems], "info": info}
