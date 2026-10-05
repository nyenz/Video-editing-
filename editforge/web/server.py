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
from .preview import PreviewMaker
from .. import combine as combine_mod
from .. import project as project_mod

STATIC_DIR = Path(__file__).parent / "static"
STATIC_FILES = {"app.js": "application/javascript; charset=utf-8", "style.css": "text/css; charset=utf-8",
                "workshop.js": "application/javascript; charset=utf-8", "workshop.css": "text/css; charset=utf-8",
                "editor.js": "application/javascript; charset=utf-8", "editor.css": "text/css; charset=utf-8",
                "combine.js": "application/javascript; charset=utf-8", "common.js": "application/javascript; charset=utf-8"}
PAGES = {"/": "workshop.html", "/edit": "editor.html", "/combine": "combine.html", "/advanced": "index.html"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"}
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


def examples_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "examples"


class AppState:
    """Everything the request handlers share."""

    def __init__(self, host: str, port: int, media_dir: Optional[str] = None, home: Optional[Path] = None):
        self.host, self.port = host, port
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
        for d in (self.projects, self.thumbs):
            d.mkdir(parents=True, exist_ok=True)
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

    def allowed_hosts(self) -> List[str]:
        h = {f"127.0.0.1:{self.port}", f"localhost:{self.port}", f"[::1]:{self.port}", f"{self.host}:{self.port}"}
        return sorted(h)

    def resolve_input(self, ident: str) -> str:
        """Turn a file id from the page ('u/<id>/<name>', 'o/<job>/<name>' or 'm/<name>') into a real path inside our folders."""
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

    def thumbnail(self, src: str, at: float) -> Optional[Path]:
        """A small JPEG of the picture at ``at`` seconds (made once, then kept). None if it cannot be made."""
        ms = max(0, int(round(at * 1000)))
        out = self.thumbs / f"{self.fingerprint(src)}_{ms}.jpg"
        if out.is_file() and out.stat().st_size > 0:
            return out
        part = out.with_name(out.name + f".{uuid.uuid4().hex[:8]}.part")
        with self._thumb_slots:
            res = run_ffmpeg(["-y", "-ss", f"{ms / 1000:.3f}", "-i", src, "-map", "0:v:0", "-frames:v", "1", "-vf", "scale=-2:96",
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

    def save_project(self, pid: str, raw: Dict[str, Any], created: Optional[float] = None) -> Dict[str, Any]:
        """Clean and store a project. The source video must still exist (its length limits the pieces)."""
        src = self.resolve_input(str(raw.get("source") or ""))
        info = probe_media(src)
        proj = project_mod.clean_project(raw, info.duration)
        proj.update({"id": pid, "created": created or time.time(), "updated": time.time()})
        atomic_write_json(self.project_path(pid), proj)
        return proj

    def list_projects(self) -> List[Dict[str, Any]]:
        out = []
        for f in self.projects.glob("*.json"):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                out.append({"id": d["id"], "name": d["name"], "source": d["source"], "source_name": str(d["source"]).rsplit("/", 1)[-1],
                            "pieces": len(d["pieces"]), "updated": d.get("updated", 0)})
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return sorted(out, key=lambda d: d["updated"], reverse=True)

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
                look.pop("start"), look.pop("end")
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
                                    "audio": f.suffix.lower() in AUDIO_EXTS})
        for f in sorted(self.media.iterdir()):
            if f.is_file() and not f.name.startswith("."):
                out.append({"id": f"m/{f.name}", "name": f.name, "size": f.stat().st_size, "where": "media folder",
                            "audio": f.suffix.lower() in AUDIO_EXTS})
        return out


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
                body = html.encode("utf-8")
                self._headers(200, "text/html; charset=utf-8", len(body))
                self.wfile.write(body)
            elif path == "/favicon.ico":
                self._headers(204, "image/x-icon", 0)
            elif path.startswith("/static/"):
                if self._guard(need_token=False) is None:
                    return
                name = path[len("/static/"):]
                if name not in STATIC_FILES:
                    self._error(404, "Not found.")
                    return
                body = (STATIC_DIR / name).read_bytes()
                self._headers(200, STATIC_FILES[name], len(body))
                self.wfile.write(body)
            elif path == "/api/state":
                if self._guard() is None:
                    return
                from ..analysis.faces import opencv_available
                from ..analysis.speech import whisper_available
                self._json({"version": __version__, "presets": [{"name": p.name, "label": p.label, "description": p.description} for p in PRESETS],
                            "features": {"captions": whisper_available(), "faces": opencv_available()},
                            "media_dir": str(self.state.media)})
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
                src = self.state.resolve_input((query.get("id") or [""])[0])
                info = probe_media(src).to_dict()
                info.pop("path", None)
                info["preview"] = self.state.previews.status(src) if info["has_video"] else {"state": "none", "progress": 0.0, "error": ""}
                self._json(info)
            elif path == "/api/preview":
                query = self._guard()
                if query is None:
                    return
                self._json(self.state.previews.status(self.state.resolve_input((query.get("id") or [""])[0])))
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
                self._json(self._project_view(self.state.load_project(path.rsplit("/", 1)[1])))
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
                self._json(self.state.previews.start(self.state.resolve_input(str(data.get("input", "")))), 202)
            elif path == "/api/projects":
                data = self._read_json()
                if data is None:
                    return
                self._json(self._new_project(data), 201)
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
                data["source"] = old["source"]          # a project always stays with its own video
                self._json(self.state.save_project(pid, data, old.get("created")))
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
                try:
                    self.state.project_path(path.rsplit("/", 1)[1]).unlink()
                    self._json({"ok": True})
                except OSError:
                    self._json({"ok": False})
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
            info = probe_media(self.state.resolve_input(proj["source"])).to_dict()
            info.pop("path", None)
        except EditForgeError as exc:
            raise EditForgeError("The video this project was made from is gone.", "Delete this project and start a new one. (" + exc.message + ")")
        return {"project": proj, "info": info}

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
        project_mod.plan_project(proj, src, music_path=music_path, preview=preview)      # fail early with a clear message
        opts = {"kind": "project", "preview": preview, "music_path": music_path, "output_dir": str(self.state.outputs),
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
        pic = self.state.thumbnail(src, at)
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
            clips.sort()
            script = "keep " + ", ".join(f"{a:.3f}-{b:.3f}" for a, b, _ in clips)
            return [self.state.manager.submit(script, src, "", dict(base, output_name=f"{stem}_joined"))]
        return [self.state.manager.submit(f"keep {a:.3f}-{b:.3f}", src, "", dict(base, output_name=f"{stem}_{name}"))
                for a, b, name in clips]

    def _media(self) -> None:
        """Send a video/audio file to the page's player, with support for jumping around (HTTP Range)."""
        query = self._guard(query_token=True)
        if query is None:
            return
        try:
            src = self.state.resolve_input((query.get("id") or [""])[0])
        except EditForgeError as exc:
            self._error(404, exc.message, exc.fix)
            return
        path = Path(src)
        if (query.get("preview") or [""])[0] == "1":
            path = self.state.previews.path_for(src)
            if not path.is_file():
                self._error(404, "The preview copy is not ready yet.")
                return
        size = path.stat().st_size
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
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
            info = probe_media(str(dest))
        except BaseException as exc:
            shutil.rmtree(folder, ignore_errors=True)
            if isinstance(exc, EditForgeError):
                self._fail(exc)
                return
            raise
        self._json({"id": f"u/{uid}/{name}", "name": name, "size": dest.stat().st_size, "duration": info.duration,
                    "has_video": info.has_video, "has_audio": info.has_audio, "width": info.width, "height": info.height}, 201)

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


def make_server(host: str = "127.0.0.1", port: int = 0, media_dir: Optional[str] = None, home: Optional[Path] = None) -> Tuple[EditForgeServer, AppState]:
    """Create the server (port 0 picks a free port; used by the tests)."""
    state = AppState(host, port, media_dir, home)
    handler = type("BoundHandler", (Handler,), {"state": state})
    srv = EditForgeServer((host, port), handler)
    state.port = srv.server_address[1]
    return srv, state


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True, media_dir: Optional[str] = None) -> int:
    """Run the web page until Ctrl+C."""
    srv = None
    for p in range(port, port + 20):
        try:
            srv, state = make_server(host, p, media_dir)
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
