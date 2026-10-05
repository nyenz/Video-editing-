"""Face tracking, active-speaker guess and smooth re-framing (optional: needs OpenCV).

Everything that does not need OpenCV (tracking, smoothing, path simplifying) lives here as plain
Python so it can be tested without it. Only ``detect_faces`` needs ``opencv-python-headless``.
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional


def opencv_available() -> bool:
    """True if OpenCV can be imported."""
    try:
        import cv2  # noqa: F401
        return True
    except Exception:
        return False


def make_face_provider(analyzer: Any) -> Optional[Callable[..., Any]]:
    """Return the function that fills in face-based crop paths, or None if OpenCV is missing."""
    if not opencv_available():
        return None
    try:
        from .facepath import fill_reframe_paths
    except ImportError:  # pragma: no cover
        return None
    return lambda segs, planner: fill_reframe_paths(segs, planner, analyzer)
