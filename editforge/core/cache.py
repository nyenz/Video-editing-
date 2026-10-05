"""Home folder, atomic file writes and the analysis cache."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Optional

from ..version import __version__
from .fingerprint import stable_hash


def home_dir() -> Path:
    """Folder where EditForge keeps caches, jobs and uploads (``EDITFORGE_HOME`` overrides)."""
    root = os.environ.get("EDITFORGE_HOME")
    path = Path(root).expanduser() if root else Path.home() / ".editforge"
    path.mkdir(parents=True, exist_ok=True)
    return path


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write a file so that readers never see a half-written file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".part", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_text(path: Path, text: str) -> None:
    """Atomically write UTF-8 text."""
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_json(path: Path, obj: Any) -> None:
    """Atomically write JSON."""
    atomic_write_text(path, json.dumps(obj, indent=1, sort_keys=True, default=str))


class AnalysisCache:
    """Disk cache for analysis results (silence, scenes, beats, faces, transcripts).

    Keys always include the content fingerprint of the source, the analysis
    parameters and the app version, so a changed file can never return old data.
    """

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root else home_dir() / "analysis"
        self.root.mkdir(parents=True, exist_ok=True)

    def key(self, kind: str, fingerprint: str, params: dict) -> str:
        return stable_hash({"kind": kind, "fp": fingerprint, "params": params, "v": __version__})

    def _path(self, kind: str, key: str) -> Path:
        return self.root / kind / f"{key}.json"

    def get(self, kind: str, fingerprint: str, params: dict) -> Optional[Any]:
        path = self._path(kind, self.key(kind, fingerprint, params))
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)["data"]
        except (OSError, ValueError, KeyError):
            return None

    def put(self, kind: str, fingerprint: str, params: dict, data: Any) -> None:
        path = self._path(kind, self.key(kind, fingerprint, params))
        atomic_write_json(path, {"data": data, "params": params, "fp": fingerprint, "v": __version__})

    def clear(self) -> int:
        """Delete every cached analysis. Returns the number of files removed."""
        count = sum(1 for _ in self.root.rglob("*.json"))
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True, exist_ok=True)
        return count
