# PyInstaller recipe for the Windows app. Run from the repository root:
#     pyinstaller packaging/editforge.spec --noconfirm
# FFmpeg (ffmpeg.exe and ffprobe.exe) must be in packaging/ffmpeg/ first; the build workflow downloads it.
import os
from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
datas = [(os.path.join(ROOT, "editforge", "web", "static"), os.path.join("editforge", "web", "static")),
         (os.path.join(ROOT, "examples"), "examples")]
binaries = []
hidden = ["editforge.web.server", "editforge.cli.main", "editforge.taskbar",
          "win32gui", "win32con", "win32process", "pythoncom", "pywintypes", "win32com.propsys.propsys", "win32com.propsys.pscon"]
ff = os.path.join(SPECPATH, "ffmpeg")
for exe in ("ffmpeg.exe", "ffprobe.exe"):
    if os.path.exists(os.path.join(ff, exe)):
        binaries.append((os.path.join(ff, exe), "ffmpeg"))

# Optional add-ons: included when installed in the build environment.
for pkg in ("faster_whisper", "ctranslate2", "cv2"):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hidden += h
    except Exception as exc:  # noqa: BLE001
        print("skipping", pkg, exc)

a = Analysis([os.path.join(SPECPATH, "editforge_app.py")], pathex=[ROOT], binaries=binaries, datas=datas,
             hiddenimports=hidden, excludes=["matplotlib", "pandas", "IPython", "jupyter", "pytest"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="EditForge", icon=os.path.join(SPECPATH, "editforge.ico"),
          console=False, upx=False, version=os.path.join(SPECPATH, "version_info.txt") if os.path.exists(os.path.join(SPECPATH, "version_info.txt")) else None)
coll = COLLECT(exe, a.binaries, a.datas, name="EditForge", upx=False)
