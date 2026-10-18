"""Make the app window look like its own program on the Windows taskbar.

The window is drawn by Microsoft Edge (or Chrome) in "app" mode. Without help, Windows groups it under the
Edge icon, and pinning it pins Edge. Here every window of EditForge's own browser profile gets:

* its own taskbar identity (AppUserModelID "NyenzEdit.App", the same as the Start-menu and desktop shortcuts),
  so it has its own taskbar button and is not mixed with your normal Edge windows;
* "pin" information: pinning it pins EditForge.exe with the Nyenz Edit name and icon;
* the Nyenz Edit icon on the window and its taskbar button.

Windows only, and best effort: if anything is missing, the app works as before.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from typing import Callable, Optional, Set

APP_ID = "NyenzEdit.App"
APP_NAME = "Nyenz Edit"


def supported() -> bool:
    if os.name != "nt":
        return False
    try:
        import win32gui  # noqa: F401
        from win32com.propsys import propsys, pscon  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _exe() -> str:
    """The program to start when the pinned button is clicked."""
    return sys.executable if getattr(sys, "frozen", False) else ""


def brand_window(hwnd: int) -> bool:
    """Give one window the Nyenz Edit taskbar identity and icon. Returns True when it worked."""
    import pythoncom
    import win32con
    import win32gui
    from win32com.propsys import propsys, pscon
    ok = True
    try:
        store = propsys.SHGetPropertyStoreForWindow(hwnd, propsys.IID_IPropertyStore)
        exe = _exe()
        if exe:
            for key, value in (("PKEY_AppUserModel_RelaunchCommand", f'"{exe}"'),
                               ("PKEY_AppUserModel_RelaunchDisplayNameResource", APP_NAME),
                               ("PKEY_AppUserModel_RelaunchIconResource", f"{exe},0")):
                pkey = getattr(pscon, key, None)
                if pkey is not None:
                    store.SetValue(pkey, propsys.PROPVARIANTType(value, pythoncom.VT_ILLEGAL))
        store.SetValue(pscon.PKEY_AppUserModel_ID, propsys.PROPVARIANTType(APP_ID, pythoncom.VT_ILLEGAL))
        store.Commit()
    except Exception:  # noqa: BLE001
        ok = False
    exe = _exe()
    if exe:
        try:
            large, small = win32gui.ExtractIconEx(exe, 0)
            if large:
                win32gui.SendMessage(hwnd, win32con.WM_SETICON, win32con.ICON_BIG, large[0])
            if small:
                win32gui.SendMessage(hwnd, win32con.WM_SETICON, win32con.ICON_SMALL, small[0])
        except Exception:  # noqa: BLE001
            ok = False
    return ok


def _windows_of(pids: Set[int]) -> list:
    import win32gui
    import win32process
    found = []

    def each(hwnd, _):
        try:
            if win32gui.IsWindowVisible(hwnd) and win32gui.GetParent(hwnd) == 0 and win32gui.GetWindowText(hwnd):
                if win32process.GetWindowThreadProcessId(hwnd)[1] in pids:
                    found.append(hwnd)
        except Exception:  # noqa: BLE001
            pass
        return True

    win32gui.EnumWindows(each, None)
    return found


def watch(profile_dir: str, log: Callable[[str], None], stop: Optional[threading.Event] = None) -> None:
    """In the background, brand every window of the app's own browser profile as it appears (also later windows)."""
    if not supported():
        log("taskbar icon: not supported here (pywin32 missing)")
        return

    def loop() -> None:
        import psutil
        done: Set[int] = set()
        again = {}                      # hwnd -> times re-applied (the browser may set its own icon after loading)
        while not (stop and stop.is_set()):
            try:
                pids = set()
                for p in psutil.process_iter(["cmdline"]):
                    try:
                        if any(profile_dir in part for part in (p.info.get("cmdline") or [])):
                            pids.add(p.pid)
                    except Exception:  # noqa: BLE001
                        continue
                for hwnd in _windows_of(pids) if pids else []:
                    n = again.get(hwnd, 0)
                    if hwnd not in done or n < 4:
                        if brand_window(hwnd) and hwnd not in done:
                            log(f"taskbar icon set on window {hwnd}")
                        done.add(hwnd)
                        again[hwnd] = n + 1
            except Exception as exc:  # noqa: BLE001
                log(f"taskbar icon: {exc}")
            time.sleep(1.5)

    threading.Thread(target=loop, name="editforge-taskbar", daemon=True).start()
