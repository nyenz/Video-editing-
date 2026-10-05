# Self review (honest)

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
