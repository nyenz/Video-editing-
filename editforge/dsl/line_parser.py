"""The line-based script language.

One instruction per line::

    keep 0:10-0:40, 1:00-1:30
    speed 2 from 10 to 20
    text "Hello world" start=00:00:01 end=00:00:04 position=bottom
    # anything after a '#' at the start of a word is a comment
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from ..core.errors import Problem, ScriptError
from ..core.timecode import looks_like_time
from .coerce import build_step, is_boolish
from .registry import Spec, canonical_names, norm, resolve_instruction, suggest, suggest_instruction
from .script import Step

OPEN_QUOTES = {'"': '"', "'": "'", "\u201c": "\u201d", "\u2018": "\u2019", "\u00ab": "\u00bb"}
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]*$")
_GREEDY_WORDS = {"from", "to", "until", "at", "for", "start", "end", "duration", "in", "out"}
_SEPARATORS = {"-", "..", "\u2013", "\u2014", "->", "\u2192", "to", "until"}


@dataclass
class Tok:
    """One word of a script line."""

    key: Optional[str]
    value: str
    quoted: bool = False


def tokenize(text: str, lineno: int = 0) -> List[Tok]:
    """Split a line into words. Quotes keep spaces together; ``key=value`` is recognised."""
    toks: List[Tok] = []
    i, n = 0, len(text)
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break
        if text[i] == "#" and (i == 0 or i + 1 >= n or text[i + 1].isspace() or not text[:i].strip()):
            break  # comment
        buf: List[str] = []
        key: Optional[str] = None
        quoted = False
        seen_quote = False
        while i < n and not text[i].isspace():
            ch = text[i]
            if ch in OPEN_QUOTES:
                close = OPEN_QUOTES[ch]
                seen_quote = True
                if not buf:
                    quoted = True
                i += 1
                while i < n and text[i] != close:
                    if text[i] == "\\" and i + 1 < n and text[i + 1] in (close, "\\", "n"):
                        buf.append("\n" if text[i + 1] == "n" else text[i + 1])
                        i += 2
                        continue
                    buf.append(text[i])
                    i += 1
                if i >= n:
                    raise ScriptError(f"You opened a quote ({ch}) but never closed it.",
                                      f"Add the closing {close} at the end of the text.", lineno)
                i += 1
                continue
            if ch == "=" and key is None and not seen_quote and _KEY_RE.match("".join(buf)):
                key = "".join(buf)
                buf = []
                quoted = False
                i += 1
                continue
            buf.append(ch)
            i += 1
        toks.append(Tok(key, "".join(buf), quoted))
    return toks


def _unknown_instruction(word: str, lineno: int) -> ScriptError:
    close = suggest_instruction(word)
    hint = f" Did you mean '{close[0]}'?" if close else ""
    return ScriptError(f"I don't know the instruction '{word}'.{hint}",
                       "Run 'editforge commands' to list every instruction, or see COMMANDS.md.", lineno)


def _merge_range_separators(values: List[str]) -> List[str]:
    out: List[str] = []
    i = 0
    while i < len(values):
        if i + 2 < len(values) + 0 and i >= 0 and values[i + 1].lower() in _SEPARATORS and i + 2 < len(values):
            out.append(f"{values[i]}..{values[i + 2]}")
            i += 3
        else:
            out.append(values[i])
            i += 1
    return out


def _speed_ramp_hook(toks: List[Tok]) -> List[Tok]:
    """Allow ``speed_ramp 1 to 3 from 5 to 8`` (the first 'to' means 'to speed 3')."""
    if len(toks) >= 3 and not toks[0].key and not toks[2].key and toks[1].value.lower() in ("to", "->", "\u2192", "..") \
            and re.fullmatch(r"[\d.]+x?", toks[0].value) and re.fullmatch(r"[\d.]+x?", toks[2].value):
        return [Tok(None, toks[0].value), Tok(None, toks[2].value)] + toks[3:]
    return toks


_FROM_WORDS = {"from", "start", "begin", "since", "in", "at", "after"}


def _merge_to_ranges(toks: List[Tok]) -> List[Tok]:
    """``keep 70 to 80`` -> one range token, but leave ``from 70 to 80`` alone."""
    out: List[Tok] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        prev = out[-1].value.lower() if out and out[-1].key is None else ""
        if (i + 2 < len(toks) and toks[i].key is None and toks[i + 1].key is None and toks[i + 2].key is None
                and toks[i + 1].value.lower() in ("to", "until", "->", "\u2192") and prev not in _FROM_WORDS
                and looks_like_time(t.value.strip(",")) and looks_like_time(toks[i + 2].value.strip(","))):
            out.append(Tok(None, f"{t.value.strip(',')}..{toks[i + 2].value}"))
            i += 3
            continue
        out.append(t)
        i += 1
    return out


def parse_arg_tokens(spec: Spec, alias: Optional[str], toks: List[Tok], lineno: int) -> Dict[str, Any]:
    """Turn the words after an instruction name into a raw ``{option: text}`` dictionary."""
    if spec.name == "speed_ramp":
        toks = _speed_ramp_hook(toks)
    if any(spec.param_map()[n].kind == "ranges" for n in spec.positional):
        toks = _merge_to_ranges(toks)
    params = spec.param_map()
    raw: Dict[str, Any] = {}
    positional: List[Tok] = []
    preset = spec.presets.get(alias or "", {}) if alias else {}

    def assign(word: str, value: Any) -> None:
        p = params.get(norm(word))
        if p is None:
            close = suggest(word, list(params))
            hint = f" Did you mean '{close[0]}'?" if close else ""
            raise ScriptError(f"'{spec.name}' has no option called '{word}'.{hint}",
                              "Options: " + ", ".join(sorted({q.name for q in spec.params})) + ".", lineno)
        if p.name in raw:
            raise ScriptError(f"You gave '{p.name}' twice in the same line.", "Keep only one of them.", lineno)
        raw[p.name] = value

    i = 0
    while i < len(toks):
        t = toks[i]
        if t.key is not None:
            assign(t.key, t.value)
            i += 1
            continue
        word = norm(t.value)
        p = None if t.quoted else params.get(word)
        if p is not None and spec.greedy:
            nxt = toks[i + 1] if i + 1 < len(toks) else None
            if not (word in _GREEDY_WORDS and nxt is not None and not nxt.key and looks_like_time(nxt.value)):
                p = None
        if p is not None and p.kind == "bool":
            if i + 1 < len(toks) and not toks[i + 1].key and not toks[i + 1].quoted and is_boolish(toks[i + 1].value):
                assign(word, toks[i + 1].value)
                i += 2
            else:
                assign(word, "true")
                i += 1
            continue
        if p is not None:
            if i + 1 >= len(toks) or toks[i + 1].key is not None:
                raise ScriptError(f"'{t.value}' needs a value after it.", f"Example:  {spec.example}", lineno)
            assign(word, toks[i + 1].value)
            i += 2
            continue
        positional.append(t)
        i += 1

    # 'loudnorm off' / 'denoise on'
    if "enabled" in params and "enabled" not in raw:
        for t in list(positional):
            if not t.quoted and is_boolish(t.value):
                raw["enabled"] = t.value
                positional.remove(t)
                break

    names = list(preset.get("_positional", spec.positional))
    names = [n for n in names if n not in raw and n not in preset]
    if spec.greedy and positional:
        if spec.greedy in raw:
            raise ScriptError(f"'{spec.name}' got its {spec.greedy} twice.", f"Example:  {spec.example}", lineno)
        raw[spec.greedy] = " ".join(t.value for t in positional)
        positional = []
    else:
        for name in names:
            if not positional:
                break
            p = params[name]
            if p.kind == "ranges":
                values = _merge_range_separators([t.value.strip(",;") for t in positional])
                raw[name] = ", ".join(v for v in values if v)
                positional = []
            else:
                raw[name] = positional.pop(0).value
    if positional:
        raise ScriptError(f"I don't understand '{positional[0].value}' after '{spec.name}'.", f"Example:  {spec.example}", lineno)
    return raw


def parse_words(toks: List[Tok], lineno: int, written: str = "") -> Step:
    """Parse an already split line into a :class:`Step`."""
    if not toks:
        raise ScriptError("This line is empty.", "", lineno)
    first = toks[0]
    if first.key is not None:
        raise ScriptError(f"A line must start with an instruction name, not '{first.key}='.",
                          "Example:  speed 2 from 10 to 20", lineno)
    spec, alias = resolve_instruction(first.value)
    if spec is None:
        raise _unknown_instruction(first.value, lineno)
    raw = parse_arg_tokens(spec, alias, toks[1:], lineno)
    return build_step(spec, alias, raw, lineno, written)


def parse_line_text(text: str, lineno: int = 0) -> Optional[Step]:
    """Parse one line of the line language. Returns None for blank or comment lines."""
    toks = tokenize(text, lineno)
    if not toks:
        return None
    return parse_words(toks, lineno, text.strip())


def parse_arg_string(spec: Spec, alias: Optional[str], text: str, lineno: int) -> Dict[str, Any]:
    """Parse the argument text used in ``- speed: 2 from 10 to 20`` (YAML/JSON one-liners)."""
    text = text.strip()
    if len(spec.positional) == 1 and not re.search(r"(^|\s)[A-Za-z_][\w.\-]*=", text) and text:
        p = spec.param_map()[spec.positional[0]]
        if p.kind in ("file", "str") and not (text[:1] in OPEN_QUOTES):
            return {p.name: text}
    return parse_arg_tokens(spec, alias, tokenize(text, lineno), lineno)
