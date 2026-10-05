# Troubleshooting

Every error EditForge shows has a "How to fix" line. Add `--verbose` to see technical details.

**"FFmpeg is not installed"** - Windows: `winget install Gyan.FFmpeg`, then reopen the window. Mac: `brew install ffmpeg`. Linux: `sudo apt install ffmpeg`.

**"The computer ran out of memory"** - use `--preset preview_480p`, lower `--max-memory`, or close other programs. 4K output needs more than the default 300 MB budget (EditForge says so in the plan notes).

**"already exists"** - add `--overwrite` or choose another name with `-o`.

**Text does not show / "no usable font"** - install a common font (DejaVu Sans, Arial) or give `font=C:/path/to/font.ttf`.

**Automatic captions are "switched off"** - install the add-on (`pip install faster-whisper`); the first use of a model downloads it once (needs internet once). Or give your own subtitle file: `captions transcript=talk.srt`.

**Face following is "switched off"** - `pip install "opencv-python-headless<5"`, or use `reframe 9:16 mode=center`.

**A cut is in the wrong place with --fast-cuts** - that is how fast cuts work (cuts snap to keyframes). Leave `--fast-cuts` off for frame-exact cuts.

**The scene detector missed a change** - FFmpeg's scene score follows brightness, so a change between two colours of equal brightness can be missed. Lower `threshold=` or cut by time.

**The job stopped or the computer restarted** - run the same command again (finished pieces are reused), or press Resume in the web page.

**Hardware encoder fails** - EditForge tests encoders first and falls back to software by itself. `editforge encoders --retest` shows what works.
