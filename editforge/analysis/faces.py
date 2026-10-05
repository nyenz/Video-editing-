"""Face tracking, active-speaker guess and smooth re-framing (optional: needs OpenCV).

Everything that does not need OpenCV (tracking, smoothing, path simplifying) lives here as plain
Python so it can be tested without it. Only ``detect_faces`` needs ``opencv-python-headless``.
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional


def opencv_available() -> bool:
    """True if a usable OpenCV is installed.

    OpenCV 5 removed the face detector EditForge uses (``CascadeClassifier``), so version 5
    counts as "not available" instead of crashing in the middle of an edit.
    """
    try:
        import cv2
        return hasattr(cv2, "CascadeClassifier") and hasattr(cv2, "data")
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
