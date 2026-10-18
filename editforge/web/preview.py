"""Light preview copies for the web page player.

Browsers can only play some video types (mostly H.264/AAC in MP4, or WebM). When the
page cannot play a file, or the file is very heavy, we make a small 480p copy that
plays and seeks smoothly. The copy is ONLY for watching in the page: finished videos
are always made from the original file, so quality is not affected.

Films often come as .mkv with H.264 picture and Dolby sound. The picture is fine for a browser,
only the box and the sound are not, so first a *quick copy* is made: the picture is copied as it is
and only the sound is changed. That takes seconds instead of minutes, so the video plays (with sound)
almost at once. The 480p light copy is then made in the background and replaces the quick copy.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from ..core.errors import EditForgeError
from ..core.ffmpeg import friendly_ffmpeg_error, run_ffmpeg
from ..core.fingerprint import file_fingerprint
from ..core.media import probe_media

PREVIEW_HEIGHT = 480


class PreviewMaker:
    """Makes preview copies in background threads and remembers their progress."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._cancel = threading.Event()
        for stale in self.folder.glob("*.part"):
            try:
                stale.unlink()
            except OSError:
                pass

    def path_for(self, source: str) -> Path:
        return self.folder / (file_fingerprint(source) + f".{PREVIEW_HEIGHT}p.mp4")

    def quick_path_for(self, source: str) -> Path:
        return self.folder / (file_fingerprint(source) + ".quick.mp4")

    def status(self, source: str) -> Dict[str, Any]:
        """Return {'state': 'none'|'running'|'ready'|'failed', 'progress': 0..1, 'error': str}."""
        out = self.path_for(source)
        with self._lock:
            job = dict(self._jobs.get(str(out), {}))
        if out.is_file() and out.stat().st_size > 0:
            return {"state": "ready", "progress": 1.0, "error": ""}
        if job.get("state") == "running":
            q = self.quick_path_for(source)
            job["quick"] = q.is_file() and q.stat().st_size > 0
            return job
        if job.get("state") == "failed":
            return job
        return {"state": "none", "progress": 0.0, "error": ""}

    def start(self, source: str, quick: bool = False) -> Dict[str, Any]:
        """Start making the preview copy unless it exists or is already being made.

        ``quick=True``: first make a quick copy (picture copied, sound made playable) to watch at once.
        """
        info = probe_media(source)
        if not info.has_video:
            raise EditForgeError("This file has no picture, so it does not need a preview copy.", "Just press play.")
        out = self.path_for(source)
        st = self.status(source)
        if st["state"] in ("running", "ready"):
            return st
        with self._lock:
            if self._jobs.get(str(out), {}).get("state") == "running":
                return dict(self._jobs[str(out)])
            self._jobs[str(out)] = {"state": "running", "progress": 0.0, "error": ""}
        threading.Thread(target=self._both, args=(source, out, info.duration, info.has_audio, quick), name="editforge-preview", daemon=True).start()
        return {"state": "running", "progress": 0.0, "error": ""}

    def shutdown(self) -> None:
        self._cancel.set()

    def _set(self, out: Path, **fields: Any) -> None:
        with self._lock:
            self._jobs.setdefault(str(out), {"state": "running", "progress": 0.0, "error": ""}).update(fields)

    def _both(self, source: str, out: Path, duration: float, has_audio: bool, quick: bool) -> None:
        q = self.quick_path_for(source)
        if quick and not (q.is_file() and q.stat().st_size > 0):
            part = str(q) + ".part"
            args = ["-y", "-i", source, "-map", "0:v:0", "-c:v", "copy"]
            args += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "192k", "-ac", "2"] if has_audio else ["-an"]
            try:
                res = run_ffmpeg(args + ["-f", "mp4", part], cancel=self._cancel)
                if res.ok:
                    os.replace(part, q)
            except Exception:  # noqa: BLE001 - the light copy below is still made
                pass
            finally:
                if os.path.exists(part):
                    try:
                        os.remove(part)
                    except OSError:
                        pass
        self._make(source, out, duration, has_audio)
        if out.is_file():
            try:
                q.unlink()                     # the light copy replaces the quick copy (saves disk space)
            except OSError:
                pass

    def _make(self, source: str, out: Path, duration: float, has_audio: bool) -> None:
        part = str(out) + ".part"
        # A keyframe every half second makes jumping around in the player quick and exact.
        # The fast scaler saves about a fifth of the time; at 480p it looks the same.
        args = ["-y", "-i", source, "-map", "0:v:0", "-vf", f"scale=-2:{PREVIEW_HEIGHT}:flags=fast_bilinear", "-c:v", "libx264",
                "-preset", "veryfast", "-crf", "27", "-g", "12", "-pix_fmt", "yuv420p"]
        args += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "96k", "-ac", "2"] if has_audio else ["-an"]
        args += ["-movflags", "+faststart", "-f", "mp4", part]

        def on_progress(d: Dict[str, str]) -> None:
            try:
                t = float(d.get("out_time_us", "0")) / 1e6
            except ValueError:
                return
            self._set(out, progress=max(0.0, min(0.99, t / max(duration, 0.001))))

        try:
            res = run_ffmpeg(args, on_progress=on_progress, cancel=self._cancel)
            if res.ok:
                os.replace(part, out)
                self._set(out, state="ready", progress=1.0, error="")
            else:
                message, fix = friendly_ffmpeg_error(res.stderr_tail, res.returncode)
                self._set(out, state="failed", error=f"{message} {fix}".strip())
        except Exception as exc:  # never let the thread die silently
            self._set(out, state="failed", error=str(exc)[:300])
        finally:
            if os.path.exists(part):
                try:
                    os.remove(part)
                except OSError:
                    pass
