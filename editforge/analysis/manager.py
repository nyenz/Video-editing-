"""One place to ask for analysis results. Results are cached by content fingerprint."""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..core.cache import AnalysisCache
from ..core.errors import EditForgeError
from ..core.fingerprint import file_fingerprint
from ..core.media import MediaInfo, probe_media
from .beats import BeatInfo, analyze_beats
from .keyframes import keyframe_times
from .speech import Word, transcribe
from .scenes import detect_scene_scores, scenes_from_scores
from .silence import detect_silences

SCENE_FLOOR = 0.08


class Analyzer:
    """Runs and caches analyses for one source file.

    The same cache is used by the dry-run plan and the real render, so nothing is analysed twice.
    """

    def __init__(self, media: MediaInfo, cache: Optional[AnalysisCache] = None,
                 cancel: Optional[threading.Event] = None, log: Optional[Callable[[str], None]] = None):
        self.media = media
        self.cache = cache or AnalysisCache()
        self.cancel = cancel
        self.notes: List[str] = []
        self._log = log
        self._fp: Dict[str, str] = {}
        self._mem: Dict[str, Any] = {}
        self.ran: List[str] = []

    def _note(self, text: str) -> None:
        self.notes.append(text)
        if self._log:
            self._log(text)

    def fingerprint(self, path: Optional[str] = None) -> str:
        path = path or self.media.path
        if path not in self._fp:
            self._fp[path] = file_fingerprint(path)
        return self._fp[path]

    def _cached(self, kind: str, params: dict, compute: Callable[[], Any], label: str, path: Optional[str] = None) -> Any:
        mem_key = f"{kind}:{sorted(params.items())}:{path}"
        if mem_key in self._mem:
            return self._mem[mem_key]
        fp = self.fingerprint(path)
        hit = self.cache.get(kind, fp, params)
        if hit is not None:
            self._note(f"{label} (reused from an earlier run)")
            self._mem[mem_key] = hit
            return hit
        self._note(f"{label}...")
        self.ran.append(kind)
        value = compute()
        self.cache.put(kind, fp, params, value)
        self._mem[mem_key] = value
        return value

    def silences(self, noise_db: float, min_duration: float) -> List[Tuple[float, float]]:
        if not self.media.has_audio:
            self._note("This file has no sound, so there is no silence to remove.")
            return []
        params = {"noise": round(noise_db, 3), "min": round(min_duration, 3)}
        data = self._cached("silence", params, lambda: [list(x) for x in detect_silences(
            self.media.path, noise_db, min_duration, duration=self.media.duration, cancel=self.cancel)],
            "Listening for silent parts")
        return [(float(a), float(b)) for a, b in data]

    def scene_scores(self) -> List[Tuple[float, float]]:
        if not self.media.has_video:
            return []
        data = self._cached("scenes", {"floor": SCENE_FLOOR}, lambda: [list(x) for x in detect_scene_scores(
            self.media.path, SCENE_FLOOR, cancel=self.cancel)], "Looking for scene changes")
        return [(float(a), float(b)) for a, b in data]

    def scenes(self, threshold: float, min_scene: float) -> List[float]:
        return scenes_from_scores(self.scene_scores(), max(threshold, SCENE_FLOOR), min_scene, self.media.duration)

    def keyframes(self) -> List[float]:
        if not self.media.has_video:
            return []
        return [float(x) for x in self._cached("keyframes", {}, lambda: keyframe_times(self.media.path),
                                               "Finding keyframes")]

    def beats(self, path: Optional[str] = None, duration: Optional[float] = None) -> BeatInfo:
        path = path or self.media.path
        dur = duration
        if dur is None:
            dur = self.media.duration if path == self.media.path else probe_media(path).duration
        data = self._cached("beats", {"dur": round(dur, 2)}, lambda: analyze_beats(path, duration=dur, cancel=self.cancel).to_dict(),
                            "Listening for the beat", path=path)
        return BeatInfo.from_dict(data)

    def transcript(self, language: str = "auto", model: str = "base", device: str = "auto") -> Tuple[List[Word], str]:
        """Words spoken in the source, with times (uses local Whisper; cached)."""
        params = {"language": language, "model": model}
        holder: Dict[str, str] = {}

        def compute() -> Any:
            words, lang = transcribe(self.media.path, language=language, model=model, device=device, cancel=self.cancel)
            return {"words": [w.to_list() for w in words], "language": lang}

        data = self._cached("transcript", params, compute, "Writing down the speech with Whisper")
        return [Word(t, float(a), float(b)) for t, a, b in data["words"]], str(data.get("language", ""))

    def faces(self, start: float, end: float, width: int = 320):
        """Face detections between two times (cached). Needs the optional OpenCV add-on.

        ``width`` is the size of the small copy that is searched. 320 is quick and finds faces that fill a
        good part of the picture; 640 is about four times slower and also finds small faces in wide shots.
        """
        from .facepath import Face, detect_faces
        params = {"a": round(start, 1), "b": round(end, 1), "fps": 4.0}
        if width != 320:
            params["w"] = width

        def compute() -> Any:
            res = detect_faces(self.media.path, start, end, self.media.width, self.media.height, 4.0, width, self.cancel)
            return [[t, [[f.x, f.y, f.w, f.h, f.mouth] for f in faces]] for t, faces in res]

        data = self._cached("faces", params, compute, "Looking for faces")
        return [(float(t), [Face(*map(float, f)) for f in faces]) for t, faces in data]
