# Changelog

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
