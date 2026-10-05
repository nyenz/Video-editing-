"""Error types. Every message is plain English and says how to fix the problem."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


class EditForgeError(Exception):
    """Base class for all errors that are shown to the user.

    Args:
        message: What went wrong, in plain English.
        fix: What the user can do about it.
        line: Line number in the user's script (1-based), if relevant.
        details: Technical details (for example the last FFmpeg lines).
    """

    def __init__(self, message: str, fix: str = "", line: Optional[int] = None, details: str = ""):
        super().__init__(message)
        self.message = message
        self.fix = fix
        self.line = line
        self.details = details

    def plain(self) -> str:
        """Return the message the way it is shown to a person."""
        head = f"Line {self.line}: " if self.line else ""
        text = f"{head}{self.message}"
        if self.fix:
            text += f"\n  How to fix: {self.fix}"
        return text

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.plain()


class ScriptError(EditForgeError):
    """The edit script could not be read or does not make sense."""


class MediaError(EditForgeError):
    """A media file is missing, unreadable or not supported."""


class MissingToolError(EditForgeError):
    """FFmpeg or ffprobe could not be found."""


class RenderError(EditForgeError):
    """FFmpeg failed while making the video."""


class FeatureUnavailable(EditForgeError):
    """An optional feature is switched off because its add-on is not installed."""


class Cancelled(EditForgeError):
    """The user cancelled the job."""

    def __init__(self, message: str = "The job was cancelled.", fix: str = "", line: Optional[int] = None):
        super().__init__(message, fix, line)


@dataclass
class Problem:
    """One finding from validation (an error, a warning or a note)."""

    level: str  # "error" | "warning" | "info"
    message: str
    fix: str = ""
    line: Optional[int] = None

    def to_dict(self) -> dict:
        return {"level": self.level, "message": self.message, "fix": self.fix, "line": self.line}

    def plain(self) -> str:
        head = f"Line {self.line}: " if self.line else ""
        text = f"{head}{self.message}"
        if self.fix:
            text += f"  How to fix: {self.fix}"
        return text
