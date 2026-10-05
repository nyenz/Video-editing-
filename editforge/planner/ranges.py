"""Sets of time (or frame) ranges: add, remove, intersect."""

from __future__ import annotations

from typing import Iterable, List, Tuple

Range = Tuple[float, float]


class IntervalSet:
    """A set of half-open ranges [a, b) kept sorted and merged."""

    def __init__(self, ranges: Iterable[Range] = ()):
        self.ranges: List[Range] = []
        for a, b in ranges:
            self.add(a, b)

    def add(self, a: float, b: float) -> None:
        if b <= a:
            return
        merged: List[Range] = []
        placed = False
        for x, y in self.ranges:
            if y < a:
                merged.append((x, y))
            elif x > b:
                if not placed:
                    merged.append((a, b))
                    placed = True
                merged.append((x, y))
            else:
                a, b = min(a, x), max(b, y)
        if not placed:
            merged.append((a, b))
        self.ranges = sorted(merged)

    def subtract(self, other: "IntervalSet") -> "IntervalSet":
        out: List[Range] = []
        cuts = other.ranges
        for a, b in self.ranges:
            cur = a
            for c, d in cuts:
                if d <= cur:
                    continue
                if c >= b:
                    break
                if c > cur:
                    out.append((cur, c))
                cur = max(cur, d)
                if cur >= b:
                    break
            if cur < b:
                out.append((cur, b))
        return IntervalSet(out)

    def intersect(self, other: "IntervalSet") -> "IntervalSet":
        out: List[Range] = []
        i = j = 0
        while i < len(self.ranges) and j < len(other.ranges):
            a1, b1 = self.ranges[i]
            a2, b2 = other.ranges[j]
            lo, hi = max(a1, a2), min(b1, b2)
            if hi > lo:
                out.append((lo, hi))
            if b1 < b2:
                i += 1
            else:
                j += 1
        return IntervalSet(out)

    def total(self) -> float:
        return sum(b - a for a, b in self.ranges)

    def __iter__(self):
        return iter(self.ranges)

    def __len__(self) -> int:
        return len(self.ranges)

    def __bool__(self) -> bool:
        return bool(self.ranges)

    def contains(self, t: float) -> bool:
        return any(a <= t < b for a, b in self.ranges)


def union_into(base: "IntervalSet", other: "IntervalSet") -> "IntervalSet":
    """Return a new set holding everything in ``base`` and ``other``."""
    out = IntervalSet(base.ranges)
    for a, b in other.ranges:
        out.add(a, b)
    return out
