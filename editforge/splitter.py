"""Split the sound of a video into three layers: voices, music and effects.

Movies, series and clips mix three kinds of sound into one track: people talking, music, and effects
(gunshots, fights, cars, footsteps). Here a free AI model (BandIt Plus, trained on film-style sound) pulls
them apart again, so each layer can be made louder, quieter or switched off on its own.

* It runs on your computer, nothing is uploaded.
* The model file (about 76 MB) is downloaded once, from GitHub, and checked before it is used.
* The three layers are kept as FLAC files (lossless) in your home folder, per video, so splitting is only
  done once per video. Editing, previewing and exporting then only mix the layers, which is instant.

How the work is done: the sound is read in 6-second chunks that overlap, every chunk goes through the model
(Short-time Fourier transform -> neural network -> three masks -> inverse transform), and the overlapping
results are faded into each other so there are no clicks. The chunks are streamed, so a 2-hour film does
not need 2 hours of sound in memory.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .core.cache import atomic_write_json, home_dir
from .core.errors import EditForgeError
from .core.ffmpeg import get_tools
from .core.fingerprint import file_fingerprint
from .core.media import probe_media
from .version import __version__

#: the three layers, in the order the model returns them, with the words shown to the person
LAYERS = ("voices", "music", "effects")
LAYER_TITLES = {"voices": "Voices", "music": "Music", "effects": "Effects"}

SAMPLE_RATE = 44100
CHUNK = 264600                    # 6 seconds
N_FFT = 2048
HOP = 512
FRAMES = CHUNK // HOP + 1         # 517 spectrogram columns per chunk
BINS = N_FFT // 2 + 1

MODEL_ID = "sound-split-1"
MODEL_FILE = MODEL_ID + ".onnx"
#: where the converted model lives (a release of this project) and its fingerprint, size and credits
MODEL_URL = "https://github.com/nyenz/Video-editing-/releases/download/sound-model-1/sound-split-1.onnx"
MODEL_SHA256 = "b43c4978c07667b2c6e6aed8c3c1ad5b263a96f8fbc8faead2cec45971646d4d"
MODEL_SIZE = 76080146
MODEL_CREDIT = ("BandIt Plus, a speech / music / effects separation model by Roman Solovyev (ZFTurbo), based on the BandIt "
                "architecture (Watcharasupat et al.) and trained on the Divide and Remaster data set.")
DOWNLOAD_PREFIX = "https://github.com/"

FADE = CHUNK // 10
#: how far the next chunk starts after the last one. "fast": the chunks only overlap by their fades (a smooth cross-fade);
#: "best": every sample is heard by two chunks and the stereo channels are split separately (about 4 times the work).
STEPS = {"fast": CHUNK - FADE, "best": CHUNK // 2}
MODES = tuple(STEPS)


# --------------------------------------------------------------------------------------------- helpers
def available() -> bool:
    """True when the two small libraries the splitter needs are installed."""
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def models_dir(home: Optional[Path] = None) -> Path:
    p = (Path(home) if home else home_dir()) / "models"
    p.mkdir(parents=True, exist_ok=True)
    return p


def stems_dir(home: Optional[Path] = None) -> Path:
    p = (Path(home) if home else home_dir()) / "stems"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _threads() -> int:
    return max(1, (os.cpu_count() or 2) - 1)


# --------------------------------------------------------------------------------------------- transforms
def _window(np):
    n = np.arange(N_FFT, dtype=np.float64)
    return (0.5 - 0.5 * np.cos(2 * np.pi * n / N_FFT)).astype(np.float32)     # periodic Hann, like the training code


def stft(x, np):
    """(channels, CHUNK) float32 -> (channels, BINS, FRAMES, 2) float32 (real, imaginary), scaled like torchaudio normalized=True."""
    ch = x.shape[0]
    pad = N_FFT // 2
    xp = np.pad(x, ((0, 0), (pad, pad)))
    win = _window(np)
    out = np.empty((ch, BINS, FRAMES, 2), dtype=np.float32)
    scale = 1.0 / np.sqrt(np.sum(win.astype(np.float64) ** 2))
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(FRAMES)[:, None]
    for c in range(ch):
        frames = xp[c][idx] * win                              # (FRAMES, N_FFT)
        spec = np.fft.rfft(frames, axis=-1).T * scale          # (BINS, FRAMES)
        out[c, :, :, 0] = spec.real
        out[c, :, :, 1] = spec.imag
    return out


def istft(spec, np, length: int = CHUNK):
    """(n, BINS, FRAMES, 2) -> (n, length) float32. The inverse of :func:`stft`."""
    win = _window(np)
    scale = np.sqrt(np.sum(win.astype(np.float64) ** 2))
    n = spec.shape[0]
    pad = N_FFT // 2
    total = HOP * (FRAMES - 1) + N_FFT
    out = np.zeros((n, total), dtype=np.float32)
    env = np.zeros(total, dtype=np.float32)
    w2 = (win * win).astype(np.float32)
    for c in range(n):
        comp = (spec[c, :, :, 0] + 1j * spec[c, :, :, 1]).T * scale        # (FRAMES, BINS)
        frames = np.fft.irfft(comp, n=N_FFT, axis=-1).astype(np.float32) * win          # (FRAMES, N_FFT)
        buf = out[c]
        for j in range(N_FFT // HOP):
            part = frames[:, j * HOP:(j + 1) * HOP].reshape(-1)
            buf[j * HOP: j * HOP + part.size] += part
    for j in range(N_FFT // HOP):
        part = np.tile(w2[j * HOP:(j + 1) * HOP], FRAMES)
        env[j * HOP: j * HOP + part.size] += part
    env = np.maximum(env, 1e-8)
    res = out / env
    return res[:, pad:pad + length]


# --------------------------------------------------------------------------------------------- the model
class Engine:
    """The AI model, run with onnxruntime on the CPU."""

    def __init__(self, path: str, threads: Optional[int] = None):
        import numpy as np
        import onnxruntime as ort
        self.np = np
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads or _threads()
        opts.inter_op_num_threads = 1
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(str(path), opts, providers=["CPUExecutionProvider"])
        self.input = self.session.get_inputs()[0].name

    def __call__(self, chunk, fast: bool = True):
        """(2, CHUNK) float32 -> (3, 2, CHUNK) float32: voices, music, effects.

        The network finds, for every moment and pitch, how much belongs to each layer (a "mask"). ``fast`` finds the masks
        once, from the middle of the two stereo channels, and uses them for both; otherwise each channel gets its own.
        """
        np = self.np
        n = chunk.shape[1]
        if float(np.abs(chunk).max()) < 1e-3:                                  # silence: nothing to split
            return np.zeros((3, chunk.shape[0], n), dtype=np.float32)
        spec = stft(chunk, np)                                                  # (2, BINS, FRAMES, 2)
        feed = stft(chunk.mean(axis=0, keepdims=True), np) if fast else spec
        masks = self.session.run(None, {self.input: feed})[0]                  # (3, 1 or 2, BINS, FRAMES, 2)
        if masks.shape[1] != spec.shape[0]:
            masks = np.repeat(masks, spec.shape[0], axis=1)
        a, b = spec[..., 0], spec[..., 1]
        out = np.empty((3,) + spec.shape, dtype=np.float32)
        for k in range(3):                                                      # complex multiply: spectrum x mask
            out[k, ..., 0] = a * masks[k, ..., 0] - b * masks[k, ..., 1]
            out[k, ..., 1] = a * masks[k, ..., 1] + b * masks[k, ..., 0]
        return np.stack([istft(out[k], np, n) for k in range(3)], axis=0)


# --------------------------------------------------------------------------------------------- reading and writing sound
class _Reader:
    """Reads the sound of a time range as 44.1 kHz stereo floats, piece by piece."""

    def __init__(self, source: str, start: float, duration: float):
        args = [get_tools().ffmpeg, "-v", "error", "-nostdin", "-ss", f"{start:.6f}", "-t", f"{duration:.6f}", "-i", source,
                "-vn", "-map", "0:a:0", "-ac", "2", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"]
        self.proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=_NO_WINDOW)

    def read(self, samples: int) -> bytes:
        want = samples * 2 * 4
        data = b""
        while len(data) < want:
            part = self.proc.stdout.read(want - len(data))
            if not part:
                break
            data += part
        return data

    def close(self) -> str:
        err = b""
        try:
            self.proc.stdout.close()
        except OSError:
            pass
        try:
            self.proc.terminate()
        except OSError:
            pass
        try:
            err = self.proc.stderr.read() or b""
            self.proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            pass
        return err.decode("utf-8", "replace")[-400:]


_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class _Writer:
    """Writes floats to a FLAC file (16-bit, lossless)."""

    def __init__(self, path: str):
        self.path = path
        args = [get_tools().ffmpeg, "-v", "error", "-y", "-f", "f32le", "-ar", str(SAMPLE_RATE), "-ac", "2", "-i", "-",
                "-c:a", "flac", "-sample_fmt", "s16", "-compression_level", "3", "-f", "flac", path]
        self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=_NO_WINDOW)

    def write(self, data) -> None:
        self.proc.stdin.write(data.tobytes())

    def close(self) -> None:
        try:
            self.proc.stdin.close()
        finally:
            self.proc.wait(timeout=120)

    def abort(self) -> None:
        try:
            self.proc.kill()
        except OSError:
            pass


class Cancelled(Exception):
    pass


def split_sound(source: str, start: float, end: float, out_dir: Path, engine: Callable, *,
                progress: Optional[Callable[[float], None]] = None, cancel: Optional[threading.Event] = None,
                mode: str = "fast") -> float:
    """Split ``source`` from ``start`` to ``end`` seconds into ``out_dir/<layer>.flac``. Returns the seconds it took.

    ``engine`` takes a (2, CHUNK) float array (and ``fast=``) and returns (3, 2, CHUNK). Raises :class:`Cancelled` when stopped.
    """
    import numpy as np
    t0 = time.time()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    duration = max(0.05, end - start)
    total = int(round(duration * SAMPLE_RATE))
    step = STEPS[mode if mode in STEPS else "fast"]
    parts = {k: str(out_dir / (k + ".flac.part")) for k in LAYERS}
    reader = _Reader(source, start, duration)
    writers = {k: _Writer(parts[k]) for k in LAYERS}
    window = np.ones(CHUNK, dtype=np.float32)
    window[:FADE] = np.linspace(0.0, 1.0, FADE, dtype=np.float32)
    window[-FADE:] = np.linspace(1.0, 0.0, FADE, dtype=np.float32)
    acc = np.zeros((3, 2, CHUNK), dtype=np.float32)
    cnt = np.zeros(CHUNK, dtype=np.float32)
    inbuf = np.zeros((2, 0), dtype=np.float32)
    eof = False
    done = 0                                  # samples written
    first = True
    try:
        while True:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            while inbuf.shape[1] < CHUNK and not eof:
                raw = reader.read(CHUNK - inbuf.shape[1])
                if not raw:
                    eof = True
                    break
                arr = np.frombuffer(raw[: len(raw) // 8 * 8], dtype=np.float32).reshape(-1, 2).T
                inbuf = np.concatenate([inbuf, arr], axis=1)
            have = inbuf.shape[1]
            if have == 0:
                break
            seg = min(CHUNK, have)
            chunk = inbuf[:, :seg]
            if seg < CHUNK:
                mode = "reflect" if seg > CHUNK // 2 else "constant"
                chunk = np.pad(chunk, ((0, 0), (0, CHUNK - seg)), mode=mode)
            out = engine(np.ascontiguousarray(chunk, dtype=np.float32), fast=(mode != "best"))
            last = eof and have <= step
            w = window.copy()
            if first:
                w[:FADE] = 1.0
            if last:
                w[-FADE:] = 1.0
            first = False
            acc[:, :, :seg] += out[:, :, :seg] * w[:seg]
            cnt[:seg] += w[:seg]
            emit = seg if last else min(step, seg)
            final = acc[:, :, :emit] / np.maximum(cnt[:emit], 1e-6)
            np.clip(final, -1.0, 1.0, out=final)
            for k, name in enumerate(LAYERS):
                writers[name].write(np.ascontiguousarray(final[k].T, dtype=np.float32))
            done += emit
            if progress:
                progress(min(0.999, done / max(total, 1)))
            if last:
                break
            acc[:, :, : CHUNK - step] = acc[:, :, step:]
            acc[:, :, CHUNK - step:] = 0.0
            cnt[: CHUNK - step] = cnt[step:]
            cnt[CHUNK - step:] = 0.0
            inbuf = inbuf[:, step:]
        err = reader.close()
        for w_ in writers.values():
            w_.close()
        if done == 0:
            raise EditForgeError("There is no sound to split in that part of the video.", err or "Choose a part that has sound.")
        for name in LAYERS:
            os.replace(parts[name], str(out_dir / (name + ".flac")))
    except BaseException:
        reader.close()
        for w_ in writers.values():
            w_.abort()
        for p in parts.values():
            try:
                os.remove(p)
            except OSError:
                pass
        raise
    return time.time() - t0


# --------------------------------------------------------------------------------------------- the store and the job
class SplitStore:
    """Where the finished layers are kept: ``stems/<video fingerprint>/<start ms>-<end ms>/<layer>.flac``."""

    def __init__(self, home: Optional[Path] = None):
        self.root = stems_dir(home)

    def folder(self, fp: str, start: float, end: float) -> Path:
        return self.root / fp / f"{int(round(start * 1000)):09d}-{int(round(end * 1000)):09d}"

    def find(self, fp: str, start: float, end: float) -> Optional[Dict[str, Any]]:
        """A finished split that covers [start, end], or None. The smallest one that fits wins."""
        base = self.root / fp
        best: Optional[Dict[str, Any]] = None
        if not base.is_dir():
            return None
        for d in base.iterdir():
            meta = self._meta(d)
            if not meta or meta.get("model") != MODEL_ID:
                continue
            a, b = float(meta["start"]), float(meta["end"])
            if a <= start + 0.01 and b >= end - 0.01 and all((d / (k + ".flac")).is_file() for k in LAYERS):
                if best is None or (b - a) < (best["end"] - best["start"]):
                    best = {"start": a, "end": b, "dir": str(d), "id": f"{fp}/{d.name}", "mode": meta.get("mode", "fast"),
                            "files": {k: str(d / (k + ".flac")) for k in LAYERS}}
        return best

    def all(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for fp_dir in sorted(self.root.iterdir()) if self.root.is_dir() else []:
            if not fp_dir.is_dir():
                continue
            for d in sorted(fp_dir.iterdir()):
                meta = self._meta(d)
                if meta:
                    size = sum(f.stat().st_size for f in d.glob("*.flac"))
                    out.append({"id": f"{fp_dir.name}/{d.name}", "fp": fp_dir.name, "start": meta["start"], "end": meta["end"],
                                "bytes": size, "dir": str(d), "when": meta.get("when", 0)})
        return out

    def resolve(self, split_id: str) -> Optional[Dict[str, Any]]:
        """Files of a stored split by its id (never accepts paths that escape the store)."""
        parts = str(split_id or "").split("/")
        if len(parts) != 2 or any(not p or p in (".", "..") or "\\" in p for p in parts):
            return None
        d = self.root / parts[0] / parts[1]
        meta = self._meta(d)
        if not meta or not all((d / (k + ".flac")).is_file() for k in LAYERS):
            return None
        return {"id": split_id, "start": float(meta["start"]), "end": float(meta["end"]), "dir": str(d),
                "files": {k: str(d / (k + ".flac")) for k in LAYERS}}

    def delete(self, split_id: str) -> int:
        info = self.resolve(split_id)
        if not info:
            return 0
        size = sum(f.stat().st_size for f in Path(info["dir"]).glob("*"))
        shutil.rmtree(info["dir"], ignore_errors=True)
        try:
            Path(info["dir"]).parent.rmdir()
        except OSError:
            pass
        return size

    @staticmethod
    def _meta(d: Path) -> Optional[Dict[str, Any]]:
        try:
            data = json.loads((d / "split.json").read_text(encoding="utf-8"))
            return data if isinstance(data, dict) and "start" in data and "end" in data else None
        except (OSError, ValueError):
            return None

    def write_meta(self, d: Path, start: float, end: float, seconds: float, mode: str) -> None:
        atomic_write_json(Path(d) / "split.json", {"model": MODEL_ID, "start": start, "end": end, "seconds": round(seconds, 2),
                                                   "mode": mode, "when": int(time.time()), "version": __version__})


class Splitter:
    """Downloads the model when needed and splits sound in a background thread, one video at a time."""

    def __init__(self, home: Optional[Path] = None, engine_factory: Optional[Callable[[str], Callable]] = None,
                 model_path: Optional[str] = None):
        self.home = Path(home) if home else home_dir()
        self.store = SplitStore(self.home)
        self.model_path = Path(model_path) if model_path else models_dir(self.home) / MODEL_FILE
        self.engine_factory = engine_factory or (lambda p: Engine(p))
        self.download_prefix = DOWNLOAD_PREFIX
        self.speed_file = self.home / "split-speed.json"
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._engine: Optional[Callable] = None
        self.job: Dict[str, Any] = {"state": "idle"}

    # ---- what can be done right now
    def model_ready(self) -> bool:
        try:
            return self.model_path.is_file() and self.model_path.stat().st_size > 1_000_000 and (not MODEL_SIZE or self.model_path.stat().st_size == MODEL_SIZE)
        except OSError:
            return False

    def speed(self, mode: str = "fast") -> float:
        """Seconds of sound split per second of waiting, measured on this computer. Until the first split it is a guess
        from the number of processor threads (the model is slow: about 0.4 s of sound per second per thread)."""
        guess = 0.4 * _threads() * (1.0 if mode == "fast" else 0.3)
        try:
            data = json.loads(self.speed_file.read_text(encoding="utf-8"))
            return max(0.02, float(data.get(mode) or (data.get("fast", 0) * (1.0 if mode == "fast" else 0.3)) or guess))
        except (OSError, ValueError, TypeError, AttributeError):
            return max(0.02, guess)

    def info(self, source: str, start: float, end: float) -> Dict[str, Any]:
        """Everything the page needs to show the Split button: is it possible, ready, how long will it take."""
        fp = file_fingerprint(source)
        hit = self.store.find(fp, start, end)
        out: Dict[str, Any] = {"available": available(), "model": self.model_ready(), "model_mb": round(MODEL_SIZE / 1e6) if MODEL_SIZE else 76,
                               "split": hit and {"id": hit["id"], "start": hit["start"], "end": hit["end"]}, "job": self.status()}
        secs = max(0.0, end - start)
        out["estimate_s"] = int(round(secs / self.speed("fast")))
        out["estimate_best_s"] = int(round(secs / self.speed("best")))
        return out

    def status(self) -> Dict[str, Any]:
        with self._lock:
            job = dict(self.job)
        if job.get("state") == "running" and job.get("t0"):
            p = float(job.get("progress") or 0)
            elapsed = time.time() - job["t0"]
            job["eta_s"] = int(elapsed * (1 - p) / p) if p > 0.02 else None
            job["elapsed_s"] = int(elapsed)
        job.pop("t0", None)
        return job

    # ---- starting and stopping
    def start(self, source: str, start: float, end: float, mode: str = "fast") -> Dict[str, Any]:
        if not available():
            raise EditForgeError("The sound splitter is not part of this copy of the program.",
                                 "Install the newest version of Nyenz Edit, or run:  pip install onnxruntime numpy")
        info = probe_media(source)
        if not info.has_audio:
            raise EditForgeError("This video has no sound to split.", "Choose a video that has sound.")
        start = max(0.0, float(start))
        end = min(float(info.duration), float(end)) if end else float(info.duration)
        if end - start < 0.5:
            raise EditForgeError("That part is too short to split.", "Choose a part that is at least a second long.")
        fp = file_fingerprint(source)
        with self._lock:
            if self.job.get("state") in ("downloading", "running"):
                return dict(self.job)
            hit = self.store.find(fp, start, end)
            if hit and not (mode == "best" and hit["mode"] != "best"):
                self.job = {"state": "ready", "progress": 1.0, "error": "", "id": hit["id"]}
                return dict(self.job)
            self._cancel.clear()
            self.job = {"state": "downloading" if not self.model_ready() else "running", "progress": 0.0, "error": "", "t0": time.time(),
                        "source": source, "start": start, "end": end}
        threading.Thread(target=self._run, args=(source, fp, start, end, mode if mode in STEPS else "fast"), name="editforge-split", daemon=True).start()
        return self.status()

    def cancel(self) -> None:
        self._cancel.set()

    def _set(self, **fields: Any) -> None:
        with self._lock:
            self.job.update(fields)

    # ---- the work
    def _run(self, source: str, fp: str, start: float, end: float, mode: str) -> None:
        out_dir = self.store.folder(fp, start, end)
        try:
            if not self.model_ready():
                self._download_model()
            if self._cancel.is_set():
                raise Cancelled()
            self._set(state="running", progress=0.0, t0=time.time())
            if self._engine is None:
                self._engine = self.engine_factory(str(self.model_path))
            seconds = split_sound(source, start, end, out_dir, self._engine, progress=lambda p: self._set(progress=p), cancel=self._cancel,
                                  mode=mode)
            self.store.write_meta(out_dir, start, end, seconds, mode)
            self._remember_speed(mode, (end - start) / max(seconds, 0.01))
            self._set(state="ready", progress=1.0, error="", id=f"{fp}/{out_dir.name}")
        except Cancelled:
            self._discard(out_dir)
            self._set(state="idle", progress=0.0, error="")
        except EditForgeError as exc:
            self._discard(out_dir)
            self._set(state="failed", error=f"{exc.message} {exc.fix}".strip())
        except Exception as exc:  # noqa: BLE001 - never let the thread die silently
            self._discard(out_dir)
            self._set(state="failed", error="The sound could not be split: " + str(exc)[:200])

    @staticmethod
    def _discard(out_dir: Path) -> None:
        """Remove what an unfinished split left behind, but never an earlier finished split of the same part."""
        if (Path(out_dir) / "split.json").exists():
            for f in Path(out_dir).glob("*.part"):
                try:
                    f.unlink()
                except OSError:
                    pass
        else:
            shutil.rmtree(out_dir, ignore_errors=True)

    def _remember_speed(self, mode: str, x: float) -> None:
        try:
            try:
                data = json.loads(self.speed_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            data[mode] = round(x, 3)
            atomic_write_json(self.speed_file, data)
        except OSError:
            pass

    def _download_model(self) -> None:
        url = MODEL_URL
        if not url.startswith(self.download_prefix) or not MODEL_SHA256:
            raise EditForgeError("The sound-splitting model is not available yet.", "Update Nyenz Edit and try again.")
        part = self.model_path.with_suffix(".part")
        h = hashlib.sha256()
        got = 0
        try:
            req = urllib.request.Request(url, headers={"User-Agent": f"EditForge/{__version__}"})
            with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as fh:
                total = MODEL_SIZE or int(r.headers.get("Content-Length") or 0)
                while True:
                    if self._cancel.is_set():
                        raise Cancelled()
                    chunk = r.read(1024 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
                    h.update(chunk)
                    got += len(chunk)
                    if total:
                        self._set(progress=min(0.99, got / total))
            if MODEL_SIZE and got != MODEL_SIZE:
                raise ValueError(f"the download has the wrong size ({got:,} instead of {MODEL_SIZE:,} bytes)")
            if h.hexdigest() != MODEL_SHA256:
                raise ValueError("the download's fingerprint does not match")
            os.replace(part, self.model_path)
        except Cancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            raise EditForgeError("The sound-splitting model could not be downloaded.", f"Check your internet and try again ({str(exc)[:120]}).")
        finally:
            try:
                part.unlink()
            except OSError:
                pass
