"""The planner: turns a parsed script plus analysis into one internal Timeline.

The renderer works only from that Timeline, so a dry run ("plan") and a real render always agree.
All positions inside the planner are whole frames on the OUTPUT frame grid, which keeps cuts
frame-accurate and makes the output length exactly predictable.
"""

from __future__ import annotations

import bisect
import copy
import math
import os
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from ..analysis.beats import BeatInfo, bpm_grid
from ..analysis.manager import Analyzer
from ..analysis.speech import Word, import_transcript
from ..core.errors import FeatureUnavailable, ScriptError
from ..core.media import MediaInfo, probe_media
from ..core.model import (AudioSpec, CameraSpec, CaptionSpec, ColorSpec, CropSpec, EffectSpec, ImageOverlay, JoinSpec,
                          MusicSpec, OutputSpec, ReframeSpec, Segment, TextOverlay, Timeline)
from ..core.timecode import parse_time_value
from ..dsl.script import Script, Step
from .captions import group_lines, remap_words
from .files import FileResolver, find_font
from .ranges import IntervalSet, union_into

VIDEO_STEPS = {"speed", "speed_ramp", "reverse", "freeze", "zoom", "pan", "crop", "reframe", "color", "effect", "every_nth",
               "transition", "beat_effect", "text", "image", "volume", "mute"}
MOD_STEPS = {"speed", "speed_ramp", "reverse", "zoom", "pan", "crop", "reframe", "color", "effect", "mute", "volume"}
PULSE_LOOKS = {"flash", "invert", "mirror", "grayscale", "blur", "sharpen", "shake", "contrast"}
MAX_SEGMENTS = 300_000


def ease(name: str, p: float) -> float:
    """Easing curves shared by zoom, pan and speed ramps."""
    p = max(0.0, min(1.0, p))
    if name == "ease_in":
        return p * p
    if name == "ease_out":
        return 1.0 - (1.0 - p) * (1.0 - p)
    if name == "ease_in_out":
        return p * p * (3.0 - 2.0 * p)
    return p


@dataclass
class Mod:
    """A change that applies to a range of source frames."""

    kind: str
    a: int
    b: int
    step: Step
    order: int


class Planner:
    """Builds a :class:`Timeline` from a script."""

    def __init__(self, script: Script, media: MediaInfo, analyzer: Analyzer, out: OutputSpec, resolver: FileResolver, *,
                 fast_cuts: bool = False, transcript_override: Optional[str] = None,
                 face_provider: Optional[Callable[..., Any]] = None):
        self.script, self.media, self.an, self.out, self.res = script, media, analyzer, out, resolver
        self.fast_cuts = fast_cuts
        self.transcript_override = transcript_override
        self.face_provider = face_provider
        self.Fr: Fraction = out.fps
        self.F: float = float(out.fps)
        self.D: float = media.duration
        self.D_f: int = max(1, int(math.floor(self.D * self.F + 1e-6)))
        self.warnings: List[str] = []
        self.notes: List[str] = []
        self.segs: List[Segment] = []
        self.total = 0
        self.has_video = out.has_video and media.has_video
        self._music_beats_cache: Dict[str, BeatInfo] = {}

    # ------------------------------------------------------------------ helpers
    def warn(self, message: str, line: Optional[int] = None) -> None:
        self.warnings.append(f"Line {line}: {message}" if line else message)

    def secs(self, canon: Any) -> float:
        fps = self.media.fps if self.media.has_video else self.out.fps
        return parse_time_value(canon).to_seconds(fps)

    def fr(self, seconds: float) -> int:
        """Seconds to whole frames, rounding halves up (so 12.5 frames is always 13)."""
        return int(math.floor(seconds * self.F + 0.5 + 1e-9))

    def _range_frames(self, st: Step, a_key: str = "start", b_key: str = "end") -> Tuple[int, int]:
        """A step's start/end as clamped frames (defaults: whole video)."""
        a = self.secs(st.args[a_key]) if st.args.get(a_key) is not None else 0.0
        b = self.secs(st.args[b_key]) if st.args.get(b_key) is not None else self.D
        return self._clamp(a, b, st)

    def _clamp(self, a: float, b: float, st: Step) -> Tuple[int, int]:
        if a >= self.D - 1e-6:
            self.warn(f"'{st.name}' starts at {a:g}s but the video is only {self.D:g}s long, so it was ignored.", st.line)
            return 0, 0
        if b > self.D + 1e-6 and st.args.get("end") is not None:
            self.warn(f"'{st.name}' ends at {b:g}s, after the end of the video ({self.D:g}s); it was shortened.", st.line)
        fa, fb = self.fr(max(0.0, a)), self.fr(min(b, self.D))
        return max(0, fa), min(self.D_f, fb)

    # ------------------------------------------------------------------ 1. selection
    def _pattern(self, st: Step) -> List[Tuple[int, int]]:
        take, every, offset = self.secs(st.args["take"]), self.secs(st.args["every"]), self.secs(st.args["offset"])
        fa, fb = self._range_frames(st)
        if every <= 0 or take <= 0:
            raise ScriptError(f"'{st.name}' needs 'every' and the piece length to be more than zero.", "Example: pattern_keep 2 every 10", st.line)
        if take >= every:
            self.warn(f"In '{st.name}' the piece ({take:g}s) is not shorter than the cycle ({every:g}s), so it selects everything.", st.line)
        out: List[Tuple[int, int]] = []
        t = fa / self.F + offset
        end = fb / self.F
        while t < end - 1e-9:
            a, b = self.fr(t), self.fr(min(t + take, end))
            if b > a:
                out.append((a, b))
            t += every
            if len(out) > 1_000_000:
                raise ScriptError(f"'{st.name}' would make more than a million pieces.", "Use a longer 'every'.", st.line)
        return out

    def _silence_cuts(self, st: Step) -> List[Tuple[int, int]]:
        if not self.media.has_audio:
            self.warn("This file has no sound, so 'silence_remove' did nothing.", st.line)
            return []
        s0 = self.secs(st.args["start"]) if st.args.get("start") is not None else 0.0
        e0 = self.secs(st.args["end"]) if st.args.get("end") is not None else self.D
        pad = self.secs(st.args["padding"])
        sil = self.an.silences(float(st.args["threshold"]), self.secs(st.args["min_silence"]))
        out: List[Tuple[int, int]] = []
        for a, b in sil:
            a2 = a if a <= 0.01 else a + pad
            b2 = b if b >= self.D - 0.05 else b - pad
            a2, b2 = max(a2, s0), min(b2, e0)
            fa, fb = self.fr(a2), self.fr(b2)
            if fb - fa >= 1:
                out.append((fa, min(fb, self.D_f)))
        self.notes.append(f"Found {len(out)} silent stretch(es) to remove.")
        return out

    def _beat_info(self, st: Step) -> BeatInfo:
        if st.args.get("source") == "bpm":
            if not st.args.get("bpm"):
                raise ScriptError(f"'{st.name}' with source=bpm needs bpm=<beats per minute>.", f"Example: {st.name} source=bpm bpm=120", st.line)
            return bpm_grid(float(st.args["bpm"]), self.secs(st.args["offset"]), 0.0, self.D, 4)
        if not self.media.has_audio:
            raise ScriptError(f"'{st.name}' listens to the sound, but this file has none.", "Give a bpm: source=bpm bpm=120", st.line)
        info = self.an.beats()
        for n in info.notes:
            self.notes.append(n)
        return info

    def _grid(self, info: BeatInfo, downbeats: bool, st: Step) -> List[float]:
        if downbeats:
            if info.downbeats:
                return list(info.downbeats)
            self.warn("Downbeats could not be found reliably in this sound (no clear accent pattern), so every beat is used instead.", st.line)
        return list(info.beats)

    def _select(self) -> Tuple[List[Tuple[int, int]], Set[int]]:
        keep, cut = IntervalSet(), IntervalSet()
        has_keep = False
        for st in self.script.steps:
            if st.name in ("keep", "cut"):
                for a, b in st.args["ranges"]:
                    fa, fb = self._clamp(self.secs(a), self.secs(b) if b is not None else self.D,
                                         Step(st.name, {"end": b}, st.line))
                    if fb > fa:
                        (keep if st.name == "keep" else cut).add(fa, fb)
                has_keep = has_keep or st.name == "keep"
            elif st.name == "pattern_keep":
                has_keep = True
                for a, b in self._pattern(st):
                    keep.add(a, b)
            elif st.name == "pattern_remove":
                for a, b in self._pattern(st):
                    cut.add(a, b)
            elif st.name == "silence_remove":
                for a, b in self._silence_cuts(st):
                    cut.add(a, b)
        sel = (keep if has_keep else IntervalSet([(0, self.D_f)])).subtract(cut)
        splits: Set[int] = set()
        for st in self.script.steps:
            if st.name == "scene_cut":
                sel = self._scene_cut(st, sel, splits)
            elif st.name == "scene_snap":
                sel = self._scene_snap(st, sel)
            elif st.name == "beat_cut":
                sel = self._beat_cut(st, sel, splits)
        return [(a, b) for a, b in sel.ranges if b > a], splits

    def _scene_cut(self, st: Step, sel: IntervalSet, splits: Set[int]) -> IntervalSet:
        if not self.media.has_video:
            self.warn("This file has no picture, so 'scene_cut' did nothing.", st.line)
            return sel
        bounds = self.an.scenes(float(st.args["threshold"]), self.secs(st.args["min_scene"]))
        w0, w1 = self._range_frames(st)
        bf = [self.fr(b) for b in bounds]
        splits.update(b for b in bf if w0 < b < w1)
        scenes = list(zip([0] + bf, bf + [self.D_f]))
        self.notes.append(f"Found {len(scenes)} scene(s).")
        if st.args.get("keep"):
            chosen = IntervalSet()
            for n in st.args["keep"]:
                if 1 <= n <= len(scenes):
                    chosen.add(*scenes[n - 1])
                else:
                    self.warn(f"There is no scene {n} (found {len(scenes)}).", st.line)
            sel = sel.intersect(chosen)
        if st.args.get("remove"):
            gone = IntervalSet()
            for n in st.args["remove"]:
                if 1 <= n <= len(scenes):
                    gone.add(*scenes[n - 1])
                else:
                    self.warn(f"There is no scene {n} (found {len(scenes)}).", st.line)
            sel = sel.subtract(gone)
        return sel

    def _scene_snap(self, st: Step, sel: IntervalSet) -> IntervalSet:
        if not self.media.has_video:
            return sel
        scenes = [self.fr(b) for b in self.an.scenes(float(st.args["threshold"]), 0.3)]
        tol = self.fr(self.secs(st.args["tolerance"]))

        def snap(x: int) -> int:
            if not scenes:
                return x
            i = bisect.bisect_left(scenes, x)
            cands = [scenes[j] for j in (i - 1, i) if 0 <= j < len(scenes)]
            best = min(cands, key=lambda c: abs(c - x))
            return best if abs(best - x) <= tol else x

        out = IntervalSet()
        moved = 0
        for a, b in sel.ranges:
            a2 = snap(a) if a > 0 else a
            b2 = snap(b) if b < self.D_f else b
            moved += (a2 != a) + (b2 != b)
            if b2 > a2:
                out.add(a2, b2)
        self.notes.append(f"Moved {moved} cut point(s) onto scene changes.")
        return out

    def _beat_cut(self, st: Step, sel: IntervalSet, splits: Set[int]) -> IntervalSet:
        info = self._beat_info(st)
        w0, w1 = self._range_frames(st)
        off = self.secs(st.args["offset"]) if st.args.get("source") != "bpm" else 0.0
        grid = sorted({self.fr(b + off) for b in self._grid(info, bool(st.args["downbeats"]), st)})
        grid = [g for g in grid if w0 <= g < w1]
        bounds = grid[::int(st.args["every"])]
        if len(bounds) < 2:
            self.warn("Not enough beats were found to cut on, so 'beat_cut' did nothing.", st.line)
            return sel
        take = float(st.args["take"])
        kept = IntervalSet()
        for x, y in zip(bounds, bounds[1:] + [w1]):
            kept.add(x, x + max(1, int(round((y - x) * take))))
        if bounds[0] > w0:
            kept.add(w0, bounds[0])
        splits.update(bounds)
        self.notes.append(f"Cutting on {len(bounds)} beat(s) (tempo about {info.tempo:g} BPM).")
        inside = IntervalSet([(w0, w1)])
        return union_into(sel.subtract(inside), sel.intersect(kept))

    # ------------------------------------------------------------------ 2. modifiers and segments
    def _mods(self) -> List[Mod]:
        mods: List[Mod] = []
        video_only = {"speed", "speed_ramp", "reverse", "zoom", "pan", "crop", "reframe", "color", "effect"}
        for i, st in enumerate(self.script.steps):
            if st.name not in MOD_STEPS:
                continue
            if st.name in video_only and not self.has_video and st.name != "speed" and st.name != "reverse" \
                    and st.name != "speed_ramp":
                self.warn(f"'{st.name}' needs a picture, but this file has none (or you chose audio-only output); it was ignored.", st.line)
                continue
            a, b = self._range_frames(st)
            if b > a:
                mods.append(Mod(st.name, a, b, st, i))
        return mods

    def _breakpoints(self, mods: Sequence[Mod], splits: Set[int]) -> List[int]:
        pts = set(splits)
        for m in mods:
            pts.update((m.a, m.b))
            if m.kind == "speed_ramp":
                n = int(m.step.args.get("steps") or 0)
                if n <= 0:
                    n = max(4, min(60, int(round((m.b - m.a) / self.F / 0.2))))
                n = max(2, min(n, m.b - m.a))
                for k in range(1, n):
                    pts.add(m.a + (m.b - m.a) * k // n)
        for st in self.script.steps:
            if st.name == "freeze":
                pts.add(self.fr(self.secs(st.args["at"])))
        return sorted(p for p in pts if 0 < p < self.D_f)

    def _build_segments(self, sel: List[Tuple[int, int]], mods: List[Mod], splits: Set[int]) -> List[Segment]:
        bps = self._breakpoints(mods, splits)
        segs: List[Segment] = []
        for fa, fb in sel:
            lo, hi = bisect.bisect_right(bps, fa), bisect.bisect_left(bps, fb)
            cuts = [fa] + bps[lo:hi] + [fb]
            for x, y in zip(cuts, cuts[1:]):
                if y > x:
                    segs.append(Segment("clip", x, y - x, y - x))
            if len(segs) > MAX_SEGMENTS:
                raise ScriptError(f"This edit would make more than {MAX_SEGMENTS:,} pieces.",
                                  "Use fewer cuts, longer 'every' values, or split the job in two.")
        rev_groups: Dict[int, int] = {}
        for m in mods:
            if m.kind == "reverse":
                rev_groups[m.order] = m.order
        for s in segs:
            mid = s.src_start_f + s.src_len_f / 2.0
            speed = 1.0
            speed_line: Optional[int] = None
            for m in mods:
                if not (m.a <= mid < m.b):
                    continue
                st, a = m.step, m.step.args
                s.lines.append(st.line)
                if m.kind == "speed":
                    if speed_line is not None and speed != a["factor"]:
                        self.warn(f"Two 'speed' lines cover the same part (lines {speed_line} and {st.line}); the later one is used.", st.line)
                    speed, speed_line = float(a["factor"]), st.line
                    s.pitch = bool(a["pitch"])
                    s.mute = s.mute or bool(a["mute"])
                elif m.kind == "speed_ramp":
                    p = (mid - m.a) / max(1, m.b - m.a)
                    e = ease(a["easing"], p)
                    speed = float(a["from_speed"]) + (float(a["to_speed"]) - float(a["from_speed"])) * e
                    s.mute = s.mute or bool(a["mute"])
                elif m.kind == "reverse":
                    s.rev_group = m.order
                    s.mute = s.mute or bool(a["mute"])
                elif m.kind == "zoom":
                    s.camera = CameraSpec(float(a["from_scale"]), float(a["scale"]), float(a["x"]), float(a["x"]), float(a["y"]),
                                          float(a["y"]), a["easing"], m.a / self.F, m.b / self.F)
                elif m.kind == "pan":
                    sc = float(a["scale"])
                    s.camera = CameraSpec(sc, sc, float(a["from_x"]), float(a["to_x"]), float(a["from_y"]), float(a["to_y"]),
                                          a["easing"], m.a / self.F, m.b / self.F)
                elif m.kind == "crop":
                    s.crop = CropSpec(a.get("aspect"), a.get("box"), a["position"])
                elif m.kind == "reframe":
                    s.reframe = ReframeSpec(a["aspect"], a["mode"], float(a["smooth"]))
                elif m.kind == "color":
                    s.color = ColorSpec(float(a["brightness"]), float(a["contrast"]), float(a["saturation"]), float(a["gamma"]), float(a["hue"]))
                elif m.kind == "effect":
                    s.effects.append(EffectSpec(a["name"], float(a["strength"])))
                elif m.kind == "mute":
                    s.mute = True
                elif m.kind == "volume":
                    s.volume *= float(a["gain"])
            s.frames = max(1, int(round(s.src_len_f / speed)))
        return segs

    # -- splitting helpers ---------------------------------------------------
    @staticmethod
    def _clone(seg: Segment) -> Segment:
        c = copy.copy(seg)
        c.effects = list(seg.effects)
        c.lines = list(seg.lines)
        return c

    def _split_at(self, seg: Segment, u: int) -> Tuple[Segment, Segment]:
        """Split a segment after ``u`` output frames. The first part keeps the fade-in, the second the fade-out."""
        u = max(1, min(seg.frames - 1, u))
        if seg.src_len_f >= 2:
            l1 = max(1, min(seg.src_len_f - 1, int(round(seg.src_len_f * u / seg.frames))))
            l2 = seg.src_len_f - l1
        else:
            l1 = l2 = seg.src_len_f
        a, b = self._clone(seg), self._clone(seg)
        a.frames, b.frames = u, seg.frames - u
        a.src_len_f, b.src_len_f = l1, l2
        if seg.reverse:
            a.src_start_f = seg.src_start_f + seg.src_len_f - l1 if seg.src_len_f >= 2 else seg.src_start_f
            b.src_start_f = seg.src_start_f
        else:
            a.src_start_f = seg.src_start_f
            b.src_start_f = seg.src_start_f + l1 if seg.src_len_f >= 2 else seg.src_start_f
        a.join_out = None
        b.join_in = None
        return a, b

    def _split_many(self, seg: Segment, points: Sequence[int]) -> List[Segment]:
        parts: List[Segment] = []
        rest, consumed = seg, 0
        for p in points:
            u = p - consumed
            if u <= 0 or u >= rest.frames:
                continue
            a, rest = self._split_at(rest, u)
            parts.append(a)
            consumed = p
        parts.append(rest)
        return parts

    def _chunk_frames(self) -> int:
        return max(6, min(150, int(40e6 / max(1, self.out.width * self.out.height * 1.5))))

    def _apply_reverse(self, segs: List[Segment]) -> List[Segment]:
        out: List[Segment] = []
        i = 0
        ch = self._chunk_frames()
        while i < len(segs):
            s = segs[i]
            if s.rev_group < 0:
                out.append(s)
                i += 1
                continue
            j = i
            while j < len(segs) and segs[j].rev_group == s.rev_group:
                j += 1
            chunks: List[Segment] = []
            for r in segs[i:j]:
                pts = list(range(ch, r.frames, ch))
                chunks.extend(self._split_many(r, pts))
            for c in reversed(chunks):
                c.reverse = True
                c.rev_group = -1
                out.append(c)
            i = j
        return out

    def _apply_freezes(self, segs: List[Segment]) -> List[Segment]:
        for st in self.script.steps:
            if st.name != "freeze":
                continue
            p = self.fr(self.secs(st.args["at"]))
            d = max(1, self.fr(self.secs(st.args["duration"])))
            placed = False
            for i, s in enumerate(segs):
                if s.kind != "clip" or s.reverse:
                    continue
                if s.src_start_f <= p < s.src_start_f + s.src_len_f:
                    if p > s.src_start_f and s.frames >= 2:
                        u = int(round((p - s.src_start_f) / s.speed))
                        a, b = self._split_at(s, u)
                        base = a
                        hold = self._freeze_from(base, p, d, st)
                        segs[i:i + 1] = [a, hold, b]
                    else:
                        hold = self._freeze_from(s, p, d, st)
                        segs.insert(i, hold)
                    placed = True
                    break
                if p == s.src_start_f + s.src_len_f and (i + 1 == len(segs) or segs[i + 1].src_start_f != p):
                    hold = self._freeze_from(s, p - 1, d, st)
                    segs.insert(i + 1, hold)
                    placed = True
                    break
            if not placed:
                self.warn(f"The freeze at {self.secs(st.args['at']):g}s is in a part that was cut out, so it was skipped.", st.line)
        return segs

    def _freeze_from(self, base: Segment, frame: int, d: int, st: Step) -> Segment:
        f = self._clone(base)
        f.kind, f.src_start_f, f.src_len_f, f.frames = "freeze", frame, 1, d
        f.reverse, f.mute, f.join_in, f.join_out, f.rev_group = False, True, None, None, -1
        f.lines = [st.line]
        if f.camera:
            f.camera = copy.copy(f.camera)
        return f

    # -- every Nth ------------------------------------------------------------
    def _apply_every_nth(self, segs: List[Segment]) -> None:
        for st in self.script.steps:
            if st.name != "every_nth":
                continue
            n, off = int(st.args["n"]), int(st.args["offset"])
            count = 0
            for s in segs:
                if s.kind != "clip":
                    continue
                count += 1
                if (count + off) % n == 0:
                    s.effects.append(EffectSpec(st.args["effect"], float(st.args["strength"])))
                    s.lines.append(st.line)
            self.notes.append(f"'every_nth {n}' applied to {count // n} of {count} segment(s).")

    # -- layout ---------------------------------------------------------------
    def _layout(self) -> None:
        t = 0
        prev: Optional[Segment] = None
        for s in self.segs:
            if prev is not None and prev.join_out is not None:
                t -= prev.join_out.frames
            s.out_start = t
            t += s.frames
            prev = s
        self.total = t

    # -- transitions ----------------------------------------------------------
    def _contiguous(self, a: Segment, b: Segment) -> bool:
        return (a.kind == "clip" and b.kind == "clip" and a.reverse == b.reverse and
                ((not a.reverse and b.src_start_f == a.src_start_f + a.src_len_f) or
                 (a.reverse and a.src_start_f == b.src_start_f + b.src_len_f)))

    def _apply_transitions(self) -> None:
        segs = self.segs
        for st in self.script.steps:
            if st.name != "transition":
                continue
            d_req = max(2, self.fr(self.secs(st.args["duration"])))
            at = self.secs(st.args["at"]) if st.args.get("at") is not None else None
            cands: List[int] = []
            for i in range(len(segs) - 1):
                a, b = segs[i], segs[i + 1]
                if not st.args["contiguous"] and self._contiguous(a, b):
                    continue
                if at is not None:
                    end_a = (a.src_start_f + a.src_len_f) / self.F
                    start_b = b.src_start_f / self.F
                    if min(abs(end_a - at), abs(start_b - at)) > 0.3:
                        continue
                cands.append(i)
            every = int(st.args["every"])
            chosen = [c for k, c in enumerate(cands) if (k + 1) % every == 0]
            made = 0
            for i in chosen:
                a, b = segs[i], segs[i + 1]
                room_a = a.frames - a.head - 1     # a clip keeps at least one frame of its own
                room_b = b.frames - b.tail - 1
                d = min(d_req, min(a.frames, b.frames) // 2, room_a, room_b)
                if d < 2:
                    self.warn("A transition was skipped because one of the clips is too short for it.", st.line)
                    continue
                if d < d_req:
                    self.warn(f"A transition was shortened to {d / self.F:.2f}s because a clip is short.", st.line)
                j = JoinSpec(st.args["type"], d)
                a.join_out = j
                b.join_in = j
                made += 1
            self.notes.append(f"Added {made} transition(s) of type '{st.args['type']}'.")

    # -- beat snap ------------------------------------------------------------
    def _music_beats(self, m: MusicSpec) -> BeatInfo:
        if m.file not in self._music_beats_cache:
            dur = probe_media(m.file).duration
            info = self.an.beats(path=m.file, duration=dur)
            for n in info.notes:
                self.notes.append(f"Music: {n}")
            self._music_beats_cache[m.file] = info
        return self._music_beats_cache[m.file]

    def _output_beats(self, st: Step, music: Optional[MusicSpec], total_s: float, downbeats: bool) -> List[float]:
        """Beat times in OUTPUT seconds for source=music / source=bpm."""
        if st.args.get("source") == "bpm":
            info = self._beat_info(st)
            return self._grid(info, downbeats, st)
        if music is None:
            raise ScriptError(f"'{st.name}' follows your background music, but the script has no 'music' line.",
                              "Add a line like:  music song.mp3   -- or use source=bpm bpm=120", st.line)
        info = self._music_beats(music)
        base = self._grid(info, downbeats, st)
        mdur = probe_media(music.file).duration
        out: List[float] = []
        k = 0
        while music.start_s + k * mdur < total_s + 1:
            out.extend(music.start_s + k * mdur + b for b in base if b < mdur)
            k += 1
            if not music.loop:
                break
        return out

    def _apply_beat_snap(self, music: Optional[MusicSpec]) -> None:
        for st in self.script.steps:
            if st.name != "beat_snap":
                continue
            self._layout()
            total_s = self.total / self.F
            beats = self._output_beats(st, music, total_s + 5, bool(st.args["downbeats"]))
            bf = sorted(self.fr(b + (self.secs(st.args["offset"]) if st.args.get("source") == "bpm" else 0.0)) for b in beats)
            if not bf:
                self.warn("No beats were found, so 'beat_snap' did nothing.", st.line)
                continue
            tol = self.fr(self.secs(st.args["tolerance"]))
            t = 0
            moved = 0
            segs = self.segs
            for i, s in enumerate(segs):
                end = t + s.frames
                if i < len(segs) - 1 and s.kind == "clip":
                    j = bisect.bisect_left(bf, end)
                    cands = [bf[k] for k in (j - 1, j) if 0 <= k < len(bf)]
                    if cands:
                        best = min(cands, key=lambda c: abs(c - end))
                        delta = best - end
                        nxt = segs[i + 1]
                        if delta != 0 and abs(delta) <= tol and s.frames + delta >= 2 and not s.join_out:
                            add_src = int(round(delta * s.speed))
                            contiguous = self._contiguous(s, nxt) and not s.reverse
                            if contiguous and nxt.frames - delta >= 2 and nxt.src_len_f - add_src >= 1 and not nxt.join_in:
                                s.src_len_f += add_src
                                s.frames += delta
                                nxt.src_start_f += add_src
                                nxt.src_len_f -= add_src
                                nxt.frames -= delta
                                moved += 1
                            elif not s.reverse and s.src_start_f + s.src_len_f + add_src <= self.D_f and s.src_len_f + add_src >= 1:
                                s.src_len_f += add_src
                                s.frames += delta
                                moved += 1
                            end = t + s.frames
                t = end
            self.notes.append(f"Moved {moved} cut point(s) onto the beat.")

    # -- beat effects ---------------------------------------------------------
    def _pulse_windows(self, music: Optional[MusicSpec]) -> List[Tuple[int, int, Step]]:
        wins: List[Tuple[int, int, Step]] = []
        self._layout()
        total_s = self.total / self.F
        clip_rows = sorted(((s.src_start_f, s.src_start_f + s.src_len_f, s) for s in self.segs if s.kind == "clip" and not s.reverse),
                           key=lambda r: r[0])
        row_starts = [r[0] for r in clip_rows]
        for st in self.script.steps:
            if st.name != "beat_effect":
                continue
            src = st.args["source"]
            down = st.args["on"] == "downbeat"
            w0, w1 = self._range_frames(st)
            if src == "audio":
                info = self._beat_info(Step(st.name, dict(st.args, source="audio"), st.line))
                times = self._grid(info, down, st)
                frames_out: List[int] = []
                for tb in times:
                    p = self.fr(tb)
                    if not (w0 <= p < w1):
                        continue
                    i = bisect.bisect_right(row_starts, p) - 1
                    if i >= 0 and clip_rows[i][0] <= p < clip_rows[i][1]:
                        s = clip_rows[i][2]
                        frames_out.append(s.out_start + int(round((p - s.src_start_f) / s.speed)))
            else:
                times = self._output_beats(st, music, total_s + 2, down)
                off = self.secs(st.args["offset"]) if src == "bpm" else 0.0
                frames_out = [self.fr(t + off) for t in times if 0 <= t + off < total_s]
            every = int(st.args["every"])
            frames_out = sorted(set(frames_out))[::every]
            length = max(2, self.fr(self.secs(st.args["length"])))
            for f in frames_out:
                wins.append((f, length, st))
            self.notes.append(f"'beat_effect {st.args['effect']}': {len(frames_out)} pulse(s).")
        return wins

    def _apply_beat_effects(self, music: Optional[MusicSpec]) -> None:
        if not any(s.name == "beat_effect" for s in self.script.steps):
            return
        wins = self._pulse_windows(music)
        if not wins:
            return
        self._layout()
        starts = [s.out_start for s in self.segs]
        per_seg: Dict[int, List[Tuple[int, int, Step]]] = {}
        for f, length, st in sorted(wins, key=lambda w: w[0]):
            i = bisect.bisect_right(starts, f) - 1
            if i < 0:
                continue
            s = self.segs[i]
            lo, hi = s.out_start + s.head, s.out_end - s.tail
            f0, f1 = max(f, lo), min(f + length, hi)
            if f1 - f0 < 1 or s.kind != "clip":
                continue
            per_seg.setdefault(i, []).append((f0 - s.out_start, f1 - f0, st))
        new: List[Segment] = []
        for i, s in enumerate(self.segs):
            if i not in per_seg:
                new.append(s)
                continue
            merged: List[Tuple[int, int, Step]] = []
            for u, l, st in per_seg[i]:
                if merged and u < merged[-1][0] + merged[-1][1]:
                    continue
                merged.append((u, l, st))
            points: List[int] = []
            for u, l, _ in merged:
                points += [u, u + l]
            points = sorted(set(p for p in points if 0 < p < s.frames))
            parts = self._split_many(s, points)
            bounds = [0] + points + [s.frames]
            pulse_ranges = {(u, u + l): st for u, l, st in merged}
            for part, (b0, b1) in zip(parts, zip(bounds, bounds[1:])):
                st = pulse_ranges.get((b0, b1))
                if st is not None:
                    name = st.args["effect"]
                    inten = float(st.args["intensity"])
                    if name == "zoom_pulse":
                        part.camera = CameraSpec(1.0 + 0.18 * inten, 1.0, 0.5, 0.5, 0.5, 0.5, "ease_out", 0.0, 1.0, pulse=True)
                    else:
                        part.effects.append(EffectSpec(name, inten, "pulse"))
                    part.lines.append(st.line)
                new.append(part)
        self.segs = new
        if len(self.segs) > MAX_SEGMENTS:
            raise ScriptError(f"The beat effects would make more than {MAX_SEGMENTS:,} pieces.", "Use every=2 or a shorter range.")

    # ------------------------------------------------------------------ 3. overlays, audio, captions
    def _out_frame_of_source(self, t: float) -> Optional[int]:
        p = self.fr(t)
        for s in self.segs:
            if s.kind == "clip" and not s.reverse and s.src_start_f <= p < s.src_start_f + s.src_len_f:
                return s.out_start + int(round((p - s.src_start_f) / s.speed))
        return None

    def _overlay_window(self, st: Step) -> Optional[Tuple[int, int]]:
        a = self.secs(st.args["start"])
        if st.args.get("end") is not None:
            b = self.secs(st.args["end"])
        elif st.args.get("duration") is not None:
            b = a + self.secs(st.args["duration"])
        else:
            b = None
        if st.args.get("time") == "source":
            fa = self._out_frame_of_source(a)
            fb = self._out_frame_of_source(b) if b is not None else None
            if fa is None:
                self.warn(f"'{st.name}' is set for {a:g}s of the original video, which was cut out, so it was skipped.", st.line)
                return None
            return fa, fb if fb is not None else self.total
        fa = self.fr(a)
        fb = self.fr(b) if b is not None else self.total
        if fa >= self.total:
            self.warn(f"'{st.name}' starts at {a:g}s but the finished video is only {self.total / self.F:.1f}s long, so it was skipped.", st.line)
            return None
        return fa, min(fb, self.total)

    def _overlays(self) -> Tuple[List[TextOverlay], List[ImageOverlay]]:
        texts: List[TextOverlay] = []
        images: List[ImageOverlay] = []
        for st in self.script.steps:
            if st.name not in ("text", "image"):
                continue
            if not self.has_video:
                self.warn(f"'{st.name}' needs a picture, but there is none in the output; it was ignored.", st.line)
                continue
            win = self._overlay_window(st)
            if win is None or win[1] <= win[0]:
                continue
            a = st.args
            if st.name == "text":
                font = find_font(a.get("font"), self.res)
                if a.get("font") and not os.path.exists(str(a["font"])) and \
                        (font is None or str(a["font"]).lower().replace(" ", "") not in font.lower().replace(" ", "").replace("-", "")):
                    self.warn(f"I could not find the font '{a['font']}', so a standard font is used.", st.line)
                texts.append(TextOverlay(a["text"], win[0], win[1], a["position"], a.get("x"), a.get("y"), float(a["size"]), a["color"],
                                         font, bool(a["box"]), a["box_color"], float(a["box_padding"]), float(a["border"]),
                                         a["border_color"], bool(a["shadow"]), self.secs(a["fade"]), st.line))
            else:
                path = self.res.resolve(a["file"], "image", st.line)
                images.append(ImageOverlay(path, win[0], win[1], a["position"], a.get("x"), a.get("y"), float(a["scale"]),
                                           float(a["width"]) if a.get("width") else None, float(a["opacity"]), float(a["margin"]), st.line))
        return texts, images

    def _audio(self) -> AudioSpec:
        spec = AudioSpec()
        for st in self.script.steps:
            a = st.args
            if st.name == "loudnorm" and a["enabled"]:
                spec.loudnorm = {"target": float(a["target"]), "true_peak": float(a["true_peak"]), "lra": float(a["lra"])}
            elif st.name == "denoise" and a["enabled"]:
                spec.denoise = {"amount": float(a["amount"]), "highpass": bool(a["highpass"])}
            elif st.name == "music":
                path = self.res.resolve(a["file"], "music file", st.line)
                info = probe_media(path)
                if not info.has_audio:
                    raise ScriptError(f"The music file '{a['file']}' has no sound.", "Choose an audio file (mp3, wav, m4a ...).", st.line)
                spec.music.append(MusicSpec(path, float(a["volume"]), bool(a["duck"]), self.secs(a["start"]),
                                            self.secs(a["end"]) if a.get("end") is not None else None, bool(a["loop"]),
                                            self.secs(a["fade_in"]), self.secs(a["fade_out"]), st.line))
            elif st.name == "fade":
                spec.fade_in, spec.fade_out = self.secs(a["fade_in"]), self.secs(a["fade_out"])
                spec.fade_audio, spec.fade_video, spec.fade_color = bool(a["audio"]), bool(a["video"]) and self.has_video, a["color"]
        if spec.loudnorm and not self.media.has_audio and not spec.music:
            self.warn("'loudnorm' was ignored because there is no sound to measure.")
            spec.loudnorm = None
        if spec.denoise and not self.media.has_audio:
            spec.denoise = None
        return spec

    def _captions(self) -> Optional[CaptionSpec]:
        steps = self.script.steps_named("captions")
        if not steps:
            return None
        st = steps[-1]
        if len(steps) > 1:
            self.warn("There is more than one 'captions' line; only the last one is used.", st.line)
        if not self.media.has_audio:
            raise ScriptError("'captions' needs speech, but this file has no sound.", "Remove the 'captions' line.", st.line)
        a = st.args
        override = a.get("transcript") or self.transcript_override
        if override:
            path = self.res.resolve(override, "transcript file", st.line)
            words = import_transcript(path)
            language, source = "", f"transcript file {path}"
        else:
            words, language = self.an.transcript(a["language"], a["model"], a["device"])
            source = f"local Whisper model '{a['model']}'"
        mapped = remap_words(words, self.segs, self.F)
        if not mapped:
            self.warn("No speech was found in the parts of the video that are kept, so no captions were made.", st.line)
        lines = group_lines(mapped, int(a["words_per_line"]), int(a["max_chars"]))
        font = a.get("font")
        if font:
            found = find_font(font, self.res)
            font = os.path.splitext(os.path.basename(font))[0] if found and ("/" in font or "\\" in font) else font
        return CaptionSpec(a["mode"], a["style"], float(a["font_size"]), a["color"], a["highlight"], a["outline"], a["position"],
                           float(a["margin"]), font, bool(a["files"]), lines, language or "", source)

    # ------------------------------------------------------------------ 4. video mode and fast cuts
    def _video_needs_processing(self) -> bool:
        for st in self.script.steps:
            if st.name in VIDEO_STEPS:
                return True
            if st.name == "captions" and st.args["mode"] in ("burn", "both"):
                return True
            if st.name == "fade" and st.args.get("video") and (self.secs(st.args["fade_in"]) > 0 or self.secs(st.args["fade_out"]) > 0):
                return True
        return False

    def _decide_video_mode(self, sel: List[Tuple[int, int]]) -> str:
        if not self.has_video:
            return "encode"
        needs = self._video_needs_processing()
        src_w, src_h = self.media.width, self.media.height
        same_shape = (self.out.width, self.out.height) == (src_w - src_w % 2, src_h - src_h % 2) and \
            abs(float(self.out.fps) - float(self.media.fps)) < 0.01 and self.media.rotation == 0
        h264 = self.media.video_codec == "h264" and self.media.pix_fmt in ("yuv420p", "yuvj420p")
        if self.fast_cuts:
            problems = []
            if needs:
                problems.append("your script uses speed, effects, text, transitions or other picture changes")
            if not same_shape:
                problems.append("a different picture size or frame rate was asked for")
            if not h264:
                problems.append(f"the video is {self.media.video_codec or 'not'} H.264 (8-bit 4:2:0)")
            if problems:
                raise ScriptError("Fast cuts cannot be used here because " + "; ".join(problems) + ".",
                                  "Turn off --fast-cuts (exact cuts always work), or remove those lines.")
            return "copy_cuts"
        if not needs and h264 and same_shape and self.out.container in ("mp4", "mov", "mkv") and len(sel) == 1 \
                and sel[0] == (0, self.D_f) and not any(s.name in ("scene_cut", "beat_cut") for s in self.script.steps):
            return "copy_all"
        return "encode"

    def _snap_to_keyframes(self, sel: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
        keys = self.an.keyframes()
        if not keys:
            self.warn("No keyframes were found; fast cuts fall back to exact cuts.")
            return sel
        kf = sorted(self.fr(k) for k in keys)
        out = IntervalSet()
        for a, b in sel:
            i = bisect.bisect_right(kf, a) - 1
            a2 = kf[max(0, i)]
            j = bisect.bisect_left(kf, b)
            b2 = kf[j] if j < len(kf) else self.D_f
            out.add(a2, min(b2, self.D_f))
        self.notes.append("Fast cuts: every cut moved to the nearest keyframe (the result can start a little earlier and end a little later than you asked).")
        return out.ranges

    # ------------------------------------------------------------------ main entry
    def build(self) -> Timeline:
        sel, splits = self._select()
        if not sel:
            raise ScriptError("Your edit removes everything, so there would be no video left.",
                              "Check your keep/cut ranges and the silence settings (for example a lower threshold like -45).")
        mode = self._decide_video_mode(sel)
        if mode == "copy_cuts":
            sel = self._snap_to_keyframes(sel)
            splits = set()
        mods = self._mods()
        segs = self._build_segments(sel, mods, splits)
        segs = self._apply_reverse(segs)
        segs = self._apply_freezes(segs)
        self.segs = segs
        self._apply_every_nth(self.segs)
        self._layout()
        audio_spec = self._audio()
        first_music = audio_spec.music[0] if audio_spec.music else None
        self._apply_beat_snap(first_music)
        self._apply_transitions()
        self._apply_beat_effects(first_music)
        self._layout()
        self._resolve_reframes()
        texts, images = self._overlays()
        captions = self._captions()
        if self.total < 1:
            raise ScriptError("The finished video would be empty.", "Check your keep/cut ranges.")
        tl = Timeline(self.segs, self.out, self.media.path, self.D, self.media.fps, self.total, texts, images, audio_spec,
                      captions, mode, self.warnings, self.notes)
        if mode == "copy_cuts":
            self.notes.append("Video is copied without re-encoding (fast cuts).")
        elif mode == "copy_all":
            self.notes.append("Nothing changes the picture, so the video is copied without re-encoding.")
        return tl

    def _resolve_reframes(self) -> None:
        wanted = [s for s in self.segs if s.reframe is not None]
        if not wanted:
            return
        if self.face_provider is None:
            for s in wanted:
                s.reframe.mode = "center" if s.reframe.mode in ("auto", "center") else s.reframe.mode
            modes = {s.reframe.mode for s in wanted}
            if modes & {"face", "speaker"}:
                raise FeatureUnavailable(
                    "Face-following re-framing is switched off because the free 'opencv-python-headless' add-on is not installed.",
                    "Install it with:  pip install opencv-python-headless   -- or use  reframe 9:16 mode=center")
            if any(st.args.get("mode") == "auto" for st in self.script.steps_named("reframe")):
                self.notes.append("Re-framing uses the centre of the picture (face tracking is off: opencv is not installed).")
            return
        self.face_provider(self.segs, self)
