"""The internal form of a script: settings plus an ordered list of steps."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..core.errors import Problem, ScriptError
from ..core.fingerprint import stable_hash


@dataclass
class Step:
    """One instruction with fully checked, canonical arguments."""

    name: str
    args: Dict[str, Any]
    line: int = 0
    written_as: str = ""

    def canonical(self) -> Dict[str, Any]:
        return {"n": self.name, "a": self.args}

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "args": self.args, "line": self.line}


@dataclass
class Script:
    """A parsed script. All three languages produce exactly this."""

    settings: Dict[str, Any] = field(default_factory=dict)
    steps: List[Step] = field(default_factory=list)
    format: str = "line"
    problems: List[Problem] = field(default_factory=list)
    setting_lines: Dict[str, int] = field(default_factory=dict)

    @property
    def errors(self) -> List[Problem]:
        return [p for p in self.problems if p.level == "error"]

    @property
    def warnings(self) -> List[Problem]:
        return [p for p in self.problems if p.level == "warning"]

    def canonical(self) -> Dict[str, Any]:
        """The normalised script: same for every language and every way of writing a time.

        ``input`` and ``output`` file names are left out because they do not change
        what the edit does (the input file is covered by its content fingerprint).
        """
        settings = {k: v for k, v in sorted(self.settings.items()) if k not in ("input", "output")}
        return {"settings": settings, "steps": [s.canonical() for s in self.steps]}

    def digest(self) -> str:
        return stable_hash(self.canonical())

    def steps_named(self, *names: str) -> List[Step]:
        return [s for s in self.steps if s.name in names]

    def raise_if_errors(self) -> None:
        """Raise one ScriptError listing every problem found."""
        errs = self.errors
        if not errs:
            return
        first = errs[0]
        text = "\n".join(e.plain() for e in errs)
        raise ScriptError(text if len(errs) > 1 else first.message, first.fix if len(errs) == 1 else "",
                          first.line if len(errs) == 1 else None)

    def to_dict(self) -> Dict[str, Any]:
        return {"format": self.format, "settings": self.settings, "steps": [s.to_dict() for s in self.steps],
                "problems": [p.to_dict() for p in self.problems]}
