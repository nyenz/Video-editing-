"""Turning raw script words into checked, canonical values."""

from __future__ import annotations

import math
import re
from fractions import Fraction
from typing import Any, Dict, List, Optional, Tuple

from ..core.errors import ScriptError
from ..core.timecode import TIME_HELP, parse_range, parse_time_value
from .registry import Param, Spec, norm, suggest
from .script import Step

TRUE_WORDS = {"true", "yes", "on", "1", "y", "enable", "enabled", "yep"}
FALSE_WORDS = {"false", "no", "off", "0", "n", "disable", "disabled", "nope"}

COLOR_NAMES = {
    "white": "#ffffff", "black": "#000000", "red": "#ff0000", "green": "#00c000", "blue": "#0000ff",
    "yellow": "#ffff00", "orange": "#ffa500", "purple": "#800080", "pink": "#ffc0cb", "cyan": "#00ffff",
    "magenta": "#ff00ff", "gray": "#808080", "grey": "#808080", "silver": "#c0c0c0", "gold": "#ffd700",
    "brown": "#a52a2a", "navy": "#000080", "teal": "#008080", "lime": "#00ff00", "maroon": "#800000",
    "olive": "#808000", "coral": "#ff7f50", "beige": "#f5f5dc", "ivory": "#fffff0", "salmon": "#fa8072",
    "violet": "#ee82ee", "turquoise": "#40e0d0", "crimson": "#dc143c", "lightgray": "#d3d3d3", "darkgray": "#404040",
}
QUALITY_NAMES = {"low": 28, "draft": 30, "fast": 28, "medium": 23, "normal": 23, "default": 23, "good": 21, "high": 20,
                 "best": 17, "max": 16, "maximum": 16, "veryhigh": 18}
SIZE_NAMES = {"4k": "3840x2160", "uhd": "3840x2160", "2160p": "3840x2160", "1440p": "2560x1440", "1080p": "1920x1080",
              "fullhd": "1920x1080", "fhd": "1920x1080", "hd": "1280x720", "720p": "1280x720", "480p": "854x480",
              "360p": "640x360", "vertical": "1080x1920", "square": "1080x1080"}
ASPECT_NAMES = {"vertical": "9:16", "portrait": "4:5", "square": "1:1", "landscape": "16:9", "wide": "16:9",
                "widescreen": "16:9", "story": "9:16", "shorts": "9:16", "tiktok": "9:16", "reels": "9:16",
                "instagram": "4:5", "cinema": "21:9", "classic": "4:3"}


def _err(spec: Spec, param: Param, message: str, fix: str = "", line: Optional[int] = None) -> ScriptError:
    return ScriptError(f"In '{spec.name}', the option '{param.name}': {message}", fix, line)


def to_bool(raw: Any) -> Optional[bool]:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw) if raw in (0, 1) else None
    if isinstance(raw, str):
        w = raw.strip().lower()
        if w in TRUE_WORDS:
            return True
        if w in FALSE_WORDS:
            return False
    return None


def is_boolish(raw: str) -> bool:
    return to_bool(raw) is not None and str(raw).strip().lower() not in ("0", "1")


def _to_float(raw: Any) -> Optional[float]:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw) if math.isfinite(raw) else None
    if isinstance(raw, str):
        text = raw.strip().replace("\u2212", "-")
        try:
            value = float(text)
        except ValueError:
            return None
        return value if math.isfinite(value) else None
    return None


def _check_bounds(spec: Spec, param: Param, value: float, line: Optional[int]) -> float:
    if param.lo is not None and value < param.lo or param.hi is not None and value > param.hi:
        lo = param.lo if param.lo is not None else "any"
        hi = param.hi if param.hi is not None else "any"
        raise _err(spec, param, f"{value:g} is outside the allowed range ({lo} to {hi}).",
                   f"Use a value between {lo} and {hi}.", line)
    return value


def _single(raw: Any, spec: Spec, param: Param, line: Optional[int]) -> Any:
    if isinstance(raw, (list, tuple)):
        if len(raw) == 1:
            return raw[0]
        raise _err(spec, param, "expects a single value but got a list.", "Write just one value.", line)
    return raw


def coerce_value(spec: Spec, param: Param, raw: Any, line: Optional[int] = None) -> Any:
    """Check ``raw`` against the parameter type and return the canonical value."""
    kind = param.kind
    if kind == "ranges":
        return coerce_ranges(spec, param, raw, line)
    if kind == "intlist":
        return coerce_intlist(spec, param, raw, line)
    if kind == "box":
        return coerce_box(spec, param, raw, line)
    raw = _single(raw, spec, param, line)
    if raw is None:
        raise _err(spec, param, "has no value.", "Write a value after it.", line)
    try:
        if kind in ("time", "dur"):
            return parse_time_value(raw).canon
    except ScriptError as exc:
        raise _err(spec, param, exc.message, exc.fix, line)
    if kind in ("num", "factor", "db", "gain"):
        return _coerce_number(spec, param, raw, line)
    if kind == "int":
        f = _to_float(raw)
        if f is None or abs(f - round(f)) > 1e-9:
            raise _err(spec, param, f"I can't read '{raw}' as a whole number.", "Use a whole number such as 2 or 4.", line)
        return int(_check_bounds(spec, param, round(f), line))
    if kind == "bool":
        b = to_bool(raw)
        if b is None:
            raise _err(spec, param, f"I can't read '{raw}' as yes/no.", "Use true or false (or on / off).", line)
        return b
    if kind == "enum":
        return _coerce_enum(spec, param, raw, line)
    if kind == "pos":
        return _coerce_pos(spec, param, raw, line)
    if kind == "color":
        return coerce_color(spec, param, raw, line)
    if kind == "size":
        return _coerce_size(spec, param, raw, line)
    if kind == "aspect":
        return _coerce_aspect(spec, param, raw, line)
    if kind == "quality":
        return _coerce_quality(spec, param, raw, line)
    if kind in ("file", "str"):
        text = str(raw).strip()
        if not text:
            raise _err(spec, param, "is empty.", "Write a value (put it in quotes if it has spaces).", line)
        return text
    raise AssertionError(f"unknown parameter kind {kind}")  # pragma: no cover


def _coerce_number(spec: Spec, param: Param, raw: Any, line: Optional[int]) -> float:
    kind = param.kind
    if isinstance(raw, str):
        text = raw.strip().lower().replace("\u2212", "-")
        if kind in ("factor", "gain") and text.endswith("%"):
            v = _to_float(text[:-1])
            if v is not None:
                return _check_bounds(spec, param, round(v / 100.0, 6), line) if kind == "factor" else max(0.0, v / 100.0)
        if kind in ("db", "gain") and text.endswith("db"):
            v = _to_float(text[:-2].strip())
            if v is not None:
                return round(v, 4) if kind == "db" and _check_bounds(spec, param, v, line) is not None else round(10 ** (v / 20.0), 6)
        if kind == "factor" and text.endswith("x"):
            raw = text[:-1]
    f = _to_float(raw)
    if f is None:
        hint = {"db": "a number of decibels such as -35 or -35dB", "gain": "a number like 0.5, -6dB or 150%",
                "factor": "a number like 2, 0.5, 2x or 200%"}.get(kind, "a number such as 1.5")
        raise _err(spec, param, f"I can't read '{raw}' as a number.", f"Use {hint}.", line)
    if kind == "gain":
        return max(0.0, round(f, 6))
    return round(_check_bounds(spec, param, f, line), 6)


def _coerce_enum(spec: Spec, param: Param, raw: Any, line: Optional[int]) -> str:
    word = norm(str(raw))
    if word in param.choices:
        return word
    if word in param.enum_alias:
        return param.enum_alias[word]
    options = list(param.choices) + list(param.enum_alias)
    close = [param.enum_alias.get(m, m) for m in suggest(word, options)]
    close = list(dict.fromkeys(close))
    hint = f" Did you mean '{close[0]}'?" if close else ""
    raise _err(spec, param, f"'{raw}' is not one of the choices.{hint}", "Choose one of: " + ", ".join(param.choices) + ".", line)


def _coerce_pos(spec: Spec, param: Param, raw: Any, line: Optional[int]) -> str:
    text = str(raw).strip()
    if re.fullmatch(r"\s*-?[\d.]+%?\s*,\s*-?[\d.]+%?\s*", text):
        return re.sub(r"\s+", "", text)
    return _coerce_enum(spec, param, raw, line)


def coerce_color(spec: Spec, param: Param, raw: Any, line: Optional[int]) -> str:
    text = str(raw).strip().lower()
    alpha = None
    if "@" in text:
        text, _, a = text.partition("@")
        alpha = _to_float(a)
        if alpha is None or not 0 <= alpha <= 1:
            raise _err(spec, param, f"'{raw}' has an opacity after @ that is not between 0 and 1.", "Example: black@0.5", line)
    if text in COLOR_NAMES:
        text = COLOR_NAMES[text]
    m = re.fullmatch(r"(?:#|0x)([0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})", text)
    if not m:
        close = suggest(text, list(COLOR_NAMES))
        hint = f" Did you mean '{close[0]}'?" if close else ""
        raise _err(spec, param, f"I don't know the colour '{raw}'.{hint}",
                   "Use a name (white, black, red ...) or a hex code such as #ff8800.", line)
    digits = m.group(1)
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    if len(digits) == 8:
        alpha = round(int(digits[6:], 16) / 255.0, 3) if alpha is None else alpha
        digits = digits[:6]
    out = "#" + digits
    if alpha is not None and alpha < 1:
        out += f"@{alpha:g}"
    return out


def color_to_rgba(canon: str) -> Tuple[int, int, int, float]:
    """Split '#rrggbb@0.5' into (r, g, b, alpha)."""
    text, _, a = canon.partition("@")
    alpha = float(a) if a else 1.0
    return int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16), alpha


def _coerce_size(spec: Spec, param: Param, raw: Any, line: Optional[int]) -> str:
    text = str(raw).strip().lower().replace(" ", "")
    text = SIZE_NAMES.get(text, text)
    m = re.fullmatch(r"(\d{2,5})[x*:\u00d7](\d{2,5})", text)
    if not m:
        raise _err(spec, param, f"I can't read '{raw}' as a picture size.",
                   "Write width x height, for example 1920x1080, or a name such as 1080p, 720p, 4k.", line)
    w, h = int(m.group(1)), int(m.group(2))
    if not (16 <= w <= 8192 and 16 <= h <= 8192):
        raise _err(spec, param, f"{w}x{h} is outside the supported range (16 to 8192).", "Choose a size between 16 and 8192.", line)
    return f"{w - w % 2}x{h - h % 2}"


def _coerce_aspect(spec: Spec, param: Param, raw: Any, line: Optional[int]) -> str:
    text = str(raw).strip().lower()
    text = ASPECT_NAMES.get(text, text)
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*[:/x]\s*(\d+(?:\.\d+)?)", text)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
    else:
        f = _to_float(text)
        if f is None or f <= 0:
            raise _err(spec, param, f"I can't read '{raw}' as an aspect ratio.",
                       "Write it like 9:16, 1:1, 4:5, 16:9 or use a name such as vertical or square.", line)
        a, b = f, 1.0
    if a <= 0 or b <= 0:
        raise _err(spec, param, f"'{raw}' is not a valid aspect ratio.", "Use positive numbers, like 9:16.", line)
    fr = Fraction(a / b).limit_denominator(100)
    return f"{fr.numerator}:{fr.denominator}"


def _coerce_quality(spec: Spec, param: Param, raw: Any, line: Optional[int]) -> str:
    text = norm(str(raw))
    if text in QUALITY_NAMES:
        return f"crf:{QUALITY_NAMES[text]}"
    f = _to_float(raw)
    if f is not None and 0 <= f <= 51:
        return f"crf:{int(round(f))}"
    raise _err(spec, param, f"'{raw}' is not a quality level.", "Use low, medium, high, max, or a number from 0 (best) to 51 (worst).", line)


def coerce_box(spec: Spec, param: Param, raw: Any, line: Optional[int]) -> List[str]:
    parts = raw if isinstance(raw, (list, tuple)) else re.split(r"[,\s]+", str(raw).strip())
    parts = [str(p).strip() for p in parts if str(p).strip()]
    if len(parts) != 4 or not all(re.fullmatch(r"\d+(?:\.\d+)?%?", p) for p in parts):
        raise _err(spec, param, f"I can't read '{raw}' as a box.", "Write four numbers: x,y,width,height (pixels, or with % signs).", line)
    return parts


def coerce_intlist(spec: Spec, param: Param, raw: Any, line: Optional[int]) -> List[int]:
    items = raw if isinstance(raw, (list, tuple)) else re.split(r"[,;\s]+", str(raw).strip())
    out: List[int] = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        m = re.fullmatch(r"(\d+)\s*(?:-|\.\.|to)\s*(\d+)", text)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if b < a or b - a > 10000:
                raise _err(spec, param, f"'{text}' is not a sensible range of numbers.", "Write it like 3-5.", line)
            out.extend(range(a, b + 1))
        elif text.isdigit():
            out.append(int(text))
        else:
            raise _err(spec, param, f"I can't read '{text}' as a list of numbers.", "Write numbers like 1,3-5.", line)
    if not out:
        raise _err(spec, param, "has no numbers.", "Write numbers like 1,3-5.", line)
    return sorted(set(out))


def _split_ranges(text: str) -> List[str]:
    """Split ``10-20, 30-40`` into pieces, keeping ``00:00:01,500-00:00:02,500`` whole."""
    text = text.strip()
    if not text:
        return []
    try:
        parse_range(text)
        return [text]
    except ScriptError:
        pass
    pieces = [p.strip() for p in re.split(r"\s*[;,]\s*", text) if p.strip()]
    return pieces


def coerce_ranges(spec: Spec, param: Param, raw: Any, line: Optional[int]) -> List[List[Optional[str]]]:
    """Canonical list of [start, end] (end may be None = until the end)."""
    items: List[Any]
    if isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        items = _split_ranges(str(raw))
    out: List[List[Optional[str]]] = []
    for item in items:
        try:
            if isinstance(item, (list, tuple)):
                if len(item) != 2:
                    raise ScriptError(f"A range needs two times, but I found {len(item)}.", "Write [start, end].")
                a, b = parse_time_value(item[0]), parse_time_value(item[1]) if item[1] not in (None, "") else None
            else:
                text = str(item).strip()
                try:
                    a, b = parse_range(text)
                except ScriptError:
                    a, b = parse_time_value(text), None  # a single time: open ended
            out.append([a.canon, b.canon if b else None])
        except ScriptError as exc:
            raise _err(spec, param, exc.message, exc.fix or ("Write a range like 10-20 or 0:10-0:20. " + TIME_HELP), line)
    return out


def post_process(spec: Spec, args: Dict[str, Any], line: int) -> None:
    """Merge start/end into ranges and check pairs that must go together."""
    if "ranges" in {p.name for p in spec.params}:
        ranges = list(args.pop("ranges", None) or [])
        s, e = args.pop("start", None), args.pop("end", None)
        if s is not None or e is not None:
            if len(ranges) == 1 and ranges[0][1] is None and e is not None and s is None:
                ranges = [[ranges[0][0], e]]
            elif not ranges:
                ranges = [[s or "0", e]]
            else:
                ranges.append([s or "0", e])
        if not ranges:
            raise ScriptError(f"'{spec.name}' needs a time range.", f"Example:  {spec.example}", line)
        args["ranges"] = ranges
    if "end" in args and "duration" in args and args.get("end") is not None and args.get("duration") is not None \
            and spec.name in ("text", "image"):
        raise ScriptError(f"In '{spec.name}' give either 'end' or 'duration', not both.", "Remove one of them.", line)
    if spec.name in ("speed", "reverse", "zoom", "pan", "crop", "reframe", "color", "effect", "mute", "volume",
                     "speed_ramp", "scene_cut", "silence_remove", "pattern_keep", "pattern_remove", "beat_cut"):
        _check_order(spec, args, line)
    if spec.name in ("keep", "cut"):
        for a, b in args["ranges"]:
            if b is not None:
                _check_pair(spec, a, b, line)
    if spec.name == "speed_ramp" and (args.get("start") is None or args.get("end") is None):
        raise ScriptError("'speed_ramp' needs both a start and an end time.", "Example:  speed_ramp 1 to 3 from 5 to 8", line)
    if spec.name == "crop" and not args.get("aspect") and not args.get("box"):
        raise ScriptError("'crop' needs an aspect ratio or a box.", "Examples:  crop 9:16   or   crop box=100,50,640,360", line)
    if spec.name == "color":
        defaults = {"brightness": 0.0, "contrast": 1.0, "saturation": 1.0, "gamma": 1.0, "hue": 0.0}
        if all(abs(args.get(k, v) - v) < 1e-9 for k, v in defaults.items()):
            raise ScriptError("'color' does not change anything yet.", "Set at least one of brightness, contrast, saturation, gamma, hue.", line)
    if spec.name == "fade" and not (_secs(args.get("fade_in")) or _secs(args.get("fade_out"))):
        raise ScriptError("'fade' needs a fade-in or fade-out length.", "Example:  fade in=1 out=2", line)
    if spec.name == "text" and not args.get("text", "").strip():
        raise ScriptError("'text' has no words to show.", 'Example:  text "Hello" start=1 end=4', line)


def _secs(canon: Optional[str]) -> float:
    if canon is None:
        return 0.0
    tv = parse_time_value(canon)
    return tv.seconds or 0.0 if tv.frame is None else 1.0


def _check_pair(spec: Spec, a: str, b: str, line: int) -> None:
    ta, tb = parse_time_value(a), parse_time_value(b)
    if ta.frame is None and tb.frame is None and not ta.extra_frames and not tb.extra_frames:
        if (ta.seconds or 0) >= (tb.seconds or 0):
            raise ScriptError(f"In '{spec.name}' the start ({a}) must be before the end ({b}).", "Swap the two times.", line)


def _check_order(spec: Spec, args: Dict[str, Any], line: int) -> None:
    if args.get("start") is not None and args.get("end") is not None:
        _check_pair(spec, args["start"], args["end"], line)


def build_step(spec: Spec, alias_used: Optional[str], raw_args: Dict[str, Any], line: int, written: str = "") -> Step:
    """Check raw arguments and return a canonical :class:`Step`.

    Raises:
        ScriptError: (with the line number) for any problem.
    """
    params = spec.param_map()
    merged: Dict[str, Any] = {}
    if alias_used and alias_used in spec.presets:
        for k, v in spec.presets[alias_used].items():
            if not k.startswith("_"):
                merged[k] = v
    merged.update(raw_args)
    args: Dict[str, Any] = {}
    for key, raw in merged.items():
        param = params.get(norm(key))
        if param is None:
            close = suggest(key, list(params))
            hint = f" Did you mean '{close[0]}'?" if close else ""
            names = ", ".join(sorted({p.name for p in spec.params}))
            raise ScriptError(f"'{spec.name}' has no option called '{key}'.{hint}", f"Options for {spec.name}: {names}.", line)
        try:
            args[param.name] = coerce_value(spec, param, raw, line)
        except ScriptError as exc:
            raise ScriptError(exc.message, exc.fix, exc.line or line)
    for p in spec.params:
        if p.name not in args:
            if p.required:
                raise ScriptError(f"'{spec.name}' needs '{p.name}'.", f"Example:  {spec.example}", line)
            if p.default is not None:
                try:
                    args[p.name] = coerce_value(spec, p, p.default, line) if isinstance(p.default, str) else p.default
                except ScriptError:
                    args[p.name] = p.default
    post_process(spec, args, line)
    return Step(spec.name, args, line, written)
