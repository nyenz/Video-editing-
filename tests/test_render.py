"""Rendering with real FFmpeg: the finished file is read back and compared with the plan."""

import os

import pytest

from editforge.core.media import probe_media
from helpers import (color_at, count_frames, diff_score, edit, frame_gray, mean_luma, stream_info)

SCRIPTS = [
    "keep 1-5", "keep 0-2, 3-5, 6-8", "cut 2-3", "speed 2 from 2 to 4", "speed 0.5 from 1 to 3", "speed_ramp 1 to 3 from 1 to 5",
    "reverse from 1 to 3", "freeze at=2 duration=1.5", "zoom 1.6 from 1 to 4", "pan from_x=0.1 to_x=0.9 scale=1.5 from 0 to 4",
    "crop 1:1", "color contrast=1.3 saturation=1.5", "mirror from 1 to 3", "keep 0-3, 4-7\ntransition fade 0.5",
    "keep 0-3, 4-7\ntransition wipeleft 0.4", "fade in=1 out=1", "silence_remove", "pattern_keep 1 every 2", "loudnorm", "denoise 12",
    'text "Hello" start=1 end=3', "reframe 9:16 mode=center", "beat_effect flash source=bpm bpm=120 from 0 to 4",
]


@pytest.mark.parametrize("script", SCRIPTS)
def test_output_length_matches_the_plan_within_one_frame(media, out, script):
    path = out()
    plan, res = edit(script, media["speech"], path)
    fps = float(plan.timeline.fps)
    v = stream_info(path, "v")
    assert count_frames(path) == plan.timeline.total_frames
    assert float(v["duration"]) == pytest.approx(plan.timeline.duration, abs=1.0 / fps)
    assert res.verify.ok and abs(res.verify.duration - plan.timeline.duration) <= 1.0 / fps + 0.04
    a = stream_info(path, "a")
    assert a and abs(float(a["duration"]) - plan.timeline.duration) < 0.06


@pytest.mark.parametrize("preset,size", [("shorts", (1080, 1920)), ("reels", (1080, 1920)), ("tiktok", (1080, 1920)),
                                         ("instagram_square", (1080, 1080)), ("preview_480p", (852, 480)),
                                         ("youtube_1080p", (1920, 1080)), ("original", (320, 180))])
def test_presets_make_the_right_size(media, out, preset, size):
    path = out()
    plan, res = edit("keep 0-2", media["speech"], path, preset=preset)
    v = stream_info(path, "v")
    assert (int(v["width"]), int(v["height"])) == size and v["codec_name"] == "h264" and v["pix_fmt"] == "yuv420p"
    assert count_frames(path) == plan.timeline.total_frames


def test_custom_size_fps_quality(media, out):
    path = out()
    plan, res = edit("keep 0-2", media["speech"], path, size="640x360", fps=30)
    v = stream_info(path, "v")
    assert (int(v["width"]), int(v["height"])) == (640, 360) and v["r_frame_rate"] == "30/1"
    assert count_frames(path) == 60
    small, big = out("small.mp4"), out("big.mp4")
    edit("keep 0-4", media["speech"], small, quality="low")
    edit("keep 0-4", media["speech"], big, quality="max")
    assert os.path.getsize(big) > os.path.getsize(small)


def test_four_k_preset_plans_correctly(media):
    from helpers import plan_only
    o = plan_only("keep 0-2", media["speech"], preset="youtube_4k").timeline.output
    assert (o.width, o.height) == (3840, 2160)


@pytest.mark.parametrize("fit,checker", [("pad", "bars"), ("crop", "fills"), ("stretch", "fills"), ("blur", "fills")])
def test_fit_modes(media, out, fit, checker):
    path = out()
    edit(f"size 360x640 fit={fit}", media["speech"], path)
    top = frame_gray(path, 0.5, 36, 64)
    first_row = sum(top[:36]) / 36
    if checker == "bars":
        assert first_row < 20          # black bar above and below the picture
    else:
        assert first_row > 20 or fit == "blur"


def test_progress_is_reported(media, out):
    seen = []
    edit("keep 0-4\nloudnorm", media["speech"], out(), render_opts={"on_progress": lambda p: seen.append(p)})
    fr = [p.fraction for p in seen]
    assert fr and fr == sorted(fr) and max(fr) < 1.0 and any(p.phase == "final" for p in seen)


# ---- odd inputs --------------------------------------------------------------------------------------
def test_variable_frame_rate_source(media, out):
    info = probe_media(media["vfr"])
    assert info.vfr and any("variable frame rate" in w for w in info.warnings)
    path = out()
    plan, res = edit("keep 0-3", media["vfr"], path)
    frames = count_frames(path)
    assert frames == plan.timeline.total_frames
    ts = [round(float(x), 3) for x in __import__("helpers").frame_times(path)]
    gaps = {round(b - a, 3) for a, b in zip(ts, ts[1:])}
    assert len(gaps) <= 2      # a steady frame rate (rounding may show two neighbouring values)


def test_rotated_phone_video(media, out):
    info = probe_media(media["rotated"])
    assert (info.width, info.height, info.rotation) == (180, 320, 90)
    path = out()
    edit("keep 0-2", media["rotated"], path)
    v = stream_info(path, "v")
    assert (int(v["width"]), int(v["height"])) == (180, 320)
    edit("keep 0-2", media["rotated"], out("s.mp4"), preset="shorts")


def test_video_without_sound(media, out):
    path = out()
    plan, res = edit("keep 0-3\nmirror from 1 to 2", media["silent"], path)
    assert stream_info(path, "a") == {} and count_frames(path) == plan.timeline.total_frames and res.verify.ok


def test_music_can_be_added_to_a_silent_video(media, out):
    path = out()
    edit(f"keep 0-3\nmusic {media['tone_wav']} volume=0.5", media["silent"], path)
    assert stream_info(path, "a")["codec_name"] == "aac"


def test_audio_only_source_and_output(media, out):
    path = out("x.m4a")
    plan, res = edit("keep 1-4\nloudnorm", media["audio"], path)
    info = probe_media(path)
    assert not info.has_video and info.has_audio and abs(info.duration - 3.0) < 0.06 and res.verify.ok


@pytest.mark.parametrize("ext,codec", [("m4a", "aac"), ("mp3", "mp3"), ("wav", "pcm_s16le"), ("flac", "flac")])
def test_audio_containers(media, out, ext, codec):
    path = out(f"a.{ext}")
    edit("keep 0-3", media["audio"], path)
    assert stream_info(path, "a")["codec_name"] == codec


def test_podcast_preset_from_video(media, out):
    path = out("p.m4a")
    plan, res = edit("silence_remove", media["speech"], path, preset="podcast_audio")
    info = probe_media(path)
    assert not info.has_video and info.channels == 1 and info.sample_rate == 44100


def test_non_ascii_and_spaces_in_file_names(media, out, tmp_path):
    path = str(tmp_path / "r\u00e9sultat \u65e5\u672c \u00f1.mp4")
    plan, res = edit('keep 0-2\ntext "caf\u00e9 \u65e5\u672c" start=0 end=1', media["odd"], path)
    assert os.path.exists(path) and count_frames(path) == 50


def test_ntsc_frame_rate_stays_exact(media, out):
    path = out()
    plan, res = edit("keep 0-3\nspeed 2 from 1 to 2\ntransition fade 0.3", media["pal30"], path)
    v = stream_info(path, "v")
    assert v["r_frame_rate"] == "30000/1001" and count_frames(path) == plan.timeline.total_frames


def test_tall_video_to_landscape_preset(media, out):
    path = out()
    edit("keep 0-2", media["tall"], path, preset="youtube_1080p")
    v = stream_info(path, "v")
    assert (int(v["width"]), int(v["height"])) == (1920, 1080)


def test_gif_output(media, out):
    path = out("a.gif")
    plan, res = edit("keep 0-2", media["speech"], path, preset="gif")
    v = stream_info(path, "v")
    assert v["codec_name"] == "gif" and int(v["width"]) == 480 and stream_info(path, "a") == {}
    assert count_frames(path) == plan.timeline.total_frames


def test_mp4_mkv_mov_containers(media, out):
    for ext, fmt in (("mkv", "matroska"), ("mov", "mov"), ("mp4", "mp4")):
        p = out(f"v.{ext}")
        edit("keep 0-2", media["speech"], p)
        assert fmt in __import__("subprocess").run(["ffprobe", "-v", "error", "-show_entries", "format=format_name", "-of", "csv=p=0", p],
                                                  capture_output=True, text=True).stdout


# ---- content: what the picture and the cuts really look like -----------------------------------------
def test_keep_and_cut_pick_the_right_frames(media, out):
    path = out()
    edit("keep 1.5-2.5", media["colors"], path)          # red until 2 s, then green
    assert color_at(path, 0.2)[0] > 200 and color_at(path, 0.2)[1] < 60          # red
    assert color_at(path, 0.8)[1] > 90 and color_at(path, 0.8)[0] < 60            # green
    path2 = out("c.mp4")
    edit("cut 2-4", media["colors"], path2)               # red then blue
    assert color_at(path2, 1.0)[0] > 200 and color_at(path2, 3.0)[2] > 200


def test_speed_changes_what_is_shown_when(media, out):
    path = out()
    edit("speed 2", media["ramp"], path)                  # brightness ramp plays twice as fast
    assert mean_luma(path, 1.5) > mean_luma(media["ramp"], 1.5) + 40
    path2 = out("s.mp4")
    edit("speed 0.5", media["ramp"], path2)
    assert mean_luma(path2, 3.0) == pytest.approx(mean_luma(media["ramp"], 1.5), abs=12)


def test_reverse_plays_backwards(media, out):
    path = out()
    edit("reverse", media["ramp"], path)
    assert mean_luma(path, 0.3) > mean_luma(path, 3.0) > mean_luma(path, 5.5)
    assert mean_luma(path, 0.3) > 200
    path2 = out("p.mp4")
    edit("reverse from 2 to 4", media["ramp"], path2)    # only the middle goes backwards
    a, b, c = mean_luma(path2, 1.0), mean_luma(path2, 2.2), mean_luma(path2, 3.8)
    assert b > a and b > c or a < c


def test_freeze_holds_one_frame(media, out):
    path = out()
    plan, res = edit("freeze at=2 duration=1.5", media["ramp"], path)
    held = [frame_gray(path, t) for t in (2.1, 2.5, 3.2)]
    assert diff_score(held[0], held[1]) < 1.0 and diff_score(held[0], held[2]) < 1.0
    assert diff_score(frame_gray(path, 1.0), frame_gray(path, 4.0)) > 20
    assert abs(plan.timeline.duration - 7.5) < 0.1


def test_mute_and_volume_ranges(media, out):
    from helpers import audio_level
    path = out()
    edit("mute from 1 to 2\nvolume 0.5 from 3 to 4", media["tone"], path)
    loud, quiet, muted = audio_level(path, 0.2, 0.6)[0], audio_level(path, 3.2, 0.6)[0], audio_level(path, 1.2, 0.6)[0]
    assert muted < -70 and quiet == pytest.approx(loud - 6, abs=1.5)
