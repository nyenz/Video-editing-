"""Slicing the timeline into pieces that can be rendered independently, in bounded memory.

A piece is either
* ``clips``: up to a few consecutive segment parts, cut from the source and joined by the concat filter,
* ``join``: the overlap of two neighbouring segments (a transition), or
* ``copy``: a stream-copied stretch (fast cuts).

Pieces tile the output exactly: piece N ends at the frame where piece N+1 begins. Each piece writes an
MPEG-TS video file and a WAV audio file. Both are only renamed to their final name when complete.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from typing import Dict, List, Optional, Tuple

from ..core.ffmpeg import get_tools, vsync_args
from ..core.fingerprint import stable_hash
from ..core.media import MediaInfo
from ..core.model import ImageOverlay, JoinSpec, Segment, TextOverlay, Timeline
from ..planner.captions import ass_events, ass_header
from ..version import __version__
from .encoders import video_encoder_args
from .filters import Graph, Item, ItemBuilder, drawtext_filter, image_filters, samples_at


@dataclass
class RenderContext:
    """Everything a piece needs to be built."""

    tl: Timeline
    media: MediaInfo
    src: str
    src_fp: str
    workdir: str
    encoder: str
    threads: int
    sr: int
    layout: str
    has_audio_out: bool
    has_video_out: bool
    font_local: Optional[str] = None


@dataclass
class PieceJob:
    index: int
    kind: str
    items: List[Item]
    g0: int
    g1: int
    join: Optional[JoinSpec] = None
    copy_range: Optional[Tuple[float, float]] = None
    key: str = ""
    video_name: str = ""
    audio_name: str = ""
    a_samples: int = 0

    @property
    def frames(self) -> int:
        return self.g1 - self.g0


@dataclass
class BuiltCommand:
    args: List[str]
    files: Dict[str, str] = field(default_factory=dict)
    outputs: List[Tuple[str, str]] = field(default_factory=list)  # (part name, final name)


def build_pieces(tl: Timeline, batch_inputs: int) -> List[PieceJob]:
    """Cut the timeline into pieces that tile it exactly."""
    pieces: List[PieceJob] = []
    run: List[Item] = []

    def flush() -> None:
        if not run:
            return
        g0 = run[0].g0
        g1 = run[-1].g0 + run[-1].n
        pieces.append(PieceJob(len(pieces), "clips", list(run), g0, g1))
        run.clear()

    segs = tl.segments
    for i, seg in enumerate(segs):
        body = seg.frames - seg.head - seg.tail
        if body > 0:
            run.append(Item(seg, seg.head, body, seg.out_start + seg.head))
            if len(run) >= batch_inputs:
                flush()
        if seg.join_out is not None:
            flush()
            nxt = segs[i + 1]
            n = seg.tail
            a = Item(seg, seg.frames - n, n, seg.out_end - n)
            b = Item(nxt, 0, n, nxt.out_start)
            pieces.append(PieceJob(len(pieces), "join", [a, b], a.g0, a.g0 + n, join=seg.join_out))
    flush()
    return pieces


def _item_spec(it: Item) -> dict:
    d = asdict(it.seg)
    for k in ("out_start", "join_in", "join_out", "lines", "rev_group"):
        d.pop(k, None)
    return {"seg": d, "u0": it.u0, "n": it.n}


def _overlays_in(tl: Timeline, g0: int, g1: int) -> Tuple[List[TextOverlay], List[ImageOverlay]]:
    return ([t for t in tl.texts if t.end_f > g0 and t.start_f < g1], [i for i in tl.images if i.end_f > g0 and i.start_f < g1])


def piece_key(job: PieceJob, ctx: RenderContext) -> str:
    """A key that changes whenever anything that affects this piece's pixels or samples changes."""
    tl = ctx.tl
    texts, images = _overlays_in(tl, job.g0, job.g1)
    cap = None
    if tl.captions and tl.captions.mode in ("burn", "both"):
        cap = [(ln.words, ) for ln in tl.captions.lines if ln.end > job.g0 / float(tl.fps) and ln.start < job.g1 / float(tl.fps)]
        cap = [asdict(tl.captions) | {"lines": None}, cap]
    spec = {
        "v": __version__, "src": ctx.src_fp, "kind": job.kind, "items": [_item_spec(i) for i in job.items],
        "join": asdict(job.join) if job.join else None, "g0": job.g0, "g1": job.g1, "copy": job.copy_range,
        "out": tl.output.to_dict(), "enc": ctx.encoder, "sr": ctx.sr, "layout": ctx.layout,
        "va": [ctx.has_video_out, ctx.has_audio_out], "texts": [asdict(t) for t in texts], "images": [asdict(i) for i in images],
        "caps": cap, "fade": [tl.audio.fade_in, tl.audio.fade_out, tl.audio.fade_video, tl.audio.fade_color, tl.total_frames],
        "font": ctx.font_local and os.path.basename(ctx.font_local),
        "src_dims": [ctx.media.width, ctx.media.height, str(ctx.media.fps), ctx.media.has_audio],
    }
    return stable_hash(spec)


def assign_names(jobs: List[PieceJob], ctx: RenderContext) -> None:
    for j in jobs:
        j.key = piece_key(j, ctx)
        j.a_samples = samples_at(j.g1, ctx.tl.fps, ctx.sr) - samples_at(j.g0, ctx.tl.fps, ctx.sr)
        j.video_name = f"p{j.index:06d}_{j.key[:12]}.ts"
        j.audio_name = f"p{j.index:06d}_{j.key[:12]}.wav"


def filter_script_args(name: str) -> List[str]:
    """How to give FFmpeg a filter graph stored in a file (the option name changed in FFmpeg 7)."""
    if get_tools().at_least(7, 0):
        return ["-/filter_complex", name]
    return ["-filter_complex_script", name]


def build_piece_command(job: PieceJob, ctx: RenderContext) -> BuiltCommand:
    """Build the FFmpeg command that renders one piece."""
    tl, F = ctx.tl, ctx.tl.fps
    out = tl.output
    b = ItemBuilder(tl, ctx.media, ctx.sr, ctx.layout, ctx.has_audio_out, ctx.has_video_out)
    g = Graph(f"p{job.index}")
    args: List[str] = []
    files: Dict[str, str] = {}
    want_v, want_a = ctx.has_video_out, ctx.has_audio_out
    for it in job.items:
        args += b.input_args(it, ctx.src, min(2, ctx.threads))
    n_inputs = len(job.items)
    vlabels: List[str] = []
    alabels: List[str] = []
    for k, it in enumerate(job.items):
        if want_v:
            vlabels.append(b.video_chain(g, f"{k}:v:0", it))
        if want_a:
            m = samples_at(it.g0 + it.n, F, ctx.sr) - samples_at(it.g0, F, ctx.sr)
            alabels.append(b.audio_chain(g, f"{k}:a:0" if ctx.media.has_audio else None, it, m if job.kind == "clips" else job.a_samples))
    vcur = acur = None
    if job.kind == "join":
        assert job.join is not None
        d = job.join.frames / float(F)
        if want_v:
            x = g.label("xf")
            g.add_raw(f"[{vlabels[0]}][{vlabels[1]}]xfade=transition={job.join.type}:duration={d:.6f}:offset=0[{x}]")
            vcur = g.add(x, f"tpad=stop_mode=clone:stop=-1,trim=end_frame={job.frames},setpts=PTS-STARTPTS", "xj")
        if want_a:
            x = g.label("ax")
            dd = max(0.001, (job.a_samples - 32) / ctx.sr)
            g.add_raw(f"[{alabels[0]}][{alabels[1]}]acrossfade=d={dd:.6f}:c1=tri:c2=tri[{x}]")
            acur = g.add(x, f"apad,atrim=end_sample={job.a_samples},asetpts=PTS-STARTPTS", "aj")
    else:
        if len(job.items) == 1:
            vcur = vlabels[0] if want_v else None
            acur = alabels[0] if want_a else None
        else:
            parts = ""
            for k in range(len(job.items)):
                parts += (f"[{vlabels[k]}]" if want_v else "") + (f"[{alabels[k]}]" if want_a else "")
            vo, ao = g.label("vc"), g.label("ac")
            outs = (f"[{vo}]" if want_v else "") + (f"[{ao}]" if want_a else "")
            g.add_raw(f"{parts}concat=n={len(job.items)}:v={int(want_v)}:a={int(want_a)}{outs}")
            vcur, acur = (vo if want_v else None), (ao if want_a else None)
    if want_v and vcur:
        vcur = _piece_overlays(g, vcur, job, ctx, args, files, n_inputs)
    outputs: List[Tuple[str, str]] = []
    gname = f"g{job.index}_{job.key[:8]}.txt"
    fargs = filter_script_args(gname)
    out_args: List[str] = []
    if want_v and vcur:
        out_args += ["-map", f"[{vcur}]"] + video_encoder_args(ctx.encoder, out, ctx.threads) + vsync_args("passthrough") + \
            ["-frames:v", str(job.frames), "-an", "-f", "mpegts", job.video_name + ".part"]
        outputs.append((job.video_name + ".part", job.video_name))
    if want_a and acur:
        out_args += ["-map", f"[{acur}]", "-c:a", "pcm_s16le", "-ar", str(ctx.sr), "-ac", "1" if ctx.layout == "mono" else "2",
                     "-f", "wav", job.audio_name + ".part"]
        outputs.append((job.audio_name + ".part", job.audio_name))
    files[gname] = g.text()
    return BuiltCommand(["-y", "-filter_threads", "1", "-filter_complex_threads", "1"] + args + fargs + out_args, files, outputs)


def _piece_overlays(g: Graph, cur: str, job: PieceJob, ctx: RenderContext, args: List[str], files: Dict[str, str], n_inputs: int) -> str:
    """Text, logos, captions and fades for the part of the video this piece covers."""
    tl = ctx.tl
    F = tl.fps
    out = tl.output
    texts, images = _overlays_in(tl, job.g0, job.g1)
    for i, t in enumerate(texts):
        name = f"t{job.index}_{i}.txt"
        flt = drawtext_filter(t, out.width, out.height, F, job.g0, job.g1, name, ctx.font_local)
        if flt:
            files[name] = t.text
            cur = g.add(cur, flt, "tx")
    idx = n_inputs
    for im in images:
        r = image_filters(im, out.width, out.height, F, job.g0, job.g1)
        if not r:
            continue
        prep, ov = r
        dur = (job.g1 - job.g0) / float(F) + 1.0
        args += ["-loop", "1", "-t", f"{dur:.3f}", "-i", im.file]
        lg = g.add(f"{idx}:v:0", prep, "lg")
        idx += 1
        o = g.label("io")
        g.add_raw(f"[{cur}][{lg}]{ov}[{o}]")
        cur = o
    cap = tl.captions
    if cap and cap.mode in ("burn", "both") and cap.lines:
        t0, t1 = job.g0 / float(F), job.g1 / float(F)
        ev = ass_events(cap.lines, cap, t0, t1)
        if ev:
            name = f"c{job.index}.ass"
            files[name] = ass_header(cap, out.width, out.height) + ev
            cur = g.add(cur, f"ass={name}", "as")
    a = tl.audio
    if a.fade_video and (a.fade_in > 0 or a.fade_out > 0):
        f = float(F)
        total_s = tl.total_frames / f
        col = "black" if a.fade_color == "black" else "white"
        if a.fade_in > 0 and job.g0 < a.fade_in * f:
            cur = g.add(cur, f"fade=t=in:st={-job.g0 / f:.5f}:d={a.fade_in:.4f}:color={col}", "fi")
        if a.fade_out > 0 and job.g1 > (total_s - a.fade_out) * f:
            cur = g.add(cur, f"fade=t=out:st={total_s - a.fade_out - job.g0 / f:.5f}:d={a.fade_out:.4f}:color={col}", "fo")
    return g.add(cur, "format=yuv420p", "vo")


def build_copy_command(job: PieceJob, ctx: RenderContext) -> BuiltCommand:
    """Stream-copy one keyframe-aligned stretch of the source (fast cuts)."""
    assert job.copy_range is not None
    a, b = job.copy_range
    args = ["-y", "-ss", f"{a:.6f}", "-t", f"{b - a:.6f}", "-i", ctx.src, "-map", "0:v:0", "-c:v", "copy", "-an",
            "-avoid_negative_ts", "make_zero", "-f", "mpegts", job.video_name + ".part"]
    return BuiltCommand(args, {}, [(job.video_name + ".part", job.video_name)])


def build_copy_audio_command(job: PieceJob, ctx: RenderContext, samples: int) -> BuiltCommand:
    """Audio for a fast-cut piece, made to match the measured length of its copied video exactly."""
    tl, b = ctx.tl, ItemBuilder(ctx.tl, ctx.media, ctx.sr, ctx.layout, True, False)
    g = Graph(f"c{job.index}")
    it = job.items[0]
    a, length = b.source_window(it)
    args = ["-y", "-ss", f"{a:.6f}", "-t", f"{length + 1:.6f}", "-i", ctx.src]
    lab = b.audio_chain(g, "0:a:0" if ctx.media.has_audio else None, it, samples)
    gname = f"g{job.index}_{job.key[:8]}.txt"
    files = {gname: g.text()}
    outp = ["-map", f"[{lab}]", "-c:a", "pcm_s16le", "-ar", str(ctx.sr), "-ac", "1" if ctx.layout == "mono" else "2",
            "-f", "wav", job.audio_name + ".part"]
    return BuiltCommand(args[:1] + ["-filter_threads", "1"] + args[1:] + filter_script_args(gname) + outp, files,
                        [(job.audio_name + ".part", job.audio_name)])
