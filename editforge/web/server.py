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
import unicodedata
import urllib.parse
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..api import plan_text, prepare, read_script, validate_script
from ..core.cache import home_dir
from ..core.errors import EditForgeError
from ..core.media import probe_media
from ..jobs.manager import JobManager
from ..jobs.store import JobStore
from ..presets.presets import PRESETS
from ..version import __version__

STATIC_DIR = Path(__file__).parent / "static"
STATIC_FILES = {"app.js": "application/javascript; charset=utf-8", "style.css": "text/css; charset=utf-8"}
MAX_JSON = 2 * 1024 * 1024
MAX_UPLOAD = 20 * 1024 ** 3
SAFE_NAME = re.compile(r"[^\w.\- ()\[\]]", re.UNICODE)
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
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
        roots = [str(self.uploads), str(self.media)]
        if examples_dir().exists():
            roots.append(str(examples_dir().resolve()))
        self.allowed_roots = roots
        self.manager = JobManager(self.store, allowed_roots=roots, base_dir=str(self.media))

    def allowed_hosts(self) -> List[str]:
        h = {f"127.0.0.1:{self.port}", f"localhost:{self.port}", f"[::1]:{self.port}", f"{self.host}:{self.port}"}
        return sorted(h)

    def resolve_input(self, ident: str) -> str:
        """Turn a file id from the page ('u/<id>/<name>' or 'm/<name>') into a real path inside our folders."""
        m = re.fullmatch(r"u/([0-9a-f]{12})/([^/\\]+)", ident or "")
        if m:
            root, path = self.uploads, self.uploads / m.group(1) / m.group(2)
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

    def list_files(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for d in sorted(self.uploads.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True) if self.uploads.exists() else []:
            if d.is_dir() and re.fullmatch(r"[0-9a-f]{12}", d.name):
                for f in d.iterdir():
                    if f.is_file():
                        out.append({"id": f"u/{d.name}/{f.name}", "name": f.name, "size": f.stat().st_size, "where": "uploaded"})
        for f in sorted(self.media.iterdir()):
            if f.is_file() and not f.name.startswith("."):
                out.append({"id": f"m/{f.name}", "name": f.name, "size": f.stat().st_size, "where": "media folder"})
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
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        for k, v in (extra or {}).items():
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

    def _read_json(self) -> Optional[Dict[str, Any]]:
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
        if not isinstance(data, dict):
            self._error(400, "The request should be a JSON object.")
            return None
        return data

    # ---- routes --------------------------------------------------------------------------------
    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/":
                if self._guard(need_token=False) is None:
                    return
                html = (STATIC_DIR / "index.html").read_text(encoding="utf-8").replace("{{TOKEN}}", self.state.token).replace("{{VERSION}}", __version__)
                body = html.encode("utf-8")
                self._headers(200, "text/html; charset=utf-8", len(body))
                self.wfile.write(body)
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
                self._json({"jobs": self.state.store.list(30)})
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

    def do_DELETE(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        try:
            if self._guard() is None:
                return
            if re.fullmatch(r"/api/jobs/[0-9a-f]{12}", path):
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
        srv.server_close()
    return 0
