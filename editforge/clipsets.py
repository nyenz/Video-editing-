"""Clip projects: one long video (a movie) and the many clips taken out of it.

Phase 1 of the work: open a movie, mark clips, and make (render) them. A clip can have several parts
(for example 1:02-1:20 and then 5:40-5:48), played one after the other as ONE clip. Each clip has its own
basic picture and sound settings. "Make" turns every clip into a small video file of its own; after that
the movie itself is no longer needed, and the app can free the space it takes.

Phase 2 (Edit) opens a made clip as an editor project, phase 3 (Final) lists finished videos.

A clip set is stored as JSON in <home>/clipsets/<id>.json. A clip remembers the export job that made it
(``made.job``) plus a fingerprint of everything that changes the picture or sound (``made.key``); when the
clip is changed afterwards, the old file is shown as "out of date".
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import project as project_mod
from .core.errors import EditForgeError

CLIPSET_ID = r"[0-9a-f]{12}"
CLIP_ID = re.compile(r"^[0-9a-f]{8}$")
JOB_ID = re.compile(r"^[0-9a-f]{12}$")
MAX_CLIPS = 300
MAX_PARTS = 60
MIN_PART = 0.04
SHAPES = ("original", "vertical", "vertical_blur", "square", "square_blur")
#: settings a clip may carry (the rest of the project settings stay at their defaults)
CLIP_SETTINGS = ("shape", "fade_in", "fade_out", "noise", "vocals", "voice_boost", "compress", "bass", "treble", "quality",
                 "mix_split", "mix_voices", "mix_music", "mix_effects")


def _num(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v != v or v in (float("inf"), float("-inf")):
        return default
    return max(lo, min(hi, v))


def clean_parts(raw: Any, duration: float) -> List[Dict[str, float]]:
    parts: List[Dict[str, float]] = []
    for p in (raw if isinstance(raw, list) else [])[:MAX_PARTS]:
        if not isinstance(p, dict):
            continue
        try:
            a, b = float(p["start"]), float(p["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if a != a or b != b:
            continue
        a, b = max(0.0, a), min(float(duration), b)
        if b - a >= MIN_PART:
            parts.append({"start": round(a, 3), "end": round(b, 3)})
    return parts


def clean_clip(raw: Any, duration: float, n: int) -> Optional[Dict[str, Any]]:
    """A safe copy of one clip, or None when it has no usable part."""
    if not isinstance(raw, dict):
        return None
    parts = clean_parts(raw.get("parts"), duration)
    if not parts:
        return None
    cid = str(raw.get("id") or "")
    if not CLIP_ID.match(cid):
        cid = secrets.token_hex(4)
    name = " ".join(str(raw.get("name") or "").split())[:60] or f"clip{n:02d}"
    color = raw.get("color") if isinstance(raw.get("color"), dict) else {}
    settings_raw = raw.get("settings") if isinstance(raw.get("settings"), dict) else {}
    full = project_mod.clean_settings({k: settings_raw[k] for k in CLIP_SETTINGS if k in settings_raw})
    settings = {k: full[k] for k in CLIP_SETTINGS}
    if settings["shape"] not in SHAPES:
        settings["shape"] = "original"
    made = raw.get("made") if isinstance(raw.get("made"), dict) else {}
    made = {"job": str(made.get("job")), "key": str(made.get("key") or "")[:40]} if JOB_ID.match(str(made.get("job") or "")) else None
    return {"id": cid, "name": name, "parts": parts,
            "color": {"brightness": round(_num(color.get("brightness"), 0.0, -1.0, 1.0), 3),
                      "contrast": round(_num(color.get("contrast"), 1.0, 0.0, 3.0), 3),
                      "saturation": round(_num(color.get("saturation"), 1.0, 0.0, 3.0), 3)},
            "speed": round(_num(raw.get("speed"), 1.0, 0.25, 4.0), 3),
            "volume": round(_num(raw.get("volume"), 1.0, 0.0, 4.0), 3),
            "mute": bool(raw.get("mute")), "settings": settings, "made": made}


def clean_clipset(raw: Any, duration: float) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise EditForgeError("That clip project could not be read.", "Reload the page and try again.")
    clips_raw = raw.get("clips") if isinstance(raw.get("clips"), list) else []
    if len(clips_raw) > MAX_CLIPS:
        raise EditForgeError(f"A clip project can have at most {MAX_CLIPS} clips.", "Start a second clip project for the rest.")
    clips: List[Dict[str, Any]] = []
    seen = set()
    for n, c in enumerate(clips_raw, 1):
        clip = clean_clip(c, duration, n)
        if clip is None:
            continue
        while clip["id"] in seen:
            clip["id"] = secrets.token_hex(4)
        seen.add(clip["id"])
        clips.append(clip)
    name = " ".join(str(raw.get("name") or "").split())[:80] or "My clips"
    return {"name": name, "source": str(raw.get("source") or "")[:300], "clips": clips}


def clip_key(source: str, clip: Dict[str, Any]) -> str:
    """Fingerprint of everything that changes the made file (not the name)."""
    spec = {"s": source, "p": clip["parts"], "c": clip["color"], "v": clip["speed"], "vol": clip["volume"], "m": clip["mute"],
            "st": clip["settings"]}
    return hashlib.sha1(json.dumps(spec, sort_keys=True).encode()).hexdigest()


def clip_length(clip: Dict[str, Any]) -> float:
    return sum(p["end"] - p["start"] for p in clip["parts"]) / (clip.get("speed") or 1.0)


def clip_project(clipset: Dict[str, Any], clip: Dict[str, Any], duration: float) -> Dict[str, Any]:
    """The editor project that makes one clip: its parts in order, each with the clip's look and sound."""
    pieces = [{"start": p["start"], "end": p["end"], "speed": clip["speed"], "volume": clip["volume"], "mute": clip["mute"],
               "color": clip["color"]} for p in clip["parts"]]
    return project_mod.clean_project({"name": clip["name"], "source": clipset["source"], "pieces": pieces,
                                      "settings": dict(clip["settings"])}, duration)


# ------------------------------------------------------------------------------------------------ scenes
class SceneFinder:
    """Finds the scene changes of a video once (in the background) and keeps them in <home>/scenes/<fingerprint>.json."""

    THRESHOLD = 0.3
    MIN_SCENE = 0.6

    def __init__(self, folder: Path, fingerprint: Callable[[str], str]):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.fingerprint = fingerprint
        self._lock = threading.Lock()
        self._jobs: Dict[str, Dict[str, Any]] = {}

    def _file(self, src: str) -> Path:
        return self.folder / (self.fingerprint(src) + ".json")

    def status(self, src: str) -> Dict[str, Any]:
        f = self._file(src)
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            return {"state": "ready", "progress": 1.0, "scenes": data["scenes"], "error": ""}
        except (OSError, ValueError, KeyError, TypeError):
            pass
        with self._lock:
            job = dict(self._jobs.get(str(f)) or {"state": "none", "progress": 0.0, "error": ""})
        job["scenes"] = []
        return job

    def start(self, src: str, duration: float, has_video: bool) -> Dict[str, Any]:
        if not has_video:
            raise EditForgeError("This file has no picture, so it has no scenes.", "Choose a video.")
        st = self.status(src)
        if st["state"] in ("ready", "running"):
            return st
        f = self._file(src)
        with self._lock:
            self._jobs[str(f)] = {"state": "running", "progress": 0.0, "error": ""}
        threading.Thread(target=self._run, args=(src, duration, f), name="editforge-scenes", daemon=True).start()
        return self.status(src)

    def _run(self, src: str, duration: float, f: Path) -> None:
        from .analysis.scenes import detect_scene_scores, scenes_from_scores
        from .core.cache import atomic_write_json

        def prog(t: float) -> None:
            with self._lock:
                self._jobs[str(f)]["progress"] = round(min(0.99, t / max(duration, 0.1)), 3)
        try:
            scores = detect_scene_scores(src, 0.08, progress=prog)
            scenes = scenes_from_scores(scores, self.THRESHOLD, self.MIN_SCENE, duration)
            atomic_write_json(f, {"scenes": scenes, "duration": duration, "when": time.time()})
            with self._lock:
                self._jobs.pop(str(f), None)
        except Exception as exc:  # noqa: BLE001 - shown to the person
            with self._lock:
                self._jobs[str(f)] = {"state": "failed", "progress": 0.0, "error": getattr(exc, "message", None) or str(exc)}


def scene_ranges(bounds: List[float], duration: float) -> List[Tuple[float, float]]:
    edges = [0.0] + [b for b in bounds if 0 < b < duration] + [duration]
    return [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]
