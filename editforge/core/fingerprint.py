"""Content fingerprints and stable hashes used by every cache key."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from typing import Any, Dict, Tuple

_BLOCK = 64 * 1024
_SAMPLES = 24
_MEMO: Dict[Tuple[str, int, int], str] = {}
_MEMO_LOCK = threading.Lock()


def file_fingerprint(path: str) -> str:
    """Return a fingerprint of a file's content.

    It hashes the size plus 24 evenly spaced 64 KiB blocks. That is fast even for a
    4-hour file, and changes whenever the content is replaced (not just the name).
    The answer is remembered for as long as the file keeps its size and time, so asking
    again (every thumbnail, every preview check) costs no disk reads.
    """
    st = os.stat(path)
    key = (os.path.abspath(path), st.st_size, st.st_mtime_ns)
    with _MEMO_LOCK:
        hit = _MEMO.get(key)
    if hit is not None:
        return hit
    hit = _fingerprint(path)
    with _MEMO_LOCK:
        if len(_MEMO) > 5000:
            _MEMO.clear()
        _MEMO[key] = hit
    return hit


def _fingerprint(path: str) -> str:
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
