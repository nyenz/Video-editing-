"""The normal "Open file" window of the computer, opened by the app itself.

The page cannot read where a chosen file lives (browsers hide the folder), so it would have to send
the whole file to EditForge: a 10 GB film became a 10 GB copy. Instead the app opens the computer's own
file window, gets the real path, and uses the file where it is ("linked", no copy).
"""

from __future__ import annotations

import os
import threading
from typing import Optional

KINDS = {
    "video": ("Videos", "*.mp4;*.mkv;*.mov;*.m4v;*.avi;*.webm;*.ts;*.mts;*.m2ts;*.wmv;*.flv;*.mpg;*.mpeg;*.3gp"),
    "media": ("Videos and sound", "*.mp4;*.mkv;*.mov;*.m4v;*.avi;*.webm;*.ts;*.mts;*.m2ts;*.wmv;*.flv;*.mpg;*.mpeg;*.3gp;"
                                  "*.mp3;*.wav;*.m4a;*.aac;*.flac;*.ogg;*.opus;*.wma"),
    "audio": ("Music and sound", "*.mp3;*.wav;*.m4a;*.aac;*.flac;*.ogg;*.opus;*.wma"),
    "subtitles": ("Subtitles", "*.srt;*.vtt"),
}
TITLES = {"video": "Choose a video", "media": "Choose a video", "audio": "Choose music", "subtitles": "Choose a subtitle file"}
_ONE_AT_A_TIME = threading.Lock()


class NoDialog(Exception):
    """This computer has no file window EditForge can open (for example a server without a screen)."""


class Busy(Exception):
    """A file window is already open."""


def choose_file(kind: str = "video", start_dir: Optional[str] = None) -> Optional[str]:
    """Show the file window and return the chosen path, or None when the person pressed Cancel."""
    if not _ONE_AT_A_TIME.acquire(blocking=False):
        raise Busy()
    try:
        if os.name == "nt":
            try:
                return _windows(kind, start_dir)
            except NoDialog:
                raise
            except Exception:  # noqa: BLE001 - try the other way below
                pass
        return _tk(kind, start_dir)
    finally:
        _ONE_AT_A_TIME.release()


def _windows(kind: str, start_dir: Optional[str]) -> Optional[str]:
    """The Windows "Open" window (comdlg32), in front of the EditForge window."""
    import ctypes
    from ctypes import wintypes

    class OPENFILENAMEW(ctypes.Structure):
        _fields_ = [("lStructSize", wintypes.DWORD), ("hwndOwner", wintypes.HWND), ("hInstance", wintypes.HINSTANCE),
                    ("lpstrFilter", ctypes.c_void_p), ("lpstrCustomFilter", ctypes.c_void_p), ("nMaxCustFilter", wintypes.DWORD),
                    ("nFilterIndex", wintypes.DWORD), ("lpstrFile", ctypes.c_void_p), ("nMaxFile", wintypes.DWORD),
                    ("lpstrFileTitle", ctypes.c_void_p), ("nMaxFileTitle", wintypes.DWORD), ("lpstrInitialDir", ctypes.c_void_p),
                    ("lpstrTitle", ctypes.c_void_p), ("Flags", wintypes.DWORD), ("nFileOffset", wintypes.WORD),
                    ("nFileExtension", wintypes.WORD), ("lpstrDefExt", ctypes.c_void_p), ("lCustData", wintypes.LPARAM),
                    ("lpfnHook", ctypes.c_void_p), ("lpTemplateName", ctypes.c_void_p), ("pvReserved", ctypes.c_void_p),
                    ("dwReserved", wintypes.DWORD), ("FlagsEx", wintypes.DWORD)]

    label, pattern = KINDS.get(kind, KINDS["video"])
    filt = ctypes.create_unicode_buffer(f"{label}\0{pattern}\0All files\0*.*\0\0")
    size = 32768
    buf = ctypes.create_unicode_buffer(size)
    title = ctypes.create_unicode_buffer(TITLES.get(kind, "Choose a file"))
    start = ctypes.create_unicode_buffer(start_dir) if start_dir and os.path.isdir(start_dir) else None
    ofn = OPENFILENAMEW()
    ofn.lStructSize = ctypes.sizeof(OPENFILENAMEW)
    ofn.hwndOwner = ctypes.windll.user32.GetForegroundWindow()        # the EditForge window, so the file window opens in front
    ofn.lpstrFilter = ctypes.addressof(filt)
    ofn.nFilterIndex = 1
    ofn.lpstrFile = ctypes.addressof(buf)
    ofn.nMaxFile = size
    ofn.lpstrTitle = ctypes.addressof(title)
    if start is not None:
        ofn.lpstrInitialDir = ctypes.addressof(start)
    # OFN_EXPLORER | OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST | OFN_HIDEREADONLY | OFN_NOCHANGEDIR | OFN_DONTADDTORECENT(no)
    ofn.Flags = 0x00080000 | 0x00001000 | 0x00000800 | 0x00000004 | 0x00000008
    get_open = ctypes.windll.comdlg32.GetOpenFileNameW
    get_open.argtypes = [ctypes.POINTER(OPENFILENAMEW)]
    get_open.restype = wintypes.BOOL
    if get_open(ctypes.byref(ofn)):
        return buf.value or None
    err = ctypes.windll.comdlg32.CommDlgExtendedError()
    if err:
        raise RuntimeError(f"file window error {err}")
    return None                                   # Cancel


def _tk(kind: str, start_dir: Optional[str]) -> Optional[str]:
    try:
        import tkinter
        from tkinter import filedialog
    except Exception as exc:  # noqa: BLE001
        raise NoDialog() from exc
    if os.name != "nt" and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY") and os.uname().sysname != "Darwin":
        raise NoDialog()
    try:
        root = tkinter.Tk()
    except Exception as exc:  # noqa: BLE001 - no screen
        raise NoDialog() from exc
    try:
        root.withdraw()
        root.attributes("-topmost", True)
        root.update()
        label, pattern = KINDS.get(kind, KINDS["video"])
        path = filedialog.askopenfilename(parent=root, title=TITLES.get(kind, "Choose a file"),
                                          initialdir=start_dir if start_dir and os.path.isdir(start_dir) else None,
                                          filetypes=[(label, " ".join(pattern.split(";"))), ("All files", "*.*")])
        return str(path) if path else None
    finally:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass
