"""What the planner decides, checked against real analysis of generated media."""

import pytest

from editforge.core.errors import EditForgeError, ScriptError
from helpers import plan_only


def secs(plan):
    return plan.timeline.duration


def segs(plan):
    return plan.timeline.to_dict()["segments"]


def test_keep_ranges(media):
    p = plan_only("keep 1-3, 5-6", media["speech"])
    assert secs(p) == pytest.approx(3.0) and len(p.timeline.segments) == 2 and p.timeline.total_frames == 75


def test_cut_ranges(media):
    p = plan_only("cut 2-3", media["speech"])
    assert secs(p) == pytest.approx(7.0) and len(p.timeline.segments) == 2


def test_keep_then_cut_inside(media):
    p = plan_only("keep 1-6\ncut 2-3", media["speech"])
    assert secs(p) == pytest.approx(4.0)


def test_no_keep_means_everything(media):
    assert secs(plan_only("loudnorm", media["speech"])) == pytest.approx(8.0)


def test_cuts_are_frame_exact_on_the_grid(media):
    p = plan_only("keep 1.013-2.5", media["speech"])  # not on a 25 fps frame
    seg = p.timeline.segments[0]
    assert seg.src_start_f == 25 and seg.frames == 37 or seg.src_start_f == 25 and seg.frames == 38
    assert p.timeline.total_frames == seg.frames


def test_pattern_keep_and_remove(media):
    assert secs(plan_only("pattern_keep 1 every 2", media["speech"])) == pytest.approx(4.0)
    assert secs(plan_only("pattern_remove 0.5 every 2", media["speech"])) == pytest.approx(6.0, abs=0.1)
    assert secs(plan_only("pattern_keep 1 every 2 offset=0.5 start=0 end=4", media["speech"])) == pytest.approx(2.0)


def test_pattern_longer_than_cycle_warns(media):
    p = plan_only("pattern_keep 3 every 2", media["speech"])
    assert any("selects everything" in w for w in p.warnings)


def test_silence_removal_with_padding(media):
    # the clip is 1 s of tone then 1 s of silence, four times
    p0 = plan_only("silence_remove padding=0", media["speech"])
    assert secs(p0) == pytest.approx(4.0, abs=0.1)
    p = plan_only("silence_remove threshold=-35 min_silence=0.5 padding=0.1", media["speech"])
    assert secs(p) == pytest.approx(4.7, abs=0.15)
    assert len(p.timeline.segments) == 4
    assert plan_only("silence_remove padding=0.3", media["speech"]).timeline.duration > p.timeline.duration


def test_silence_removal_threshold_and_min_length(media):
    # nothing is silent for 2 s, so nothing is removed
    assert secs(plan_only("silence_remove min_silence=2", media["speech"])) == pytest.approx(8.0)
    # the quiet parts here are digital silence, so even a very low threshold still finds them
    assert secs(plan_only("silence_remove threshold=-90 padding=0", media["speech"])) == pytest.approx(4.0, abs=0.1)
    # a loud threshold treats the tone itself as "quiet"
    with pytest.raises(ScriptError):      # at -3 dB everything counts as silence, so nothing would be left
        plan_only("silence_remove threshold=-3 padding=0", media["speech"])


def test_silence_removal_on_file_without_sound_warns(media):
    p = plan_only("silence_remove", media["silent"])
    assert secs(p) == pytest.approx(4.0) and any("no sound" in w for w in p.warnings)


def test_removing_everything_is_a_clear_error(media):
    with pytest.raises(ScriptError) as e:
        plan_only("cut 0-8", media["speech"])
    assert "removes everything" in e.value.message and e.value.fix


def test_ranges_past_the_end(media):
    p = plan_only("keep 5-99", media["speech"])
    assert secs(p) == pytest.approx(3.0) and any("shortened" in w for w in p.warnings)
    p = plan_only("keep 0-2\ncut 20-30", media["speech"])
    assert secs(p) == pytest.approx(2.0) and any("ignored" in w for w in p.warnings)


def test_scene_cut_splits_and_filters(media):
    p = plan_only("scene_cut", media["scenes"])
    assert len(p.timeline.segments) == 3 and secs(p) == pytest.approx(6.0)
    assert secs(plan_only("scene_cut keep=2", media["scenes"])) == pytest.approx(2.0)
    assert secs(plan_only("scene_cut remove=1,3", media["scenes"])) == pytest.approx(2.0)
    with pytest.raises(ScriptError):
        plan_only("scene_cut keep=9", media["scenes"])   # no such scene: nothing would be left


def test_scene_detection_follows_brightness_not_colour(media):
    # red and green here have almost the same brightness, so FFmpeg's scene score does not see that change.
    p = plan_only("scene_cut", media["colors"])
    assert len(p.timeline.segments) == 2


def test_scene_snap_moves_cuts_onto_scene_changes(media):
    p = plan_only("keep 1.85-3.9\nscene_snap tolerance=0.3", media["scenes"])
    s = segs(p)[0]
    assert s["source_start"] == pytest.approx(2.0, abs=0.05) and s["source_end"] == pytest.approx(4.0, abs=0.05)
    p = plan_only("keep 1.85-3.9\nscene_snap tolerance=0.05", media["scenes"])
    assert segs(p)[0]["source_start"] == pytest.approx(1.84, abs=0.05)


def test_speed_changes_length(media):
    assert secs(plan_only("speed 2 from 2 to 4", media["speech"])) == pytest.approx(7.0)
    assert secs(plan_only("speed 0.5 from 1 to 3", media["speech"])) == pytest.approx(10.0)
    assert secs(plan_only("speed 4", media["speech"])) == pytest.approx(2.0)


def test_speed_ramp_changes_speed_smoothly(media):
    p = plan_only("speed_ramp 1 to 4 from 0 to 6", media["speech"])
    speeds = [s["speed"] for s in segs(p) if s["source_end"] <= 6.01]
    assert len(speeds) >= 8 and speeds == sorted(speeds) and speeds[0] < 1.5 and speeds[-1] > 3.0
    assert secs(p) < 8.0 and secs(p) > 4.0


def test_reverse_reorders_and_keeps_length(media):
    p = plan_only("reverse from 1 to 4", media["speech"])
    assert secs(p) == pytest.approx(8.0)
    rev = [s for s in segs(p) if s["reverse"]]
    assert rev and [s["source_start"] for s in rev] == sorted([s["source_start"] for s in rev], reverse=True)
    assert min(s["source_start"] for s in rev) == pytest.approx(1.0) and max(s["source_end"] for s in rev) == pytest.approx(4.0)


def test_reverse_is_chunked_to_bound_memory(media):
    p = plan_only("reverse", media["speech"])
    assert all(s.frames <= 150 for s in p.timeline.segments)


def test_freeze_adds_time(media):
    p = plan_only("freeze at=2 duration=1.5", media["speech"])
    assert secs(p) == pytest.approx(9.5, abs=0.05)
    assert [s["kind"] for s in segs(p)] == ["clip", "freeze", "clip"]
    p = plan_only("keep 0-1\nfreeze at=5 duration=1", media["speech"])
    assert any("cut out" in w for w in p.warnings) and secs(p) == pytest.approx(1.0)


def test_every_nth_marks_segments(media):
    p = plan_only("scene_cut\nevery_nth 2 effect=invert", media["scenes"])
    names = [s["effects"] for s in segs(p)]
    assert names == [[], ["invert"], []]
    p = plan_only("scene_cut\nevery_nth 2 effect=invert offset=1", media["scenes"])
    assert [s["effects"] for s in segs(p)] == [["invert"], [], ["invert"]]
    p = plan_only("scene_cut\nevery_nth 1 effect=blur", media["scenes"])
    assert all(s["effects"] == ["blur"] for s in segs(p))


def test_effects_on_a_range_split_segments(media):
    p = plan_only("mirror from 2 to 4", media["speech"])
    assert [s["effects"] for s in segs(p)] == [[], ["mirror"], []]
    p = plan_only("greyscale\nblur from 1 to 2", media["speech"])
    assert len(segs(p)) == 3


def test_transitions_overlap_and_skip_contiguous_joins(media):
    p = plan_only("keep 0-2, 3-5\ntransition fade 0.5", media["speech"])
    assert secs(p) == pytest.approx(3.5, abs=0.05)
    s = segs(p)
    assert s[0]["transition_out"] == "fade" and s[1]["transition_in"] == "fade"
    assert s[1]["output_start"] == pytest.approx(s[0]["output_end"] - 0.5, abs=0.05)
    p = plan_only("keep 0-5\nspeed 2 from 1 to 2\ntransition fade 0.5", media["speech"])
    assert secs(p) == pytest.approx(4.5, abs=0.05) and all(x["transition_in"] is None for x in segs(p))


def test_transition_selection_options(media):
    script = "keep 0-1, 2-3, 4-5, 6-7\n"
    assert secs(plan_only(script + "transition fade 0.4", media["speech"])) == pytest.approx(4 - 3 * 0.4, abs=0.05)
    assert secs(plan_only(script + "transition fade 0.4 every=2", media["speech"])) == pytest.approx(4 - 1 * 0.4, abs=0.05)
    p = plan_only(script + "transition fade 0.4 at=4", media["speech"])
    assert secs(p) == pytest.approx(4 - 0.4, abs=0.05)
    assert sum(1 for x in segs(p) if x["transition_in"]) == 1


def test_long_transition_is_shortened_with_a_warning(media):
    p = plan_only("keep 0-1, 3-4\ntransition fade 5", media["speech"])
    assert any("shortened" in w for w in p.warnings) and secs(p) == pytest.approx(1.5, abs=0.1)


def test_transition_type_names(media):
    for t in ("wipeleft", "slideright", "fadeblack", "circleopen", "dissolve", "pixelize"):
        p = plan_only(f"keep 0-2, 3-5\ntransition {t} 0.3", media["speech"])
        assert segs(p)[0]["transition_out"] == t
    with pytest.raises(ScriptError):
        plan_only("keep 0-2, 3-5\ntransition spin 0.3", media["speech"]).script.raise_if_errors()


def test_overlay_times_follow_the_edit(media):
    # output time is the default; source time is mapped through the cut
    p = plan_only('cut 0-2\ntext "x" start=1 end=2', media["speech"])
    t = p.timeline.texts[0]
    assert (t.start_f, t.end_f) == (25, 50)
    p = plan_only('cut 0-2\ntext "x" start=3 end=4 time=source', media["speech"])
    t = p.timeline.texts[0]
    assert (t.start_f, t.end_f) == (25, 50)
    p = plan_only('cut 0-2\ntext "x" start=1 end=1.5 time=source', media["speech"])
    assert not p.timeline.texts and any("cut out" in w for w in p.warnings)
    p = plan_only('keep 0-2\ntext "late" start=5', media["speech"])
    assert not p.timeline.texts and any("only" in w for w in p.warnings)


def test_text_without_end_lasts_to_the_end(media):
    p = plan_only('text "x" start=2', media["speech"])
    assert p.timeline.texts[0].end_f == p.timeline.total_frames


def test_audio_only_source(media):
    p = plan_only("keep 1-4\nloudnorm", media["audio"])
    assert not p.timeline.output.has_video and p.timeline.output.container == "m4a"
    assert secs(p) == pytest.approx(3.0) and p.timeline.total_frames == 300
    p = plan_only("zoom 2\nmirror", media["audio"])
    assert len([w for w in p.warnings if "needs a picture" in w]) == 2


def test_video_only_source(media):
    p = plan_only("keep 0-2\nloudnorm", media["silent"])
    assert any("no sound to measure" in w for w in p.warnings)
    assert not p.timeline.audio.loudnorm


def test_video_mode_decisions(media):
    assert plan_only("loudnorm", media["tone"]).timeline.video_mode == "copy_all"
    assert plan_only("keep 0-3", media["tone"]).timeline.video_mode == "encode"
    assert plan_only("loudnorm", media["tone"], preset="shorts").timeline.video_mode == "encode"
    assert plan_only("fade in=1", media["tone"]).timeline.video_mode == "encode"
    assert plan_only("keep 1-4", media["tone"], fast_cuts=True).timeline.video_mode == "copy_cuts"


def test_fast_cuts_snap_to_keyframes_and_say_so(media):
    p = plan_only("keep 1.3-3.7", media["speech"], fast_cuts=True)
    s = segs(p)[0]
    assert s["source_start"] == pytest.approx(1.0) and s["source_end"] == pytest.approx(4.0)
    assert any("keyframe" in n for n in p.notes)


def test_fast_cuts_refuse_picture_changes(media):
    for script in ("speed 2", "mirror", 'text "x"', "keep 0-2, 3-4\ntransition fade 0.3"):
        with pytest.raises(EditForgeError) as e:
            plan_only(script, media["speech"], fast_cuts=True)
        assert "Fast cuts cannot be used" in e.value.message and "--fast-cuts" in e.value.fix
    with pytest.raises(EditForgeError):
        plan_only("keep 0-3", media["speech"], fast_cuts=True, preset="shorts")


def test_presets_and_output_settings(media):
    p = plan_only("keep 0-2", media["speech"], preset="shorts")
    o = p.timeline.output
    assert (o.width, o.height, o.fit) == (1080, 1920, "blur")
    o = plan_only("keep 0-2", media["speech"], preview=True).timeline.output
    assert o.height == 480 and o.width == 852 and o.x264_preset == "ultrafast"
    o = plan_only("keep 0-2", media["speech"], preset="gif").timeline.output
    assert o.container == "gif" and float(o.fps) == 12 and o.width == 480
    o = plan_only("keep 0-2", media["speech"], preset="podcast_audio").timeline.output
    assert o.container == "m4a" and not o.has_video and o.channels == 1
    o = plan_only("keep 0-2", media["speech"], size="640x360", fps=30, quality="high").timeline.output
    assert (o.width, o.height, float(o.fps), o.crf) == (640, 360, 30.0, 20)
    o = plan_only("size 720p fit=crop\nfps 24\nquality low", media["speech"]).timeline.output
    assert (o.width, o.height, o.fit, float(o.fps), o.crf) == (1280, 720, "crop", 24.0, 28)
    assert plan_only("keep 0-2", media["speech"], output_path="x.mp3").timeline.output.container in ("mp3", "m4a")


def test_unknown_preset_suggests_the_right_one(media):
    with pytest.raises(ScriptError) as e:
        plan_only("keep 0-2", media["speech"], preset="shortz")
    assert "Did you mean 'shorts'" in e.value.message


def test_missing_files_are_reported_with_help(media):
    for script in ("music nothere.mp3", "image nothere.png", "captions transcript=nothere.srt"):
        with pytest.raises(ScriptError) as e:
            plan_only(script, media["speech"])
        assert "can't find" in e.value.message and e.value.fix
    with pytest.raises(EditForgeError):
        plan_only("keep 0-2", "/nonexistent/video.mp4")


def test_plan_digest_changes_with_the_edit_and_not_with_wording(media):
    a = plan_only("keep 0-4", media["speech"]).digest
    b = plan_only("keep 0-5", media["speech"]).digest
    c = plan_only("keep from 0s to 00:00:04", media["speech"]).digest
    d = plan_only("# comment\n\nkeep 0:00-0:04", media["speech"]).digest
    assert a != b and a == c == d
    assert plan_only("keep 0-4", media["speech"], preset="shorts").digest != a


def test_beat_cut_lands_on_beats(media):
    p = plan_only("beat_cut every=4 take=0.5", media["beatvideo"])
    s = segs(p)
    assert 8 <= len(s) <= 14
    for x in s[1:-1]:      # the first piece is the lead-in before the first beat
        assert (x["source_end"] - x["source_start"]) == pytest.approx(1.0, abs=0.1)   # half of 4 beats at 120 BPM
        off = (x["source_start"] - 0.5) % 2.0
        assert min(off, 2.0 - off) < 0.12


def test_beat_cut_with_fixed_tempo_and_downbeats(media):
    p = plan_only("beat_cut source=bpm bpm=120 every=2 offset=0.5", media["tone"])
    assert len(segs(p)) >= 5
    p = plan_only("beat_cut downbeats=true every=1", media["beatvideo"])
    assert 11 <= len(segs(p)) <= 14    # one cut per bar (2 s) over 24 s, plus the lead-in
    assert p.timeline.total_frames == 600


def test_beat_effect_counts(media):
    beats = plan_only("beat_effect flash", media["beatvideo"])
    pulses = [s for s in beats.timeline.segments if any(e.kind == "pulse" for e in s.effects)]
    assert 40 <= len(pulses) <= 48
    down = plan_only("beat_effect flash on=downbeat", media["beatvideo"])
    dp = [s for s in down.timeline.segments if any(e.kind == "pulse" for e in s.effects)]
    assert 10 <= len(dp) <= 13 and len(dp) < len(pulses) / 3
    half = plan_only("beat_effect zoom_pulse every=2", media["beatvideo"])
    hp = [s for s in half.timeline.segments if s.camera and s.camera.pulse]
    assert 20 <= len(hp) <= 25
    assert beats.timeline.total_frames == 600   # pulses never change the length


def test_beat_effect_on_downbeat_without_a_clear_metre_says_so(media):
    p = plan_only("beat_effect flash on=downbeat", media["flat_video"])
    assert any("Downbeats could not be found" in w for w in p.warnings)
    assert p.timeline.total_frames == 600


def test_beat_effect_needs_music_when_asked(media):
    with pytest.raises(ScriptError) as e:
        plan_only("beat_effect flash source=music", media["tone"])
    assert "music" in e.value.message
    p = plan_only("music click_4_4.wav\nbeat_effect flash source=music", media["beatvideo"])
    assert sum(1 for s in p.timeline.segments if any(x.kind == "pulse" for x in s.effects)) >= 40


def test_beat_snap_moves_cuts_to_music_beats(media):
    p = plan_only("keep 0-3.13, 5-8.07, 10-12\nmusic click_4_4.wav\nbeat_snap tolerance=0.3", media["beatvideo"])
    ends = [s["output_end"] for s in segs(p)][:-1]
    for e in ends:
        assert ((e - 0.5) % 0.5) == pytest.approx(0.0, abs=0.05) or abs(((e - 0.5) % 0.5) - 0.5) < 0.05
    assert any("onto the beat" in n for n in p.notes)


def test_dry_run_reports_estimated_duration_for_complex_edit(media):
    p = plan_only("keep 0-6\nspeed 2 from 1 to 3\nfreeze at=4 duration=1\nkeep 0-6", media["speech"])
    assert p.timeline.duration == pytest.approx(6 - 1 + 1, abs=0.1)
    assert "Length:" in p.summary()
