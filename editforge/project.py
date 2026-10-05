"""Projects: a video cut into pieces that can be removed, re-ordered, stretched and given edits one by one.

The script language works on ranges of the source in time order. A project is freer: pieces can be
moved, repeated and made longer so they overlap. So a project is turned straight into the engine's
internal :class:`~editforge.core.model.Timeline` (the renderer only ever reads that model).

A project is plain JSON::

    {"name": "...", "source": "<file id>",
     "pieces": [{"start": 0.0, "end": 2.0, "off": false, "speed": 1.0, "reverse": false, "mute": false,
                 "volume": 1.0, "fx": ["hflip"], "zoom": {"mode": "in", "amount": 1.3},
                 "color": {"brightness": 0.0, "contrast": 1.0, "saturation": 1.0}}, ...],
     "settings": {"shape": "original", "transition": "none", "transition_s": 0.3, "music": "", "music_volume": 0.25,
                  "duck": true, "original_sound": true, "even_loudness": false, "fade_in": 0.0, "fade_out": 0.0,
                  "quality": "best"}}
"""

from __future__ import annotations

import math
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from .analysis import follow as follow_mod
from .analysis.faces import make_face_provider
from .analysis.manager import Analyzer
from .analysis.speech import Word, import_transcript
from .api import Plan, default_output_path
from .core.errors import EditForgeError, FeatureUnavailable, ScriptError
from .core.fingerprint import source_identity
from .core.media import MediaInfo, probe_media
from .core.model import (AudioSpec, CameraSpec, CaptionSpec, ColorSpec, EffectSpec, JoinSpec, MusicSpec, ReframeSpec, Segment,
                         TextOverlay, Timeline)
from .dsl.loader import load_script
from .dsl.registry import EFFECT_NAMES, XFADE
from .planner.captions import group_lines
from .planner.files import FileResolver, find_font
from .planner.planner import Planner
from .presets.presets import resolve_output

MAX_PIECES = 5000
MIN_PIECE_SECONDS = 0.04
ZOOM_MODES = ("none", "in", "out", "hold")
FOLLOW_MODES = ("none", "face", "object")
MAX_FOLLOW_POINTS = 600
MAX_TEXTS = 50
TEXT_POSITIONS = ("top", "center", "bottom", "top_left", "top_right", "bottom_left", "bottom_right")
TEXT_SIZES = {"small": 40.0, "medium": 64.0, "large": 96.0, "huge": 140.0}
TEXT_COLORS = {"white": "#ffffff", "black": "#000000", "yellow": "#ffe600", "red": "#ff3030", "blue": "#3090ff", "green": "#30d060"}
CAPTION_STYLES = ("karaoke", "plain", "boxed")
CAPTION_SIZES = {"small": 42.0, "medium": 56.0, "large": 76.0}
#: shape name -> (output preset, crop aspect or None, follow faces?, fit)
SHAPES: Dict[str, tuple] = {
    "original": ("original", None, False, None),
    "vertical": ("shorts", "9:16", False, None),
    "vertical_follow": ("shorts", "9:16", True, None),
    "vertical_blur": ("shorts", None, False, "blur"),
    "square": ("instagram_square", "1:1", False, None),
    "square_follow": ("instagram_square", "1:1", True, None),
    "square_blur": ("instagram_square", None, False, "blur"),
}
#: quality name -> (CRF, x264 preset). Lower CRF is better; "medium" squeezes more quality into each megabyte than "veryfast".
QUALITIES: Dict[str, tuple] = {"best": (17, "medium"), "high": (20, "veryfast"), "small": (23, "veryfast")}
DEFAULT_SETTINGS: Dict[str, Any] = {
    "shape": "original", "transition": "none", "transition_s": 0.3, "music": "", "music_volume": 0.25, "duck": True,
    "original_sound": True, "even_loudness": False, "fade_in": 0.0, "fade_out": 0.0, "quality": "best",
    "captions": "off", "caption_style": "karaoke", "caption_position": "bottom", "caption_size": "medium",
}


def _num(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v != v or v in (float("inf"), float("-inf")):
        return default
    return max(lo, min(hi, v))


def clean_piece(raw: Any, duration: float) -> Optional[Dict[str, Any]]:
    """Return a safe, complete copy of one piece, or None if it is not usable."""
    if not isinstance(raw, dict):
        return None
    try:
        start, end = float(raw["start"]), float(raw["end"])
    except (KeyError, TypeError, ValueError):
        return None
    if start != start or end != end:
        return None
    start, end = max(0.0, start), min(float(duration), end)
    if end - start < MIN_PIECE_SECONDS:
        return None
    fx_raw = raw.get("fx") if isinstance(raw.get("fx"), list) else []
    fx: List[str] = []
    for name in fx_raw:
        if name in EFFECT_NAMES and name not in fx:
            fx.append(name)
    zoom_raw = raw.get("zoom") if isinstance(raw.get("zoom"), dict) else {}
    mode = zoom_raw.get("mode") if zoom_raw.get("mode") in ZOOM_MODES else "none"
    color_raw = raw.get("color") if isinstance(raw.get("color"), dict) else {}
    follow_raw = raw.get("follow") if isinstance(raw.get("follow"), dict) else {}
    follow_mode = follow_raw.get("mode") if follow_raw.get("mode") in FOLLOW_MODES else "none"
    points: List[List[float]] = []
    if follow_mode == "object" and isinstance(follow_raw.get("path"), list):
        for pt in follow_raw["path"][:MAX_FOLLOW_POINTS]:
            try:
                t, x, y = float(pt[0]), float(pt[1]), float(pt[2])
            except (TypeError, ValueError, IndexError):
                continue
            if t == t and x == x and y == y and abs(t) < 1e7:
                points.append([round(t, 3), round(max(0.0, min(1.0, x)), 4), round(max(0.0, min(1.0, y)), 4)])
        points.sort()
    if follow_mode == "object" and len(points) < 2:
        follow_mode = "none"
    return {
        "start": round(start, 4), "end": round(end, 4), "off": bool(raw.get("off")),
        "speed": round(_num(raw.get("speed"), 1.0, 0.1, 16.0), 4), "reverse": bool(raw.get("reverse")),
        "mute": bool(raw.get("mute")), "volume": round(_num(raw.get("volume"), 1.0, 0.0, 4.0), 3), "fx": fx,
        "zoom": {"mode": mode, "amount": round(_num(zoom_raw.get("amount"), 1.3, 1.0, 4.0), 3)},
        "color": {"brightness": round(_num(color_raw.get("brightness"), 0.0, -1.0, 1.0), 3),
                  "contrast": round(_num(color_raw.get("contrast"), 1.0, 0.0, 3.0), 3),
                  "saturation": round(_num(color_raw.get("saturation"), 1.0, 0.0, 3.0), 3)},
        "follow": {"mode": follow_mode, "zoom": round(_num(follow_raw.get("zoom"), 1.6, 1.1, 4.0), 3),
                   "path": points if follow_mode == "object" else []},
    }


def clean_texts(raw: Any) -> List[Dict[str, Any]]:
    """Words to show on the picture. Times are seconds of the FINISHED video; an end of 0 means "until the end"."""
    out: List[Dict[str, Any]] = []
    for item in (raw if isinstance(raw, list) else [])[:MAX_TEXTS]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").replace("\r", "").strip()[:200]
        if not text:
            continue
        start = round(_num(item.get("start"), 0.0, 0.0, 1e6), 3)
        end = round(_num(item.get("end"), 0.0, 0.0, 1e6), 3)
        out.append({"text": text, "start": start, "end": end if end > start else 0.0,
                    "position": item.get("position") if item.get("position") in TEXT_POSITIONS else "bottom",
                    "size": item.get("size") if item.get("size") in TEXT_SIZES else "medium",
                    "color": item.get("color") if item.get("color") in TEXT_COLORS else "white",
                    "box": bool(item.get("box", True))})
    return out


def clean_settings(raw: Any) -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    s = dict(DEFAULT_SETTINGS)
    if raw.get("shape") in SHAPES:
        s["shape"] = raw["shape"]
    if raw.get("transition") == "none" or raw.get("transition") in XFADE:
        s["transition"] = raw["transition"]
    s["transition_s"] = round(_num(raw.get("transition_s"), 0.3, 0.1, 3.0), 3)
    s["music"] = str(raw.get("music") or "")[:300]
    s["music_volume"] = round(_num(raw.get("music_volume"), 0.25, 0.0, 2.0), 3)
    for key in ("duck", "original_sound", "even_loudness"):
        if key in raw:
            s[key] = bool(raw[key])
    s["fade_in"] = round(_num(raw.get("fade_in"), 0.0, 0.0, 10.0), 3)
    s["fade_out"] = round(_num(raw.get("fade_out"), 0.0, 0.0, 10.0), 3)
    if raw.get("quality") in QUALITIES:
        s["quality"] = raw["quality"]
    s["captions"] = str(raw.get("captions") or "off")[:300]          # "off", "auto" (listen to the speech) or a subtitle file id
    if raw.get("caption_style") in CAPTION_STYLES:
        s["caption_style"] = raw["caption_style"]
    if raw.get("caption_position") in ("top", "center", "bottom"):
        s["caption_position"] = raw["caption_position"]
    if raw.get("caption_size") in CAPTION_SIZES:
        s["caption_size"] = raw["caption_size"]
    return s


def clean_project(raw: Any, duration: float) -> Dict[str, Any]:
    """Validate a project that came from the page. Bad pieces are dropped; bad values fall back to defaults."""
    if not isinstance(raw, dict):
        raise EditForgeError("That project could not be read.", "Reload the page and try again.")
    pieces_raw = raw.get("pieces") if isinstance(raw.get("pieces"), list) else []
    if len(pieces_raw) > MAX_PIECES:
        raise EditForgeError(f"A project can have at most {MAX_PIECES:,} pieces.", "Slice into longer pieces, or work on a shorter clip.")
    pieces = [p for p in (clean_piece(x, duration) for x in pieces_raw) if p is not None]
    name = " ".join(str(raw.get("name") or "").split())[:80] or "My project"
    return {"name": name, "source": str(raw.get("source") or "")[:300], "pieces": pieces, "settings": clean_settings(raw.get("settings")),
            "texts": clean_texts(raw.get("texts"))}


def slice_pieces(duration: float, interval: float) -> List[Dict[str, Any]]:
    """Cut 0..duration into pieces of ``interval`` seconds (a last piece shorter than 2 frames joins the one before)."""
    if interval < 0.1:
        raise EditForgeError("Pieces must be at least 0.1 seconds long.", "Choose a longer time, for example 2 seconds.")
    n = max(1, int(math.ceil(duration / interval - 1e-9)))
    if n > MAX_PIECES:
        raise EditForgeError(f"That would make {n:,} pieces (the most is {MAX_PIECES:,}).", "Choose a longer time for each piece.")
    edges = [round(min(duration, i * interval), 4) for i in range(n)] + [round(duration, 4)]
    if len(edges) > 2 and edges[-1] - edges[-2] < 0.08:
        edges.pop(-2)
    return [p for p in (clean_piece({"start": a, "end": b}, duration) for a, b in zip(edges, edges[1:])) if p]


def slice_at(duration: float, times: List[float]) -> List[Dict[str, Any]]:
    """Cut 0..duration at the given times (used for cutting on music beats)."""
    edges = [0.0]
    for t in sorted(float(x) for x in times):
        if 0.08 <= t - edges[-1] and duration - t >= 0.08:
            edges.append(round(t, 4))
    edges.append(round(duration, 4))
    if len(edges) - 1 > MAX_PIECES:
        raise EditForgeError(f"That would make more than {MAX_PIECES:,} pieces.", "Cut on every 2nd or 4th beat instead.")
    return [p for p in (clean_piece({"start": a, "end": b}, duration) for a, b in zip(edges, edges[1:])) if p]


class _ProjectPlanner(Planner):
    """Reuses the planner's frame maths, reverse chunking, layout and face tracking for a list of pieces."""

    def piece_segments(self, piece: Dict[str, Any], reframe: Optional[ReframeSpec], silent: bool,
                       follow_path: Optional[List[follow_mod.Point]] = None) -> List[Segment]:
        a = max(0, min(self.D_f, self.fr(piece["start"])))
        b = max(0, min(self.D_f, self.fr(piece["end"])))
        if b <= a:
            return []
        seg = Segment("clip", a, b - a, max(1, int(round((b - a) / piece["speed"]))))
        seg.mute = bool(piece["mute"]) or silent
        seg.volume = float(piece["volume"])
        if self.has_video:
            seg.effects = [EffectSpec(name, 0.5) for name in piece["fx"]]
            zoom, amount = piece["zoom"]["mode"], float(piece["zoom"]["amount"])
            if follow_path and reframe is None:
                # Follow: a zoomed view that glides after the subject. Flips happen before the camera, so they flip the path too.
                z = float(piece["follow"]["zoom"])
                fx, fy = "hflip" in piece["fx"], ("vflip" in piece["fx"]) != ("flip" in piece["fx"])
                pts = [(t, follow_mod.view_position(1.0 - x if fx else x, z), follow_mod.view_position(1.0 - y if fy else y, z))
                       for t, x, y in follow_mod.window(follow_path, a / self.F - 0.1, b / self.F + 0.1)]
                seg.camera = CameraSpec(z, z, 0.5, 0.5, 0.5, 0.5, "linear", a / self.F, b / self.F, False, pts)
            elif zoom != "none" and amount > 1.0:
                z0, z1 = {"in": (1.0, amount), "out": (amount, 1.0), "hold": (amount, amount)}[zoom]
                seg.camera = CameraSpec(z0, z1, 0.5, 0.5, 0.5, 0.5, "ease_in_out", a / self.F, b / self.F)
            c = piece["color"]
            if (c["brightness"], c["contrast"], c["saturation"]) != (0.0, 1.0, 1.0):
                seg.color = ColorSpec(c["brightness"], c["contrast"], c["saturation"])
            if reframe is not None:
                seg.reframe = ReframeSpec(reframe.aspect, reframe.mode, reframe.smooth)
        if not piece["reverse"]:
            return [seg]
        chunk = self._chunk_frames()          # reversing holds frames in memory, so it is done in small chunks
        n = max(1, int(math.ceil(seg.frames / chunk)))      # equal chunks, so none is too short for a transition
        parts = self._split_many(seg, [seg.frames * i // n for i in range(1, n)])
        for p in parts:
            p.reverse = True
            if p.reframe is not None:
                p.reframe = ReframeSpec(reframe.aspect, reframe.mode, reframe.smooth)
        return list(reversed(parts))

    def join(self, a: Segment, b: Segment, kind: str, seconds: float) -> bool:
        """Put a transition between two neighbouring segments (same rules as the script planner)."""
        want = max(2, self.fr(seconds))
        d = min(want, min(a.frames, b.frames) // 2, a.frames - a.head - 1, b.frames - b.tail - 1)
        if d < 2:
            return False
        j = JoinSpec(kind, d)
        a.join_out, b.join_in = j, j
        return True


def remap_words(words: Sequence[Word], segments: Sequence[Segment], fps: float) -> List[tuple]:
    """Move spoken words from source time to finished-video time, piece by piece, in playing order.

    Unlike the script planner's version this handles pieces that were moved or repeated: a word is shown
    every time its piece plays. Backwards and muted pieces have no readable speech, so they get no words.
    """
    ordered = sorted(words, key=lambda w: w.start)
    out: List[tuple] = []
    for seg in segments:
        if seg.kind != "clip" or seg.reverse or seg.mute:
            continue
        s0, s1 = seg.src_start_f / fps, (seg.src_start_f + seg.src_len_f) / fps
        for w in ordered:
            if w.start >= s1:
                break
            lo, hi = max(w.start, s0), min(w.end, s1)
            if hi > lo and (hi - lo) >= 0.4 * max(w.end - w.start, 1e-6):
                o0 = seg.out_start / fps + (lo - s0) / seg.speed
                o1 = seg.out_start / fps + (hi - s0) / seg.speed
                out.append((w.text, o0, max(o1, o0 + 0.02)))
    out.sort(key=lambda x: x[1])
    fixed: List[tuple] = []
    for text, a, b in out:                       # words must not overlap in time
        if fixed and a < fixed[-1][2]:
            pt, pa, pb = fixed[-1]
            fixed[-1] = (pt, pa, max(pa + 0.02, min(pb, a)))
        fixed.append((text, a, max(b, a + 0.02)))
    return fixed


def plan_project(project: Dict[str, Any], src_path: str, *, music_path: Optional[str] = None, captions_path: Optional[str] = None,
                 preview: bool = False,
                 output_path: Optional[str] = None, cancel: Optional[threading.Event] = None,
                 log: Optional[Callable[[str], None]] = None) -> Plan:
    """Turn a project into a render :class:`Plan`.

    ``project`` must already be cleaned with :func:`clean_project`. ``music_path`` is the real path of the
    music file (the page only knows file ids). ``preview=True`` gives a small, fast 480p version.
    """
    media: MediaInfo = probe_media(src_path)
    st = project["settings"]
    live = [p for p in project["pieces"] if not p["off"]]
    if not live:
        raise ScriptError("Every piece is removed, so there would be no video left.", "Put at least one piece back.")
    preset, aspect, follow, fit = SHAPES[st["shape"]]
    crf, x264 = QUALITIES[st["quality"]]
    notes: List[str] = list(media.warnings)
    out = resolve_output(media, preset=preset, fit=fit, quality=f"crf:{crf}", preview=preview, notes=notes)
    if not preview:
        out.x264_preset = x264
    if preview and preset != "original" and out.has_video:      # keep the chosen shape in the small preview
        full = resolve_output(media, preset=preset, fit=fit)
        out.height = 480 if full.height <= full.width else 854
        out.width = int(round(out.height * full.width / full.height / 2)) * 2
        out.fit = full.fit
    if not out.has_video:
        from fractions import Fraction
        out.fps = Fraction(100)
    analyzer = Analyzer(media, None, cancel, log)
    planner = _ProjectPlanner(load_script(""), media, analyzer, out, FileResolver(), face_provider=make_face_provider(analyzer))
    reframe = ReframeSpec(aspect, "face" if follow else "center") if aspect and planner.has_video else None
    silent = not st["original_sound"]
    face_path: List[follow_mod.Point] = []
    wants_face = [p for p in live if p["follow"]["mode"] == "face"]
    if wants_face and planner.has_video and reframe is None:
        if planner.face_provider is None:
            raise FeatureUnavailable("Following faces is switched off because the free 'opencv-python-headless' add-on is not installed.",
                                     "Install it with:  pip install 'opencv-python-headless<5'   -- or switch off 'Follow' for those pieces.")
        face_path = follow_mod.face_points(analyzer.faces(min(p["start"] for p in wants_face), max(p["end"] for p in wants_face), 640))
        if not face_path:
            planner.warn("No faces were found, so the pieces set to follow a face are shown without following.")
    if reframe is not None and any(p["follow"]["mode"] != "none" for p in live):
        planner.warn("'Follow' on single pieces only works with the original shape, so it was left out. "
                     "For a tall or square video choose a 'follow faces' shape instead.")

    def path_for(p: Dict[str, Any]) -> Optional[List[follow_mod.Point]]:
        if p["follow"]["mode"] == "face":
            return face_path or None
        if p["follow"]["mode"] == "object":
            return [tuple(pt) for pt in p["follow"]["path"]]
        return None

    groups = [g for g in (planner.piece_segments(p, reframe, silent, path_for(p)) for p in live) if g]
    if not groups:
        raise ScriptError("The pieces that are left are empty.", "Put a piece back, or make a piece longer.")
    if st["transition"] != "none" and planner.has_video:
        made = sum(1 for g1, g2 in zip(groups, groups[1:]) if planner.join(g1[-1], g2[0], st["transition"], st["transition_s"]))
        skipped = len(groups) - 1 - made
        planner.notes.append(f"Added {made} transition(s) between pieces.")
        if skipped:
            planner.warn(f"{skipped} transition(s) were skipped because a piece is too short for one.")
    planner.segs = [s for g in groups for s in g]
    planner._layout()
    planner._resolve_reframes()
    if planner.total < 1:
        raise ScriptError("The finished video would be empty.", "Put a piece back, or make a piece longer.")
    audio = AudioSpec()
    if music_path:
        if not probe_media(music_path).has_audio:
            raise ScriptError("The music file has no sound.", "Choose an audio file (mp3, wav, m4a ...).")
        audio.music.append(MusicSpec(music_path, float(st["music_volume"]), bool(st["duck"]) and not silent and media.has_audio,
                                     0.0, None, True, 0.5, 1.5))
    if st["even_loudness"] and (music_path or (media.has_audio and not silent)):
        audio.loudnorm = {"target": -14.0, "true_peak": -1.5, "lra": 11.0}
    audio.fade_in, audio.fade_out = float(st["fade_in"]), float(st["fade_out"])
    audio.fade_video = planner.has_video
    texts: List[TextOverlay] = []
    if planner.has_video and project.get("texts"):
        font = find_font(None)
        for t in project["texts"]:
            a = min(planner.total, planner.fr(t["start"]))
            b = planner.total if t["end"] <= 0 else min(planner.total, planner.fr(t["end"]))
            if b > a:
                texts.append(TextOverlay(t["text"], a, b, t["position"], None, None, TEXT_SIZES[t["size"]], TEXT_COLORS[t["color"]], font,
                                         bool(t["box"]), "#000000@0.5", 14.0, 0.0 if t["box"] else 3.0, "#000000", False, 0.0, 0))
            else:
                planner.warn(f"The text '{t['text'][:30]}' starts after the video ends, so it is not shown.")
    captions = None
    if st["captions"] != "off" and planner.has_video:
        if captions_path:
            words, source = import_transcript(captions_path), "subtitle file"
        elif not media.has_audio:
            raise ScriptError("Automatic captions need speech, but this video has no sound.", "Switch captions off, or add a subtitle file.")
        else:
            words, _ = analyzer.transcript("auto", "base", "auto")
            source = "speech recognition (Whisper)"
        lines = group_lines(remap_words(words, planner.segs, planner.F))
        if not lines:
            planner.warn("No speech was found in the pieces that are kept, so there are no captions.")
        else:
            captions = CaptionSpec("burn", st["caption_style"], CAPTION_SIZES[st["caption_size"]], "#ffffff", "#ffe600", "#000000",
                                   st["caption_position"], 70.0, None, False, lines, "", source)
            planner.notes.append(f"Captions: {len(lines)} line(s) from {source}.")
    tl = Timeline(planner.segs, out, media.path, planner.D, media.fps, planner.total, texts, [], audio, captions, "encode",
                  planner.warnings, planner.notes)
    out_path = output_path or default_output_path(src_path, out.container)
    return Plan(tl, media, planner.script, out_path, list(planner.warnings), notes + list(planner.notes), list(analyzer.notes),
                source_identity(src_path), analyzer, {"fast_cuts": False, "preview": preview})


def output_stem(project: Dict[str, Any], src_path: str, preview: bool) -> str:
    """A safe file name (without extension) for a project's finished video."""
    keep = "".join(ch if ch.isalnum() or ch in " -_()" else "_" for ch in project["name"]).strip(" .")[:60]
    return (keep or Path(src_path).stem[:60]) + ("_preview" if preview else "")
