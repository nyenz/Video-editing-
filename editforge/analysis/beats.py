"""Beat, tempo and downbeat detection.

The built-in detector needs only FFmpeg. It measures how loud the sound is in three
frequency bands 100 times a second, turns sudden rises into an "onset" curve, finds
the tempo by autocorrelation, and places beats with dynamic programming (Ellis, 2007).

Downbeats are NOT assumed to be "every 4th beat". For each candidate metre (2, 3, 4,
5, 6, 7 beats per bar) and each start position, the detector measures how much
stronger the accents are on that position than on the others. It reports a metre
only when one clearly stands out; otherwise it says the metre is unknown and returns
no downbeats. It is a heuristic and can be wrong on music without clear accents.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..core.errors import Cancelled
from ..core.ffmpeg import raise_for_result, run_ffmpeg

FPS = 100
WINDOW_SECONDS = 300.0
METRES = (2, 3, 4, 5, 6, 7)
METRE_MIN_CONTRAST = 1.3


@dataclass
class BeatInfo:
    """Result of beat analysis (all times in seconds)."""

    tempo: float = 0.0
    beats: List[float] = field(default_factory=list)
    downbeats: List[float] = field(default_factory=list)
    meter: Optional[int] = None
    beat_confidence: float = 0.0
    meter_confidence: float = 0.0
    engine: str = "builtin"
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "BeatInfo":
        return BeatInfo(**{k: v for k, v in d.items() if k in BeatInfo.__dataclass_fields__})


def bpm_grid(bpm: float, offset: float, start: float, end: float, beats_per_bar: int = 4) -> BeatInfo:
    """A fixed-tempo beat grid (used when the user gives ``bpm=``). Every ``beats_per_bar``-th beat is a downbeat."""
    period = 60.0 / bpm
    beats: List[float] = []
    t = offset
    while t < start - 1e-9:
        t += period
    while t < end - 1e-9:
        beats.append(round(t, 6))
        t += period
    downs = beats[::beats_per_bar]
    return BeatInfo(tempo=bpm, beats=beats, downbeats=downs, meter=beats_per_bar, beat_confidence=1.0,
                    meter_confidence=1.0, engine="fixed-bpm",
                    notes=[f"Fixed tempo of {bpm:g} BPM; downbeats assume {beats_per_bar} beats per bar because you gave the tempo yourself."])


# ------------------------------------------------------------------ envelopes

_DB = re.compile(r"lavfi\.astats\.Overall\.RMS_level=(\S+)")


def _read_levels(path: str) -> List[float]:
    levels: List[float] = []
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            m = _DB.search(line)
            if m:
                text = m.group(1)
                try:
                    v = float(text)
                except ValueError:
                    v = -90.0
                levels.append(max(-90.0, v) if v == v else -90.0)
    return levels


def band_envelopes(path: str, start: float, length: float, cancel: Optional[threading.Event] = None) -> Dict[str, List[float]]:
    """Loudness (dB) per 10 ms for a low, mid and high band, using FFmpeg only."""
    tmp = tempfile.mkdtemp(prefix="ef_beats_")
    try:
        def branch(tag: str, flt: str) -> str:
            return (f"[{tag}]{flt}asetnsamples=n=160:p=0,astats=metadata=1:reset=1:measure_perchannel=none:"
                    f"measure_overall=RMS_level,ametadata=mode=print:key=lavfi.astats.Overall.RMS_level:file={tag}.txt[o{tag}]")
        graph = ("[0:a:0]aresample=16000,aformat=channel_layouts=mono,asplit=3[lo][mid][hi];"
                 + branch("lo", "lowpass=f=180,") + ";" + branch("mid", "bandpass=f=900:width_type=h:width=1400,") + ";"
                 + branch("hi", "highpass=f=2500,"))
        args = ["-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", os.path.abspath(path), "-filter_complex", graph]
        for tag in ("lo", "mid", "hi"):
            args += ["-map", f"[o{tag}]", "-f", "null", "-"]
        result = run_ffmpeg(args, cwd=tmp, cancel=cancel)
        if result.cancelled:
            raise Cancelled()
        raise_for_result(result, "listening to the music")
        return {tag: _read_levels(os.path.join(tmp, f"{tag}.txt")) for tag in ("lo", "mid", "hi")}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------- maths

def onset_curve(env: Dict[str, List[float]]) -> List[float]:
    """Combine the three bands into one curve that rises where a new sound starts."""
    n = min(len(v) for v in env.values())
    weights = {"lo": 1.0, "mid": 0.6, "hi": 0.5}
    out = [0.0] * n
    for tag, w in weights.items():
        e = env[tag]
        # A rise in decibels alone makes a quiet hi-hat coming out of silence look as strong as a kick drum,
        # so the tracker used to lock onto the off-beats. Each rise is therefore scaled by how loud the new
        # sound is compared with the loud moments of its band (square-root of the amplitude ratio).
        levels = sorted(max(v, -70.0) for v in e[:n])
        loud = levels[int(0.98 * (len(levels) - 1))] if levels else -70.0
        prev = max(e[0], -70.0)
        for i in range(1, n):
            cur = max(e[i], -70.0)
            d = cur - prev
            if d > 0:
                out[i] += w * min(d, 30.0) * min(1.0, 10 ** ((cur - loud) / 40.0))
            prev = cur
    ordered = sorted(out)
    scale = ordered[int(0.95 * (len(ordered) - 1))] if ordered else 0.0
    if scale <= 1e-9:
        scale = max(out) if out and max(out) > 0 else 1.0
    return [min(2.0, v / scale) for v in out]


def estimate_tempo(onset: Sequence[float], fps: int = FPS, lo_bpm: float = 60.0, hi_bpm: float = 200.0) -> Tuple[float, float]:
    """Return (bpm, confidence 0..1) from the autocorrelation of the onset curve."""
    n = len(onset)
    if n < fps * 4:
        return 0.0, 0.0
    mean = sum(onset) / n
    x = [v - mean for v in onset]
    lag_lo, lag_hi = int(fps * 60.0 / hi_bpm), int(fps * 60.0 / lo_bpm)
    zero = sum(v * v for v in x) / n or 1e-9
    ac: Dict[int, float] = {}
    for lag in range(max(2, lag_lo - 1), min(n // 2, lag_hi + 2)):
        s = 0.0
        for i in range(n - lag):
            s += x[i] * x[i + lag]
        ac[lag] = s / (n - lag)
    best_lag, best = 0, -1e18
    for lag in range(lag_lo, lag_hi + 1):
        if lag not in ac:
            continue
        bpm = 60.0 * fps / lag
        prior = math.exp(-0.5 * (math.log2(bpm / 120.0) / 0.9) ** 2)
        # A peak is also supported by its double lag (same tempo, one bar later).
        support = ac[lag] + (0.5 * ac[2 * lag] if 2 * lag in ac else 0.0)
        score = support * prior
        if score > best:
            best, best_lag = score, lag
    if best_lag == 0:
        return 0.0, 0.0
    lag_f = float(best_lag)
    if best_lag - 1 in ac and best_lag + 1 in ac:
        a, b, c = ac[best_lag - 1], ac[best_lag], ac[best_lag + 1]
        denom = a - 2 * b + c
        if abs(denom) > 1e-12:
            lag_f = best_lag + 0.5 * (a - c) / denom
    conf = max(0.0, min(1.0, ac[best_lag] / zero))
    return 60.0 * fps / lag_f, conf


def track_beats(onset: Sequence[float], period: float, tightness: float = 100.0) -> List[int]:
    """Dynamic-programming beat tracker; returns beat positions as frame indices."""
    n = len(onset)
    if n == 0 or period < 2:
        return []
    lo, hi = max(1, int(round(period / 2.0))), int(round(period * 2.0))
    penalty = {tau: tightness * (math.log(tau / period)) ** 2 for tau in range(lo, hi + 1)}
    taus = list(penalty.items())
    score = [0.0] * n
    back = [-1] * n
    for t in range(n):
        best, arg = 0.0, -1
        for tau, pen in taus:
            p = t - tau
            if p < 0:
                break
            v = score[p] - pen
            if v > best:
                best, arg = v, p
        score[t] = onset[t] + best
        back[t] = arg
    tail_start = max(0, n - int(period))
    end = max(range(tail_start, n), key=lambda i: score[i])
    beats: List[int] = []
    while end >= 0:
        beats.append(end)
        end = back[end]
    beats.reverse()
    return beats


def refine_tempo(onset: Sequence[float], bpm: float, lo_bpm: float = 55.0, hi_bpm: float = 210.0) -> float:
    """Fix "wrong multiple" tempo mistakes (half, double, two thirds ...).

    Autocorrelation cannot tell 80 BPM from 160 BPM. So each related tempo is tried for real: beats are
    placed, and the tempo wins where (a) most beats land on a strong onset and (b) most strong onsets get
    a beat. Half tempo fails (b), double tempo fails (a), and two-thirds fails both. The first guess is
    kept unless another tempo is clearly better.
    """
    onset = onset[:FPS * 60]          # the first minute is enough to choose between related tempos, and keeps this quick
    n = len(onset)
    peaks = [i for i in range(1, n - 1) if onset[i] > 0 and onset[i] >= onset[i - 1] and onset[i] > onset[i + 1]]
    if len(peaks) < 8 or bpm <= 0:
        return bpm
    heights = sorted(onset[i] for i in peaks)
    threshold = 0.5 * heights[int(0.9 * (len(heights) - 1))]
    strong = [i for i in peaks if onset[i] >= threshold]
    if len(strong) < 8:
        return bpm
    strong_set = set(strong)

    def score(candidate: float) -> float:
        frames = track_beats(onset, FPS * 60.0 / candidate)
        if len(frames) < 4:
            return 0.0
        beat_set = set(frames)
        hit = sum(1 for f in frames if any((f + d) in strong_set for d in range(-3, 4))) / len(frames)
        cover = sum(1 for p in strong if any((p + d) in beat_set for d in range(-3, 4))) / len(strong)
        return hit * cover

    best, best_score = bpm, score(bpm)
    for factor in (2.0, 0.5, 1.5, 2.0 / 3.0, 3.0, 1.0 / 3.0):
        candidate = bpm * factor
        if lo_bpm <= candidate <= hi_bpm:
            sc = score(candidate)
            if sc > best_score * 1.1 + 0.02:
                best, best_score = candidate, sc
    return best


def _trim_unsupported(frames: List[int], onset: Sequence[float]) -> List[int]:
    """Drop beats at the very start and end that have no sound under them.

    The tracker keeps counting into the silence before the music starts and after it stops.
    Beats inside the music are never removed, even in a quiet passage.
    """
    if len(frames) < 4:
        return frames
    peaks = sorted(_local_peak(onset, f, 4) for f in frames)
    floor = 0.15 * peaks[len(peaks) // 2]
    lo, hi = 0, len(frames)
    while hi - lo > 1 and _local_peak(onset, frames[hi - 1], 4) < floor:
        hi -= 1
    while hi - lo > 1 and _local_peak(onset, frames[lo], 4) < floor:
        lo += 1
    return frames[lo:hi]


def _local_peak(env_lin: Sequence[float], idx: int, radius: int = 4) -> float:
    lo, hi = max(0, idx - radius), min(len(env_lin), idx + radius + 1)
    return max(env_lin[lo:hi]) if lo < hi else 0.0


def beat_accents(beat_frames: Sequence[int], env: Dict[str, List[float]], onset: Sequence[float]) -> List[float]:
    """How strong each beat is: a mix of low-frequency level and onset strength."""
    lo = [10 ** (max(v, -70.0) / 20.0) for v in env["lo"]]
    mid = [10 ** (max(v, -70.0) / 20.0) for v in env["mid"]]
    out = []

    def energy(env_lin: Sequence[float], f: int) -> float:
        # Summed over the length of a drum hit, not just its highest 10 ms slice: the peak of a short hit
        # depends on where it falls between two slices, which made equal beats look unequal in a repeating
        # pattern (at 140 BPM every 7th beat lines up, so "7 beats per bar" was reported for flat music).
        a, b = max(0, f - 3), min(len(env_lin), f + 9)
        return sum(env_lin[a:b]) / 4.0 if a < b else 0.0

    for f in beat_frames:
        o = _local_peak(onset, f, 3)
        out.append(1.0 * energy(lo, f) + 0.5 * energy(mid, f) + 0.02 * o)
    return out


def find_meter(accents: Sequence[float]) -> Tuple[Optional[int], int, float]:
    """Choose beats-per-bar and the start position from accent strengths.

    Returns (meter or None, phase, contrast). Contrast is how much stronger the bar-start
    beats are than the other beats, relative to the spread of all accents.
    """
    n = len(accents)
    if n < 8:
        return None, 0, 0.0
    med = sorted(accents)[n // 2] or 1e-9
    a = [v / med for v in accents]
    mean = sum(a) / n
    var = sum((v - mean) ** 2 for v in a) / n
    std = math.sqrt(var) or 1e-9
    best: Tuple[float, int, int] = (-1e9, 0, 0)
    scores: Dict[int, Tuple[float, int]] = {}
    for m in METRES:
        if n < m * 3:
            continue
        for p in range(m):
            on = [a[i] for i in range(p, n, m)]
            off = [a[i] for i in range(n) if (i - p) % m != 0]
            if not on or not off:
                continue
            if sum(on) / len(on) < 1.1 * (sum(off) / len(off)):
                continue        # bar starts must be at least 10% stronger, however regular a tiny difference is
            contrast = (sum(on) / len(on) - sum(off) / len(off)) / std
            # consistency: how often a bar-start beat beats the average of its bar
            wins = 0
            total = 0
            for i in range(p, n - m + 1, m):
                bar = a[i:i + m]
                total += 1
                if a[i] >= max(bar) - 1e-9:
                    wins += 1
            consistency = wins / total if total else 0.0
            score = contrast * (0.5 + 0.5 * consistency)
            if score > scores.get(m, (-1e9, 0))[0]:
                scores[m] = (score, p)
    if not scores:
        return None, 0, 0.0
    # Prefer the smallest metre whose score is close to the best (a 4-beat pattern also looks like 2).
    top = max(s for s, _ in scores.values())
    for m in sorted(scores):
        s, p = scores[m]
        if s >= 0.92 * top:
            best = (s, m, p)
            break
    score, m, p = best
    if score < METRE_MIN_CONTRAST:
        return None, 0, max(0.0, score)
    return m, p, score


def _halve_if_alternating(frames: List[int], accents: List[float], bpm: float) -> Tuple[List[int], List[float], float]:
    """Fix the "double tempo" mistake.

    Off-beat hi-hats make the detector lock onto twice the real tempo. In that case the
    accents alternate strong / weak / strong / weak very regularly, so the true pulse is the
    strong beats only. This is only tried for fast tempos (above 150 BPM).
    """
    if bpm <= 150 or len(frames) < 12:
        return frames, accents, bpm
    med = sorted(accents)[len(accents) // 2] or 1e-9
    a = [v / med for v in accents]
    for phase in (0, 1):
        strong = a[phase::2]
        weak = a[1 - phase::2]
        if not strong or not weak:
            continue
        ratio = (sum(strong) / len(strong)) / max(1e-9, sum(weak) / len(weak))
        pairs = min(len(strong), len(weak))
        wins = sum(1 for i in range(pairs) if strong[i] > weak[i] * 1.15)
        if ratio > 1.3 and wins >= 0.85 * pairs:
            return frames[phase::2], accents[phase::2], bpm / 2.0
    return frames, accents, bpm


def _analyse_window(path: str, start: float, length: float, cancel: Optional[threading.Event]) -> Tuple[List[float], float, float, List[float], Dict[str, Any]]:
    env = band_envelopes(path, start, length, cancel)
    onset = onset_curve(env)
    bpm, conf = estimate_tempo(onset)
    if bpm <= 0 or max(onset, default=0.0) <= 0.0:          # silence has no beat
        return [], 0.0, 0.0, [], {}
    bpm = refine_tempo(onset, bpm)
    frames = track_beats(onset, FPS * 60.0 / bpm)
    frames = _trim_unsupported(frames, onset)
    if len(frames) < 4:                                     # too few to call it a beat
        return [], 0.0, 0.0, [], {}
    if len(frames) >= 3:
        gaps = sorted(b - a for a, b in zip(frames, frames[1:]))
        median = gaps[len(gaps) // 2]
        if median > 0:
            bpm = 60.0 * FPS / median
    accents = beat_accents(frames, env, onset)
    frames, accents, bpm = _halve_if_alternating(frames, accents, bpm)
    return [start + f / FPS for f in frames], bpm, conf, accents, {}


def analyze_beats(path: str, *, start: float = 0.0, end: Optional[float] = None, duration: Optional[float] = None,
                  cancel: Optional[threading.Event] = None) -> BeatInfo:
    """Detect tempo, beats and (when clear) the bar structure of an audio or video file."""
    total = (end if end is not None else duration if duration is not None else start + WINDOW_SECONDS)
    info = BeatInfo()
    all_beats: List[float] = []
    all_acc: List[float] = []
    tempos: List[Tuple[float, float]] = []
    t = start
    while t < total - 1.0:
        length = min(WINDOW_SECONDS, total - t)
        beats, bpm, conf, acc, _ = _analyse_window(path, t, length, cancel)
        if beats:
            cutoff = t + length - (0.0 if t + length >= total else 1.0)
            keep = [(b, a) for b, a in zip(beats, acc) if b < cutoff and (not all_beats or b > all_beats[-1] + 0.25)]
            all_beats += [b for b, _ in keep]
            all_acc += [a for _, a in keep]
            tempos.append((bpm, conf))
        t += length
    if not all_beats:
        info.notes.append("No clear beat was found. The sound may be too quiet, too short, or without a steady pulse.")
        return info
    info.beats = [round(b, 4) for b in all_beats]
    total_conf = sum(c for _, c in tempos) or 1.0
    info.tempo = round(sum(b * c for b, c in tempos) / total_conf, 2) if len(tempos) > 1 else round(tempos[0][0], 2)
    info.beat_confidence = round(max(c for _, c in tempos), 3)
    meter, phase, contrast = find_meter(all_acc)
    info.meter_confidence = round(min(1.0, max(0.0, contrast / 2.0)), 3)
    if meter is None:
        info.notes.append("No clear accent pattern was found, so the number of beats per bar is unknown and no downbeats are reported.")
    else:
        info.meter = meter
        info.downbeats = [info.beats[i] for i in range(phase, len(info.beats), meter)]
        info.notes.append(f"Detected {meter} beats per bar (a heuristic based on accent strength; it can be wrong).")
    return info
