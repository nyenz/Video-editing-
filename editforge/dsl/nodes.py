"""Tiny tree used by the YAML and JSON readers so both share one walker (with line numbers)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Union


@dataclass
class Scalar:
    value: Optional[str]
    line: int
    quoted: bool = False


@dataclass
class Seq:
    items: List["Node"]
    line: int


@dataclass
class Map:
    pairs: List[Tuple[Scalar, "Node"]]
    line: int


Node = Union[Scalar, Seq, Map]
