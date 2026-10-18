# Benchmarks

## Method
`tests/test_robustness.py::test_memory_stays_flat_with_many_cuts` runs `editforge edit` in a child process and samples the resident memory of the
whole process tree (Python + every FFmpeg) every 30 ms, keeping the peak (`PeakMemoryMonitor`, summed RSS, so shared pages are counted more than once - a conservative number).
To benchmark yourself: make a long source with `python tools/make_samples.py`, run `editforge edit script.txt -i long.mp4 --no-resume` and read "peak memory" in the summary line.

## Measured numbers (real runs, in the build sandbox ONLY)
Machine: 1 CPU, 4 GB RAM, FFmpeg 6.1.1 (libx264), software encoder, default `--max-memory 300`. Source: 120 s, H.264, 25 fps.

| source | cuts | output | peak memory (whole tree) | time |
|---|---|---|---|---|
| 640x360 | 6 | 7.2 s | 171 MB | 1.4 s |
| 640x360 | 48 | 57.6 s | 180 MB | 9.1 s |
| 1920x1080 | 6 | 7.2 s | 296 MB | 6.9 s |
| 1920x1080 | 48 | 57.6 s | 297 MB | 52.4 s |

Memory does not grow with the number of cuts. The automated test (640x360, 6 vs 40 cuts) asserts < 300 MB and "40 cuts within 40 MB of 6 cuts".
Before tuning, the same 1080p job measured about 403 MB; the fix was size-dependent x264 settings plus a measured memory model (`core/resources.py`).

## Loading (version 1.5.0, `python tools/bench_app.py film.mkv`)
Sandbox: 2 CPUs, Linux, FFmpeg from the system. Film: 5 min, 1920x1080 H.264 with 5.1 AC-3 sound in .mkv (500 MB). Not a Windows PC.

| step | 1.4.0 | 1.5.0 |
|---|---|---|
| server ready | 0.17 s | 0.18 s |
| first page with its scripts | 87 KB | 27 KB (gzip; then kept by the window) |
| add-on check | 0.19 s | 0.02 s |
| video facts, first time / again | 0.40 s / 0.16 s | 0.27 s / 0.002 s |
| new project / open / autosave | 0.21 / 0.07 / 0.08 s | 0.008 / 0.005 / 0.008 s |
| film plays with sound | 44 s (light copy) | 8 s (quick copy), light copy after 50 s |

The Windows self-test in GitHub Actions prints `TIME` lines (app start to server ready, each page, the add-on check).

## Not measured
* No comparison with the four older apps was run (they were not available here), so no speed-up claim is made.
* 4K output: the memory model predicts about 490 MB for one worker; this was not run.
* A 4-hour source was not run. Analysis is streamed and the renderer keeps no per-cut state in memory, but that is a design argument, not a measurement.
* Multi-core speed-up and hardware encoders could not be measured on a 1-CPU machine without a GPU.
