"""Reads YAML into the shared node tree (values stay as text, so ``1:30`` is never turned into 90)."""

from __future__ import annotations

from typing import Optional

from ..core.errors import FeatureUnavailable, ScriptError
from .nodes import Map, Node, Scalar, Seq

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None


def parse_yaml_nodes(text: str) -> Optional[Node]:
    """Parse YAML text into nodes carrying line numbers."""
    if yaml is None:  # pragma: no cover
        raise FeatureUnavailable("Reading YAML scripts needs the 'PyYAML' package, which is not installed.",
                                 "Run:  pip install pyyaml   (or use the line or JSON script form).")
    text = text.replace("\t", "  ") if "\t" in text and not text.lstrip().startswith(("{", "[")) else text
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = (mark.line + 1) if mark is not None else None
        problem = getattr(exc, "problem", "") or str(exc)
        raise ScriptError(f"The YAML has a problem: {problem}.",
                          "Use spaces (not tabs) to indent, put a space after every colon, and put text with a colon in quotes.", line)
    return _convert(root) if root is not None else None


def _convert(node) -> Node:
    line = node.start_mark.line + 1
    if isinstance(node, yaml.ScalarNode):
        is_null = node.tag == "tag:yaml.org,2002:null" and node.style is None and node.value in ("", "~", "null", "Null", "NULL")
        return Scalar(None if is_null else node.value, line, node.style is not None)
    if isinstance(node, yaml.SequenceNode):
        return Seq([_convert(v) for v in node.value], line)
    if isinstance(node, yaml.MappingNode):
        pairs = []
        for k, v in node.value:
            key = _convert(k)
            if not isinstance(key, Scalar):
                raise ScriptError("A name in the YAML is not plain text.", "Use simple names like 'speed:'.", line)
            pairs.append((key, _convert(v)))
        return Map(pairs, line)
    raise ScriptError("Something in the YAML is not supported.", "Use plain text, lists and name: value pairs.", line)
