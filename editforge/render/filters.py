"""Building FFmpeg filter graphs for one piece of the output.

A piece is made of *items*. An item is a part of one Segment: its first output frame ``u0`` and
its number of frames ``n``. Every item is turned into exactly ``n`` frames and an exact number of
audio samples, so pieces always join without drift.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Dict, List, Optional, Sequence, Tuple

from ..core.errors import ScriptError
from ..core.ffmpeg import get_tools
from ..core.media import MediaInfo
from ..core.model import (CameraSpec, EffectSpec, ImageOverlay, Segment, TextOverlay, Timeline)
from ..dsl.coerce import color_to_rgba

SAFE_EXPR = re.compile(r"^[0-9A-Za-z_+\-*/(). %]*$")
MARGIN_FRACTION = 0.05


@dataclass
class Item:
    """A sub-range of one segment."""

    seg: Segment
    u0: int
    n: int
    g0: int  # global output frame where this item starts (used for overlays / fades)


def fps_str(f: Fraction) -> str:
    return f"{f.numerator}/{f.denominator}"


def samples_at(frame: int, fps: Fraction, sr: int) -> int:
    """Audio sample index at an output frame, rounded once (so pieces never drift)."""
    return (frame * sr * fps.denominator * 2 + fps.numerator) // (2 * fps.numerator)


def even(v: float) -> int:
    v = int(round(v))
    return max(2, v - v % 2)


def safe_expr(text: str, what: str = "position", line: Optional[int] = None) -> str:
    """Accept a simple arithmetic expression (numbers, w, h, +-*/). Percentages become fractions of w or h."""
    t = text.strip()
    if not SAFE_EXPR.match(t):
        raise ScriptError(f"The {what} '{text}' contains characters that are not allowed.",
                          "Use numbers, w, h and + - * / ( ) only, for example 10, 5%, (w-text_w)/2.", line)
    return t


def pos_expr(value: str, axis: str) -> str:
    """Convert '10' / '5%' / '(w-text_w)/2' to an FFmpeg expression."""
    v = value.strip()
    if v.endswith("%") and re.fullmatch(r"[\d.]+%", v):
        return f"{axis}*{float(v[:-1]) / 100.0:.6f}"
    return v


def tempo_chain(t: float) -> str:
    parts: List[str] = []
    while t > 2.0:
        parts.append("atempo=2.0")
        t /= 2.0
    while t < 0.5:
        parts.append("atempo=0.5")
        t /= 0.5
    parts.append(f"atempo={t:.8f}")
    return ",".join(parts)


def ease_expr(name: str, p: str) -> str:
    if name == "ease_in":
        return f"(({p})*({p}))"
    if name == "ease_out":
        return f"(1-(1-({p}))*(1-({p})))"
    if name == "ease_in_out":
        return f"(({p})*({p})*(3-2*({p})))"
    return f"({p})"


class Graph:
    """Collects filter statements and hands out unique labels."""

    def __init__(self, prefix: str = "g"):
        self.stmts: List[str] = []
        self.prefix = prefix
        self._n = 0

    def label(self, hint: str = "x") -> str:
        self._n += 1
        return f"{self.prefix}{hint}{self._n}"

    def add(self, src: str, flt: str, hint: str = "x") -> str:
        dst = self.label(hint)
        self.stmts.append(f"[{src}]{flt}[{dst}]")
        return dst

    def add_raw(self, stmt: str) -> None:
        self.stmts.append(stmt)

    def text(self) -> str:
        return ";\n".join(self.stmts)


@dataclass
class Dims:
    w: int
    h: int


class ItemBuilder:
    """Creates the video and audio filter chains for items."""

    def __init__(self, tl: Timeline, media: MediaInfo, sr: int, layout: str, has_audio_out: bool, has_video_out: bool):
        self.tl, self.media = tl, media
        self.out = tl.output
        self.F = tl.output.fps
        self.Ff = float(self.F)
        self.sr, self.layout = sr, layout
        self.has_audio_out, self.has_video_out = has_audio_out, has_video_out
        self.src_fps = float(media.fps) if media.has_video else self.Ff
        self.guard = 0.25 / self.src_fps
        # the video's sound can come from its three layers (voices / music / effects) instead of the original track
        self.mix = tl.audio.mix if (tl.audio.mix and media.has_audio) else None
        self.layers = [n for n in ("voices", "music", "effects") if float(self.mix["gain"].get(n, 1.0)) > 0.001] if self.mix else []
        #: how many FFmpeg inputs one item uses: the source, plus one per layer that is heard
        self.stride = 1 + len(self.layers)

    # ---- what to read from the source -------------------------------------------------------
    def source_window(self, it: Item) -> Tuple[float, float]:
        """(start seconds, length seconds) to read for this item (with a little margin)."""
        seg, F = it.seg, self.Ff
        if seg.kind == "freeze":
            a = seg.src_start_f / F
            length = 0.6
        else:
            sp = seg.speed
            if not seg.reverse:
                a = (seg.src_start_f + it.u0 * sp) / F
                b = a + it.n * sp / F
            else:
                b = (seg.src_start_f + seg.src_len_f - it.u0 * sp) / F
                a = b - it.n * sp / F
            length = (b - a)
        a = max(0.0, a - self.guard)
        return a, length + self.guard + 3.0 / self.src_fps

    def input_args(self, it: Item, src: str, dec_threads: int = 1, extra: float = 0.0) -> List[str]:
        a, length = self.source_window(it)
        args = ["-threads", str(max(1, dec_threads)), "-ss", f"{a:.6f}", "-t", f"{length + extra:.6f}", "-i", src]
        if self.mix:
            sa = max(0.0, a - float(self.mix["start"]))          # the layer files begin where the split began
            for name in self.layers:
                args += ["-threads", "1", "-ss", f"{sa:.6f}", "-t", f"{length + extra:.6f}", "-i", self.mix["files"][name]]
        return args

    def audio_input(self, g: "Graph", k: int) -> Optional[str]:
        """The label of the video's sound for item ``k`` (None = silence): the original track, or the mix of the layers."""
        if not self.media.has_audio:
            return None
        base = k * self.stride
        if not self.mix:
            return f"{base}:a:0"
        if not self.layers:
            return None
        labels = []
        loud = False
        for j, name in enumerate(self.layers):
            gain = float(self.mix["gain"].get(name, 1.0))
            loud = loud or gain > 1.0
            labels.append(g.add(f"{base + 1 + j}:a:0", f"aformat=sample_fmts=fltp:sample_rates={self.sr}:channel_layouts=stereo,volume={gain:.5f}", "sx"))
        if len(labels) == 1:
            cur = labels[0]
        else:
            cur = g.label("sm")
            norm = ":normalize=0" if get_tools().amix_normalize else ""
            g.add_raw("".join(f"[{x}]" for x in labels) + f"amix=inputs={len(labels)}:duration=longest:dropout_transition=0{norm}[{cur}]")
            if not norm:
                cur = g.add(cur, f"volume={len(labels)}", "sv")
        if loud:
            cur = g.add(cur, "asoftclip=type=tanh:threshold=0.95", "sc")     # louder layers must not click when they add up
        return cur

    # ---- video ---------------------------------------------------------------------------------
    def _look(self, g: Graph, cur: str, e: EffectSpec, dims: Dims, item_seconds: float) -> str:
        name, s = e.name, e.strength
        W, H = dims.w, dims.h
        if e.kind == "pulse":
            d = max(item_seconds, 0.04)
            if name == "flash":
                return g.add(cur, f"eq=brightness='{0.7 * s:.4f}*max(0,1-t/{d:.4f})':eval=frame", "fl")
            if name == "contrast":
                return g.add(cur, f"eq=contrast='1+{1.2 * s:.4f}*max(0,1-t/{d:.4f})':eval=frame", "ct")
            if name == "shake":
                amp = 0.03 * s / 0.5
                return g.add(cur, (f"crop=w=trunc(iw*0.94/2)*2:h=trunc(ih*0.94/2)*2:x='(iw-out_w)/2+sin(t*90)*iw*{amp:.4f}':"
                                   f"y='(ih-out_h)/2+cos(t*77)*ih*{amp:.4f}',scale={W}:{H}"), "sh")
        if name == "hflip":
            return g.add(cur, "hflip", "hf")
        if name in ("flip", "vflip"):
            return g.add(cur, "vflip", "vf")
        if name == "invert":
            return g.add(cur, "negate", "ng")
        if name == "grayscale":
            return g.add(cur, "hue=s=0", "gr")
        if name == "blur":
            return g.add(cur, f"gblur=sigma={1 + s * 12:.2f}:steps=2", "bl")
        if name == "sharpen":
            return g.add(cur, f"unsharp=luma_msize_x=5:luma_msize_y=5:luma_amount={0.5 + s * 2.5:.2f}", "sp")
        if name == "sepia":
            return g.add(cur, "colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131:0", "se")
        if name == "vignette":
            return g.add(cur, f"vignette=angle=PI/{2 + (1 - s) * 4:.2f}", "vg")
        if name == "mirror":
            half = (W // 2) - ((W // 2) % 2)
            a, b, l, r = g.label("ma"), g.label("mb"), g.label("ml"), g.label("mr")
            g.add_raw(f"[{cur}]split=2[{a}][{b}]")
            g.add_raw(f"[{a}]crop={half}:{H}:0:0[{l}]")
            g.add_raw(f"[{b}]crop={half}:{H}:0:0,hflip[{r}]")
            m = g.label("mm")
            g.add_raw(f"[{l}][{r}]hstack=inputs=2[{m}]")
            if half * 2 != W:
                return g.add(m, f"pad={W}:{H}:{(W - half * 2) // 2}:0:black", "mp")
            return m
        raise ValueError(f"unknown effect {name}")

    def _fit(self, g: Graph, cur: str, dims: Dims) -> Tuple[str, Dims]:
        W, H, fit = self.out.width, self.out.height, self.out.fit
        cw, ch = dims.w, dims.h
        if (cw, ch) == (W, H):
            return cur, dims
        if fit == "stretch":
            return g.add(cur, f"scale={W}:{H}:flags=lanczos", "fs"), Dims(W, H)
        if fit == "crop":
            k = max(W / cw, H / ch)
            fw, fh = max(W, even(math.ceil(cw * k))), max(H, even(math.ceil(ch * k)))
            return g.add(cur, f"scale={fw}:{fh}:flags=lanczos,crop={W}:{H}", "fc"), Dims(W, H)
        k = min(W / cw, H / ch)
        fw, fh = min(W, even(cw * k)), min(H, even(ch * k))
        if fit == "blur":
            a, b, bg, fg, res = g.label("ba"), g.label("bb"), g.label("bg"), g.label("bf"), g.label("br")
            kb = max(W / cw, H / ch)
            bw, bh = max(W, even(math.ceil(cw * kb))), max(H, even(math.ceil(ch * kb)))
            g.add_raw(f"[{cur}]split=2[{a}][{b}]")
            g.add_raw(f"[{a}]scale={bw}:{bh},crop={W}:{H},gblur=sigma=28:steps=2[{bg}]")
            g.add_raw(f"[{b}]scale={fw}:{fh}:flags=lanczos[{fg}]")
            g.add_raw(f"[{bg}][{fg}]overlay=(W-w)/2:(H-h)/2[{res}]")
            return res, Dims(W, H)
        chain = f"scale={fw}:{fh}:flags=lanczos"
        if (fw, fh) != (W, H):
            chain += f",pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black"
        return g.add(cur, chain, "fp"), Dims(W, H)

    def _frame(self, g: Graph, cur: str, dims: Dims, n: int) -> Tuple[str, Dims]:
        """Two layers on a canvas of the chosen shape (like CapCut):
        below, the same picture filling the canvas (blurred, or black / white);
        above, the picture fitted, then scaled, moved and turned, and optionally cut to a mask shape with soft edges."""
        fr = self.out.frame or {}
        W, H = self.out.width, self.out.height
        cw, ch = dims.w, dims.h
        a, b, bg, bg1, bg2, fg, top = (g.label(x) for x in ("ka", "kb", "kg", "kg1", "kg2", "kf", "kt"))
        g.add_raw(f"[{cur}]split=2[{a}][{b}]")
        kc = max(W / cw, H / ch)
        bw, bh = max(W, even(math.ceil(cw * kc))), max(H, even(math.ceil(ch * kc)))
        back = f"scale={bw}:{bh},crop={W}:{H}"
        if fr.get("bg") == "blur":
            sigma = 1.0 + float(fr.get("blur", 50)) / 100.0 * min(W, H) * 0.06
            back += f",gblur=sigma={sigma:.2f}:steps=2"
        else:
            color = "white" if fr.get("bg") == "white" else "black"
            back += f",drawbox=x=0:y=0:w=iw:h=ih:color={color}@1:t=fill"
        g.add_raw(f"[{a}]{back},format=yuv420p[{bg}]")
        k = min(W / cw, H / ch) * float(fr.get("scale", 1.0))
        fw, fh = max(2, even(cw * k)), max(2, even(ch * k))
        front = f"scale={fw}:{fh}:flags=lanczos"
        rot = float(fr.get("rotate", 0.0))
        if abs(rot) > 0.01:
            front += f",format=rgba,rotate={math.radians(rot):.6f}:ow=rotw({math.radians(rot):.6f}):oh=roth({math.radians(rot):.6f}):c=0x00000000"
        g.add_raw(f"[{b}]{front}[{fg}]")
        dx, dy = float(fr.get("x", 0.0)) * W / 2, float(fr.get("y", 0.0)) * H / 2
        mask = (fr.get("mask") or {}).get("shape", "none")
        if mask == "none":
            g.add_raw(f"[{bg}][{fg}]overlay=x='(W-w)/2+{dx:.1f}':y='(H-h)/2+{dy:.1f}':format=auto,format=yuv420p[{top}]")
            return top, Dims(W, H)
        g.add_raw(f"[{bg}]split=2[{bg1}][{bg2}]")
        sharp, alpha, cut, res = g.label("ks"), g.label("km"), g.label("kc"), g.label("kr")
        g.add_raw(f"[{bg1}][{fg}]overlay=x='(W-w)/2+{dx:.1f}':y='(H-h)/2+{dy:.1f}':format=auto,format=yuva420p[{sharp}]")
        g.add_raw(f"{self.mask_source(fr['mask'], W, H, n)}[{alpha}]")
        g.add_raw(f"[{sharp}][{alpha}]alphamerge[{cut}]")
        g.add_raw(f"[{bg2}][{cut}]overlay=0:0:format=auto,format=yuv420p[{res}]")
        return res, Dims(W, H)

    def mask_source(self, m: Dict[str, Any], W: int, H: int, n: int) -> str:
        """A grey picture (white = the sharp picture shows) drawn ONCE and repeated for ``n`` frames."""
        cx, cy = float(m.get("x", 0.5)) * W, float(m.get("y", 0.5)) * H
        hw, hh = max(1.0, float(m.get("w", 0.5)) * W / 2), max(1.0, float(m.get("h", 0.3)) * H / 2)
        shape = m.get("shape")
        if shape == "rect":
            e = f"lte(abs(X-{cx:.1f}),{hw:.1f})*lte(abs(Y-{cy:.1f}),{hh:.1f})"
        elif shape == "circle":
            e = f"lte(pow((X-{cx:.1f})/{hw:.1f},2)+pow((Y-{cy:.1f})/{hh:.1f},2),1)"
        elif shape == "split":
            e = f"gte(Y,{cy:.1f})"
        elif shape == "vstrip":
            e = f"lte(abs(X-{cx:.1f}),{hw:.1f})"
        else:                                                   # "strip": a band across the whole width
            e = f"lte(abs(Y-{cy:.1f}),{hh:.1f})"
        if m.get("invert"):
            e = f"1-{e}"
        chain = (f"color=c=black:s={W}x{H}:r={fps_str(self.F)},trim=end_frame=1,format=gray,geq=lum='255*({e})'")
        feather = float(m.get("feather", 0.0))
        if feather > 0.5:
            chain += f",gblur=sigma={feather / 100.0 * min(W, H) * 0.08:.2f}:steps=3"
        chain += f",loop=loop={max(0, n - 1)}:size=1:start=0,setpts=N/({self.Ff:.9f}*TB)"
        return chain

    def _crop(self, seg: Segment, dims: Dims) -> Optional[Tuple[int, int, int, int]]:
        c = seg.crop
        if c is None:
            return None
        cw, ch = dims.w, dims.h
        if c.box:
            def val(t: str, total: int) -> int:
                return int(round(float(t[:-1]) / 100.0 * total)) if t.endswith("%") else int(round(float(t)))
            x, y, w, h = val(c.box[0], cw), val(c.box[1], ch), val(c.box[2], cw), val(c.box[3], ch)
            w, h = max(2, min(w, cw)), max(2, min(h, ch))
            w, h = w - w % 2, h - h % 2
            return w, h, max(0, min(x, cw - w)), max(0, min(y, ch - h))
        return self._aspect_crop(c.aspect or "16:9", c.position, dims)

    @staticmethod
    def _aspect_crop(aspect: str, position: str, dims: Dims) -> Tuple[int, int, int, int]:
        a, b = (int(x) for x in aspect.split(":"))
        cw, ch = dims.w, dims.h
        if cw * b > ch * a:
            h = ch
            w = int(round(ch * a / b))
        else:
            w = cw
            h = int(round(cw * b / a))
        w, h = max(2, w - w % 2), max(2, h - h % 2)
        x = {"left": 0, "right": cw - w}.get(position, (cw - w) // 2)
        y = {"top": 0, "bottom": ch - h}.get(position, (ch - h) // 2)
        return w, h, x - x % 2, y - y % 2

    def _reframe(self, g: Graph, cur: str, it: Item, dims: Dims) -> Tuple[str, Dims]:
        r = it.seg.reframe
        assert r is not None
        w, h, x, y = self._aspect_crop(r.aspect, "center", dims)
        if r.path and len(r.path) >= 2:
            expr = self.path_expr(it, r.path, dims.w, w)
            return g.add(cur, f"crop={w}:{h}:x='{expr}':y={y}", "rf"), Dims(w, h)
        return g.add(cur, f"crop={w}:{h}:{x}:{y}", "rc"), Dims(w, h)

    def path_expr(self, it: Item, path: Sequence[Tuple[float, float]], frame_w: int, crop_w: int) -> str:
        """Piecewise-linear crop x-position over item time, from (source time, face centre 0..1) keyframes."""
        seg, F = it.seg, self.Ff
        sp = seg.speed
        span = max(0, frame_w - crop_w)
        pts: List[Tuple[float, float]] = []
        for tau, cx in path:
            u = (tau * F - seg.src_start_f) / sp if not seg.reverse else (seg.src_start_f + seg.src_len_f - tau * F) / sp
            pts.append(((u - it.u0) / F, max(0.0, min(float(span), cx * frame_w - crop_w / 2.0))))
        pts.sort()
        dedup: List[Tuple[float, float]] = []
        for p in pts:
            if not dedup or p[0] > dedup[-1][0] + 1e-6:
                dedup.append(p)
        pts = dedup
        t_end = it.n / F
        inside = [p for p in pts if -0.5 <= p[0] <= t_end + 0.5]
        before = [p for p in pts if p[0] < -0.5]
        after = [p for p in pts if p[0] > t_end + 0.5]
        use = ([before[-1]] if before else []) + inside + ([after[0]] if after else [])
        if len(use) < 2:
            return f"{use[0][1]:.2f}" if use else f"{span / 2:.2f}"
        expr = f"{use[0][1]:.2f}"
        for i in range(len(use) - 1):
            d = use[i + 1][0] - use[i][0]
            slope = (use[i + 1][1] - use[i][1]) / d
            expr += f"+({slope:.4f})*clip(t-({use[i][0]:.4f}),0,{d:.4f})"
        return expr

    @staticmethod
    def follow_expr(path: Sequence[Tuple[float, float, float]], column: int, tau: str) -> str:
        """Straight-line glide through (time, x, y) keyframes as an FFmpeg expression of the source time ``tau``.

        Before the first keyframe the value stays at the first one, after the last at the last one.
        """
        pts = sorted((float(p[0]), max(0.0, min(1.0, float(p[column])))) for p in path)
        keep: List[Tuple[float, float]] = []
        for p in pts:
            if not keep or p[0] > keep[-1][0] + 1e-4:
                keep.append(p)
        expr = f"({keep[0][1]:.5f}"
        for (t0, v0), (t1, v1) in zip(keep, keep[1:]):
            d = t1 - t0
            if abs(v1 - v0) > 1e-6:
                expr += f"+({(v1 - v0) / d:.6f})*clip({tau}-({t0:.4f}),0,{d:.4f})"
        return expr + ")"

    def _camera(self, g: Graph, cur: str, it: Item, dims: Dims) -> str:
        c = it.seg.camera
        assert c is not None
        seg, F = it.seg, self.Ff
        W, H = dims.w, dims.h
        if c.pulse:
            p = f"clip((on+{it.u0})/{max(1, seg.frames)},0,1)"
            z = f"(1+({c.z0:.4f}-1)*(1-{p})*(1-{p}))"
            fx, fy = "0.5", "0.5"
        elif c.path:
            if seg.kind == "freeze":
                tau = f"({seg.src_start_f / F:.6f})"
            elif not seg.reverse:
                tau = f"(({seg.src_start_f}+({seg.speed:.8f})*(on+{it.u0}))/{F:.6f})"
            else:
                tau = f"(({seg.src_start_f + seg.src_len_f}-({seg.speed:.8f})*(on+{it.u0}))/{F:.6f})"
            z = f"{max(1.0, c.z0):.4f}"
            fx, fy = self.follow_expr(c.path, 1, tau), self.follow_expr(c.path, 2, tau)
        else:
            span = max(c.r1 - c.r0, 1e-6)
            if not seg.reverse:
                base, direction = seg.src_start_f, seg.speed
            else:
                base, direction = seg.src_start_f + seg.src_len_f, -seg.speed
            if seg.kind == "freeze":
                p = f"clip(({seg.src_start_f}/{F:.6f}-({c.r0:.6f}))/{span:.6f},0,1)"
            else:
                p = f"clip((({base}+({direction:.8f})*(on+{it.u0}))/{F:.6f}-({c.r0:.6f}))/{span:.6f},0,1)"
            e = ease_expr(c.easing, p)
            z = f"({c.z0:.4f}+({c.z1 - c.z0:.4f})*{e})"
            fx = f"({c.x0:.4f}+({c.x1 - c.x0:.4f})*{e})"
            fy = f"({c.y0:.4f}+({c.y1 - c.y0:.4f})*{e})"
        up = g.add(cur, f"scale={W * 2}:{H * 2}:flags=bicubic", "cu")
        return g.add(up, f"zoompan=z='{z}':x='(iw-iw/{z})*{fx}':y='(ih-ih/{z})*{fy}':d=1:s={W}x{H}:fps={fps_str(self.F)}", "cz")

    def video_chain(self, g: Graph, in_label: str, it: Item) -> str:
        """Filters that turn the raw input into exactly ``it.n`` output frames at the output size."""
        seg = it.seg
        cur = in_label
        cur = g.add(cur, "setpts=PTS-STARTPTS", "v0")
        if seg.kind == "freeze":
            cur = g.add(cur, f"trim=end_frame=1,loop=loop=-1:size=1:start=0,setpts=N/({self.Ff:.9f}*TB)", "fz")
            cur = g.add(cur, f"fps=fps={fps_str(self.F)},trim=end_frame={it.n},setpts=PTS-STARTPTS", "ft")
        else:
            sp = seg.speed
            if abs(sp - 1.0) > 1e-9:
                cur = g.add(cur, f"setpts=PTS/{sp:.9f}", "sp")
            cur = g.add(cur, f"fps=fps={fps_str(self.F)}:round=near", "fp")
            cur = g.add(cur, f"tpad=stop_mode=clone:stop=-1,trim=end_frame={it.n},setpts=PTS-STARTPTS", "tp")
        dims = Dims(self.media.width, self.media.height)
        cropped = self._crop(seg, dims)
        if cropped:
            w, h, x, y = cropped
            cur = g.add(cur, f"crop={w}:{h}:{x}:{y}", "cr")
            dims = Dims(w, h)
        elif seg.reframe is not None:
            cur, dims = self._reframe(g, cur, it, dims)
        if self.out.frame:
            cur, dims = self._frame(g, cur, dims, it.n)
        else:
            cur, dims = self._fit(g, cur, dims)
        if seg.reverse:
            cur = g.add(cur, "reverse", "rv")
            cur = g.add(cur, "setpts=PTS-STARTPTS", "rz")
        if seg.color:
            c = seg.color
            parts = []
            if (c.brightness, c.contrast, c.saturation, c.gamma) != (0.0, 1.0, 1.0, 1.0):
                parts.append(f"eq=brightness={c.brightness:.4f}:contrast={c.contrast:.4f}:saturation={c.saturation:.4f}:gamma={c.gamma:.4f}")
            if c.hue:
                parts.append(f"hue=h={c.hue:.2f}")
            if parts:
                cur = g.add(cur, ",".join(parts), "co")
        secs = it.n / self.Ff
        for e in seg.effects:
            cur = self._look(g, cur, e, dims, secs)
        if seg.camera is not None:
            cur = self._camera(g, cur, it, dims)
        cur = g.add(cur, "setsar=1,format=yuv420p", "vz")
        return cur

    # ---- audio ---------------------------------------------------------------------------------
    def audio_chain(self, g: Graph, in_label: Optional[str], it: Item, samples: int) -> str:
        """Exactly ``samples`` audio samples for this item (silence if the segment is muted or has no sound)."""
        seg = it.seg
        silent = (in_label is None) or seg.mute or seg.kind == "freeze" or not self.media.has_audio
        if silent:
            src = g.label("as")
            g.add_raw(f"anullsrc=r={self.sr}:cl={self.layout}[{src}]")
            cur = g.add(src, f"atrim=end_sample={samples},asetpts=PTS-STARTPTS", "az")
            return cur
        assert in_label is not None
        cur = g.add(in_label, f"atrim=start={self.guard:.6f},asetpts=PTS-STARTPTS", "a0")
        cur = g.add(cur, f"aformat=sample_fmts=fltp:sample_rates={self.sr}:channel_layouts={self.layout}", "af")
        if abs(seg.volume - 1.0) > 1e-9:
            cur = g.add(cur, f"volume={seg.volume:.6f}", "av")
        sp = seg.speed
        if abs(sp - 1.0) > 1e-9:
            if seg.pitch:
                cur = g.add(cur, tempo_chain(sp), "at")
            else:
                cur = g.add(cur, f"asetrate={self.sr * sp:.3f},aresample={self.sr}", "ar")
        if seg.reverse:
            cur = g.add(cur, "areverse", "ar")
        cur = g.add(cur, f"apad,atrim=end_sample={samples},asetpts=PTS-STARTPTS", "az")
        return cur


# ---- overlays applied to a whole piece (after its items are joined) ---------------------------------
def color_ff(canon: str) -> str:
    r, g, b, a = color_to_rgba(canon)
    return f"0x{r:02x}{g:02x}{b:02x}" + (f"@{a:g}" if a < 1 else "")


def text_position(pos: str, x: Optional[str], y: Optional[str]) -> Tuple[str, str]:
    m = f"h*{MARGIN_FRACTION}"
    table = {
        "bottom": ("(w-text_w)/2", f"h-text_h-{m}"), "top": ("(w-text_w)/2", m), "center": ("(w-text_w)/2", "(h-text_h)/2"),
        "top_left": (m, m), "top_right": (f"w-text_w-{m}", m), "bottom_left": (m, f"h-text_h-{m}"),
        "bottom_right": (f"w-text_w-{m}", f"h-text_h-{m}"), "left": (m, "(h-text_h)/2"), "right": (f"w-text_w-{m}", "(h-text_h)/2"),
    }
    if re.fullmatch(r"\s*-?[\d.]+%?\s*,\s*-?[\d.]+%?\s*", pos or ""):
        px, py = pos.split(",")
        return pos_expr(px, "w"), pos_expr(py, "h")
    bx, by = table.get(pos, table["bottom"])
    return (pos_expr(safe_expr(x), "w") if x else bx), (pos_expr(safe_expr(y), "h") if y else by)


def drawtext_filter(t: TextOverlay, out_w: int, out_h: int, fps: Fraction, g0: int, g1: int, textfile: str,
                    fontfile: Optional[str]) -> Optional[str]:
    """One drawtext filter for the part of ``t`` that falls inside the piece [g0, g1)."""
    s, e = max(t.start_f, g0), min(t.end_f, g1)
    if e <= s:
        return None
    f = float(fps)
    a, b = (s - g0) / f, (e - g0) / f
    scale = min(out_w, out_h) / 1080.0
    size = max(6, int(round(t.size * scale)))
    x, y = text_position(t.position, t.x, t.y)
    parts = [f"textfile='{textfile}'", "expansion=none", "reload=0", f"fontsize={size}", f"fontcolor={color_ff(t.color)}",
             f"x='{x}'", f"y='{y}'"]
    if fontfile:
        parts.insert(0, f"fontfile='{fontfile}'")
    if t.box:
        parts += ["box=1", f"boxcolor={color_ff(t.box_color)}", f"boxborderw={max(0, int(round(t.box_padding * scale)))}"]
    if t.border > 0:
        parts += [f"borderw={max(1, int(round(t.border * scale)))}", f"bordercolor={color_ff(t.border_color)}"]
    if t.shadow:
        sh = max(1, int(round(3 * scale)))
        parts += [f"shadowx={sh}", f"shadowy={sh}", "shadowcolor=0x000000@0.6"]
    if t.fade > 0:
        d = t.fade
        parts.append(f"alpha='if(lt(t,{a + d:.4f}),(t-{a:.4f})/{d:.4f},if(gt(t,{b - d:.4f}),({b:.4f}-t)/{d:.4f},1))'")
    parts.append(f"enable='between(t,{a:.4f},{b:.4f})'")
    return "drawtext=" + ":".join(parts)


def image_filters(im: ImageOverlay, out_w: int, out_h: int, fps: Fraction, g0: int, g1: int) -> Optional[Tuple[str, str]]:
    """(prepare-chain for the logo input, overlay filter) for the part of the image inside [g0, g1)."""
    s, e = max(im.start_f, g0), min(im.end_f, g1)
    if e <= s:
        return None
    f = float(fps)
    a, b = (s - g0) / f, (e - g0) / f
    width = int(im.width) if im.width else even(out_w * im.scale)
    width = max(2, width - width % 2)
    prep = f"scale={width}:-2,format=rgba" + (f",colorchannelmixer=aa={im.opacity:.3f}" if im.opacity < 1 else "")
    m = int(round(im.margin * min(out_w, out_h) / 1080.0))
    table = {
        "top_left": (f"{m}", f"{m}"), "top_right": (f"W-w-{m}", f"{m}"), "bottom_left": (f"{m}", f"H-h-{m}"),
        "bottom_right": (f"W-w-{m}", f"H-h-{m}"), "center": ("(W-w)/2", "(H-h)/2"), "top": ("(W-w)/2", f"{m}"),
        "bottom": ("(W-w)/2", f"H-h-{m}"), "left": (f"{m}", "(H-h)/2"), "right": (f"W-w-{m}", "(H-h)/2"),
    }
    x, y = table.get(im.position, table["top_right"])
    if im.x:
        x = pos_expr(safe_expr(im.x), "W")
    if im.y:
        y = pos_expr(safe_expr(im.y), "H")
    if re.fullmatch(r"\s*-?[\d.]+%?\s*,\s*-?[\d.]+%?\s*", im.position or ""):
        px, py = im.position.split(",")
        x, y = pos_expr(px, "W"), pos_expr(py, "H")
    return prep, f"overlay=x='{x}':y='{y}':enable='between(t,{a:.4f},{b:.4f})':format=auto"
