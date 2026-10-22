"""The local web page: a small HTTP server built on the Python standard library.

Safety rules:
* listens on 127.0.0.1 (this computer only) unless you explicitly choose another address,
* every request must carry a random per-run token (so other web pages cannot talk to it),
* the Host and Origin headers are checked (protects against DNS-rebinding and cross-site requests),
* the page never inserts user text as HTML (it uses textContent) and sends a strict Content-Security-Policy,
* uploaded names are cleaned, files live in their own folder, and every path is checked to stay inside its folder.
"""

from __future__ import annotations

import json
import math
import mimetypes
import os
import re
import secrets
import shutil
import sys
import threading
import time
import unicodedata
import urllib.parse
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..api import plan_text, prepare, read_script, validate_script
from ..core.cache import atomic_write_json, home_dir
from ..core.errors import EditForgeError
from ..core.ffmpeg import run_ffmpeg
from ..core.fingerprint import file_fingerprint
from ..core.media import probe_media
from ..jobs.manager import JobManager
from ..jobs.store import JobStore
from ..presets.presets import PRESETS
from ..version import __version__
from . import choose as choose_mod
from .preview import PreviewMaker
from .. import combine as combine_mod
from .. import project as project_mod
from .. import clipsets as clip_mod

STATIC_DIR = Path(__file__).parent / "static"
JS, CSS = "application/javascript; charset=utf-8", "text/css; charset=utf-8"
STATIC_FILES = {"ui.css": CSS, "ui.js": JS, "app.js": JS, "workshop.js": JS, "editor.js": JS, "steps.js": JS, "layers.js": JS, "frame.js": JS, "combine.js": JS,
                "home.js": JS, "logo.svg": "image/svg+xml", "logo.png": "image/png"}
_STATIC: Dict[str, Tuple[int, bytes, bytes, str]] = {}          # name -> (mtime, body, gzipped body, etag)
_STATIC_REF = re.compile(r'(/static/([\w.]+))(?=")')


def static_file(name: str) -> Tuple[bytes, bytes, str]:
    """A file of the page, its gzipped form and a short content hash (made once, again only if the file changes)."""
    import gzip
    import hashlib
    path = STATIC_DIR / name
    mtime = path.stat().st_mtime_ns
    hit = _STATIC.get(name)
    if hit is None or hit[0] != mtime:
        body = path.read_bytes()
        packed = gzip.compress(body, 9, mtime=0) if not name.endswith(".png") else b""
        hit = (mtime, body, packed, hashlib.sha256(body).hexdigest()[:16])
        _STATIC[name] = hit
    return hit[1], hit[2], hit[3]


def versioned(html: str) -> str:
    """Give every /static/ link in a page its content hash (?v=...), so the window keeps the file until it really changes."""
    def fix(m: "re.Match[str]") -> str:
        if m.group(2) not in STATIC_FILES:
            return m.group(1)
        return m.group(1) + "?v=" + static_file(m.group(2))[2]
    return _STATIC_REF.sub(fix, html)
#: what browsers (Chrome, Edge, Brave, Firefox) play inside a page without help
BROWSER_VIDEO = {"h264", "vp8", "vp9", "av1"}
BROWSER_AUDIO = {"aac", "mp3", "opus", "vorbis", "flac"}
BROWSER_CONTAINERS = {".mp4", ".m4v", ".mov", ".webm"}
PAGES = {"/": "home.html", "/clips": "workshop.html", "/edit": "editor.html", "/combine": "combine.html", "/advanced": "index.html"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"}
SUBTITLE_EXTS = {".srt", ".vtt"}
MAX_SUBTITLE_BYTES = 5 * 1024 * 1024
PROJECT_ID = r"[0-9a-f]{12}"
MAX_RECIPES = 100
MAX_CLIPS = 200
MIN_CLIP_SECONDS = 0.04
CLIP_QUALITIES = {"best": "best", "high": "high", "small": "medium"}
MAX_JSON = 2 * 1024 * 1024
MAX_UPLOAD = 20 * 1024 ** 3
SAFE_NAME = re.compile(r"[^\w.\- ()\[\]]", re.UNICODE)
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")


def clean_filename(name: str, default: str = "upload") -> str:
    """A file name that is safe to store: no folders, no odd characters, no leading dots, limited length."""
    name = unicodedata.normalize("NFC", urllib.parse.unquote(name or ""))
    name = name.replace("\\", "/").split("/")[-1]
    name = SAFE_NAME.sub("_", name).strip(" .")
    if not name:
        name = default
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    stem = stem[:100] or default
    return stem + ("." + ext[:10] if ext else "")


def playback_check(path: str, info: Dict[str, Any]) -> Tuple[bool, str]:
    """Can a browser play this file directly, with picture AND sound? Returns (yes, reason when not).

    Films often come as .mkv with Dolby sound (AC-3, E-AC-3, DTS): the picture may play but the
    sound is silent, so the page makes a light copy with normal sound for watching.
    """
    ext = Path(path).suffix.lower()
    if info.get("has_audio") and info.get("audio_codec") not in BROWSER_AUDIO:
        return False, f"browsers cannot play {str(info.get('audio_codec') or 'this').upper()} sound"
    if info.get("has_video"):
        if ext not in BROWSER_CONTAINERS:
            return False, f"browsers cannot open {ext or 'this kind of'} files"
        if info.get("video_codec") not in BROWSER_VIDEO or info.get("pix_fmt") not in ("yuv420p", "yuvj420p"):
            return False, f"browsers cannot show {info.get('video_codec') or 'this'} video"
    return True, ""


_FEATURES: Dict[str, bool] = {}


def quick_features() -> Dict[str, bool]:
    """Which add-ons are installed, WITHOUT loading them (loading OpenCV and Whisper takes seconds on a cold start).

    They are loaded only when face tracking or captions are really used.
    """
    if not _FEATURES:
        import importlib.util

        def has(mod: str) -> bool:
            try:
                return importlib.util.find_spec(mod) is not None
            except (ImportError, ValueError):
                return False

        cv = has("cv2") and _opencv_major() < 5           # OpenCV 5 dropped the face finder EditForge uses
        _FEATURES.update({"captions": has("faster_whisper"), "faces": cv, "objects": cv and has("numpy")})
    return dict(_FEATURES)


def _opencv_major() -> int:
    try:
        from importlib import metadata
    except ImportError:  # pragma: no cover
        return 4
    for dist in ("opencv-python-headless", "opencv-python", "opencv-contrib-python", "opencv-contrib-python-headless"):
        try:
            return int(metadata.version(dist).split(".")[0])
        except Exception:  # noqa: BLE001 - not installed under this name (or no metadata in the app): try the next
            continue
    return 4


def examples_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "examples"


class AppState:
    """Everything the request handlers share."""

    def __init__(self, host: str, port: int, media_dir: Optional[str] = None, home: Optional[Path] = None,
                 app_mode: bool = False):
        self.host, self.port = host, port
        # app_mode: the real app (desktop or `editforge web`): light copies start as soon as a video is added,
        # old copies are cleaned up, and updates are looked for. Tests leave it off.
        self.app_mode = app_mode
        self.token = secrets.token_hex(16)
        self.home = Path(home) if home else home_dir()
        self.uploads = self.home / "uploads"
        self.outputs = self.home / "outputs"
        self.media = Path(media_dir).resolve() if media_dir else self.home / "media"
        for d in (self.uploads, self.outputs, self.media):
            d.mkdir(parents=True, exist_ok=True)
        self.uploads, self.outputs, self.media = self.uploads.resolve(), self.outputs.resolve(), self.media.resolve()
        self.store = JobStore(self.home / "jobs.sqlite3")
        self.previews = PreviewMaker(self.home / "previews")
        self.projects = self.home / "projects"
        self.thumbs = self.home / "thumbs"
        self.clipsets = self.home / "clipsets"
        for d in (self.projects, self.thumbs, self.clipsets):
            d.mkdir(parents=True, exist_ok=True)
        self.scenes = clip_mod.SceneFinder(self.home / "scenes", self.fingerprint)
        self.recipes_file = self.home / "recipes.json"
        self._fp_cache: Dict[Tuple[str, int, int], str] = {}
        self._thumb_slots = threading.Semaphore(3)
        self._lock = threading.Lock()
        # Finished videos can be opened again as a new source ("save, then edit the result").
        roots = [str(self.uploads), str(self.media), str(self.outputs)]
        if examples_dir().exists():
            roots.append(str(examples_dir().resolve()))
        self.allowed_roots = roots
        self.manager = JobManager(self.store, allowed_roots=roots, base_dir=str(self.media))
        # Linked files: videos used where they are on the computer (no copy). Only files the person chose
        # in the file window are linked, and only those exact files may be read (each is added to allowed_roots).
        self.links_file = self.home / "links.json"
        self.links: Dict[str, Dict[str, Any]] = {}
        self.last_dir = ""
        self._load_links()
        self.chooser = choose_mod.choose_file          # replaced in the tests
        self.quit_app = lambda: None                   # the desktop app closes itself here (after an update)
        from ..updater import Updater
        self.updater = Updater(self.home)
        from ..splitter import Splitter
        self.splitter = Splitter(self.home)            # splits a video's sound into voices / music / effects (nothing is loaded until used)
        if app_mode:
            self.updater.clean()
            self.updater.check_in_background()
            threading.Thread(target=self.auto_clean, name="editforge-clean", daemon=True).start()

    # ---- linked files ------------------------------------------------------------------------------
    def _load_links(self) -> None:
        try:
            data = json.loads(self.links_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        self.last_dir = str(data.get("last_dir") or "")
        for lid, e in (data.get("files") or {}).items():
            if re.fullmatch(r"[0-9a-f]{12}", str(lid)) and isinstance(e, dict) and e.get("path"):
                self.links[lid] = {"path": str(e["path"]), "name": str(e.get("name") or Path(str(e["path"])).name),
                                   "size": int(e.get("size") or 0), "mtime": float(e.get("mtime") or 0), "added": float(e.get("added") or 0)}
                self._allow(self.links[lid]["path"])

    def _save_links(self) -> None:
        with self._lock:
            data = {"last_dir": self.last_dir, "files": self.links}
            atomic_write_json(self.links_file, data)

    def _allow(self, path: str) -> None:
        if path not in self.allowed_roots:
            self.allowed_roots.append(path)            # the job runner may read exactly this file

    def link_file(self, path: str) -> str:
        """Use a file where it is. Returns its file id 'l/<id>/<name>' (the same id if it was linked before)."""
        real = Path(path).expanduser().resolve()
        if not real.is_file():
            raise EditForgeError("I can't find that file.", "Choose it again.")
        st = real.stat()
        for lid, e in self.links.items():
            if e["path"] == str(real):
                e.update(size=st.st_size, mtime=st.st_mtime)
                self._save_links()
                return f"l/{lid}/{clean_filename(e['name'])}"
        lid = uuid.uuid4().hex[:12]
        self.links[lid] = {"path": str(real), "name": real.name, "size": st.st_size, "mtime": st.st_mtime, "added": time.time()}
        self.last_dir = str(real.parent)
        self._allow(str(real))
        self._save_links()
        return f"l/{lid}/{clean_filename(real.name)}"

    def link_path(self, ident: str) -> Optional[str]:
        """Where a linked file lives (for the page to show), or None."""
        m = re.fullmatch(r"l/([0-9a-f]{12})/[^/\\]+", ident or "")
        e = self.links.get(m.group(1)) if m else None
        return e["path"] if e else None

    def can_choose(self) -> bool:
        return os.name == "nt" or sys.platform == "darwin" or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

    def choose(self, kind: str) -> Optional[str]:
        """Open the computer's file window; returns the new file id, or None when cancelled."""
        try:
            path = self.chooser(kind if kind in choose_mod.KINDS else "video", self.last_dir or str(Path.home()))
        except choose_mod.Busy:
            raise EditForgeError("A file window is already open.", "Look for it behind the EditForge window.")
        return self.link_file(path) if path else None

    def after_add(self, ident: str) -> None:
        """A video was just added: start its light copy right away (app only), so it is ready when the Editor opens."""
        if not self.app_mode:
            return
        try:
            src = self.resolve_input(ident)
            info = probe_media(src)
            if info.has_video:
                view = self.media_view(src)
                if view["preview"]["state"] == "none":
                    self.start_preview(src, view)
        except Exception:  # noqa: BLE001 - only a head start; the page asks again when it needs it
            pass

    def start_preview(self, src: str, view: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        view = view or self.media_view(src)
        quick = (not view["plays_in_browser"]) and view.get("video_codec") in BROWSER_VIDEO and view.get("pix_fmt") in ("yuv420p", "yuvj420p")
        return self.previews.start(src, quick=quick)

    def allowed_hosts(self) -> List[str]:
        h = {f"127.0.0.1:{self.port}", f"localhost:{self.port}", f"[::1]:{self.port}", f"{self.host}:{self.port}"}
        return sorted(h)

    def resolve_input(self, ident: str) -> str:
        """Turn a file id from the page into a real path: 'u/<id>/<name>', 'o/<job>/<name>' or 'm/<name>' inside our
        folders, or 'l/<id>/<name>' for a file the person linked (chose in the file window)."""
        m = re.fullmatch(r"l/([0-9a-f]{12})/([^/\\]+)", ident or "")
        if m:
            e = self.links.get(m.group(1))
            if not e or clean_filename(e["name"]) != m.group(2):
                raise EditForgeError("That file choice is not valid.", "Choose the file again.")
            if not os.path.isfile(e["path"]):
                raise EditForgeError("I can't find that video where it was: " + e["path"], "It was moved or deleted. Choose it again.")
            return e["path"]
        m = re.fullmatch(r"([uo])/([0-9a-f]{12})/([^/\\]+)", ident or "")
        if m:
            root = self.uploads if m.group(1) == "u" else self.outputs
            path = root / m.group(2) / m.group(3)
        else:
            m = re.fullmatch(r"m/([^/\\]+)", ident or "")
            if not m:
                raise EditForgeError("That file choice is not valid.", "Pick a file from the list or upload one.")
            root, path = self.media, self.media / m.group(1)
        if ".." in path.name or path.name.startswith("."):
            raise EditForgeError("That file name is not allowed.", "Pick a file from the list.")
        try:
            real = path.resolve()
            real.relative_to(root)
        except (ValueError, OSError):
            raise EditForgeError("That file is outside the allowed folder.", "Pick a file from the list.")
        if not real.is_file():
            raise EditForgeError("I can't find that file any more.", "Upload it again.")
        return str(real)

    # ---- fingerprints and thumbnails --------------------------------------------------------------
    def fingerprint(self, path: str) -> str:
        st = os.stat(path)
        key = (path, st.st_size, st.st_mtime_ns)
        with self._lock:
            hit = self._fp_cache.get(key)
        if hit is None:
            hit = file_fingerprint(path)
            with self._lock:
                self._fp_cache[key] = hit
        return hit

    def media_view(self, src: str) -> Dict[str, Any]:
        """Facts about a file for the page: length, size, and whether it plays in a browser as it is."""
        info = probe_media(src).to_dict()
        info.pop("path", None)
        info["plays_in_browser"], info["playback_note"] = playback_check(src, info)
        info["preview"] = self.previews.status(src) if info["has_video"] else {"state": "none", "progress": 0.0, "error": ""}
        return info

    def thumbnail(self, src: str, at: float, height: int = 96) -> Optional[Path]:
        """A small JPEG of the picture at ``at`` seconds (made once, then kept). None if it cannot be made."""
        ms = max(0, int(round(at * 1000)))
        out = self.thumbs / (f"{self.fingerprint(src)}_{ms}.jpg" if height == 96 else f"{self.fingerprint(src)}_{ms}_h{height}.jpg")
        if out.is_file() and out.stat().st_size > 0:
            return out
        light = self.previews.path_for(src)          # the light copy decodes many times faster than a big original
        if light.is_file() and light.stat().st_size > 0:
            src = str(light)
        part = out.with_name(out.name + f".{uuid.uuid4().hex[:8]}.part")
        with self._thumb_slots:
            res = run_ffmpeg(["-y", "-ss", f"{ms / 1000:.3f}", "-i", src, "-map", "0:v:0", "-frames:v", "1", "-vf", f"scale=-2:{height}",
                              "-q:v", "6", "-f", "image2", str(part)], timeout=30)
        if res.ok and part.is_file() and part.stat().st_size > 0:
            os.replace(part, out)
            return out
        try:
            part.unlink()
        except OSError:
            pass
        return None

    # ---- projects ----------------------------------------------------------------------------------
    def project_path(self, pid: str) -> Path:
        if not re.fullmatch(PROJECT_ID, pid or ""):
            raise EditForgeError("That project id is not valid.", "Open the project from the list.")
        return self.projects / f"{pid}.json"

    def load_project(self, pid: str) -> Dict[str, Any]:
        path = self.project_path(pid)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise EditForgeError("I can't find that project any more.", "Open another project, or start a new one.")
        if not isinstance(data, dict):
            raise EditForgeError("That project file is damaged.", "Start a new project.")
        return data

    def save_project(self, pid: str, raw: Dict[str, Any], created: Optional[float] = None, old: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Clean and store a project. The source video must still exist (its length limits the pieces)."""
        src = self.resolve_input(str(raw.get("source") or ""))
        info = probe_media(src)
        proj = project_mod.clean_project(raw, info.duration)
        old = old or {}
        proj.update({"id": pid, "created": created or time.time(), "updated": time.time(),
                     "opened": float(old.get("opened") or time.time()), "trashed": float(old.get("trashed") or 0),
                     "src": {"name": Path(src).name, "size": os.path.getsize(src), "duration": round(info.duration, 3),
                             "has_video": info.has_video, "path": self.link_path(proj["source"]) or ""}})
        atomic_write_json(self.project_path(pid), proj)
        return proj

    def edit_project(self, pid: str, **fields: Any) -> Dict[str, Any]:
        """Change a few fields of a stored project without checking its video (it may be missing)."""
        proj = self.load_project(pid)
        proj.update(fields)
        atomic_write_json(self.project_path(pid), proj)
        return proj

    def all_projects(self) -> List[Dict[str, Any]]:
        out = []
        for f in self.projects.glob("*.json"):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(d, dict) and d.get("id") and isinstance(d.get("pieces"), list):
                    out.append(d)
            except (OSError, ValueError):
                continue
        return out

    def list_projects(self) -> List[Dict[str, Any]]:
        """The Home screen's cards: newest opened first. 'missing' when the video was moved or deleted."""
        out = []
        for d in self.all_projects():
            try:
                src = d.get("src") if isinstance(d.get("src"), dict) else {}
                try:
                    path = self.resolve_input(str(d["source"]))
                    missing = False
                    size, dur = os.path.getsize(path), float(src.get("duration") or 0)
                except EditForgeError:
                    path, missing = "", True
                    size, dur = int(src.get("size") or 0), float(src.get("duration") or 0)
                pieces = d["pieces"]
                out.append({"id": d["id"], "name": d["name"], "source": d["source"], "source_name": str(d["source"]).rsplit("/", 1)[-1],
                            "pieces": len(pieces), "updated": d.get("updated", 0), "opened": d.get("opened") or d.get("updated", 0),
                            "trashed": d.get("trashed") or 0, "missing": missing, "size": size, "duration": dur,
                            "length": round(project_mod.step_length(project_mod.clean_project(d, 1e9)["pieces"]), 3),
                            "steps": len(d.get("steps") or []), "has_video": bool(src.get("has_video", True)),
                            "linked": str(d["source"]).startswith("l/"), "where": self.link_path(str(d["source"])) or path,
                            "thumb_at": _thumb_time(pieces, dur)})
            except (KeyError, TypeError, ValueError):
                continue
        return sorted(out, key=lambda d: d["opened"], reverse=True)

    def delete_project(self, pid: str) -> bool:
        """Delete for good. A video copy that was sent in (not linked) and that no other project uses is deleted too."""
        try:
            proj = self.load_project(pid)
        except EditForgeError:
            return False
        try:
            self.project_path(pid).unlink()
        except OSError:
            return False
        m = re.fullmatch(r"u/([0-9a-f]{12})/[^/\\]+", str(proj.get("source") or ""))
        if m and m.group(1) not in self.used_uploads():
            shutil.rmtree(self.uploads / m.group(1), ignore_errors=True)
        return True

    def duplicate_project(self, pid: str) -> Dict[str, Any]:
        proj = self.load_project(pid)
        new = dict(proj, id=uuid.uuid4().hex[:12], name=(str(proj.get("name") or "Project")[:74] + " copy"), created=time.time(),
                   updated=time.time(), opened=time.time(), trashed=0)
        atomic_write_json(self.project_path(new["id"]), new)
        return new

    # ---- clip projects (phase 1: take clips out of a movie) ---------------------------------------
    def used_uploads(self) -> set:
        """Folders of sent-in copies that a project or a clip project still uses."""
        return {str(d.get("source")).split("/")[1] for d in self.all_projects() + self.all_clipsets()
                if str(d.get("source")).startswith("u/") and not d.get("freed")}

    def clipset_path(self, cid: str) -> Path:
        if not re.fullmatch(clip_mod.CLIPSET_ID, cid or ""):
            raise EditForgeError("That clip project id is not valid.", "Open it from Home.")
        return self.clipsets / f"{cid}.json"

    def load_clipset(self, cid: str) -> Dict[str, Any]:
        try:
            data = json.loads(self.clipset_path(cid).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise EditForgeError("I can't find that clip project any more.", "Open another one from Home.")
        if not isinstance(data, dict) or not isinstance(data.get("clips"), list):
            raise EditForgeError("That clip project file is damaged.", "Start a new clip project.")
        return data

    def all_clipsets(self) -> List[Dict[str, Any]]:
        out = []
        for f in self.clipsets.glob("*.json"):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(d, dict) and d.get("id") and isinstance(d.get("clips"), list):
                    out.append(d)
            except (OSError, ValueError):
                continue
        return out

    def source_duration(self, ident: str) -> Optional[float]:
        """Length of a project's video, or None when it is gone (moved, deleted, or freed)."""
        try:
            return probe_media(self.resolve_input(ident)).duration
        except EditForgeError:
            return None

    def save_clipset(self, cid: str, raw: Dict[str, Any], old: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        old = old or {}
        source = str(old.get("source") or raw.get("source") or "")
        dur = self.source_duration(source)
        if dur is None and not old:
            self.resolve_input(source)                       # raises the clear "can't find" message
        cs = clip_mod.clean_clipset(dict(raw, source=source), dur if dur is not None else 1e9)
        if dur is None:                                      # the movie is gone: only names may change, never the parts
            byid = {c["id"]: c for c in old.get("clips") or []}
            cs["clips"] = [dict(byid[c["id"]], name=c["name"]) for c in cs["clips"] if c["id"] in byid]
        olds = {c["id"]: c for c in old.get("clips") or [] if isinstance(c, dict)}
        for c in cs["clips"]:                                # "made" is only ever set here, by the app, never by the page
            c["made"] = (olds.get(c["id"]) or {}).get("made")
        now = time.time()
        src = old.get("src") if isinstance(old.get("src"), dict) else {}
        if dur is not None:
            path = self.resolve_input(source)
            info = probe_media(path)
            src = {"name": Path(path).name, "size": os.path.getsize(path), "duration": round(info.duration, 3),
                   "has_video": info.has_video, "path": self.link_path(source) or ""}
        cs.update({"id": cid, "kind": "clips", "created": float(old.get("created") or now), "updated": now,
                   "opened": float(old.get("opened") or now), "trashed": float(old.get("trashed") or 0),
                   "freed": float(old.get("freed") or 0), "src": src})
        atomic_write_json(self.clipset_path(cid), cs)
        return cs

    def edit_clipset(self, cid: str, **fields: Any) -> Dict[str, Any]:
        cs = self.load_clipset(cid)
        cs.update(fields)
        atomic_write_json(self.clipset_path(cid), cs)
        return cs

    def clip_state(self, cs: Dict[str, Any], clip: Dict[str, Any]) -> Dict[str, Any]:
        """Is this clip made? {'state': 'none'|'queued'|'running'|'done'|'failed'|'old', 'file': id, 'progress', 'error'}."""
        made = clip.get("made") or {}
        job = self.store.get(made["job"]) if made.get("job") else None
        if not job:
            return {"state": "none", "file": "", "progress": 0.0, "error": "", "size": 0}
        status = job.get("status")
        fid = self.output_id(job) or ""
        out = {"state": status if status in ("queued", "running", "done", "failed") else "failed", "file": fid,
               "progress": float(job.get("progress") or 0), "error": str(job.get("error") or ""), "size": 0}
        if status == "done" and not fid:
            out["state"] = "none"                            # the made file was removed in Exports
        if fid:
            try:
                out["size"] = os.path.getsize(self.resolve_input(fid))
            except (EditForgeError, OSError):
                pass
        if out["state"] == "done" and made.get("key") != clip_mod.clip_key(cs["source"], clip):
            out["state"] = "old"                             # changed after it was made
        return out

    def clipset_view(self, cs: Dict[str, Any]) -> Dict[str, Any]:
        info = None
        try:
            path = self.resolve_input(cs["source"])
            info = self.media_view(path)
            info["where"] = self.link_path(cs["source"]) or ""
        except EditForgeError:
            pass
        states = {c["id"]: self.clip_state(cs, c) for c in cs["clips"]}
        return {"clipset": cs, "info": info, "states": states}

    def list_clipsets(self) -> List[Dict[str, Any]]:
        out = []
        for d in self.all_clipsets():
            try:
                src = d.get("src") if isinstance(d.get("src"), dict) else {}
                clips = [c for c in d["clips"] if isinstance(c, dict) and c.get("parts")]
                states = [self.clip_state(d, c)["state"] for c in clips]
                missing = self.source_duration(str(d["source"])) is None
                first = clips[0]["parts"][0]["start"] if clips else 0.0
                made = [c for c, s in zip(clips, states) if s == "done"]
                thumb_file = self.clip_state(d, made[0])["file"] if made and missing else ""
                out.append({"id": d["id"], "kind": "clips", "name": d["name"], "source": d["source"],
                            "source_name": str(d["source"]).rsplit("/", 1)[-1], "clips": len(clips), "made": states.count("done"),
                            "length": round(sum(clip_mod.clip_length(c) for c in clips), 3), "updated": d.get("updated", 0),
                            "opened": d.get("opened") or d.get("updated", 0), "trashed": d.get("trashed") or 0,
                            "freed": d.get("freed") or 0, "missing": missing, "size": int(src.get("size") or 0),
                            "duration": float(src.get("duration") or 0), "has_video": bool(src.get("has_video", True)),
                            "thumb_at": round(first + 0.5, 2), "thumb_file": thumb_file})
            except (KeyError, TypeError, ValueError, IndexError):
                continue
        return sorted(out, key=lambda d: d["opened"], reverse=True)

    def delete_clipset(self, cid: str) -> bool:
        try:
            cs = self.load_clipset(cid)
            self.clipset_path(cid).unlink()
        except (EditForgeError, OSError):
            return False
        m = re.fullmatch(r"u/([0-9a-f]{12})/[^/\\]+", str(cs.get("source") or ""))
        if m and m.group(1) not in self.used_uploads():
            shutil.rmtree(self.uploads / m.group(1), ignore_errors=True)
        return True

    def make_clips(self, cid: str, ids: Optional[List[str]] = None) -> List[str]:
        """Start one export per clip that is not made yet (or changed since). Returns the ids of the clips started."""
        cs = self.load_clipset(cid)
        src = self.resolve_input(cs["source"])
        dur = probe_media(src).duration
        cs = self.save_clipset(cid, cs, cs)                  # clean it against the movie as it is now
        stem = Path(src).stem[:40]
        started = []
        for clip in cs["clips"]:
            if ids is not None and clip["id"] not in ids:
                continue
            st = self.clip_state(cs, clip)
            if st["state"] in ("done", "queued", "running"):
                continue
            proj = clip_mod.clip_project(cs, clip, dur)
            project_mod.plan_project(proj, src, stems_home=str(self.home))          # a clear message before anything starts
            name = clean_filename(f"{stem}_{clip['name']}").rsplit(".", 1)[0][:80]
            job = self.manager.submit(json.dumps(proj), src, "", {"kind": "project", "preview": False, "music_path": None,
                                                                 "output_dir": str(self.outputs), "stems_home": str(self.home),
                                                                 "output_name": name, "clipset": cid, "clip": clip["id"]})
            clip["made"] = {"job": job, "key": clip_mod.clip_key(cs["source"], clip)}
            started.append(clip["id"])
        if not started and not cs["clips"]:
            raise EditForgeError("There are no clips to make.", "Mark a part, then press Add clip.")
        atomic_write_json(self.clipset_path(cid), dict(cs, updated=time.time()))
        return started

    def free_movie(self, cid: str, delete_original: bool) -> Dict[str, Any]:
        """After every clip is made, the movie is no longer needed. A sent-in copy is deleted; a movie used where it is
        on the computer is deleted only when the person says so (delete_original)."""
        cs = self.load_clipset(cid)
        states = [self.clip_state(cs, c)["state"] for c in cs["clips"]]
        if not states or any(s != "done" for s in states):
            raise EditForgeError("Make every clip first.", "Press Make clips and wait until all of them are done.")
        others = [d for d in self.all_projects() + self.all_clipsets() if d.get("id") != cid and str(d.get("source")) == cs["source"]
                  and not d.get("trashed") and not d.get("freed")]
        if others:
            raise EditForgeError("Another project still uses this movie.", "Delete or finish that project first.")
        freed = 0
        try:
            path = self.resolve_input(cs["source"])
        except EditForgeError:
            path = ""
        if path:
            size = os.path.getsize(path)
            try:
                pv = [self.previews.path_for(path), self.previews.quick_path_for(path)]
            except OSError:
                pv = []
            if cs["source"].startswith("u/"):
                shutil.rmtree(self.uploads / cs["source"].split("/")[1], ignore_errors=True)
                freed += size
            elif cs["source"].startswith("l/"):
                if not delete_original:
                    return {"ask": True, "path": path, "size": size}
                try:
                    os.remove(path)
                except OSError as exc:
                    raise EditForgeError("The movie file could not be deleted: " + str(exc), "Close any program that plays it, then try again.")
                freed += size
            else:
                raise EditForgeError("This movie is not one the app may delete.", "Delete it yourself if you no longer need it.")
            for f in pv:
                if f.is_file():
                    freed += f.stat().st_size
                    _unlink(f)
        self.edit_clipset(cid, freed=time.time())
        return {"freed": freed}

    def made_clips(self) -> List[Dict[str, Any]]:
        """Every made clip of every clip project (to start an Edit project from one)."""
        out = []
        for cs in sorted(self.all_clipsets(), key=lambda d: d.get("opened") or 0, reverse=True):
            if cs.get("trashed"):
                continue
            for c in cs["clips"]:
                if not isinstance(c, dict) or not c.get("parts"):
                    continue
                st = self.clip_state(cs, c)
                if st["state"] in ("done", "old") and st["file"]:
                    out.append({"file": st["file"], "name": c.get("name") or "", "clipset": cs["id"], "clipset_name": cs.get("name") or "",
                                "length": round(clip_mod.clip_length(c), 3), "size": st["size"]})
        return out

    def final_videos(self) -> List[Dict[str, Any]]:
        """Phase 3: finished videos made from Edit projects (not quick previews, not clips)."""
        names = {d["id"]: d.get("name") for d in self.all_projects()}
        out = []
        for job in self.store.list(500):
            opts = job.get("options") if isinstance(job.get("options"), dict) else {}
            if opts.get("preview") or opts.get("clipset") or opts.get("kind") not in ("project", "combine"):
                continue
            fid = self.output_id(job)
            if not fid:
                continue
            f = Path(job["output_path"])
            res = job.get("result") if isinstance(job.get("result"), dict) else {}
            pid = str(opts.get("project_id") or "")
            out.append({"id": fid, "job": job["id"], "name": f.name, "size": f.stat().st_size, "duration": float(res.get("duration") or 0),
                        "when": float(job.get("updated") or job.get("created") or 0),
                        "project_id": pid if pid in names else "", "project_name": names.get(pid) or ""})
        return out

    def fork_project(self, pid: str, step: int) -> Dict[str, Any]:
        """A new project that starts again from one step (keeps the first pattern cut for another video)."""
        proj = self.load_project(pid)
        steps = proj.get("steps") if isinstance(proj.get("steps"), list) else []
        new = dict(proj, id=uuid.uuid4().hex[:12], created=time.time(), updated=time.time(), opened=time.time(), trashed=0)
        if 1 <= step <= len(steps):
            st = steps[step - 1]
            new.update(pieces=st.get("pieces") or [], rule=st.get("rule"), steps=steps[:step - 1])
        new["name"] = (str(proj.get("name") or "Project")[:66] + f" from step {step}")
        atomic_write_json(self.project_path(new["id"]), new)
        return new

    # ---- storage ------------------------------------------------------------------------------------
    def storage(self) -> Dict[str, Any]:
        def size(d: Path) -> int:
            total = 0
            for root, _dirs, files in os.walk(d):
                for f in files:
                    try:
                        total += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
            return total
        parts = {"uploads": self.uploads, "previews": self.previews.folder, "exports": self.outputs, "thumbs": self.thumbs,
                 "work": self.home / "work", "stems": self.splitter.store.root, "models": self.home / "models"}
        out = {k: size(v) for k, v in parts.items()}
        try:
            free = shutil.disk_usage(self.home).free
        except OSError:
            free = 0
        used_uploads = self.used_uploads()
        loose = sum(size(d) for d in self.uploads.iterdir() if d.is_dir() and d.name not in used_uploads) if self.uploads.exists() else 0
        return {"sizes": out, "total": sum(out.values()), "free": free, "home": str(self.home), "loose_uploads": loose,
                "linked": len(self.links)}

    def clean_storage(self, what: str) -> int:
        """Free space. Returns how many files or folders were removed."""
        n = 0
        if what == "previews":                   # light copies: made again when a video is opened
            busy = {Path(k).name for k, v in self.previews._jobs.items() if v.get("state") == "running"}
            for f in self.previews.folder.glob("*.mp4"):
                if f.name not in busy:
                    n += _unlink(f)
        elif what == "thumbs":
            for f in self.thumbs.glob("*.jpg"):
                n += _unlink(f)
        elif what == "uploads":                  # copies of videos sent in by dragging, that no project uses
            used = self.used_uploads()
            for d in list(self.uploads.iterdir()):
                if d.is_dir() and d.name not in used:
                    shutil.rmtree(d, ignore_errors=True)
                    n += 1
        elif what == "stems":                    # split sound layers that no saved project uses (made again when needed)
            used = {str((d.get("settings") or {}).get("mix_split") or "") for d in self.all_projects()}
            busy = self.splitter.status().get("state") in ("downloading", "running")
            for item in self.splitter.store.all():
                if item["id"] not in used and not busy:
                    self.splitter.store.delete(item["id"])
                    n += 1
        elif what == "work":
            if not any(j["status"] in ("queued", "running") for j in self.store.list(200)):
                for d in (self.home / "work").glob("*"):
                    shutil.rmtree(d, ignore_errors=True) if d.is_dir() else _unlink(d)
                    n += 1
        else:
            raise EditForgeError("Nothing to clean there.", "")
        return n

    def auto_clean(self, days: float = 30.0) -> None:
        """At start: remove light copies and pictures not used for a month, and sent-in copies no project uses
        that are older than a month. Saved projects keep their light copies."""
        try:
            cutoff = time.time() - days * DAY_SECONDS
            keep = set()
            for d in self.all_projects() + self.all_clipsets():
                try:
                    keep.add(self.fingerprint(self.resolve_input(str(d["source"]))))
                except Exception:  # noqa: BLE001
                    continue
            for f in self.previews.folder.glob("*.mp4"):
                if f.name.split(".")[0] not in keep and f.stat().st_mtime < cutoff:
                    _unlink(f)
            for f in self.thumbs.glob("*.jpg"):
                if f.stat().st_mtime < cutoff:
                    _unlink(f)
            used = self.used_uploads()
            for d in self.uploads.iterdir():
                if d.is_dir() and d.name not in used and d.stat().st_mtime < cutoff:
                    shutil.rmtree(d, ignore_errors=True)
        except Exception:  # noqa: BLE001 - cleaning must never stop the app
            pass

    def load_recipes(self) -> List[Dict[str, Any]]:
        try:
            data = json.loads(self.recipes_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return data if isinstance(data, list) else []

    def save_recipes(self, raw: Any) -> List[Dict[str, Any]]:
        """Saved patterns: 'in every group of N pick these positions, and give them these edits'."""
        if not isinstance(raw, list) or len(raw) > MAX_RECIPES:
            raise EditForgeError(f"You can keep at most {MAX_RECIPES} saved patterns.", "Delete one you no longer use.")
        out = []
        for r in raw:
            if not isinstance(r, dict):
                continue
            if r.get("type") == "steps":            # a whole stack of steps, to repeat on another video
                rules = [x for x in (project_mod.clean_rule(x) for x in (r.get("steps") or [])[:project_mod.MAX_STEPS]) if x and x["interval"] > 0]
                name = " ".join(str(r.get("name") or "").split())[:60]
                if name and rules:
                    out.append({"name": name, "type": "steps", "steps": rules})
                continue
            try:
                group = max(1, min(100, int(r.get("group", 1))))
                picks = sorted({int(x) for x in r.get("picks", []) if 1 <= int(x) <= group})
            except (TypeError, ValueError):
                continue
            name = " ".join(str(r.get("name") or "").split())[:60]
            if not name or not picks:
                continue
            look = project_mod.clean_piece(dict(r.get("look") or {}, start=0, end=10), 10.0) if isinstance(r.get("look"), dict) else None
            if look:
                look.pop("start"), look.pop("end"), look.pop("unit", None)
            out.append({"name": name, "group": group, "picks": picks, "look": look})
        atomic_write_json(self.recipes_file, out)
        return out

    def output_id(self, job: Dict[str, Any]) -> Optional[str]:
        """The file id of a finished job's video, or None if it is not there."""
        if job.get("status") != "done" or not job.get("output_path"):
            return None
        path = Path(job["output_path"])
        try:
            path.resolve().relative_to(self.outputs / job["id"])
        except (ValueError, OSError):
            return None
        return f"o/{job['id']}/{path.name}" if path.is_file() else None

    def list_files(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for job in self.store.list(60):
            opts = job.get("options") if isinstance(job.get("options"), dict) else {}
            ident = None if opts.get("preview") else self.output_id(job)      # quick previews are for watching, not for editing
            if ident:
                f = Path(job["output_path"])
                out.append({"id": ident, "name": f.name, "size": f.stat().st_size, "where": "made here", "audio": False})
        for d in sorted(self.uploads.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True) if self.uploads.exists() else []:
            if d.is_dir() and re.fullmatch(r"[0-9a-f]{12}", d.name):
                for f in d.iterdir():
                    if f.is_file():
                        out.append({"id": f"u/{d.name}/{f.name}", "name": f.name, "size": f.stat().st_size, "where": "uploaded",
                                    "audio": f.suffix.lower() in AUDIO_EXTS, "subtitles": f.suffix.lower() in SUBTITLE_EXTS})
        for lid, e in sorted(self.links.items(), key=lambda kv: kv[1]["added"], reverse=True):
            p = Path(e["path"])
            if p.is_file():
                ext = p.suffix.lower()
                out.append({"id": f"l/{lid}/{clean_filename(e['name'])}", "name": e["name"], "size": p.stat().st_size, "where": str(p.parent),
                            "linked": True, "audio": ext in AUDIO_EXTS, "subtitles": ext in SUBTITLE_EXTS})
        for f in sorted(self.media.iterdir()):
            if f.is_file() and not f.name.startswith("."):
                out.append({"id": f"m/{f.name}", "name": f.name, "size": f.stat().st_size, "where": "media folder",
                            "audio": f.suffix.lower() in AUDIO_EXTS, "subtitles": f.suffix.lower() in SUBTITLE_EXTS})
        return out


DAY_SECONDS = 24 * 3600


def _jobs_busy(state: "AppState") -> bool:
    try:
        return any(j["status"] in ("queued", "running") for j in state.store.list(200))
    except Exception:  # noqa: BLE001
        return False


def _float(value: Any, default: float) -> float:
    try:
        v = float(value)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _unlink(p: Path) -> int:
    try:
        p.unlink()
        return 1
    except OSError:
        return 0


def _thumb_time(pieces: List[Dict[str, Any]], duration: float) -> float:
    """A moment for the project's picture: a little into the first kept piece (not the black first frame)."""
    for p in pieces:
        if isinstance(p, dict) and not p.get("off"):
            try:
                a, b = float(p["start"]), float(p["end"])
                return round(min(b - 0.05, a + min(2.0, (b - a) / 2)), 2)
            except (KeyError, TypeError, ValueError):
                break
    return round(min(1.0, max(0.0, duration - 0.05)), 2)


class Handler(BaseHTTPRequestHandler):
    server_version = "EditForge"
    protocol_version = "HTTP/1.1"
    state: AppState

    # ---- plumbing ------------------------------------------------------------------------------
    def log_message(self, fmt: str, *args: Any) -> None:  # keep the console quiet
        if os.environ.get("EDITFORGE_WEB_LOG"):
            sys.stderr.write("web: " + fmt % args + "\n")

    def _headers(self, status: int, ctype: str, length: int, extra: Optional[Dict[str, str]] = None) -> None:
        head = {"Content-Type": ctype, "Content-Length": str(length), "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer", "Content-Security-Policy": CSP,
                "Cross-Origin-Resource-Policy": "same-origin"}
        head.update(extra or {})
        self.send_response(status)
        for k, v in head.items():
            self.send_header(k, v)
        self.end_headers()

    def _send(self, body: bytes, ctype: str, extra: Optional[Dict[str, str]] = None, packed: Optional[bytes] = None) -> None:
        """Send a page or file, gzipped when the window accepts it (scripts shrink to about a quarter)."""
        extra = dict(extra or {})
        if "gzip" in (self.headers.get("Accept-Encoding") or "") and len(body) > 1024:
            if packed is None:
                import gzip
                packed = gzip.compress(body, 6, mtime=0)
            if packed:
                body = packed
                extra.update({"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
        self._headers(200, ctype, len(body), extra)
        self.wfile.write(body)

    def _json(self, obj: Any, status: int = 200) -> None:
        body = json.dumps(obj, default=str).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def _error(self, status: int, message: str, fix: str = "", line: Optional[int] = None, details: str = "") -> None:
        self._json({"error": message, "fix": fix, "line": line, "details": details}, status)

    def _fail(self, exc: Exception) -> None:
        if isinstance(exc, EditForgeError):
            self._error(400, exc.message, exc.fix, exc.line, (exc.details or "")[-1500:])
        else:
            self._error(500, "Something unexpected went wrong.", "Try again. If it keeps happening, restart EditForge.", None, str(exc)[:300])

    def _guard(self, need_token: bool = True, query_token: bool = False) -> Optional[Dict[str, List[str]]]:
        """Check Host, Origin and token. Returns the parsed query, or None after sending an error."""
        st = self.state
        host = self.headers.get("Host", "")
        if host not in st.allowed_hosts():
            self._error(403, "This request is not allowed (unknown Host).", "Open the page from the address EditForge printed.")
            return None
        origin = self.headers.get("Origin")
        if origin is not None and origin != f"http://{host}":
            self._error(403, "This request came from another web page and was blocked.")
            return None
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        if need_token:
            given = self.headers.get("X-EditForge-Token") or (query.get("t", [""])[0] if query_token else "")
            if not secrets.compare_digest(given.encode(), st.token.encode()):
                self._error(403, "Missing or wrong security token.", "Reload the page.")
                return None
        return query

    def _read_json(self, allow_list: bool = False) -> Optional[Any]:
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            self._error(415, "Expected JSON.")
            return None
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_JSON:
            self._error(413, "That request is too large.")
            return None
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            self._error(400, "The request was not valid JSON.")
            return None
        if not isinstance(data, dict) and not (allow_list and isinstance(data, list)):
            self._error(400, "The request should be a JSON object.")
            return None
        return data

    # ---- routes --------------------------------------------------------------------------------
    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        try:
            if path in PAGES:
                if self._guard(need_token=False) is None:
                    return
                html = (STATIC_DIR / PAGES[path]).read_text(encoding="utf-8").replace("{{TOKEN}}", self.state.token).replace("{{VERSION}}", __version__)
                self._send(versioned(html).encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/favicon.ico":
                body = (STATIC_DIR / "logo.svg").read_bytes()
                self._headers(200, "image/svg+xml", len(body), {"Cache-Control": "max-age=86400"})
                self.wfile.write(body)
            elif path == "/api/hello":
                # No key needed: lets a second start of the app find this one instead of starting twice.
                self._json({"app": "EditForge", "version": __version__})
            elif path.startswith("/static/"):
                if self._guard(need_token=False) is None:
                    return
                name = path[len("/static/"):]
                if name not in STATIC_FILES:
                    self._error(404, "Not found.")
                    return
                body, packed, tag = static_file(name)
                want = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("v", [""])[0]
                cache = "public, max-age=31536000, immutable" if want == tag else "no-cache"
                etag = f'"{tag}"'
                if self.headers.get("If-None-Match") == etag:
                    self._headers(304, STATIC_FILES[name], 0, {"Cache-Control": cache, "ETag": etag})
                    return
                self._send(body, STATIC_FILES[name], {"Cache-Control": cache, "ETag": etag}, packed)
            elif path == "/api/state":
                if self._guard() is None:
                    return
                self._json({"version": __version__, "presets": [{"name": p.name, "label": p.label, "description": p.description} for p in PRESETS],
                            "features": quick_features(), "media_dir": str(self.state.media), "can_choose": self.state.can_choose()})
            elif path == "/api/files":
                if self._guard() is None:
                    return
                self._json({"files": self.state.list_files()})
            elif path == "/api/examples":
                if self._guard() is None:
                    return
                self._json({"examples": self._examples()})
            elif path == "/api/jobs":
                if self._guard() is None:
                    return
                jobs = self.state.store.list(60)
                for job in jobs:
                    job["file_id"] = self.state.output_id(job)
                    opts = job.pop("options", None) or {}
                    job["kind"] = opts.get("kind") or "script"
                    job["preview"] = bool(opts.get("preview"))
                    job.pop("script", None)          # can be large, and the page does not need it
                    job.pop("plan", None)
                self._json({"jobs": jobs})
            elif path == "/api/info":
                query = self._guard()
                if query is None:
                    return
                self._json(self.state.media_view(self.state.resolve_input((query.get("id") or [""])[0])))
            elif path == "/api/preview":
                query = self._guard()
                if query is None:
                    return
                self._json(self.state.previews.status(self.state.resolve_input((query.get("id") or [""])[0])))
            elif path == "/api/split":
                query = self._guard()
                if query is None:
                    return
                src = self.state.resolve_input((query.get("id") or [""])[0])
                dur = probe_media(src).duration
                a = _float((query.get("start") or ["0"])[0], 0.0)
                b = _float((query.get("end") or [str(dur)])[0], dur)
                self._json(self.state.splitter.info(src, a, min(b, dur) if b > 0 else dur))
            elif path == "/media":
                self._media()
            elif path == "/thumb":
                self._thumb()
            elif path == "/api/projects":
                if self._guard() is None:
                    return
                self._json({"projects": self.state.list_projects()})
            elif re.fullmatch(rf"/api/projects/{PROJECT_ID}", path):
                if self._guard() is None:
                    return
                pid = path.rsplit("/", 1)[1]
                view = self._project_view(self.state.load_project(pid))
                self.state.edit_project(pid, opened=time.time())
                self._json(view)
            elif path == "/api/clipsets":
                if self._guard() is None:
                    return
                self._json({"clipsets": self.state.list_clipsets()})
            elif re.fullmatch(rf"/api/clipsets/{clip_mod.CLIPSET_ID}", path):
                if self._guard() is None:
                    return
                cid = path.rsplit("/", 1)[1]
                cs = self.state.edit_clipset(cid, opened=time.time())
                self._json(self.state.clipset_view(cs))
            elif path == "/api/scenes":
                query = self._guard()
                if query is None:
                    return
                self._json(self.state.scenes.status(self.state.resolve_input((query.get("id") or [""])[0])))
            elif path == "/api/clips/made":
                if self._guard() is None:
                    return
                self._json({"clips": self.state.made_clips()})
            elif path == "/api/final":
                if self._guard() is None:
                    return
                self._json({"videos": self.state.final_videos()})
            elif path == "/api/storage":
                if self._guard() is None:
                    return
                self._json(self.state.storage())
            elif path == "/api/update":
                if self._guard() is None:
                    return
                self._json(self.state.updater.status())
            elif path == "/api/recipes":
                if self._guard() is None:
                    return
                self._json({"recipes": self.state.load_recipes()})
            elif re.fullmatch(r"/api/jobs/[0-9a-f]{12}", path):
                if self._guard() is None:
                    return
                job = self.state.store.get(path.rsplit("/", 1)[1])
                if not job:
                    self._error(404, "No such job.")
                else:
                    job.pop("script", None)
                    self._json(job)
            elif re.fullmatch(r"/download/[0-9a-f]{12}", path):
                self._download(path.rsplit("/", 1)[1])
            else:
                self._error(404, "Not found.")
        except Exception as exc:  # pragma: no cover - defensive
            self._fail(exc)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            query = self._guard()
            if query is None:
                return
            if path == "/api/upload":
                self._upload(query)
            elif path == "/api/choose":
                data = self._read_json()
                if data is None:
                    return
                try:
                    ident = self.state.choose(str(data.get("kind") or "video"))
                except choose_mod.NoDialog:
                    self._json({"no_dialog": True})
                    return
                self._json(self._file_reply(ident) if ident else {"cancelled": True})
            elif re.fullmatch(rf"/api/projects/{PROJECT_ID}/(rename|duplicate|trash|restore|relink)", path):
                data = self._read_json()
                if data is None:
                    return
                self._json(self._project_action(path.split("/")[3], path.split("/")[4], data))
            elif path == "/api/trash/empty":
                n = sum(self.state.delete_project(d["id"]) for d in self.state.list_projects() if d["trashed"])
                self._json({"deleted": n})
            elif path == "/api/storage/clean":
                data = self._read_json()
                if data is None:
                    return
                n = self.state.clean_storage(str(data.get("what") or ""))
                self._json({"removed": n, "storage": self.state.storage()})
            elif path == "/api/update/check":
                self._json(self.state.updater.check(force=True))
            elif path == "/api/update/download":
                self._json(self.state.updater.download())
            elif path == "/api/update/skip":
                data = self._read_json()
                if data is None:
                    return
                self.state.updater.skip(str(data.get("version") or ""))
                self._json(self.state.updater.status())
            elif path == "/api/update/install":
                from ..updater import can_install
                if not can_install():
                    raise EditForgeError("Only the installed app can update itself.", "Download the new EditForge-Setup.exe from the release page.")
                self._json(self.state.updater.install(lambda: _jobs_busy(self.state), self.state.quit_app))
            elif path == "/api/validate":
                data = self._read_json()
                if data is None:
                    return
                inp = self.state.resolve_input(data["input"]) if data.get("input") else None
                self._json(validate_script(str(data.get("script", "")), inp, base_dir=str(self.state.media),
                                           allowed_roots=self.state.allowed_roots))
            elif path == "/api/preview":
                data = self._read_json()
                if data is None:
                    return
                self._json(self.state.start_preview(self.state.resolve_input(str(data.get("input", "")))), 202)
            elif path == "/api/split":
                data = self._read_json()
                if data is None:
                    return
                src = self.state.resolve_input(str(data.get("input", "")))
                self._json(self.state.splitter.start(src, _float(data.get("start"), 0.0), _float(data.get("end"), 0.0),
                                                     "best" if data.get("mode") == "best" else "fast"), 202)
            elif path == "/api/split/cancel":
                if self._read_json() is None:
                    return
                self.state.splitter.cancel()
                self._json({"ok": True})
            elif path == "/api/projects":
                data = self._read_json()
                if data is None:
                    return
                self._json(self._new_project(data), 201)
            elif re.fullmatch(rf"/api/projects/{PROJECT_ID}/fork", path):
                data = self._read_json()
                if data is None:
                    return
                self._json({"project": self.state.fork_project(path.split("/")[3], int(_float(data.get("step"), 1)))}, 201)
            elif path == "/api/clipsets":
                data = self._read_json()
                if data is None:
                    return
                self._json(self._new_clipset(data), 201)
            elif re.fullmatch(rf"/api/clipsets/{clip_mod.CLIPSET_ID}/(rename|trash|restore|make|free)", path):
                data = self._read_json()
                if data is None:
                    return
                self._json(self._clipset_action(path.split("/")[3], path.split("/")[4], data))
            elif path == "/api/scenes":
                data = self._read_json()
                if data is None:
                    return
                src = self.state.resolve_input(str(data.get("id") or ""))
                info = probe_media(src)
                self._json(self.state.scenes.start(src, info.duration, info.has_video))
            elif re.fullmatch(rf"/api/projects/{PROJECT_ID}/render", path):
                data = self._read_json()
                if data is None:
                    return
                self._json({"id": self._render_project(path.split("/")[3], bool(data.get("preview")))}, 201)
            elif path == "/api/beats":
                data = self._read_json()
                if data is None:
                    return
                self._json(self._beats(str(data.get("input", ""))))
            elif path == "/api/track":
                data = self._read_json()
                if data is None:
                    return
                self._json(self._track(data))
            elif path == "/api/combine":
                data = self._read_json()
                if data is None:
                    return
                self._json({"id": self._combine(data)}, 201)
            elif path == "/api/clips":
                data = self._read_json()
                if data is None:
                    return
                self._json({"jobs": self._make_clips(data)}, 201)
            elif path == "/api/plan":
                data = self._read_json()
                if data is None:
                    return
                plan = self._make_plan(data)
                self._json(plan.to_dict())
            elif path == "/api/jobs":
                data = self._read_json()
                if data is None:
                    return
                plan = self._make_plan(data)  # fail early with a clear message
                opts = self._options(data)
                opts["output_dir"] = str(self.state.outputs)
                job_id = self.state.manager.submit(str(data.get("script", "")), plan.media.path, "", opts)
                self._json({"id": job_id, "warnings": plan.warnings}, 201)
            elif re.fullmatch(r"/api/jobs/[0-9a-f]{12}/cancel", path):
                ok = self.state.manager.cancel(path.split("/")[3])
                self._json({"ok": ok})
            elif re.fullmatch(r"/api/jobs/[0-9a-f]{12}/resume", path):
                ok = self.state.manager.resume(path.split("/")[3])
                self._json({"ok": ok})
            else:
                self._error(404, "Not found.")
        except Exception as exc:
            self._fail(exc)

    def do_PUT(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        try:
            if self._guard() is None:
                return
            data = self._read_json(allow_list=path == "/api/recipes")
            if data is None:
                return
            if re.fullmatch(rf"/api/projects/{PROJECT_ID}", path):
                pid = path.rsplit("/", 1)[1]
                old = self.state.load_project(pid)
                data["source"] = old["source"]          # a project always stays with its own video (Find it again changes it)
                self._json(self.state.save_project(pid, data, old.get("created"), old))
            elif re.fullmatch(rf"/api/clipsets/{clip_mod.CLIPSET_ID}", path):
                cid = path.rsplit("/", 1)[1]
                old = self.state.load_clipset(cid)
                self._json(self.state.clipset_view(self.state.save_clipset(cid, data, old)))
            elif path == "/api/recipes":
                self._json({"recipes": self.state.save_recipes(data)})
            else:
                self._error(404, "Not found.")
        except Exception as exc:
            self._fail(exc)

    def do_DELETE(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        try:
            if self._guard() is None:
                return
            if re.fullmatch(rf"/api/projects/{PROJECT_ID}", path):
                self._json({"ok": self.state.delete_project(path.rsplit("/", 1)[1])})
            elif re.fullmatch(rf"/api/clipsets/{clip_mod.CLIPSET_ID}", path):
                self._json({"ok": self.state.delete_clipset(path.rsplit("/", 1)[1])})
            elif re.fullmatch(r"/api/jobs/[0-9a-f]{12}", path):
                jid = path.rsplit("/", 1)[1]
                job = self.state.store.get(jid)
                if job and job["status"] in ("done", "failed", "cancelled", "interrupted"):
                    shutil.rmtree(self.state.outputs / jid, ignore_errors=True)
                    self.state.store.delete(jid)
                    self._json({"ok": True})
                else:
                    self._json({"ok": False})
            else:
                self._error(404, "Not found.")
        except Exception as exc:  # pragma: no cover
            self._fail(exc)

    # ---- helpers -------------------------------------------------------------------------------
    def _options(self, data: Dict[str, Any]) -> Dict[str, Any]:
        o = data.get("options") or {}
        if not isinstance(o, dict):
            o = {}
        out: Dict[str, Any] = {}
        if o.get("preset"):
            out["preset"] = str(o["preset"])[:40]
        out["preview"] = bool(o.get("preview"))
        out["fast_cuts"] = True if o.get("fast_cuts") else None
        if o.get("quality"):
            out["quality"] = str(o["quality"])[:20]
        return out

    def _make_plan(self, data: Dict[str, Any]):
        if not data.get("input"):
            raise EditForgeError("No video was chosen.", "Upload a video or pick one from the list first.")
        inp = self.state.resolve_input(str(data["input"]))
        o = self._options(data)
        script = read_script(str(data.get("script", "")))
        return prepare(script, input_path=inp, preset=o.get("preset"), quality=o.get("quality"), fast_cuts=o.get("fast_cuts"),
                       preview=bool(o.get("preview")), base_dir=str(self.state.media), allowed_roots=self.state.allowed_roots)

    # ---- projects, beats, combine ------------------------------------------------------------------
    def _project_view(self, proj: Dict[str, Any]) -> Dict[str, Any]:
        """A project plus the facts about its video that the editor page needs."""
        try:
            info = self.state.media_view(self.state.resolve_input(proj["source"]))
        except EditForgeError as exc:
            raise EditForgeError("The video this project was made from is gone.", "Delete this project and start a new one. (" + exc.message + ")")
        info["where"] = self.state.link_path(proj["source"]) or ""
        return {"project": proj, "info": info}

    def _new_clipset(self, data: Dict[str, Any]) -> Dict[str, Any]:
        src_id = str(data.get("source") or "")
        if not src_id:
            raise EditForgeError("No movie was chosen.", "Choose a movie first.")
        src = self.state.resolve_input(src_id)
        info = probe_media(src)
        if info.duration < 0.5:
            raise EditForgeError("That file is too short to take clips from.", "Choose a longer video.")
        name = " ".join(str(data.get("name") or Path(src).stem).split())[:80]
        cs = self.state.save_clipset(uuid.uuid4().hex[:12], {"name": name, "source": src_id, "clips": []})
        return self.state.clipset_view(cs)

    def _clipset_action(self, cid: str, action: str, data: Dict[str, Any]) -> Dict[str, Any]:
        st = self.state
        if action == "rename":
            name = " ".join(str(data.get("name") or "").split())[:80]
            if not name:
                raise EditForgeError("The name is empty.", "Type a name.")
            return st.edit_clipset(cid, name=name, updated=time.time())
        if action == "trash":
            return st.edit_clipset(cid, trashed=time.time())
        if action == "restore":
            return st.edit_clipset(cid, trashed=0)
        if action == "make":
            ids = data.get("ids")
            started = st.make_clips(cid, [str(x) for x in ids] if isinstance(ids, list) else None)
            return dict(st.clipset_view(st.load_clipset(cid)), started=started)
        return st.free_movie(cid, bool(data.get("delete_original")))

    def _project_action(self, pid: str, action: str, data: Dict[str, Any]) -> Dict[str, Any]:
        st = self.state
        if action == "rename":
            name = " ".join(str(data.get("name") or "").split())[:80]
            if not name:
                raise EditForgeError("The name is empty.", "Type a name.")
            return st.edit_project(pid, name=name, updated=time.time())
        if action == "duplicate":
            return st.duplicate_project(pid)
        if action == "trash":
            return st.edit_project(pid, trashed=time.time())
        if action == "restore":
            return st.edit_project(pid, trashed=0)
        # relink: "Find it again" -- choose the moved video (or give a file id the page already has)
        ident = str(data.get("file") or "")
        if not ident:
            try:
                ident = st.choose("video") or ""
            except choose_mod.NoDialog:
                return {"no_dialog": True}
            if not ident:
                return {"cancelled": True}
        src = st.resolve_input(ident)
        info = probe_media(src)
        old = st.load_project(pid)
        want = float((old.get("src") or {}).get("duration") or 0)
        note = ""
        if want and abs(info.duration - want) > max(1.0, want * 0.01):
            note = f"This video is {info.duration:.0f} s long; the old one was {want:.0f} s. Pieces past its end were dropped."
        old["source"] = ident
        proj = st.save_project(pid, old, old.get("created"), old)
        return {"project": proj, "note": note}

    def _file_reply(self, ident: str) -> Dict[str, Any]:
        """What the page needs to know about a newly linked file."""
        src = self.state.resolve_input(ident)
        info = probe_media(src)
        out = {"id": ident, "name": Path(src).name, "size": os.path.getsize(src), "duration": info.duration, "has_video": info.has_video,
               "has_audio": info.has_audio, "width": info.width, "height": info.height, "linked": True, "where": src,
               "subtitles": Path(src).suffix.lower() in SUBTITLE_EXTS}
        if out["subtitles"]:
            from ..analysis.speech import import_transcript
            out["words"] = len(import_transcript(src))
            if not out["words"]:
                raise EditForgeError("I could not find any timed lines in that subtitle file.", "Choose a .srt or .vtt file with times.")
        threading.Thread(target=self.state.after_add, args=(ident,), daemon=True).start()
        return out

    def _new_project(self, data: Dict[str, Any]) -> Dict[str, Any]:
        src_id = str(data.get("source") or "")
        if not src_id:
            raise EditForgeError("No video was chosen.", "Choose a video first.")
        src = self.state.resolve_input(src_id)
        info = probe_media(src)
        if info.duration < 0.1:
            raise EditForgeError("That file is too short to edit.", "Choose a longer video.")
        try:
            interval = float(data.get("interval") or 0)
        except (TypeError, ValueError):
            interval = 0.0
        pieces = project_mod.slice_pieces(info.duration, interval) if interval > 0 else project_mod.slice_pieces(info.duration, info.duration + 1)
        name = str(data.get("name") or Path(src).stem)
        proj = self.state.save_project(uuid.uuid4().hex[:12], {"name": name, "source": src_id, "pieces": pieces, "settings": {}})
        return self._project_view(proj)

    def _render_project(self, pid: str, preview: bool) -> str:
        proj = self.state.load_project(pid)
        src = self.state.resolve_input(proj["source"])
        proj = project_mod.clean_project(proj, probe_media(src).duration)
        music_path = None
        if proj["settings"]["music"]:
            try:
                music_path = self.state.resolve_input(proj["settings"]["music"])
            except EditForgeError:
                raise EditForgeError("The music file of this project is gone.", "Choose the music again in the Music box, or choose 'No music'.")
        captions_path = None
        cap = proj["settings"]["captions"]
        if cap not in ("off", "auto"):
            try:
                captions_path = self.state.resolve_input(cap)
            except EditForgeError:
                raise EditForgeError("The subtitle file of this project is gone.", "Choose it again in the Captions box, or switch captions off.")
        if cap == "auto":
            from ..analysis.speech import whisper_available
            if not whisper_available():
                raise EditForgeError("Automatic captions need the free 'faster-whisper' add-on, which is not installed.",
                                     "Install it with:  pip install faster-whisper   -- or add a subtitle file (.srt) instead.")
        else:                                                    # fail early with a clear message (speech recognition is too slow to try here)
            project_mod.plan_project(proj, src, music_path=music_path, captions_path=captions_path, preview=preview,
                                     stems_home=str(self.state.home))
        opts = {"kind": "project", "preview": preview, "music_path": music_path, "captions_path": captions_path, "project_id": pid,
                "output_dir": str(self.state.outputs), "stems_home": str(self.state.home),
                "output_name": project_mod.output_stem(proj, src, preview)}
        return self.state.manager.submit(json.dumps(proj), src, "", opts)

    def _beats(self, ident: str) -> Dict[str, Any]:
        """Tempo and beat times of a music file (the result is cached, so asking twice is quick)."""
        from ..analysis.manager import Analyzer
        path = self.state.resolve_input(ident)
        info = probe_media(path)
        if not info.has_audio:
            raise EditForgeError("That file has no sound, so there is no beat to find.", "Choose a music file.")
        beats = Analyzer(info).beats()
        return {"tempo": beats.tempo, "beats": beats.beats, "duration": info.duration, "notes": beats.notes}

    def _track(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Follow the thing inside a box the user drew on the video, between two times."""
        from ..analysis.follow import track_object
        src = self.state.resolve_input(str(data.get("input", "")))
        info = probe_media(src)
        if not info.has_video:
            raise EditForgeError("This file has no picture to follow anything in.", "Choose a video.")
        try:
            at, start, end = float(data["at"]), float(data["start"]), float(data["end"])
            box = [float(data["box"][k]) for k in ("x", "y", "w", "h")]
        except (KeyError, TypeError, ValueError):
            raise EditForgeError("The box or the times are missing.", "Draw the box on the video again.")
        if any(v != v for v in box + [at, start, end]) or box[2] <= 0 or box[3] <= 0:
            raise EditForgeError("The box is empty.", "Drag a box around the thing you want to follow.")
        start, end = max(0.0, start), min(info.duration, end)
        if end - start < 0.2:
            raise EditForgeError("The picked pieces are too short to follow anything.", "Pick longer pieces.")
        points, found = track_object(src, at, box, start, end, info.width, info.height)
        return {"path": [list(p) for p in points], "found": found}

    def _combine(self, data: Dict[str, Any]) -> str:
        if not data.get("a") or not data.get("b"):
            raise EditForgeError("Two videos are needed.", "Choose the first and the second video.")
        a, b = self.state.resolve_input(str(data["a"])), self.state.resolve_input(str(data["b"]))
        opts = combine_mod.clean_options(data.get("options"))
        combine_mod.build_command(probe_media(a), probe_media(b), opts, "check.mp4")     # fail early with a clear message
        name = f"{Path(a).stem[:40]}_{opts['mode']}_{Path(b).stem[:40]}"
        return self.state.manager.submit(json.dumps(opts), a, "", {"kind": "combine", "second_path": b, "output_name": name,
                                                                    "output_dir": str(self.state.outputs)})

    def _thumb(self) -> None:
        """A small picture of one moment of a video, for the piece grid."""
        query = self._guard(query_token=True)
        if query is None:
            return
        try:
            src = self.state.resolve_input((query.get("id") or [""])[0])
            at = float((query.get("at") or ["0"])[0])
            if not (0 <= at < 1e6):
                raise ValueError
        except (EditForgeError, ValueError):
            self._error(404, "No picture for that.")
            return
        pic = self.state.thumbnail(src, at, 240 if (query.get("h") or [""])[0] == "240" else 96)
        if pic is None:
            self._error(404, "No picture for that.")
            return
        body = pic.read_bytes()
        self._headers(200, "image/jpeg", len(body), {"Cache-Control": "private, max-age=86400"})
        self.wfile.write(body)

    def _make_clips(self, data: Dict[str, Any]) -> List[str]:
        """Start one job per marked clip (or one job that joins them) and return the job ids.

        Clips are always cut from the ORIGINAL file, frame-exact, never from the preview copy.
        """
        if not data.get("input"):
            raise EditForgeError("No video was chosen.", "Choose a video in step 1 first.")
        src = self.state.resolve_input(str(data["input"]))
        info = probe_media(src)
        raw = data.get("clips")
        if not isinstance(raw, list) or not raw:
            raise EditForgeError("There are no clips to make.", "Mark a start and an end, then press 'Add clip'.")
        if len(raw) > MAX_CLIPS:
            raise EditForgeError(f"That is more than {MAX_CLIPS} clips at once.", "Make them in smaller groups.")
        clips: List[Tuple[float, float, str]] = []
        for n, c in enumerate(raw, 1):
            try:
                start, end = float(c["start"]), float(c["end"])
            except (TypeError, ValueError, KeyError):
                raise EditForgeError(f"Clip {n} has a start or end that is not a time.", "Delete that clip and mark it again.")
            if not (start == start and end == end):  # NaN
                raise EditForgeError(f"Clip {n} has a start or end that is not a time.", "Delete that clip and mark it again.")
            start, end = max(0.0, start), min(end, info.duration)
            if end - start < MIN_CLIP_SECONDS:
                raise EditForgeError(f"Clip {n} is empty or too short (the end must come after the start).",
                                     "Move the end later, or delete that clip.")
            name = clean_filename(str(c.get("name") or ""), default=f"clip{n:02d}").rsplit(".", 1)[0][:60] or f"clip{n:02d}"
            clips.append((start, end, name))
        quality = CLIP_QUALITIES.get(str(data.get("quality") or "best"), "best")
        stem = Path(src).stem[:60]
        base = {"quality": quality, "output_dir": str(self.state.outputs), "preview": False, "fast_cuts": None}
        if data.get("join"):
            # Joined in the order of the list (not time order), so it goes through the project planner.
            proj = project_mod.clean_project({"name": f"{stem}_joined", "source": "", "settings": {"quality": {"medium": "small"}.get(quality, quality)},
                                              "pieces": [{"start": a, "end": b} for a, b, _ in clips]}, info.duration)
            project_mod.plan_project(proj, src)
            return [self.state.manager.submit(json.dumps(proj), src, "", {"kind": "project", "preview": False, "music_path": None,
                                                                         "output_dir": str(self.state.outputs), "output_name": f"{stem}_joined"})]
        return [self.state.manager.submit(f"keep {a:.3f}-{b:.3f}", src, "", dict(base, output_name=f"{stem}_{name}"))
                for a, b, name in clips]

    def _media(self) -> None:
        """Send a video/audio file to the page's player, with support for jumping around (HTTP Range)."""
        query = self._guard(query_token=True)
        if query is None:
            return
        layer = (query.get("layer") or [""])[0]
        if layer:                                   # one sound layer (voices / music / effects) of a split video
            from ..splitter import LAYERS
            found = self.state.splitter.store.resolve((query.get("split") or [""])[0])
            if not found or layer not in LAYERS:
                self._error(404, "That sound layer was not found.", "Open Sound and press Split.")
                return
            src, path, kind = found["files"][layer], Path(found["files"][layer]), ""
        else:
            try:
                src = self.state.resolve_input((query.get("id") or [""])[0])
            except EditForgeError as exc:
                self._error(404, exc.message, exc.fix)
                return
            path = Path(src)
            kind = (query.get("preview") or [""])[0]
        if kind in ("1", "q"):
            path = self.state.previews.path_for(src) if kind == "1" else self.state.previews.quick_path_for(src)
            if not path.is_file():
                self._error(404, "The preview copy is not ready yet.")
                return
            if not self.headers.get("Range", "").startswith("bytes=") or self.headers.get("Range", "").startswith("bytes=0-"):
                try:
                    os.utime(path)                 # "last used", for the clean-up of old light copies
                except OSError:
                    pass
        size = path.stat().st_size
        ctype = "audio/flac" if path.suffix == ".flac" else (mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        start, end, status = 0, size - 1, 200
        rng = self.headers.get("Range")
        if rng:
            m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip())
            if not m or (not m.group(1) and not m.group(2)):
                self._headers(416, ctype, 0, {"Content-Range": f"bytes */{size}"})
                return
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            if start > end or start >= size:
                self._headers(416, ctype, 0, {"Content-Range": f"bytes */{size}"})
                return
            status = 206
        extra = {"Accept-Ranges": "bytes"}
        if status == 206:
            extra["Content-Range"] = f"bytes {start}-{end}/{size}"
        self._headers(status, ctype, end - start + 1, extra)
        remaining = end - start + 1
        try:
            with open(path, "rb") as fh:
                fh.seek(start)
                while remaining > 0:
                    chunk = fh.read(min(256 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the player stopped reading (normal when you jump to another time)

    def _examples(self) -> List[Dict[str, str]]:
        out = []
        d = examples_dir()
        if d.exists():
            for f in sorted(d.glob("*.txt")):
                text = f.read_text(encoding="utf-8")
                first = text.splitlines()[0].lstrip("# ").strip() if text else f.stem
                out.append({"name": f.stem.replace("_", " "), "description": first, "script": text})
        return out

    def _upload(self, query: Dict[str, List[str]]) -> None:
        name = clean_filename((query.get("name") or ["upload"])[0])
        try:
            length = int(self.headers.get("Content-Length") or -1)
        except ValueError:
            length = -1
        if length <= 0:
            self._error(411, "The upload has no size.", "Try uploading the file again.")
            return
        if length > MAX_UPLOAD:
            self._error(413, "That file is too large for the web page.", "Put it in the media folder instead.")
            return
        uid = uuid.uuid4().hex[:12]
        folder = self.state.uploads / uid
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / name
        part = folder / (name + ".part")
        remaining = length
        try:
            with open(part, "wb") as fh:
                while remaining > 0:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise EditForgeError("The upload stopped before it finished.", "Try uploading the file again.")
                    fh.write(chunk)
                    remaining -= len(chunk)
            os.replace(part, dest)
            if dest.suffix.lower() in SUBTITLE_EXTS:            # a subtitle file for captions, not a video
                if length > MAX_SUBTITLE_BYTES:
                    raise EditForgeError("That subtitle file is too large.", "Subtitle files are small text files (.srt or .vtt).")
                from ..analysis.speech import import_transcript
                words = import_transcript(str(dest))
                if not words:
                    raise EditForgeError("I could not find any timed lines in that subtitle file.", "Choose a .srt or .vtt file with times.")
                self._json({"id": f"u/{uid}/{name}", "name": name, "size": dest.stat().st_size, "subtitles": True, "words": len(words),
                            "has_video": False, "has_audio": False, "duration": 0, "width": 0, "height": 0}, 201)
                return
            info = probe_media(str(dest))
        except BaseException as exc:
            shutil.rmtree(folder, ignore_errors=True)
            if isinstance(exc, EditForgeError):
                self._fail(exc)
                return
            raise
        self._json({"id": f"u/{uid}/{name}", "name": name, "size": dest.stat().st_size, "duration": info.duration,
                    "has_video": info.has_video, "has_audio": info.has_audio, "width": info.width, "height": info.height}, 201)
        threading.Thread(target=self.state.after_add, args=(f"u/{uid}/{name}",), daemon=True).start()

    def _download(self, job_id: str) -> None:
        if self._guard(query_token=True) is None:
            return
        job = self.state.store.get(job_id)
        if not job or job["status"] != "done" or not job.get("output_path"):
            self._error(404, "That result is not ready.")
            return
        path = Path(job["output_path"]).resolve()
        try:
            path.relative_to(self.state.outputs)
        except ValueError:
            self._error(403, "That file is not available.")
            return
        if not path.is_file():
            self._error(404, "The file has been removed.")
            return
        size = path.stat().st_size
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        quoted = urllib.parse.quote(clean_filename(path.name))
        self._headers(200, ctype, size, {"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"})
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)


class EditForgeServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_server(host: str = "127.0.0.1", port: int = 0, media_dir: Optional[str] = None, home: Optional[Path] = None,
                app_mode: bool = False) -> Tuple[EditForgeServer, AppState]:
    """Create the server (port 0 picks a free port; used by the tests)."""
    state = AppState(host, port, media_dir, home, app_mode)
    handler = type("BoundHandler", (Handler,), {"state": state})
    srv = EditForgeServer((host, port), handler)
    state.port = srv.server_address[1]
    return srv, state


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True, media_dir: Optional[str] = None) -> int:
    """Run the web page until Ctrl+C."""
    srv = None
    for p in range(port, port + 20):
        try:
            srv, state = make_server(host, p, media_dir, app_mode=True)
            break
        except OSError:
            continue
    if srv is None:
        raise EditForgeError(f"Ports {port} to {port + 19} are all in use.", "Close the other EditForge window, or choose --port 9000.")
    url = f"http://127.0.0.1:{state.port}/" if host in ("127.0.0.1", "localhost") else f"http://{host}:{state.port}/"
    print(f"EditForge is running at {url}")
    print(f"Put videos you want to edit in: {state.media}")
    if host not in ("127.0.0.1", "localhost", "::1"):
        print("WARNING: you chose an address that other computers can reach. Anyone who can open that address can use EditForge.")
    print("Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        state.manager.shutdown()
        state.previews.shutdown()
        srv.server_close()
    return 0
