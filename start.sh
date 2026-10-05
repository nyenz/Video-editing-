#!/usr/bin/env bash
# EditForge launcher for Mac and Linux. Run:  bash start.sh   (or double-click start.command on a Mac)
set -e
cd "$(dirname "$0")"
if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  echo "FFmpeg is not installed."
  echo "How to fix:  Mac:   install Homebrew from https://brew.sh then run:  brew install ffmpeg"
  echo "             Linux: run:  sudo apt install ffmpeg    (or: sudo dnf install ffmpeg)"
  exit 1
fi
PY=python3
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "Python 3.10 or newer is not installed. Get it from https://www.python.org/downloads/"
  exit 1
fi
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'; then
  echo "Your Python is too old. EditForge needs Python 3.10 or newer: https://www.python.org/downloads/"
  exit 1
fi
if [ ! -d .venv ]; then
  echo "First start: setting things up (this takes a minute, needs internet once)..."
  "$PY" -m venv .venv
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --quiet -r requirements.txt
fi
exec .venv/bin/python -m editforge web "$@"
