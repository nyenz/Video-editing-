# Changelog

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
