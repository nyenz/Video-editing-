# Changelog

## 1.5.1
* **Own taskbar icon.** The app window was grouped under Microsoft Edge on the taskbar, and pinning it pinned Edge. Now the window has
  its own taskbar button with the Nyenz Edit icon, separate from your normal Edge windows, and pinning it pins Nyenz Edit.
  (If you pinned it before, unpin that old Edge button once and pin the new one.)

## 1.5.0
* **Also called Nyenz Edit.** The window, Home screen and installer icons say Nyenz Edit (the program file is still EditForge.exe,
  so updates replace the old version).
* **Home screen** (CapCut-simple): one big Create project, the three tools, and a projects grid with picture, name and `size | length`,
  newest opened first. Search, grid or list, Trash (undo, empty), and a menu per project: Open, Rename, Duplicate, Move to trash.
  A moved or deleted video shows "Video not found" with **Find it again**. The Clips page moved to `/clips`.
* **No more copies of big videos.** Choose file opens the normal Windows file window and uses the video where it is (linked). Only
  those exact files may be read. Dropped files are still copied, and that copy is deleted with the last project that uses it.
  **Storage** panel with Free up buttons; light copies and pictures unused for a month are cleaned up at start.
* **Steps** in the Editor: pin a result (with hand edits) and add another cut and pattern on top, as many times as you like, with no
  render in between. Pieces always stay ranges of the original video, so extending a cut reaches the original footage (also what an
  earlier step removed) and stops at the next piece. Export cuts the last step from the original once. Steps are saved with the
  project, and can be saved and repeated on another video.
* **Sound clean-up** (Editor, new Sound panel): remove noise (light, medium, strong), remove voices or keep only voices (stereo), clearer voices,
  balance quiet and loud parts, bass and treble.
* **Automatic updates**: a quiet "Update available" button; downloads, checks size and SHA-256, waits for exports, installs silently
  and reopens. Checks at most once a day, "Skip this version", silent when offline. Releases now carry `EditForge-Setup.exe.sha256`.
* **Faster:** pages are gzipped and kept by the window until they change (first screen 87 KB to 27 KB); the add-on check no longer
  loads OpenCV and Whisper (0.19 s to 0.02 s); video facts and fingerprints are remembered (opening a video again: 0.16 s to 0.002 s,
  autosave 0.08 s to 0.01 s); films with Dolby sound play after a quick copy (5 min 1080p film: 44 s to 8 s); a light copy starts as
  soon as a video is added; the Exports list asks less often when nothing is being made.
* Custom dialogs replace the browser's own question boxes. Leaving the Editor right after a change no longer loses it.

## 1.4.0
* **New look.** A dark, calm editing-suite design with real icons, custom menus, switches and sliders everywhere (no plain browser
  controls), and an app icon for the window, the taskbar, the desktop and the browser tab.
* **One-file Windows installer** (`EditForge-Setup.exe`): Python, FFmpeg, face tracking and captions inside. EditForge opens in its own
  window and stops when you close it. Built and tested on Windows by GitHub Actions for every change; tags publish a release.
* **Editor rebuilt like CapCut:** tools on the left, the selected pieces on the right, a real timeline at the bottom.
  Patterns change the timeline and the viewer at once. Drag to move pieces, drag edges to trim or extend, select many (Ctrl, Shift or a box),
  split at the playhead, undo. The viewer plays the edit live, without making a video first. Secondary things (effects, colour, zoom, follow)
  are folded away; music, shape and text are in a separate "Finish" group.
* **Length everywhere:** the finished length is always on screen and updates while you drag; the marked part, each clip, ticked clips
  and the combined result all show their length before anything is made.
* **Clips page:** mark parts by dragging on a timeline strip, tick clips and join just those, see the joined length on the button.
* **Sound from the start:** films with Dolby/DTS sound or formats a browser cannot play get a light copy with normal sound automatically.
* **Combine** shows the finished length and size before combining. A single **Exports** panel on every page.

## 1.3.0
* **Follow** (Editor, step 3): zoom in and keep a face in the middle, or draw a box around any thing and the view follows it.
  Works in the original shape, piece by piece. Faces are now also found when they are small in a wide shot.
* **Words on the picture** (Editor, step 4): add words with a start and end time, place, size, colour and an optional dark box.
* **Captions** (Editor, step 4): automatic from the speech (needs the faster-whisper add-on) or from a subtitle file (.srt or .vtt).
  Captions move with their pieces, also when pieces are moved or repeated.
* **Clip workshop:** joined clips now come out in the order of the list, with Up and Down buttons.
* **Quick play** in the Editor now also shows flip, colour looks, zoom and sound (not backwards and not following).
* **Beat detector:** no longer locks onto quiet off-beat hi-hats, tries half, double and two-thirds tempo for real and keeps the one
  that fits, drops beats counted into silence, and no longer reports a false bar length for music without accents.
  On 48 test tracks from 66 to 195 BPM it now gets 46 right (before: 31).
* Notes from a finished job (for example "no faces were found") are shown under it in the page.
* All tests pass: the five old failures are gone. Two were the beat detector (fixed), one was a test mistake, and two were tests
  that assumed a 1-CPU computer.
* 32 new tests (`tests/test_follow_text.py`).

## 1.2.0
New **Editor** page: a video cut into pieces that you pick and change.
* Cut into pieces of any length (for example 2 seconds), or on every Nth beat of your music.
* Pick pieces by clicking, Shift-clicking a range, "the others", or a pattern ("in every group of 3, pick number 1").
* Change the picked pieces: remove / put back, flip, mirror, upside down, black and white, invert, sepia, blur, sharpen, dark corners,
  speed, backwards, zoom in/out, colour, mute, loudness, start/end earlier or later, move earlier/later, make a copy, clear edits.
* Patterns and hand edits work together, at any time. Undo and redo. Projects are saved on disk as you work.
* Saved patterns: keep a pattern together with its edits and use it again on any project.
* Whole video: shape (original, tall 9:16, square; middle, follow faces, or blurred edges), transitions between pieces, music with
  loudness and "quieter when someone talks", keep or drop the original sound, even loudness, fades, quality.
* Quick preview (small and fast) and full-quality render. "Best" quality now uses a slower, more careful encoder setting.
New **Combine** page: two videos one after the other (with or without a transition), side by side, top and bottom,
small one in a corner, or one over the other.
* Pieces can be re-ordered, repeated and stretched over each other, which the script language cannot do.
* Fixed: "follow faces" stood still for any piece in the middle of a long, steady camera move.
* Jobs started from the page now use up to a quarter of the free memory (300 MB to 4 GB) instead of a fixed 300 MB, so they finish sooner.
* 53 new tests (`tests/test_project.py`, `tests/test_web_editor.py`).

## 1.1.0
New first page: the **Clip workshop**. Play your video, press "Set START here" and "Set END here", collect clips, and make them
(one video per clip, or all joined into one). No typing of commands. The old script page moved to "Advanced".
* Video player with seeking, frame and second steps, keyboard shortcuts, and a bar that shows your clips.
* If the browser cannot play a file, a light 480p copy is made just for watching. Finished clips are always cut from the original.
* Finished videos can be watched in the page and opened again as a new source ("Edit this video").
* Clips are cut frame-exact from the original at "Best" quality by default.
* Fixed: the face add-on crashed with OpenCV 5. EditForge now asks for OpenCV 4 (`pip install "opencv-python-headless<5"`) and
  reports the feature as switched off when version 5 is installed.
* 19 new tests that run the real web server (`tests/test_web_workshop.py`).

## 1.0.0
First release. Engine: bounded-memory piece renderer (MPEG-TS pieces joined by stream copy), exact frame/sample accounting, keyed resume,
atomic writes, line/YAML/JSON scripts with identical features, planner/dry-run, analysis cache, local web page, SQLite job store,
beat/downbeat detection, face tracking (optional OpenCV), captions from a transcript or local Whisper (optional), loudness (two-pass), music ducking.
See SELF_REVIEW.md for what is and is not verified.
