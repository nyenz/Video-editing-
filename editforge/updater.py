"""Automatic updates for the installed Windows app.

Once a day (in the background, never slowing the window) EditForge asks GitHub for the newest release.
When there is a newer version the page shows a quiet "Update" button. Clicking it downloads
EditForge-Setup.exe, checks its size and SHA-256 fingerprint against the release, waits until no video is
being made, then runs the installer silently: EditForge closes, updates and opens again by itself.
Everything you made stays (it lives in your home folder, not in the program folder).

No internet? Nothing happens and nothing breaks; it tries again the next day.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .core.cache import atomic_write_json, home_dir
from .version import __version__

REPO = "nyenz/Video-editing-"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSET = "EditForge-Setup.exe"
DAY = 24 * 3600
TIMEOUT = 8


def parse_version(text: str) -> tuple:
    """'v1.10.2' -> (1, 10, 2). Anything odd gives (0,)."""
    m = re.match(r"v?(\d+(?:\.\d+)*)", str(text or "").strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else (0,)


def is_newer(latest: str, current: str = __version__) -> bool:
    return parse_version(latest) > parse_version(current)


def can_install() -> bool:
    """Only the installed app updates itself (not when running from the source code)."""
    return bool(getattr(sys, "frozen", False)) and os.name == "nt"


class Updater:
    """Remembers the last check in ``update.json`` and does the download in a background thread."""

    def __init__(self, home: Optional[Path] = None, fetch: Optional[Callable[[str], bytes]] = None):
        self.home = Path(home) if home else home_dir()
        self.file = self.home / "update.json"
        self.folder = self.home / "updates"
        self.fetch = fetch or _fetch
        self.download_prefix = "https://github.com/"          # only installers from GitHub are ever downloaded
        self._lock = threading.Lock()
        self.job: Dict[str, Any] = {"state": "idle", "progress": 0.0, "error": ""}

    # ---- remembered facts
    def _read(self) -> Dict[str, Any]:
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, data: Dict[str, Any]) -> None:
        try:
            atomic_write_json(self.file, data)
        except OSError:
            pass

    # ---- checking
    def check(self, force: bool = False) -> Dict[str, Any]:
        """Ask GitHub (at most once a day unless ``force``). Never raises."""
        data = self._read()
        if not force and time.time() - float(data.get("checked") or 0) < DAY:
            return self.status()
        try:
            rel = json.loads(self.fetch(API).decode("utf-8"))
            asset = next((a for a in rel.get("assets") or [] if a.get("name") == ASSET), None)
            latest = str(rel.get("tag_name") or "").lstrip("v")
            info = {"checked": time.time(), "latest": latest, "url": "", "size": 0, "sha256": "", "notes": str(rel.get("body") or "")[:2000]}
            if asset:
                info.update({"url": str(asset.get("browser_download_url") or ""), "size": int(asset.get("size") or 0),
                             "sha256": _sha_from_digest(asset.get("digest"))})
                if not info["sha256"]:                                # older releases: a small .sha256 file next to it
                    side = next((a for a in rel.get("assets") or [] if a.get("name") == ASSET + ".sha256"), None)
                    if side:
                        try:
                            info["sha256"] = self.fetch(str(side.get("browser_download_url")))[:200].decode("ascii", "replace").split()[0].lower()
                        except Exception:  # noqa: BLE001
                            pass
            data.update(info)
            data.pop("error", None)
        except Exception as exc:  # noqa: BLE001 - offline, GitHub down, rate limit: try again tomorrow
            data["checked"] = time.time()
            data["error"] = str(exc)[:200]
        self._write(data)
        return self.status()

    def check_in_background(self) -> None:
        threading.Thread(target=self.check, name="editforge-update-check", daemon=True).start()

    def status(self) -> Dict[str, Any]:
        data = self._read()
        latest = str(data.get("latest") or "")
        available = bool(latest and is_newer(latest) and data.get("url") and data.get("skip") != latest)
        with self._lock:
            job = dict(self.job)
        return {"current": __version__, "latest": latest, "available": available, "size": int(data.get("size") or 0),
                "can_install": can_install(), "job": job, "skipped": data.get("skip") or ""}

    def skip(self, version: str) -> None:
        data = self._read()
        data["skip"] = str(version)[:20]
        self._write(data)

    # ---- downloading and installing
    def _set(self, **kw: Any) -> None:
        with self._lock:
            self.job.update(kw)

    def download(self) -> Dict[str, Any]:
        """Start downloading the installer in the background (once)."""
        with self._lock:
            if self.job["state"] in ("downloading", "ready", "installing"):
                return dict(self.job)
            self.job = {"state": "downloading", "progress": 0.0, "error": ""}
        threading.Thread(target=self._download, name="editforge-update-download", daemon=True).start()
        return dict(self.job)

    def _download(self) -> None:
        data = self._read()
        url, size, sha = str(data.get("url") or ""), int(data.get("size") or 0), str(data.get("sha256") or "").lower()
        if not url.startswith(self.download_prefix) or not sha:
            self._set(state="failed", error="The update has no download or no fingerprint to check it, so it was not used.")
            return
        self.folder.mkdir(parents=True, exist_ok=True)
        dest = self.folder / f"EditForge-Setup-{data.get('latest')}.exe"
        part = dest.with_suffix(".part")
        h = hashlib.sha256()
        got = 0
        try:
            req = urllib.request.Request(url, headers={"User-Agent": f"EditForge/{__version__}"})
            with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as fh:
                total = size or int(r.headers.get("Content-Length") or 0)
                while True:
                    chunk = r.read(1024 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
                    h.update(chunk)
                    got += len(chunk)
                    if total:
                        self._set(progress=min(0.99, got / total))
            if size and got != size:
                raise ValueError(f"the download has the wrong size ({got:,} instead of {size:,} bytes)")
            if h.hexdigest() != sha:
                raise ValueError("the download's fingerprint does not match the release")
            os.replace(part, dest)
            self._set(state="ready", progress=1.0, path=str(dest))
        except Exception as exc:  # noqa: BLE001
            try:
                part.unlink()
            except OSError:
                pass
            self._set(state="failed", error="The update could not be downloaded: " + str(exc)[:200])

    def install(self, busy: Callable[[], bool], quit_app: Callable[[], None]) -> Dict[str, Any]:
        """Run the downloaded installer silently once no video is being made, then close EditForge."""
        with self._lock:
            if self.job.get("state") != "ready":
                return dict(self.job)
            self.job["state"] = "waiting" if busy() else "installing"
            path = self.job.get("path")

        def go() -> None:
            while busy():                                # never update in the middle of a render
                time.sleep(2)
            self._set(state="installing")
            flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0     # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            subprocess.Popen([str(path), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS",
                              "/RESTARTAPPLICATIONS", "/RELAUNCH"], creationflags=flags, close_fds=True)
            time.sleep(1.5)
            quit_app()

        threading.Thread(target=go, name="editforge-update-install", daemon=True).start()
        with self._lock:
            return dict(self.job)

    def clean(self) -> None:
        """Remove old downloaded installers (called at start)."""
        for f in self.folder.glob("EditForge-Setup-*"):
            try:
                f.unlink()
            except OSError:
                pass


def _sha_from_digest(digest: Any) -> str:
    d = str(digest or "")
    return d.split(":", 1)[1].lower() if d.lower().startswith("sha256:") else ""


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": f"EditForge/{__version__}", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read(2 * 1024 * 1024)
