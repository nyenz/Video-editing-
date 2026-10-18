"""EditForge as a desktop app: start the local server and open it in its own app window.

This is what ``EditForge.exe`` runs. It:

* starts the web server on a free port, only reachable from this computer;
* opens the page in a Microsoft Edge (or Chrome) "app" window with its own profile, so it looks like a
  normal program: no address bar, no tabs, its own icon in the taskbar;
* stops when that window is closed (after any video still being made is finished);
* if EditForge is already running, just opens another window of it.

``EditForge.exe --selftest`` checks that everything inside the app works and prints the result.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from pathlib import Path
from typing import List, Optional

from .core.cache import home_dir
from .version import __version__

APP = "EditForge"


# ------------------------------------------------------------------ small helpers
def _log_file() -> Path:
    return home_dir() / "desktop.log"


def log(text: str) -> None:
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + text
    try:
        with open(_log_file(), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    if sys.stdout:
        try:
            print(line, flush=True)
        except Exception:  # noqa: BLE001 - a missing console must never stop the app
            pass


def message(title: str, text: str) -> None:
    """Show a message box (the app has no console window)."""
    log(title + ": " + text)
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)
            return
        except Exception:  # noqa: BLE001
            pass
    try:
        import tkinter
        from tkinter import messagebox
        root = tkinter.Tk()
        root.withdraw()
        messagebox.showinfo(title, text)
        root.destroy()
    except Exception:  # noqa: BLE001
        pass


def _hello(port: int, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/hello", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8")).get("app") == APP
    except Exception:  # noqa: BLE001
        return False


def _state_file() -> Path:
    return home_dir() / "desktop.json"


# ------------------------------------------------------------------ finding a browser for the app window
def find_browser() -> Optional[str]:
    """Microsoft Edge first (every Windows PC has it), then Chrome, Brave or Chromium."""
    env = os.environ.get("EDITFORGE_BROWSER")
    if env and os.path.isfile(env):
        return env
    places: List[str] = []
    if os.name == "nt":
        roots = [os.environ.get(k, "") for k in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")]
        for sub in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe",
                    r"BraveSoftware\Brave-Browser\Application\brave.exe"):
            places += [os.path.join(r, sub) for r in roots if r]
    elif sys.platform == "darwin":
        places += ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                   "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
                   "/Applications/Chromium.app/Contents/MacOS/Chromium"]
    for name in ("microsoft-edge", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "brave-browser"):
        found = shutil.which(name)
        if found:
            places.append(found)
    for p in places:
        if p and os.path.isfile(p):
            return p
    return None


def _profile_dir() -> Path:
    d = home_dir() / "app-window"
    d.mkdir(parents=True, exist_ok=True)
    return d


def open_window(url: str, browser: Optional[str]) -> Optional[subprocess.Popen]:
    """Open the page as an app window. Returns the browser process, or None if the normal browser was used."""
    if browser:
        args = [browser, f"--app={url}", f"--user-data-dir={_profile_dir()}", "--no-first-run", "--no-default-browser-check",
                "--start-maximized", "--disable-features=Translate", "--autoplay-policy=no-user-gesture-required"]
        try:
            flags = 0x08000000 if os.name == "nt" else 0  # no console window
            return subprocess.Popen(args, creationflags=flags)
        except OSError as exc:
            log(f"could not start {browser}: {exc}")
    webbrowser.open(url)
    return None


def _window_still_open() -> bool:
    """True while any browser process is using the app's own profile (Edge sometimes hands over to a new process)."""
    try:
        import psutil
    except ImportError:
        return False
    key = str(_profile_dir())
    for p in psutil.process_iter(["cmdline"]):
        try:
            cmd = p.info.get("cmdline") or []
            if any(key in part for part in cmd):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _jobs_busy(state) -> bool:
    try:
        return any(j["status"] in ("queued", "running") for j in state.store.list(200))
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------------------------ the app
def run() -> int:
    log(f"{APP} {__version__} starting")
    # Already running? Then only open another window.
    try:
        old = json.loads(_state_file().read_text(encoding="utf-8"))
        if _hello(int(old.get("port", 0))):
            log("already running, opening another window")
            open_window(f"http://127.0.0.1:{old['port']}/", find_browser())
            return 0
    except (OSError, ValueError, TypeError):
        pass

    from .core.ffmpeg import find_executable
    if not find_executable("ffmpeg") or not find_executable("ffprobe"):
        message(APP, "FFmpeg, the video engine, is missing. Please reinstall EditForge.")
        return 1

    from .web.server import make_server
    srv = state = None
    for port in [8765 + i for i in range(20)] + [0]:
        try:
            srv, state = make_server("127.0.0.1", port, app_mode=True)
            break
        except OSError:
            continue
    if srv is None:
        message(APP, "EditForge could not start: no free network port on this computer. Restart the computer and try again.")
        return 1
    threading.Thread(target=srv.serve_forever, name="editforge-web", daemon=True).start()
    url = f"http://127.0.0.1:{state.port}/"
    try:
        _state_file().write_text(json.dumps({"port": state.port, "pid": os.getpid()}), encoding="utf-8")
    except OSError:
        pass
    log("serving " + url)

    browser = find_browser()
    proc = open_window(url, browser)
    quitting = threading.Event()

    def quit_app() -> None:
        """Close the window and stop (used by the updater, after the installer has started)."""
        quitting.set()
        _close_window(proc)

    state.quit_app = quit_app
    if proc is not None:                       # own taskbar button and icon, not grouped under Edge
        from . import taskbar
        taskbar.watch(str(_profile_dir()), log, quitting)
    try:
        if proc is None:
            _fallback_window(url)
        else:
            while proc.poll() is None and not quitting.is_set():
                time.sleep(0.5)
            # Edge may pass the window to another process; wait until no process uses our profile.
            quiet = 0
            while quiet < 3 and not quitting.is_set():
                time.sleep(1)
                quiet = 0 if _window_still_open() else quiet + 1
        while _jobs_busy(state) and not quitting.is_set():
            log("window closed; finishing a video that is still being made")
            time.sleep(3)
    except KeyboardInterrupt:
        pass
    finally:
        log("stopping")
        try:
            _state_file().unlink()
        except OSError:
            pass
        state.manager.shutdown()
        state.previews.shutdown()
        srv.shutdown()
        srv.server_close()
    return 0


def _close_window(proc: Optional[subprocess.Popen]) -> None:
    """Close the app window (every browser process that uses EditForge's own profile)."""
    try:
        if proc is not None and proc.poll() is None:
            proc.terminate()
    except OSError:
        pass
    try:
        import psutil
    except ImportError:
        return
    key = str(_profile_dir())
    for p in psutil.process_iter(["cmdline"]):
        try:
            if any(key in part for part in (p.info.get("cmdline") or [])):
                p.terminate()
        except Exception:  # noqa: BLE001
            continue


def _fallback_window(url: str) -> None:
    """No Edge or Chrome: the page opens in the normal browser, and this small window keeps EditForge running."""
    try:
        import tkinter
        root = tkinter.Tk()
        root.title(APP)
        root.geometry("360x150")
        tkinter.Label(root, text="EditForge is running.\nIt opened in your web browser.", pady=14).pack()
        row = tkinter.Frame(root)
        row.pack(pady=6)
        tkinter.Button(row, text="Open again", width=12, command=lambda: webbrowser.open(url)).pack(side="left", padx=6)
        tkinter.Button(row, text="Quit", width=12, command=root.destroy).pack(side="left", padx=6)
        root.mainloop()
    except Exception:  # noqa: BLE001 - no window toolkit: keep running until stopped
        while True:
            time.sleep(3600)


def _say(line: str) -> None:
    print(line, flush=True)
    target = os.environ.get("EDITFORGE_SELFTEST_OUT")
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def selftest() -> int:
    """Check the parts of the app: FFmpeg, the web server, the page, the optional add-ons."""
    ok = True

    def say(good: bool, what: str) -> None:
        nonlocal ok
        ok = ok and good
        _say(("OK   " if good else "FAIL ") + what)

    import tempfile
    try:
        import psutil
        since = time.time() - psutil.Process().create_time()
    except Exception:  # noqa: BLE001
        since = None
    t0 = time.perf_counter()
    from .web.server import make_server
    home = Path(tempfile.mkdtemp(prefix="editforge-selftest-"))
    srv, state = make_server("127.0.0.1", 0, home=home)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        say(_hello(state.port, 5), "web server answers")
        if since is not None:
            _say(f"TIME app start to web server ready: {since + time.perf_counter() - t0:.2f} s")
        for path, word in (("/", b"home.js"), ("/clips", b"workshop.js"), ("/edit", b"editor.js"), ("/edit", b"steps.js"),
                           ("/static/ui.js", b"enhanceSelect")):
            t = time.perf_counter()
            with urllib.request.urlopen(f"http://127.0.0.1:{state.port}{path}", timeout=5) as r:
                say(word in r.read(), "page " + path)
            _say(f"TIME page {path}: {time.perf_counter() - t:.3f} s")
        t = time.perf_counter()
        req = urllib.request.Request(f"http://127.0.0.1:{state.port}/api/state", headers={"X-EditForge-Token": state.token})
        with urllib.request.urlopen(req, timeout=10) as r:
            feats = json.loads(r.read())["features"]
        _say(f"TIME features check: {time.perf_counter() - t:.3f} s ({feats})")
    finally:
        state.manager.shutdown()
        state.previews.shutdown()
        srv.shutdown()
        srv.server_close()
    from .core.ffmpeg import find_executable
    ff, fp = find_executable("ffmpeg"), find_executable("ffprobe")
    say(bool(ff and fp), f"FFmpeg found: {ff}")
    if ff:
        out = subprocess.run([ff, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=60).stdout
        say("libx264" in out, "FFmpeg can make H.264 video")
    from . import taskbar
    if os.name == "nt":
        say(taskbar.supported(), "own taskbar icon (pywin32)")
    t = time.perf_counter()
    try:
        import cv2  # noqa: F401
        say(hasattr(cv2, "CascadeClassifier"), f"face tracking add-on (loads in {time.perf_counter() - t:.2f} s, only when used)")
    except Exception as exc:  # noqa: BLE001
        _say("INFO face tracking add-on not included: " + str(exc))
    t = time.perf_counter()
    try:
        import faster_whisper  # noqa: F401
        _say(f"OK   captions add-on (loads in {time.perf_counter() - t:.2f} s, only when used)")
    except Exception as exc:  # noqa: BLE001
        _say("INFO captions add-on not included: " + str(exc))
    _say("ALL OK" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


def _quiet_windows() -> None:
    """In the app (no console), FFmpeg must not flash a black window each time it runs, and printing must not fail."""
    if sys.stdout is None or sys.stderr is None:
        sink = open(_log_file(), "a", encoding="utf-8", buffering=1)  # noqa: SIM115 - lives as long as the app
        sys.stdout = sys.stdout or sink
        sys.stderr = sys.stderr or sink
    if os.name == "nt":
        base = subprocess.Popen

        class _NoWindowPopen(base):  # type: ignore[misc, valid-type]
            def __init__(self, *args, **kwargs):
                kwargs["creationflags"] = kwargs.get("creationflags", 0) | 0x08000000  # CREATE_NO_WINDOW
                super().__init__(*args, **kwargs)

        subprocess.Popen = _NoWindowPopen  # type: ignore[misc]


def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    _quiet_windows()
    if "--selftest" in argv:
        return selftest()
    if "--version" in argv:
        print(__version__)
        return 0
    try:
        return run()
    except Exception:  # noqa: BLE001 - show the problem instead of vanishing
        log(traceback.format_exc())
        message(APP, "EditForge stopped because of a problem. Details are in:\n" + str(_log_file()))
        return 1


if __name__ == "__main__":
    sys.exit(main())
