#!/usr/bin/env python3
"""Make sample media for trying EditForge and for the automated tests.

Everything is generated locally with FFmpeg (and plain Python for music), so there is nothing to download.

    python tools/make_samples.py            # writes ./samples/*.mp4 and *.wav
    python tools/make_samples.py OUTFOLDER
"""

from __future__ import annotations

import array
import math
import os
import random
import struct
import subprocess
import sys
import wave
from pathlib import Path
from typing import List, Optional, Sequence


def _ffmpeg() -> str:
    from editforge.core.ffmpeg import get_tools
    return get_tools().ffmpeg


def run(args: Sequence[str]) -> None:
    """Run ffmpeg quietly and raise if it fails."""
    cmd = [_ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + proc.stderr.decode("utf-8", "replace")[-600:])


def make_video(path: str, seconds: float = 6.0, size: str = "320x180", fps: str = "25", audio: str = "tone",
               gop: int = 25, preset: str = "ultrafast", pattern: str = "testsrc2", extra_video: Sequence[str] = ()) -> str:
    """Make a small test video. ``audio``: 'tone', 'speech' (bursts), 'none', or 'silence'."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    args = ["-f", "lavfi", "-i", f"{pattern}=size={size}:rate={fps}:duration={seconds}"]
    if audio == "tone":
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}"]
    elif audio == "speech":
        # 1 second of "speech" (tone) then 1 second of silence, repeating
        args += ["-f", "lavfi", "-i", f"aevalsrc='if(lt(mod(t,2),1),0.4*sin(2*PI*300*t)+0.2*sin(2*PI*600*t),0)':s=48000:d={seconds}"]
    elif audio == "silence":
        args += ["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={seconds}"]
    args += ["-c:v", "libx264", "-preset", preset, "-g", str(gop), "-pix_fmt", "yuv420p", *extra_video]
    if audio != "none":
        args += ["-c:a", "aac", "-b:a", "96k", "-shortest"]
    else:
        args += ["-an"]
    run(args + [path])
    return path


def make_ramp_video(path: str, seconds: float = 6.0, size: str = "160x90", fps: str = "25") -> str:
    """A video that gets steadily brighter (black to white). Used to check reversing and cutting."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    run(["-f", "lavfi", "-i", f"color=c=black:size={size}:rate={fps}:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=330:sample_rate=48000:duration={seconds}",
         "-vf", f"geq=lum='255*T/{seconds}':cb=128:cr=128", "-c:v", "libx264", "-preset", "ultrafast", "-g", "25",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path])
    return path


def make_color_video(path: str, colors: Sequence[str], seconds_each: float = 2.0, size: str = "160x90", fps: str = "25",
                     audio: bool = True) -> str:
    """A video made of solid colour blocks (easy to check by eye or by pixel)."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    inputs: List[str] = []
    for c in colors:
        inputs += ["-f", "lavfi", "-i", f"color=c={c}:size={size}:rate={fps}:duration={seconds_each}"]
    n = len(colors)
    graph = "".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0[v]"
    args = inputs
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds_each * n}"]
    args += ["-filter_complex", graph, "-map", "[v]"]
    if audio:
        args += ["-map", f"{n}:a", "-c:a", "aac", "-shortest"]
    args += ["-c:v", "libx264", "-preset", "ultrafast", "-g", "25", "-pix_fmt", "yuv420p", path]
    run(args)
    return path


def make_audio(path: str, expr: str, seconds: float, sample_rate: int = 48000) -> str:
    """Make an audio file from an FFmpeg expression (aevalsrc)."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    run(["-f", "lavfi", "-i", f"aevalsrc='{expr}':s={sample_rate}:d={seconds}", path])
    return path


def write_click_track(path: str, bpm: float = 120.0, beats_per_bar: int = 4, seconds: float = 24.0,
                      accent: float = 2.0, sample_rate: int = 22050, offbeat: bool = True, seed: int = 1) -> List[float]:
    """Write a WAV drum-like track with known beats. Returns the true beat times.

    The first beat of every bar is louder and lower (a kick with an accent); ``accent=1``
    makes every beat identical, so no metre can be told from the sound.
    """
    rnd = random.Random(seed)
    n = int(seconds * sample_rate)
    buf = [0.0] * n
    period = 60.0 / bpm
    beats: List[float] = []

    def add_kick(t0: float, amp: float, freq: float) -> None:
        i0 = int(t0 * sample_rate)
        for k in range(int(0.09 * sample_rate)):
            i = i0 + k
            if i >= n:
                break
            tt = k / sample_rate
            buf[i] += amp * math.sin(2 * math.pi * (freq + 40 * math.exp(-tt * 40)) * tt) * math.exp(-tt * 35)

    def add_hat(t0: float, amp: float) -> None:
        i0 = int(t0 * sample_rate)
        for k in range(int(0.03 * sample_rate)):
            i = i0 + k
            if i >= n:
                break
            buf[i] += amp * (rnd.random() * 2 - 1) * math.exp(-k / sample_rate * 150)

    idx = 0
    t = 0.5
    while t < seconds - 0.2:
        beats.append(round(t, 6))
        strong = idx % beats_per_bar == 0
        add_kick(t, 0.9 if strong else 0.9 / accent, (55.0 if strong else 75.0) if accent != 1.0 else 65.0)
        add_hat(t, 0.15)
        if offbeat:
            add_hat(t + period / 2, 0.08)
        idx += 1
        t += period
    peak = max(1e-9, max(abs(v) for v in buf))
    data = array.array("h", (int(max(-1.0, min(1.0, v / peak * 0.9)) * 32767) for v in buf))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(data.tobytes())
    return beats


def make_all(folder: str) -> List[str]:
    """Create the standard sample set in ``folder`` and return the file names."""
    out = Path(folder)
    out.mkdir(parents=True, exist_ok=True)
    made = []
    made.append(make_video(str(out / "sample_landscape.mp4"), 20, "640x360", "25", "speech"))
    made.append(make_video(str(out / "sample_short.mp4"), 6, "320x180", "25", "tone"))
    made.append(make_color_video(str(out / "sample_colors.mp4"), ["red", "green", "blue", "yellow"], 3.0))
    write_click_track(str(out / "sample_music_120bpm.wav"), 120, 4, 30)
    made.append(str(out / "sample_music_120bpm.wav"))
    return made


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    target = sys.argv[1] if len(sys.argv) > 1 else "samples"
    for name in make_all(target):
        print("made", name)
