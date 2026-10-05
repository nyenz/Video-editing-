@echo off
rem EditForge launcher for Windows. Double-click this file.
cd /d "%~dp0"
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo FFmpeg is not installed.
  echo How to fix: open the Start menu, type cmd, and run:  winget install Gyan.FFmpeg
  echo Then close this window and double-click start.bat again.
  pause
  exit /b 1
)
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.10 or newer is not installed. Get it from https://www.python.org/downloads/
  echo Tick "Add python.exe to PATH" in the installer.
  pause
  exit /b 1
)
if not exist .venv (
  echo First start: setting things up. This takes a minute and needs internet once...
  python -m venv .venv
  .venv\Scripts\python -m pip install --quiet --upgrade pip
  .venv\Scripts\python -m pip install --quiet -r requirements.txt
)
.venv\Scripts\python -m editforge web %*
pause
