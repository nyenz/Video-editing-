"""Shared test setup: a private EditForge home and a set of small generated media files."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))


@pytest.fixture(scope="session", autouse=True)
def private_home(tmp_path_factory):
    home = tmp_path_factory.mktemp("ef_home")
    os.environ["EDITFORGE_HOME"] = str(home)
    yield home


@pytest.fixture(scope="session")
def media(tmp_path_factory, private_home):
    """Small generated media used by most tests (nothing is downloaded)."""
    from tools.make_samples import (make_audio, make_color_video, make_ramp_video, make_video, run, write_click_track)
    d = tmp_path_factory.mktemp("media")
    m = {"dir": d}
    m["speech"] = make_video(str(d / "speech.mp4"), 8, "320x180", "25", "speech")
    m["tone"] = make_video(str(d / "tone.mp4"), 6, "320x180", "25", "tone")
    m["ramp"] = make_ramp_video(str(d / "ramp.mp4"), 6)
    m["colors"] = make_color_video(str(d / "colors.mp4"), ["red", "green", "blue"], 2.0)
    m["scenes"] = make_color_video(str(d / "scenes.mp4"), ["red", "lime", "blue"], 2.0)
    m["black"] = make_color_video(str(d / "black.mp4"), ["black"], 4.0, audio=False)
    m["music_tone"] = make_audio(str(d / "music_1k.wav"), "0.3*sin(2*PI*1000*t)", 12.0)
    m["music_short"] = make_audio(str(d / "music_short.wav"), "0.3*sin(2*PI*1000*t)", 2.0)
    m["loud"] = make_audio(str(d / "loud.wav"), "0.97*sin(2*PI*440*t)", 10.0)
    run(["-f", "lavfi", "-i", "sine=frequency=440:duration=6", "-f", "lavfi", "-i", "anoisesrc=amplitude=0.08:color=white:duration=6:seed=3",
         "-filter_complex", "[0:a]volume=0.4[a];[a][1:a]amix=inputs=2:normalize=0", str(d / "noisy.wav")])
    m["noisy"] = str(d / "noisy.wav")
    m["silent"] = make_video(str(d / "silent.mp4"), 4, "320x180", "25", "none")
    m["silent8"] = make_video(str(d / "silent8.mp4"), 8, "320x180", "25", "none")
    m["pal30"] = make_video(str(d / "ntsc.mp4"), 5, "320x180", "30000/1001", "tone")
    m["tall"] = make_video(str(d / "tall.mp4"), 4, "180x320", "25", "tone")
    m["audio"] = make_audio(str(d / "bursts.wav"), "if(lt(mod(t,3),1.5),0.5*sin(2*PI*440*t),0)", 9.0)
    m["tone_wav"] = make_audio(str(d / "tone.wav"), "0.3*sin(2*PI*440*t)", 12.0)
    m["quiet"] = make_audio(str(d / "quiet.wav"), "0.02*sin(2*PI*300*t)+0.01*sin(2*PI*555*t)", 15.0)
    m["click4"] = str(d / "click_4_4.wav")
    write_click_track(m["click4"], 120, 4, 24)
    m["click3"] = str(d / "click_3_4.wav")
    write_click_track(m["click3"], 100, 3, 24)
    m["flat"] = str(d / "click_flat.wav")
    write_click_track(m["flat"], 120, 4, 24, accent=1.0)
    # a video whose sound is a click track (for beat tests)
    run(["-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=24", "-i", m["click4"], "-c:v", "libx264", "-preset", "ultrafast", "-g", "25",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(d / "beatvideo.mp4")])
    m["beatvideo"] = str(d / "beatvideo.mp4")
    run(["-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=24", "-i", m["flat"], "-c:v", "libx264", "-preset", "ultrafast", "-g", "25",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(d / "flatvideo.mp4")])
    m["flat_video"] = str(d / "flatvideo.mp4")
    # variable frame rate
    run(["-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=6", "-f", "lavfi", "-i", "sine=frequency=440:duration=6", "-vf",
         "select='lt(mod(n,10),6)+gt(mod(n,30),24)'", "-fps_mode", "vfr", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
         "-shortest", str(d / "vfr_raw.mp4")])
    # a file with a rotation flag (like phone video)
    run(["-display_rotation", "90", "-i", m["tone"], "-c", "copy", str(d / "rotated.mp4")])
    m["rotated"] = str(d / "rotated.mp4")
    m["vfr"] = str(d / "vfr_raw.mp4")
    odd = d / "d\u00e9j\u00e0 vu \u00f1 \u65e5\u672c\u8a9e (1).mp4"
    shutil.copy(m["tone"], odd)
    m["odd"] = str(odd)
    # image for logos
    run(["-f", "lavfi", "-i", "color=c=red:size=64x64:d=1", "-frames:v", "1", str(d / "logo.png")])
    m["logo"] = str(d / "logo.png")
    # a short timed transcript
    (d / "talk.srt").write_text("1\n00:00:00,500 --> 00:00:02,000\nHello brave new world\n\n2\n00:00:04,000 --> 00:00:05,500\nsecond line here\n\n"
                                "3\n00:00:06,000 --> 00:00:07,500\nthird line ends it\n", encoding="utf-8")
    m["srt"] = str(d / "talk.srt")
    return m


@pytest.fixture()
def out(tmp_path):
    def make(name: str = "out.mp4") -> str:
        return str(tmp_path / name)
    return make
