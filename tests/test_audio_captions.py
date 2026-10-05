"""Sound features (loudness, silence removal, noise reduction, music with ducking, fades) and captions."""

import os
import re

import pytest

from editforge.analysis.silence import detect_silences
from editforge.analysis.speech import import_transcript, whisper_available
from editforge.core.errors import EditForgeError, FeatureUnavailable
from editforge.core.media import probe_media
from helpers import (audio_level, band_level, color_at, count_frames, diff_score, edit, frame_gray, hf_noise_level, loudness, plan_only,
                     stream_info)


# ---- loudness ----------------------------------------------------------------------------------------
@pytest.mark.parametrize("target", [-16, -23, -14])
def test_loudnorm_hits_the_target(media, out, target):
    path = out("n.m4a")
    plan, res = edit(f"loudnorm {target}", media["quiet"], path)
    lufs, peak = loudness(path)
    assert lufs == pytest.approx(target, abs=1.2)
    assert peak <= -0.9
    assert any("measured" in n and "normalised" in n for n in res.notes)       # the two passes really ran


def test_loudnorm_before_is_quiet(media):
    from helpers import ff
    lufs, _ = loudness(media["quiet"])
    assert lufs < -30


def test_loudnorm_clipping_guard(media, out):
    path = out("n.m4a")
    edit("loudnorm -10 true_peak=-1", media["loud"], path)
    lufs, peak = loudness(path)
    assert peak <= -0.8 and audio_level(path, 0, 8)[1] <= -0.8


def test_loudnorm_on_video_keeps_picture_untouched(media, out):
    path = out()
    plan, res = edit("loudnorm", media["tone"], path)
    assert plan.timeline.video_mode == "copy_all"           # the picture is copied, not re-encoded
    assert count_frames(path) == 150 and stream_info(path, "v")["codec_name"] == "h264"
    assert loudness(path)[0] == pytest.approx(-16, abs=1.5)


def test_loudnorm_of_silence_is_left_alone(media, out):
    from tools.make_samples import make_video
    quiet = make_video(str(media["dir"] / "mute_audio.mp4"), 4, "320x180", "25", "silence")
    path = out()
    plan, res = edit("loudnorm", quiet, path)
    assert any("silent" in n for n in res.notes) and res.verify.ok


# ---- silence removal ----------------------------------------------------------------------------------
def test_silence_removal_output_has_no_long_silence(media, out):
    path = out()
    plan, res = edit("silence_remove min_silence=0.5 padding=0.1", media["speech"], path)
    assert res.verify.duration == pytest.approx(plan.timeline.duration, abs=0.08)
    assert plan.timeline.duration < 5.2
    left = detect_silences(path, -35, 0.45, duration=res.verify.duration)
    assert left == []           # the longest remaining quiet stretch is the padding only


def test_silence_removal_keeps_the_speech(media, out):
    path = out()
    edit("silence_remove padding=0.05", media["speech"], path)
    mean, peak = audio_level(path, 0.0, 3.5)
    assert mean > -25 and peak > -12


def test_silence_removal_on_audio_file(media, out):
    path = out("s.m4a")
    plan, res = edit("silence_remove padding=0", media["audio"], path)
    assert probe_media(path).duration == pytest.approx(4.5, abs=0.12)       # three 1.5 s bursts out of a 9 s file


# ---- noise reduction ---------------------------------------------------------------------------------
def test_denoise_lowers_the_noise_but_keeps_the_tone(media, out):
    path = out("d.m4a")
    edit("denoise 20", media["noisy"], path)
    before, after = hf_noise_level(media["noisy"], 0.5, 3), hf_noise_level(path, 0.5, 3)
    assert after < before - 6
    assert band_level(path, 0.5, 3, 440) > band_level(media["noisy"], 0.5, 3, 440) - 4


# ---- music and ducking --------------------------------------------------------------------------------
def test_music_is_mixed_in_at_the_chosen_volume(media, out):
    soft, loud = out("soft.mp4"), out("loud.mp4")
    edit(f"music {media['music_tone']} volume=0.1 duck=false fade_in=0 fade_out=0", media["silent"], soft)
    edit(f"music {media['music_tone']} volume=0.2 duck=false fade_in=0 fade_out=0", media["silent"], loud)
    d = band_level(loud, 1.0, 2.0, 1000) - band_level(soft, 1.0, 2.0, 1000)
    assert d == pytest.approx(6.0, abs=1.0)


def test_music_ducks_under_speech(media, out):
    ducked, plain = out("duck.mp4"), out("plain.mp4")
    edit(f"music {media['music_tone']} volume=0.6 duck=true fade_in=0 fade_out=0", media["speech"], ducked)
    edit(f"music {media['music_tone']} volume=0.6 duck=false fade_in=0 fade_out=0", media["speech"], plain)
    # the clip speaks during 0-1 s and 2-3 s, and is quiet during 1-2 s
    talk_d, quiet_d = band_level(ducked, 2.15, 0.7, 1000), band_level(ducked, 1.2, 0.6, 1000)
    talk_p, quiet_p = band_level(plain, 2.15, 0.7, 1000), band_level(plain, 1.2, 0.6, 1000)
    assert quiet_d > talk_d + 5            # ducked: the music is much quieter while someone talks
    assert abs(quiet_p - talk_p) < 1.5     # not ducked: it stays level


def test_music_start_end_loop_and_fades(media, out):
    path = out()
    edit(f"music {media['music_short']} volume=0.5 duck=false start=2 end=7 loop=true fade_in=0 fade_out=0", media["silent8"], path)
    assert band_level(path, 0.2, 1.0, 1000) < -60                      # before it starts
    assert band_level(path, 2.5, 1.0, 1000) > -25 and band_level(path, 5.5, 1.0, 1000) > -25   # looped past its 2 s length
    assert band_level(path, 7.2, 0.6, 1000) < -60                      # after it ends
    p2 = out("noloop.mp4")
    edit(f"music {media['music_short']} volume=0.5 duck=false loop=false fade_in=0 fade_out=0", media["silent8"], p2)
    assert band_level(p2, 0.5, 1.0, 1000) > -25 and band_level(p2, 3.0, 0.8, 1000) < -60
    p3 = out("fade.mp4")
    edit(f"music {media['music_tone']} volume=0.5 duck=false fade_in=2 fade_out=2", media["silent"], p3)
    assert band_level(p3, 0.0, 0.3, 1000) < band_level(p3, 2.5, 0.5, 1000) - 6


def test_music_without_a_file_is_a_clear_error(media):
    with pytest.raises(EditForgeError) as e:
        plan_only("music nothing.mp3", media["speech"])
    assert "can't find" in e.value.message


def test_music_file_without_sound_is_refused(media):
    with pytest.raises(EditForgeError) as e:
        plan_only(f"music {media['silent']}", media["speech"])
    assert "no sound" in e.value.message


# ---- fades -------------------------------------------------------------------------------------------
def test_fades_affect_sound_and_picture(media, out):
    path = out()
    edit("fade in=1 out=1", media["colors"], path)
    assert audio_level(path, 0.0, 0.15)[0] < audio_level(path, 2.0, 0.3)[0] - 8
    assert audio_level(path, 5.85, 0.15)[0] < audio_level(path, 3.0, 0.3)[0] - 8
    assert max(color_at(path, 0.02)) < 40 and color_at(path, 1.5)[0] > 200 and max(color_at(path, 5.96)) < 60
    assert count_frames(path) == 150


def test_fade_only_audio_or_with_white(media, out):
    path = out()
    edit("fade in=1 video=false", media["colors"], path)
    assert color_at(path, 0.02)[0] > 200
    p2 = out("w.mp4")
    edit("fade in=1 audio=false color=white", media["colors"], p2)
    assert min(color_at(p2, 0.02)) > 200 and audio_level(p2, 0.0, 0.2)[0] > -25


def test_fade_across_pieces_is_continuous(media, out):
    # a long fade over a many-piece edit still works (fade times are shifted for each piece)
    path = out()
    plan, res = edit("keep 0-1, 1.5-2.5, 3-4, 4.5-5.5\nfade in=2 out=2", media["colors"], path)
    assert res.pieces_total >= 1 and color_at(path, 0.02)[0] < 60 and max(color_at(path, 1.9)) > 100


def test_mute_whole_file(media, out):
    path = out()
    edit("mute", media["tone"], path)
    assert audio_level(path, 0.5, 2)[0] < -70


# ---- captions ----------------------------------------------------------------------------------------
def test_transcript_import_srt_vtt_json(media, tmp_path):
    words = import_transcript(media["srt"])
    assert [w.text for w in words[:4]] == ["Hello", "brave", "new", "world"] and words[0].start == pytest.approx(0.5)
    vtt = tmp_path / "a.vtt"
    vtt.write_text("WEBVTT\n\n00:01.000 --> 00:02.000\nhi there\n", encoding="utf-8")
    assert [w.text for w in import_transcript(str(vtt))] == ["hi", "there"]
    js = tmp_path / "a.json"
    js.write_text('{"segments":[{"start":1,"end":2,"text":"a b","words":[{"word":" a","start":1,"end":1.4},{"word":"b","start":1.5,"end":2}]}]}')
    assert [(w.text, w.start) for w in import_transcript(str(js))] == [("a", 1.0), ("b", 1.5)]
    bad = tmp_path / "bad.srt"
    bad.write_text("nothing here")
    with pytest.raises(EditForgeError):
        import_transcript(str(bad))


def test_captions_follow_cuts_and_speed(media):
    p = plan_only(f"captions transcript={media['srt']}", media["speech"])
    assert p.timeline.captions.lines[0].start == pytest.approx(0.5, abs=0.05)
    p = plan_only(f"cut 0-2\ncaptions transcript={media['srt']}", media["speech"])       # first cue (0.5-2 s) is cut away
    texts = [ln.text for ln in p.timeline.captions.lines]
    assert all("Hello" not in t for t in texts) and any("second" in t for t in texts)
    assert p.timeline.captions.lines[0].start == pytest.approx(2.0, abs=0.05)             # 4.0 s moved to 2.0 s
    p = plan_only(f"speed 2\ncaptions transcript={media['srt']}", media["speech"])
    assert p.timeline.captions.lines[0].start == pytest.approx(0.25, abs=0.05)
    p = plan_only(f"keep 3.5-6\nspeed 0.5\ncaptions transcript={media['srt']}", media["speech"])
    first = p.timeline.captions.lines[0]
    assert first.start == pytest.approx((4.0 - 3.5) / 0.5, abs=0.1)


def test_words_cut_in_the_middle_are_shortened_or_dropped(media):
    p = plan_only(f"keep 0-1.2, 4-8\ncaptions transcript={media['srt']}", media["speech"])
    words = [w for ln in p.timeline.captions.lines for w in ln.words]
    assert words[0][0] == "Hello" and all(w[2] <= 1.25 + 0.05 or w[1] >= 1.2 for w in words)
    assert not any(w[0] == "world" for w in words)          # "world" starts at 1.7 s, in the removed part


def test_caption_files_srt_and_karaoke_ass(media, out):
    path = out()
    plan, res = edit(f"cut 0-2\ncaptions transcript={media['srt']} style=karaoke", media["speech"], path)
    srt, ass = path[:-4] + ".srt", path[:-4] + ".ass"
    assert os.path.exists(srt) and os.path.exists(ass) and set(res.sidecars) == {srt, ass}
    s = open(srt, encoding="utf-8").read()
    assert re.search(r"1\n00:00:0\d,\d{3} --> 00:00:0\d,\d{3}\nsecond line here", s)
    a = open(ass, encoding="utf-8").read()
    assert "[Events]" in a and re.search(r"\{\\k\d+\}second", a) and "PlayResX: 320" in a
    # the karaoke timings of one line add up to the line's length
    ev = [ln for ln in a.splitlines() if ln.startswith("Dialogue")][0]
    total_cs = sum(int(x) for x in re.findall(r"\\k(\d+)", ev))
    st, en = re.findall(r"(\d+):(\d\d):(\d\d)\.(\d\d)", ev)[:2]
    cs = lambda t: ((int(t[0]) * 60 + int(t[1])) * 60 + int(t[2])) * 100 + int(t[3])
    assert total_cs == pytest.approx(cs(en) - cs(st), abs=3)
    path2 = out("plain.mp4")
    edit(f"captions transcript={media['srt']} style=plain files=false", media["speech"], path2)
    assert not os.path.exists(path2[:-4] + ".ass")


def test_plain_style_has_no_karaoke_tags(media, out):
    path = out()
    edit(f"captions transcript={media['srt']} style=plain", media["speech"], path)
    assert "\\k" not in open(path[:-4] + ".ass", encoding="utf-8").read()


def test_burned_in_captions_show_at_the_right_time(media, out):
    path = out()
    edit(f"captions transcript={media['srt']} font_size=400 mode=burn files=false", media["colors"], path)
    base = media["colors"]
    on = diff_score(frame_gray(path, 1.0, 160, 90), frame_gray(base, 1.0, 160, 90))       # a cue is on screen
    off = diff_score(frame_gray(path, 3.0, 160, 90), frame_gray(base, 3.0, 160, 90))      # between cues
    assert on > 3 and off < 0.6


def test_burned_in_captions_are_shifted_through_cuts(media, out):
    path = out()
    edit(f"cut 0-2\ncaptions transcript={media['srt']} font_size=400 files=false", media["colors"], path)
    # "second line here" was at 4.0-5.5 s and now plays at 2.0-3.5 s
    from helpers import frame_gray as fg
    ref = out("ref.mp4")
    edit("cut 0-2", media["colors"], ref)
    assert diff_score(fg(path, 2.5, 160, 90), fg(ref, 2.5, 160, 90)) > 3
    assert diff_score(fg(path, 0.5, 160, 90), fg(ref, 0.5, 160, 90)) < 0.6
    assert diff_score(fg(path, 4.2, 160, 90), fg(ref, 4.2, 160, 90)) < 5     # the third cue starts at 4.0 s here


def test_soft_subtitles_are_a_separate_track(media, out):
    path = out()
    plan, res = edit(f"captions transcript={media['srt']} mode=soft files=false", media["colors"], path)
    s = stream_info(path, "s")
    assert s.get("codec_name") == "mov_text"
    ref = out("r.mp4")
    edit("keep 0-6", media["colors"], ref)
    assert diff_score(frame_gray(path, 1.0, 160, 90), frame_gray(ref, 1.0, 160, 90)) < 0.6     # not burned in


def test_captions_mode_files_only(media, out):
    path = out()
    plan, res = edit(f"captions transcript={media['srt']} mode=files", media["colors"], path)
    assert os.path.exists(path[:-4] + ".srt") and stream_info(path, "s") == {}


def test_captions_in_a_cut_with_no_speech_say_so(media, out):
    path = out()
    plan, res = edit(f"keep 2.2-3.8\ncaptions transcript={media['srt']} mode=soft", media["colors"], path)
    assert any("No speech" in w for w in plan.warnings)


def test_captions_without_whisper_say_what_is_switched_off(media):
    if whisper_available():
        pytest.skip("faster-whisper is installed here, so it would really transcribe")
    with pytest.raises(FeatureUnavailable) as e:
        plan_only("captions", media["speech"])
    assert "faster-whisper" in e.value.message and "transcript" in e.value.fix


def test_captions_need_sound(media):
    with pytest.raises(EditForgeError):
        plan_only(f"captions transcript={media['srt']}", media["silent"])
