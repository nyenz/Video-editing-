"""Load a script from text or a file. Detects line / YAML / JSON automatically."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.errors import Problem, ScriptError
from .json_parser import parse_json_nodes
from .line_parser import parse_arg_string, parse_arg_tokens, parse_line_text, tokenize, parse_words
from .nodes import Map, Node, Scalar, Seq
from .registry import SETTINGS_NAMES, Spec, norm, resolve_instruction, suggest, suggest_instruction, canonical_names
from .coerce import build_step
from .script import Script, Step
from .yaml_parser import parse_yaml_nodes

STEP_KEYS = ("steps", "edits", "instructions", "script", "timeline", "actions", "edit", "operations")
NAME_KEYS = ("do", "op", "action", "step", "instruction", "command")
_YAML_KEY = re.compile(r"^\s*[A-Za-z_][\w\-]*\s*:(\s|$)")


def detect_format(text: str) -> str:
    """Guess whether text is JSON, YAML or the line language."""
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped[0] in "{[":
            return "json"
        if stripped.startswith("- ") or stripped == "-" or _YAML_KEY.match(raw):
            return "yaml"
        return "line"
    return "line"


def _add(script: Script, step: Step) -> None:
    if step.name in SETTINGS_NAMES:
        _apply_setting(script, step)
    else:
        script.steps.append(step)


def _apply_setting(script: Script, step: Step) -> None:
    a, s = step.args, script.settings
    if step.name == "input":
        s["input"] = a["file"]
    elif step.name == "output":
        s["output"] = a["file"]
    elif step.name == "preset":
        s["preset"] = a["name"].strip().lower().replace("-", "_").replace(" ", "_")
    elif step.name == "size":
        s["size"] = a["size"]
        s["fit"] = a.get("fit", "pad")
        script.setting_lines["fit"] = step.line
    elif step.name == "fps":
        s["fps"] = a["fps"]
    elif step.name == "quality":
        s["quality"] = a["level"]
    elif step.name == "fast_cuts":
        s["fast_cuts"] = bool(a["enabled"])
    script.setting_lines[step.name] = step.line


def _record(script: Script, exc: ScriptError, line: int) -> None:
    script.problems.append(Problem("error", exc.message, exc.fix, exc.line or line))


def parse_line_script(text: str) -> Script:
    """Parse the line language."""
    script = Script(format="line")
    for lineno, raw in enumerate(text.splitlines(), start=1):
        try:
            step = parse_line_text(raw, lineno)
        except ScriptError as exc:
            _record(script, exc, lineno)
            continue
        if step is not None:
            _add(script, step)
    return script


def _raw_of(node: Node, spec: Spec, key: str) -> Any:
    if isinstance(node, Scalar):
        return node.value
    if isinstance(node, Seq):
        return [_raw_of(i, spec, key) for i in node.items]
    raise ScriptError(f"The option '{key}' of '{spec.name}' cannot contain nested options.", "Use plain values or lists.", node.line)


def _step_from_named(name_node: Scalar, value: Optional[Node], extra: List, line: int) -> Step:
    word = name_node.value or ""
    spec, alias = resolve_instruction(word)
    if spec is None:
        close = suggest_instruction(word)
        hint = f" Did you mean '{close[0]}'?" if close else ""
        raise ScriptError(f"I don't know the instruction '{word}'.{hint}",
                          "Run 'editforge commands' to list every instruction, or see COMMANDS.md.", name_node.line)
    raw: Dict[str, Any] = {}
    if value is None or (isinstance(value, Scalar) and value.value is None):
        pass
    elif isinstance(value, Scalar):
        raw = parse_arg_string(spec, alias, value.value or "", value.line)
    elif isinstance(value, Map):
        for k, v in value.pairs:
            raw[norm(k.value or "")] = _raw_of(v, spec, k.value or "")
    elif isinstance(value, Seq):
        parts = [_raw_of(i, spec, spec.name) for i in value.items]
        if spec.positional and len(spec.positional) >= 1:
            p0 = spec.param_map()[spec.positional[0]]
            raw[p0.name] = parts if p0.kind == "ranges" else (parts[0] if len(parts) == 1 else parts)
    for k, v in extra:
        raw[norm(k.value or "")] = _raw_of(v, spec, k.value or "")
    return build_step(spec, alias, raw, name_node.line, word)


def _step_from_item(item: Node) -> Optional[Step]:
    if isinstance(item, Scalar):
        if item.value is None or not item.value.strip():
            return None
        return parse_line_text(item.value, item.line)
    if isinstance(item, Map):
        if not item.pairs:
            return None
        name_pairs = [(k, v) for k, v in item.pairs if norm(k.value or "") in NAME_KEYS]
        if name_pairs:
            key, val = name_pairs[0]
            if not isinstance(val, Scalar):
                raise ScriptError("The instruction name must be plain text.", "Example:  - do: speed", item.line)
            rest = [(k, v) for k, v in item.pairs if k is not key]
            return _step_from_named(val, None, rest, item.line)
        if len(item.pairs) != 1:
            names = ", ".join(repr(k.value) for k, _ in item.pairs[:4])
            raise ScriptError(f"Each step must be one instruction, but this one has several names ({names}).",
                              "Write one instruction per '-' line, like:  - speed: {factor: 2}", item.line)
        key, val = item.pairs[0]
        return _step_from_named(key, val, [], item.line)
    raise ScriptError("Each step should be an instruction (for example  speed: 2  or  {speed: {factor: 2}}).",
                      "Use one instruction per list item.", item.line)


def script_from_nodes(root: Optional[Node], fmt: str) -> Script:
    """Build a Script from YAML/JSON nodes."""
    script = Script(format=fmt)
    if root is None:
        return script
    items: List[Node] = []
    if isinstance(root, Seq):
        items = list(root.items)
    elif isinstance(root, Map):
        for key, val in root.pairs:
            name = norm(key.value or "")
            if name in STEP_KEYS:
                if isinstance(val, Seq):
                    items.extend(val.items)
                elif isinstance(val, Scalar) and val.value is None:
                    continue
                elif isinstance(val, Map):
                    items.extend(Map([p], val.line) for p in val.pairs)
                else:
                    script.problems.append(Problem("error", f"'{key.value}' should be a list of instructions.",
                                                   "Write one instruction per '-' line.", key.line))
                continue
            spec, alias = resolve_instruction(name)
            if spec is not None and spec.name in SETTINGS_NAMES:
                try:
                    _add(script, _step_from_named(key, val, [], key.line))
                except ScriptError as exc:
                    _record(script, exc, key.line)
                continue
            close = suggest(name, list(SETTINGS_NAMES) + list(STEP_KEYS))
            hint = f" Did you mean '{close[0]}'?" if close else ""
            script.problems.append(Problem("error", f"I don't know the top-level name '{key.value}'.{hint}",
                                           "Use input, output, preset, size, fps, quality, fast_cuts or steps.", key.line))
    else:
        script.problems.append(Problem("error", "The script should be a list of steps or have a 'steps:' list.",
                                       "Example:  steps:\n  - keep: 0-10", root.line))
        return script
    for item in items:
        try:
            step = _step_from_item(item)
        except ScriptError as exc:
            _record(script, exc, getattr(item, "line", 0))
            continue
        if step is not None:
            _add(script, step)
    return script


def load_script(text: str, fmt: Optional[str] = None) -> Script:
    """Parse script text (format auto-detected unless ``fmt`` is given).

    Problems do not raise; they are collected in ``script.problems`` so every mistake
    can be shown at once. Call ``script.raise_if_errors()`` to stop on errors.
    """
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    fmt = fmt or detect_format(text)
    if fmt == "line":
        return parse_line_script(text)
    try:
        root = parse_json_nodes(text) if fmt == "json" else parse_yaml_nodes(text)
    except ScriptError as exc:
        script = Script(format=fmt)
        _record(script, exc, exc.line or 0)
        return script
    return script_from_nodes(root, fmt)


def load_script_file(path: str) -> Script:
    """Load a script from a file (UTF-8; the extension picks the language when it is clear)."""
    p = Path(path)
    if not p.exists():
        raise ScriptError(f"I can't find the script file '{path}'.", "Check the name and folder.")
    try:
        text = p.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        raise ScriptError(f"'{path}' is not a text file I can read.", "Save it as plain text (UTF-8).")
    ext = p.suffix.lower()
    fmt = {".json": "json", ".yaml": "yaml", ".yml": "yaml"}.get(ext)
    return load_script(text, fmt)
