# Self review (honest)

## Version 1.2.0 (Editor and Combine) - what was and was not checked
* One pass on Linux, FFmpeg 6.1.1, Python 3.13, 2 CPUs: **659 passed, 5 failed, 4 skipped**. The 5 failures are the same old ones
  listed under 1.1.0 below (three beat/bar tests; two tests that only pass on a 1-CPU machine).
* The Editor and Combine pages were driven end to end in a real Chromium browser (with a WebM video): new project, cut into pieces,
  pattern picks, every kind of edit button, Shift-click range, move, copy, undo/redo, Delete key, saved pattern save and use, music upload,
  cut on the beat, quick preview, full render, reload (everything kept), combine in a corner, and the buttons that jump between pages.
* Output was checked by reading pixels and sound levels: removed, moved, repeated, stretched, flipped, black-and-white, backwards, muted,
  music, transitions, every shape, and every combine layout.
* "Follow faces" was run once on a test video with a real face photo sliding across the frame (OpenCV 4.14). The crop followed it after
  the fix in this version. It was **not** tried on real footage, several faces, or turned faces. It only works with the tall and square
  shapes; there is no "zoom in and follow" in the original shape yet.
* **Not checked:** H.264/MP4 playback in the page (the test browser cannot play it), Windows and Mac, very long videos in the Editor.
  A project can have at most 5,000 pieces; the grid was only tried with up to 60.
* The quick play in step 2 of the Editor shows cuts, order and speed only, not looks or backwards. Use Quick preview for those.
* Transitions make the video a little shorter at every cut, so with "Cut on the beat" they drift off the beat (the page says so).
  Next to a backwards piece a transition can be shorter than asked, more so at large picture sizes.
* Combine renders in one FFmpeg run (not the low-memory piece renderer). It was only tried with short clips.
* Cut on the beat uses the beat detector, which fails three of its own tests on unusual tempos. It was right on a 120 BPM click track.

## Version 1.1.0 (Clip workshop) - what was and was not checked
* Ran in one pass on Linux, FFmpeg 6.1.1, Python 3.13, 2 CPUs: **606 passed, 5 failed, 4 skipped**. The 5 failures are the same ones
  as in 1.0.0: three beat/bar tests, and two tests (resume after cancel, memory "+40 MB") that only pass on a 1-CPU machine.
  Resume itself works when the settings are the same (checked by killing a 150-cut render and running it again: 52 pieces reused).
* The workshop page was driven end to end in a real Chromium browser with a WebM video: upload, play, jump, frame steps, set start/end,
  typed times, keyboard shortcuts, "Play this part", one video per clip, joined video, reload (clips remembered), "Edit this video".
  Clip lengths were checked with ffprobe and were exact to the frame.
* **Not checked:** playing H.264/MP4 in the page. The test browser has no H.264 support, so only the "light copy" switch-over logic ran
  there, not the picture. Chrome and Edge play H.264, but that was not seen here.
* **Not checked:** Windows and Mac (launchers, paths), a 4-hour file in the page, and uploads of many gigabytes through the browser.
* The clip list is remembered in the browser only (per video). It is not yet a saved project on disk.
* Joined clips always come out in time order, not in the order you added them.

## Version 1.0.0 notes (unchanged below)

This release was packaged early at the user's request, before the planned test suite and docs were finished.

## What was actually run (real FFmpeg, generated media, in the build sandbox)
| test file | result when last run |
|---|---|
| tests/test_timecode.py + tests/test_dsl.py | 146 passed |
| tests/test_planner_times.py (every time format on 27 instructions) | 217 passed |
| tests/test_planner.py | 44 passed |
| tests/test_render.py | 57 passed |
| tests/test_effects.py | 33 passed in the first run; the 4 failures were test mistakes and, after fixing them, only those 4 were re-run (passed); the whole file was not re-run in one go |
| tests/test_audio_captions.py | 31 passed; the noise-reducer failure was fixed afterwards and only that test re-run (passed) |
| tests/test_robustness.py | 26 passed in one run; the 3 failures (thread limit, fast-cut packet count, encoder fallback) were fixed and those 3 re-run (passed) |
| tests/test_beats_faces.py | 3 failed, 30 passed in 30.40s (see below) |

About 563 tests exist. **They were never run together in one final pass.** `pytest` is not yet green.

## Known failing tests (not fixed)
* `test_tempo_beats_and_metre_of_synthetic_music[140-4]`: for one synthetic 140 BPM track the bar structure was reported as unknown (the detector is deliberately cautious).
* `test_beats_of_a_video_file_and_short_or_silent_input` and `test_metre_finder_on_ideal_accents[2]`: not investigated; they may be test mistakes or detector weaknesses.

## Not written
* No tests for the web server, the CLI, or the docs (the README options were not machine-checked against the code).
  The web server was only exercised by hand (token, host check, upload, plan, job, download, path traversal) and worked.
* The CLI was run by hand for features/probe/plan/edit/validate/presets/encoders, not by automated tests.

## Not verified at all
* Real Whisper transcription (no model can be downloaded here). Caption timing, SRT/ASS writing, burn-in and soft tracks ARE tested using a transcript file.
* Hardware encoders: only the "test-encode then fall back to software" path ran (NVENC and QuickSync exist in this FFmpeg but there is no GPU). The option strings for each vendor are unit-tested for shape only.
* The Windows/Mac launchers (`start.bat`, `start.command`) were never run; Windows font/path handling is untested.
* A 4-hour source, 4K output, and multi-core parallel speed.
* Speed-ramp audio quality (steps are joined without a crossfade; small clicks are possible).

## Known weaknesses
* Memory: under 300 MB holds up to 1080p (measured). 4K cannot fit; the plan says so.
* Pieces are encoded with B-frames and joined at keyframes; quality is the same as a normal CRF encode but file size can be a few percent larger than one single encode.
* Scene detection follows brightness (FFmpeg scene score); changes between colours of equal brightness are missed.
* Downbeat detection and "active speaker" are heuristics. Uniform or weakly accented music reports "unknown" rather than guessing.
* Fast cuts need H.264 8-bit 4:2:0 sources and cannot be mixed with picture changes.
* `zoom`/`pan` use FFmpeg `zoompan`, which rounds to whole pixels (slight shimmer at slow zooms). Slow motion repeats frames (no interpolation).
* Transitions are not added between neighbouring clips that are continuous in the source (it would replay footage); use `contiguous=true` to force.
* Face tracking uses OpenCV Haar cascades: weaker for turned or small faces.
