"""Finding the files an edit refers to (music, logos, transcripts, fonts) safely."""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional, Sequence

from ..core.errors import ScriptError


class FileResolver:
    """Resolves file names relative to a folder and (optionally) keeps them inside allowed folders."""

    def __init__(self, base_dir: Optional[str] = None, allowed_roots: Optional[Sequence[str]] = None):
        self.base_dir = Path(base_dir).resolve() if base_dir else Path.cwd()
        self.allowed = [Path(r).resolve() for r in allowed_roots] if allowed_roots else None

    def resolve(self, name: str, what: str = "file", line: Optional[int] = None) -> str:
        """Return the absolute path of ``name`` or raise a friendly error."""
        p = Path(os.path.expanduser(name))
        if not p.is_absolute():
            p = self.base_dir / p
        try:
            p = p.resolve()
        except OSError:
            raise ScriptError(f"The {what} name '{name}' is not valid.", "Check the spelling.", line)
        if self.allowed is not None and not any(_inside(p, root) for root in self.allowed):
            raise ScriptError(f"For safety, the {what} '{name}' is outside the folders the web page may use.",
                              "Upload the file in the web page, or put it in the EditForge media folder.", line)
        if not p.exists():
            raise ScriptError(f"I can't find the {what} '{name}'.",
                              f"Check the spelling and folder (I looked in {p.parent}).", line)
        if p.is_dir():
            raise ScriptError(f"The {what} '{name}' is a folder, not a file.", "Choose the file itself.", line)
        return str(p)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


FONT_CANDIDATES: List[str] = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf", "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "/usr/share/fonts/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc", "/Library/Fonts/Arial.ttf", "C:\\Windows\\Fonts\\arial.ttf",
    "C:\\Windows\\Fonts\\segoeui.ttf",
]


def find_font(name: Optional[str], resolver: Optional[FileResolver] = None) -> Optional[str]:
    """Return a font file path. ``name`` may be a file path or a font family (best effort)."""
    if name:
        cand = Path(os.path.expanduser(name))
        if resolver and not cand.is_absolute():
            cand = resolver.base_dir / cand
        if cand.exists():
            return str(cand)
        low = name.lower().replace(" ", "")
        for root in ("/usr/share/fonts", "/System/Library/Fonts", "/Library/Fonts", "C:\\Windows\\Fonts",
                     str(Path.home() / ".fonts"), str(Path.home() / ".local/share/fonts")):
            if os.path.isdir(root):
                for dirpath, _, files in os.walk(root):
                    for f in files:
                        if f.lower().endswith((".ttf", ".otf", ".ttc")) and low in f.lower().replace(" ", "").replace("-", ""):
                            return os.path.join(dirpath, f)
    for c in FONT_CANDIDATES:
        if os.path.exists(c):
            return c
    return None
