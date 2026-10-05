"""Content fingerprints and stable hashes used by every cache key."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

_BLOCK = 64 * 1024
_SAMPLES = 24


def file_fingerprint(path: str) -> str:
    """Return a fingerprint of a file's content.

    It hashes the size plus 24 evenly spaced 64 KiB blocks. That is fast even for a
    4-hour file, and changes whenever the content is replaced (not just the name).
    """
    size = os.path.getsize(path)
    h = hashlib.sha256()
    h.update(str(size).encode())
    with open(path, "rb") as fh:
        if size <= _BLOCK * _SAMPLES:
            h.update(fh.read())
        else:
            for i in range(_SAMPLES):
                offset = (size - _BLOCK) * i // (_SAMPLES - 1)
                fh.seek(offset)
                h.update(fh.read(_BLOCK))
    return h.hexdigest()[:32]


def source_identity(path: str) -> dict:
    """Return everything that identifies a source file for cache keys."""
    st = os.stat(path)
    return {"fp": file_fingerprint(path), "size": st.st_size, "mtime_ns": st.st_mtime_ns}


def canonical_json(obj: Any) -> str:
    """Serialise ``obj`` to JSON in one canonical form (sorted keys, no spaces)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=True)


def stable_hash(obj: Any, length: int = 32) -> str:
    """Hash any JSON-able object in a stable way."""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()[:length]
