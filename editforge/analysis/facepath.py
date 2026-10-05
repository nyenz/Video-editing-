"""Face detection, tracking with stable IDs, an active-speaker guess, and smooth crop paths.

Only ``detect_faces`` needs OpenCV. The rest is plain Python so it can be checked without it.

HONESTY NOTE: the "active speaker" choice is a HEURISTIC. It looks at how much the mouth area of each
face moves while someone is talking. It is often right for a person talking to camera and can be wrong
when several people move, when a face is turned away, or in poor light.
"""

from __future__ import annotations

import math
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..core.errors import Cancelled, FeatureUnavailable
from ..core.ffmpeg import get_tools


@dataclass
class Face:
    """A face box in fractions of the frame (0..1) plus how much its mouth area moved."""

    x: float
    y: float
    w: float
    h: float
    mouth: float = 0.0

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


def iou(a: Face, b: Face) -> float:
    x0, y0 = max(a.x, b.x), max(a.y, b.y)
    x1, y1 = min(a.x + a.w, b.x + b.w), min(a.y + a.h, b.y + b.h)
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = a.w * a.h + b.w * b.h - inter
    return inter / union if union > 0 else 0.0


@dataclass
class Track:
    """One person followed through time."""

    id: int
    samples: List[Tuple[float, Face]] = field(default_factory=list)
    vx: float = 0.0
    vy: float = 0.0

    @property
    def last_t(self) -> float:
        return self.samples[-1][0]

    @property
    def last(self) -> Face:
        return self.samples[-1][1]

    def predict(self, t: float) -> Tuple[float, float]:
        dt = max(0.0, t - self.last_t)
        return self.last.cx + self.vx * dt, self.last.cy + self.vy * dt


class FaceTracker:
    """Gives each face an ID that stays the same while the person stays in view.

    A new detection joins the existing track whose predicted position is nearest (using the overlap of the
    boxes first, then the distance between centres). A track that is not seen for ``max_gap`` seconds is closed
    and a face that appears later gets a new ID.
    """

    def __init__(self, max_gap: float = 1.2, min_iou: float = 0.12, max_dist: float = 0.22):
        self.max_gap, self.min_iou, self.max_dist = max_gap, min_iou, max_dist
        self.tracks: List[Track] = []
        self._open: List[Track] = []
        self._next = 1

    def update(self, t: float, faces: Sequence[Face]) -> List[Tuple[int, Face]]:
        self._open = [tr for tr in self._open if t - tr.last_t <= self.max_gap]
        pairs = []
        for ti, tr in enumerate(self._open):
            px, py = tr.predict(t)
            for fi, f in enumerate(faces):
                dist = math.hypot(f.cx - px, f.cy - py)
                ov = iou(tr.last, f)
                if ov >= self.min_iou or dist <= self.max_dist:
                    pairs.append((-(ov * 2.0) + dist, ti, fi))
        pairs.sort()
        used_t, used_f = set(), set()
        out: List[Tuple[int, Face]] = []
        for _, ti, fi in pairs:
            if ti in used_t or fi in used_f:
                continue
            used_t.add(ti)
            used_f.add(fi)
            tr, f = self._open[ti], faces[fi]
            dt = max(1e-3, t - tr.last_t)
            tr.vx = 0.6 * tr.vx + 0.4 * (f.cx - tr.last.cx) / dt
            tr.vy = 0.6 * tr.vy + 0.4 * (f.cy - tr.last.cy) / dt
            tr.samples.append((t, f))
            out.append((tr.id, f))
        for fi, f in enumerate(faces):
            if fi in used_f:
                continue
            tr = Track(self._next, [(t, f)])
            self._next += 1
            self.tracks.append(tr)
            self._open.append(tr)
            out.append((tr.id, f))
        return out


def track_all(samples: Sequence[Tuple[float, Sequence[Face]]], **kw: Any) -> List[Track]:
    """Run the tracker over a list of (time, faces) samples."""
    tr = FaceTracker(**kw)
    for t, faces in samples:
        tr.update(t, faces)
    return tr.tracks


def active_speaker(tracks: Sequence[Track], times: Sequence[float], speech: Optional[Sequence[Tuple[float, float]]] = None,
                   window: float = 0.8, switch_ratio: float = 1.6, min_hold: float = 1.0) -> List[Optional[int]]:
    """Guess who is speaking at each sample time (a heuristic: see the module note)."""
    def speaking(t: float) -> bool:
        return True if speech is None else any(a <= t <= b for a, b in speech)

    def motion(tr: Track, t: float) -> float:
        vals = [f.mouth for ts, f in tr.samples if t - window <= ts <= t]
        return sum(vals) / len(vals) if vals else 0.0

    def present(tr: Track, t: float) -> bool:
        return any(abs(ts - t) <= 0.6 for ts, _ in tr.samples)

    current: Optional[int] = None
    since = -1e9
    out: List[Optional[int]] = []
    for t in times:
        here = [tr for tr in tracks if present(tr, t)]
        if not here:
            out.append(current)
            continue
        if current is None or not any(tr.id == current for tr in here):
            biggest = max(here, key=lambda tr: min((f.w * f.h for ts, f in tr.samples if abs(ts - t) <= 0.6), default=0) or 0)
            current, since = biggest.id, t
        elif speaking(t):
            cur_m = motion(next(tr for tr in here if tr.id == current), t)
            best = max(here, key=lambda tr: motion(tr, t))
            if best.id != current and motion(best, t) > switch_ratio * max(cur_m, 1e-4) and t - since >= min_hold:
                current, since = best.id, t
        out.append(current)
    return out


def raw_centres(tracks: Sequence[Track], times: Sequence[float], chosen: Sequence[Optional[int]], fallback_largest: bool = True) -> List[Optional[float]]:
    """Horizontal centre (0..1) of the chosen face at each time (None when no face is known yet)."""
    by_id = {tr.id: tr for tr in tracks}
    out: List[Optional[float]] = []
    for t, cid in zip(times, chosen):
        val = None
        if cid is not None and cid in by_id:
            near = [(abs(ts - t), f) for ts, f in by_id[cid].samples if abs(ts - t) <= 1.0]
            if near:
                val = min(near, key=lambda x: x[0])[1].cx
        elif fallback_largest:
            cands = [f for tr in tracks for ts, f in tr.samples if abs(ts - t) <= 0.3]
            if cands:
                val = max(cands, key=lambda f: f.w * f.h).cx
        out.append(val)
    return out


def smooth_path(times: Sequence[float], centres: Sequence[Optional[float]], smooth: float = 0.7, dead_zone: float = 0.035,
                max_speed: float = 0.35) -> List[Tuple[float, float]]:
    """Turn noisy face centres into a calm camera path with no flicker.

    * missing values hold the last known position (or the first known one at the start),
    * movement below ``dead_zone`` (a fraction of the frame width) is ignored,
    * the camera moves toward the target with easing and never faster than ``max_speed`` widths per second.
    """
    first = next((c for c in centres if c is not None), 0.5)
    alpha = max(0.05, 1.0 - 0.92 * smooth)
    speed = max_speed * (1.3 - smooth)
    pos = first
    out: List[Tuple[float, float]] = []
    prev_t = times[0] if times else 0.0
    for t, c in zip(times, centres):
        target = pos if c is None else c
        dt = max(1e-3, t - prev_t)
        prev_t = t
        err = target - pos
        if abs(err) > dead_zone:
            step = alpha * (err - math.copysign(dead_zone * 0.5, err))
            limit = speed * dt
            pos += max(-limit, min(limit, step))
        out.append((round(t, 4), round(pos, 5)))
    return out


def simplify(points: Sequence[Tuple[float, float]], tol: float = 0.004) -> List[Tuple[float, float]]:
    """Ramer-Douglas-Peucker on a (time, value) path: keeps the shape with few points."""
    pts = list(points)
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        (t0, v0), (t1, v1) = pts[a], pts[b]
        worst, idx = 0.0, -1
        for i in range(a + 1, b):
            t, v = pts[i]
            expect = v0 + (v1 - v0) * ((t - t0) / (t1 - t0) if t1 > t0 else 0.0)
            d = abs(v - expect)
            if d > worst:
                worst, idx = d, i
        if worst > tol and idx > 0:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [p for p, k in zip(pts, keep) if k]


def build_path(samples: Sequence[Tuple[float, Sequence[Face]]], mode: str, speech: Optional[Sequence[Tuple[float, float]]],
               smooth: float) -> Tuple[List[Tuple[float, float]], Dict[str, Any]]:
    """Full pipeline from detections to a simplified camera path. Returns (path, info)."""
    times = [t for t, _ in samples]
    tracks = track_all(samples)
    info: Dict[str, Any] = {"faces": len(tracks), "mode": mode}
    if not tracks or not times:
        return [], info
    if mode == "speaker":
        chosen = active_speaker(tracks, times, speech)
    else:  # follow the face seen the most (steady), not whoever is biggest this instant
        main = max(tracks, key=lambda tr: len(tr.samples))
        chosen = [main.id] * len(times)
    centres = raw_centres(tracks, times, chosen)
    return simplify(smooth_path(times, centres, smooth)), info


# ---------------------------------------------------------------------------- detection (OpenCV)

def detect_faces(path: str, start: float, end: float, src_w: int, src_h: int, sample_fps: float = 4.0, width: int = 320,
                 cancel: Optional[threading.Event] = None) -> List[Tuple[float, List[Face]]]:
    """Look for faces several times a second. Reads one small grey frame at a time (memory stays flat)."""
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except Exception:
        raise FeatureUnavailable("Face tracking is switched off because the free 'opencv-python-headless' add-on is not installed.",
                                 "Install it with:  pip install opencv-python-headless   -- or use  reframe 9:16 mode=center")
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    w = width - width % 2
    h = max(2, int(round(w * src_h / src_w / 2)) * 2)
    cmd = [get_tools().ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{max(0.1, end - start):.3f}",
           "-i", path, "-an", "-vf", f"fps={sample_fps},scale={w}:{h},format=gray", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    out: List[Tuple[float, List[Face]]] = []
    prev = None
    n = 0
    size = w * h
    assert proc.stdout is not None
    try:
        while True:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            gray = np.frombuffer(buf, dtype=np.uint8).reshape(h, w)
            boxes = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(max(18, w // 14), max(18, w // 14)))
            faces: List[Face] = []
            for (x, y, bw, bh) in boxes:
                mouth = 0.0
                if prev is not None:
                    y0, y1 = int(y + bh * 0.62), int(y + bh * 0.96)
                    x0, x1 = int(x + bw * 0.2), int(x + bw * 0.8)
                    if y1 > y0 and x1 > x0:
                        mouth = float(np.mean(np.abs(gray[y0:y1, x0:x1].astype(np.int16) - prev[y0:y1, x0:x1].astype(np.int16)))) / 40.0
                faces.append(Face(float(x) / w, float(y) / h, float(bw) / w, float(bh) / h, min(1.0, float(mouth))))
            out.append((start + n / sample_fps, faces))
            prev = gray
            n += 1
    finally:
        try:
            proc.kill()
        except OSError:
            pass
        proc.wait()
    return out


def fill_reframe_paths(segs: Sequence[Any], planner: Any, analyzer: Any) -> None:
    """Planner hook: give every face-following reframe segment a smooth crop path."""
    wanted = [s for s in segs if s.reframe is not None and s.kind == "clip"]
    if not wanted:
        return
    F = planner.F
    a = min(s.src_start_f for s in wanted) / F
    b = max(s.src_start_f + s.src_len_f for s in wanted) / F
    if not planner.media.has_video:
        return
    samples = analyzer.faces(a, b)
    speech = None
    if planner.media.has_audio and any(s.reframe.mode == "speaker" for s in wanted):
        sil = analyzer.silences(-35.0, 0.3)
        speech, cur = [], 0.0
        for s0, s1 in sil:
            if s0 > cur:
                speech.append((cur, s0))
            cur = s1
        if cur < planner.D:
            speech.append((cur, planner.D))
    paths: Dict[Tuple[str, float], List[Tuple[float, float]]] = {}
    for s in wanted:
        r = s.reframe
        want = r.mode if r.mode != "auto" else "face"
        if want == "center":
            continue
        key = (want, r.smooth)
        if key not in paths:
            paths[key], info = build_path(samples, want, speech, r.smooth)
            planner.notes.append(f"Face tracking ({want}): found {info['faces']} face track(s)."
                                 + (" The speaker choice is a heuristic and can be wrong." if want == "speaker" else ""))
            if not paths[key]:
                planner.warn("No faces were found, so the re-frame uses the centre of the picture.")
        path = paths[key]
        if path:
            lo, hi = s.src_start_f / F - 1.5, (s.src_start_f + s.src_len_f) / F + 1.5
            s.reframe = type(r)(r.aspect, want, r.smooth, [p for p in path if lo <= p[0] <= hi] or path[:1])
        else:
            s.reframe = type(r)(r.aspect, "center", r.smooth, None)
