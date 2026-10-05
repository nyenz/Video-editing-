"""Captions: remap words through the edit, group them into lines, write SRT and ASS (karaoke)."""

from __future__ import annotations

import bisect
from typing import List, Optional, Sequence, Tuple

from ..analysis.speech import Word
from ..core.model import CaptionLine, CaptionSpec, Segment, Timeline
from ..core.timecode import format_ass, format_srt
from ..dsl.coerce import color_to_rgba

POS_ALIGN = {"bottom": 2, "top": 8, "center": 5, "top_left": 7, "top_right": 9, "bottom_left": 1, "bottom_right": 3,
             "left": 4, "right": 6}


def remap_words(words: Sequence[Word], segments: Sequence[Segment], fps: float) -> List[Tuple[str, float, float]]:
    """Move each word from source time to output time.

    Words inside removed parts are dropped. Words that are mostly cut are dropped, and words
    that straddle a cut are shortened. Speed changes squeeze or stretch the word's duration.
    Reversed and frozen parts have no readable speech, so their words are dropped.
    """
    rows = []
    for seg in segments:
        if seg.kind != "clip" or seg.reverse or seg.mute:
            continue
        rows.append((seg.src_start_f / fps, (seg.src_start_f + seg.src_len_f) / fps, seg))
    rows.sort(key=lambda r: r[0])
    starts = [r[0] for r in rows]
    out: List[Tuple[str, float, float]] = []
    for w in sorted(words, key=lambda x: x.start):
        i = bisect.bisect_right(starts, w.end) - 1
        best = None
        j = i
        while j >= 0 and j > i - 8:
            s0, s1, seg = rows[j]
            ov0, ov1 = max(w.start, s0), min(w.end, s1)
            span = w.end - w.start
            if ov1 > ov0 and (ov1 - ov0) >= 0.4 * max(span, 1e-6):
                best = (s0, seg, ov0, ov1)
                break
            j -= 1
        if best is None:
            continue
        s0, seg, ov0, ov1 = best
        o0 = seg.out_start / fps + (ov0 - s0) / seg.speed
        o1 = seg.out_start / fps + (ov1 - s0) / seg.speed
        if o1 - o0 < 0.02:
            o1 = o0 + 0.02
        out.append((w.text, o0, o1))
    out.sort(key=lambda x: x[1])
    fixed: List[Tuple[str, float, float]] = []
    for text, a, b in out:  # words must not overlap in time
        if fixed and a < fixed[-1][2]:
            pt, pa, pb = fixed[-1]
            fixed[-1] = (pt, pa, max(pa + 0.02, min(pb, a)))
        fixed.append((text, a, max(b, a + 0.02)))
    return fixed


def group_lines(words: Sequence[Tuple[str, float, float]], max_words: int = 6, max_chars: int = 32,
                max_gap: float = 0.8, max_duration: float = 6.0) -> List[CaptionLine]:
    """Group words into caption lines by count, length, pauses and duration."""
    lines: List[CaptionLine] = []
    cur: List[Tuple[str, float, float]] = []

    def flush() -> None:
        nonlocal cur
        if cur:
            lines.append(CaptionLine(list(cur)))
        cur = []

    for w in words:
        if cur:
            chars = sum(len(x[0]) + 1 for x in cur) + len(w[0])
            gap = w[1] - cur[-1][2]
            if len(cur) >= max_words or chars > max_chars or gap > max_gap or (w[2] - cur[0][1]) > max_duration \
                    or cur[-1][0].endswith((".", "?", "!")) and len(cur) >= 2:
                flush()
        cur.append(w)
    flush()
    return lines


def to_srt(lines: Sequence[CaptionLine]) -> str:
    """Write SRT text."""
    out = []
    for i, ln in enumerate(lines, 1):
        out.append(f"{i}\n{format_srt(ln.start)} --> {format_srt(max(ln.end, ln.start + 0.05))}\n{ln.text}\n")
    return "\n".join(out)


def ass_color(canon: str) -> str:
    r, g, b, a = color_to_rgba(canon)
    return f"&H{int(round((1 - a) * 255)):02X}{b:02X}{g:02X}{r:02X}"


def _esc(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", "\\N")


def ass_header(spec: CaptionSpec, width: int, height: int) -> str:
    """The [Script Info] and [V4+ Styles] part of an ASS file."""
    scale = min(width, height) / 1080.0
    size = max(8, int(round(spec.font_size * scale)))
    outline = max(1, int(round(3 * scale)))
    border = 3 if spec.style == "boxed" else 1
    back = ass_color(spec.outline + "@0.6") if spec.style == "boxed" else ass_color("#000000@0.5")
    outline_col = ass_color(spec.outline + "@0.6") if spec.style == "boxed" else ass_color(spec.outline)
    align = POS_ALIGN.get(spec.position, 2)
    margin_v = int(round(spec.margin * scale))
    font = (spec.font or "Arial")
    # PrimaryColour is the highlighted (already sung) colour, SecondaryColour is the waiting colour.
    primary = ass_color(spec.highlight) if spec.style == "karaoke" else ass_color(spec.color)
    secondary = ass_color(spec.color)
    return (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {width}\nPlayResY: {height}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font},{size},{primary},{secondary},{outline_col},{back},-1,0,0,0,100,100,0,0,{border},{outline},0,"
        f"{align},{int(40 * scale)},{int(40 * scale)},{margin_v},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


def ass_events(lines: Sequence[CaptionLine], spec: CaptionSpec, t0: float = 0.0, t1: Optional[float] = None) -> str:
    """Dialogue lines for the window [t0, t1) with times shifted so t0 becomes 0."""
    out = []
    for ln in lines:
        if ln.end <= t0 or (t1 is not None and ln.start >= t1):
            continue
        s = max(ln.start, t0)
        e = ln.end if t1 is None else min(ln.end, t1)
        if e - s < 0.02:
            continue
        if spec.style == "karaoke":
            parts = []
            words = ln.words
            prev_cum = 0
            for i, (text, ws, we) in enumerate(words):
                h_next = words[i + 1][1] if i + 1 < len(words) else we
                cum = int(round(max(0.0, h_next - s) * 100))
                k = max(0, cum - prev_cum)
                prev_cum = max(prev_cum, cum)
                parts.append(f"{{\\k{k}}}{_esc(text)}")
            body = " ".join(parts)
        else:
            body = _esc(ln.text)
        out.append(f"Dialogue: 0,{format_ass(s - t0)},{format_ass(e - t0)},Default,,0,0,0,,{body}")
    return "\n".join(out) + ("\n" if out else "")


def to_ass(lines: Sequence[CaptionLine], spec: CaptionSpec, width: int, height: int) -> str:
    """A complete ASS file (karaoke, plain or boxed)."""
    return ass_header(spec, width, height) + ass_events(lines, spec)
