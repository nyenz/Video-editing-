"""Time parsing and formatting.

Every instruction accepts every one of these formats:

* seconds: ``12.5`` or ``12``
* seconds with a unit: ``12.5s``, ``250ms``, ``1.5m``, ``2h``, ``1h2m3.5s``
* ``MM:SS`` and ``MM:SS.mmm``
* ``HH:MM:SS``, ``HH:MM:SS.mmm`` (a comma also works: ``00:00:01,500``)
* ``HH:MM:SS:FF`` (last part is a frame number inside that second)
* frames: ``f120`` or ``120f`` (frame number; needs the frame rate)
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from fractions import Fraction
from typing import Optional, Union

from .errors import ScriptError

TIME_HELP = (
    "Use seconds (12.5), 12.5s, MM:SS (1:30), HH:MM:SS (00:01:30), "
    "HH:MM:SS.mmm (00:01:30.500) or a frame number (f120)."
)

_NUM = r"(?:\d+(?:\.\d+)?|\.\d+)"
_RE_PLAIN = re.compile(rf"^{_NUM}$")
_RE_UNITS = re.compile(rf"^(?:({_NUM})h)?\s*(?:({_NUM})m(?!s))?\s*(?:({_NUM})s(?:ec)?)?\s*(?:({_NUM})ms)?$")
_RE_MIN = re.compile(rf"^({_NUM})\s*(?:min|mins|minutes?)$")
_RE_FRAME = re.compile(r"^(?:f\s*(\d+)|(\d+)\s*f)$")
_RE_COLON = re.compile(r"^\d+(?::\d+){1,3}(?:[.,]\d+)?$")


@dataclass(frozen=True)
class TimeVal:
    """A time that may still need a frame rate to become seconds.

    Attributes:
        seconds: Whole time in seconds (None for a pure frame number).
        frame: Frame number (only for the ``f120`` form).
        extra_frames: Frames after the seconds (only for ``HH:MM:SS:FF``).
    """

    seconds: Optional[float] = None
    frame: Optional[int] = None
    extra_frames: int = 0

    @property
    def canon(self) -> str:
        """Canonical text, identical for every way of writing the same time."""
        if self.frame is not None:
            return f"f{self.frame}"
        text = f"{self.seconds:.6f}".rstrip("0").rstrip(".") if self.seconds is not None else "0"
        text = text or "0"
        if self.extra_frames:
            return f"{text}+{self.extra_frames}f"
        return text

    def to_seconds(self, fps: Union[Fraction, float, None] = None) -> float:
        """Convert to seconds. ``fps`` is required for frame-based forms."""
        if self.frame is not None:
            if not fps:
                raise ScriptError(
                    "A frame number (like f120) needs the video's frame rate, which is not known yet.",
                    "Give an input video, or write the time in seconds (for example 4.8).",
                )
            return round(self.frame / float(fps), 6)
        secs = self.seconds or 0.0
        if self.extra_frames:
            if not fps:
                raise ScriptError(
                    "A time with frames (HH:MM:SS:FF) needs the video's frame rate, which is not known yet.",
                    "Give an input video, or write the time as HH:MM:SS.mmm.",
                )
            secs += self.extra_frames / float(fps)
        return round(secs, 6)


def _bad(text: object) -> ScriptError:
    return ScriptError(f"I can't read '{text}' as a time.", TIME_HELP)


_RE_CANON = re.compile(r"^(\d+(?:\.\d+)?)\+(\d+)f$")


def parse_time_value(value: object) -> TimeVal:
    """Parse any supported time format into a :class:`TimeVal`.

    Raises:
        ScriptError: if the text is not a time or is negative.
    """
    if isinstance(value, TimeVal):
        return value
    if isinstance(value, bool):
        raise _bad(value)
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise _bad(value)
        if value < 0:
            raise ScriptError(f"A time can't be negative ({value}).", "Use a time of 0 or more.")
        return TimeVal(seconds=round(float(value), 6))
    if not isinstance(value, str):
        raise _bad(value)
    text = value.strip().lower()
    if not text:
        raise _bad(value)
    if text.startswith("-"):
        raise ScriptError(f"A time can't be negative ('{value}').", "Use a time of 0 or more.")
    m = _RE_CANON.match(text)
    if m:
        return TimeVal(seconds=round(float(m.group(1)), 6), extra_frames=int(m.group(2)))
    m = _RE_FRAME.match(text)
    if m:
        return TimeVal(frame=int(m.group(1) or m.group(2)))
    if _RE_PLAIN.match(text):
        return TimeVal(seconds=round(float(text), 6))
    if _RE_COLON.match(text):
        return _parse_colon(text, value)
    m = _RE_MIN.match(text)
    if m:
        return TimeVal(seconds=round(float(m.group(1)) * 60, 6))
    m = _RE_UNITS.match(text)
    if m and any(m.groups()):
        h, mi, s, ms = (float(g) if g else 0.0 for g in m.groups())
        return TimeVal(seconds=round(h * 3600 + mi * 60 + s + ms / 1000.0, 6))
    raise _bad(value)


def _parse_colon(text: str, original: object) -> TimeVal:
    text = text.replace(",", ".")
    frac = 0.0
    frames = 0
    main = text
    if "." in text:
        main, dec = text.split(".", 1)
        frac = float("0." + dec)
    nums = [int(p) for p in main.split(":")]
    if len(nums) == 2:
        mm, ss = nums
        if ss >= 60:
            raise ScriptError(f"'{original}' has {ss} seconds; the seconds part must be under 60.", TIME_HELP)
        secs = mm * 60 + ss + frac
    elif len(nums) == 3:
        hh, mm, ss = nums
        if mm >= 60 or ss >= 60:
            raise ScriptError(f"'{original}' has minutes or seconds of 60 or more.", TIME_HELP)
        secs = hh * 3600 + mm * 60 + ss + frac
    else:
        hh, mm, ss, frames = nums
        if mm >= 60 or ss >= 60 or frac:
            raise ScriptError(f"'{original}' is not a valid HH:MM:SS:FF time.", TIME_HELP)
        secs = hh * 3600 + mm * 60 + ss
    return TimeVal(seconds=round(secs, 6), extra_frames=frames)


def parse_time(value: object, fps: Union[Fraction, float, None] = None) -> float:
    """Parse a time in any supported format and return seconds."""
    return parse_time_value(value).to_seconds(fps)


def looks_like_time(text: str) -> bool:
    """Return True if ``text`` parses as a time."""
    try:
        parse_time_value(text)
        return True
    except ScriptError:
        return False


def format_time(seconds: float, millis: bool = True) -> str:
    """Format seconds as ``HH:MM:SS.mmm`` (or ``HH:MM:SS``)."""
    seconds = max(0.0, float(seconds))
    total_ms = int(round(seconds * 1000))
    h, rem = divmod(total_ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}" if millis else f"{h:02d}:{m:02d}:{s:02d}"


def format_srt(seconds: float) -> str:
    """Format seconds the way SRT files write them (comma before milliseconds)."""
    return format_time(seconds).replace(".", ",")


def format_ass(seconds: float) -> str:
    """Format seconds the way ASS files write them (H:MM:SS.cc)."""
    cs_total = int(round(max(0.0, seconds) * 100))
    h, rem = divmod(cs_total, 360_000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def parse_range(text: str) -> tuple:
    """Split ``A-B``, ``A..B``, ``A to B`` or ``A -> B`` into two :class:`TimeVal`.

    The second value may be None for a range with only a start (``10-``).
    """
    raw = text.strip()
    for sep in ("..", "->", "\u2192", "\u2013", "\u2014", " to ", " until "):
        if sep in raw:
            a, b = raw.split(sep, 1)
            return (parse_time_value(a.strip()), parse_time_value(b.strip()) if b.strip() else None)
    # Plain hyphen: try each hyphen as the separator until both sides are valid times.
    for i, ch in enumerate(raw):
        if ch == "-" and i > 0:
            left, right = raw[:i].strip(), raw[i + 1:].strip()
            if looks_like_time(left) and (not right or looks_like_time(right)):
                return (parse_time_value(left), parse_time_value(right) if right else None)
    raise ScriptError(
        f"I can't read '{text}' as a time range.",
        "Write a range like 10-20, 0:10-0:20, 00:00:10..00:00:20 or '10 to 20'.",
    )
