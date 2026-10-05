"""Projects (pieces with per-piece edits) and combining two videos, checked with real FFmpeg."""

from __future__ import annotations

import pytest

from editforge.analysis.facepath import path_value, window_of_path
from editforge.combine import clean_options, combine
from editforge.core.errors import EditForgeError
from editforge.project import MAX_PIECES, clean_project, plan_project, slice_at, slice_pieces
from editforge.render.engine import RenderOptions, render
from helpers import audio_level, color_at, count_frames, stream_info


def project(pieces, **settings):
    return clean_project({"name": "t", "source": "x", "pieces": pieces, "settings": dict({"quality": "high"}, **settings)}, 1e9)


def make(proj, src, out, **kw):
    plan = plan_project(proj, src, output_path=out, **kw)
    return plan, render(plan, RenderOptions(overwrite=True))


def is_red(c):
    return c[0] > 150 and c[1] < 90 and c[2] < 90


def is_green(c):
    return c[1] > 150 and c[0] < 90 and c[2] < 90


def is_blue(c):
    return c[2] > 150 and c[0] < 90 and c[1] < 90


# ---------------------------------------------------------------------------- slicing and cleaning
def test_slice_into_equal_pieces():
    p = slice_pieces(10.0, 2.0)
    assert [(x["start"], x["end"]) for x in p] == [(0, 2), (2, 4), (4, 6), (6, 8), (8, 10)]
    assert [round(x["end"] - x["start"], 2) for x in slice_pieces(7.0, 3.0)] == [3, 3, 1]
    assert len(slice_pieces(6.03, 2.0)) == 3 and slice_pieces(6.03, 2.0)[-1]["end"] == 6.03      # no sliver at the end
    assert len(slice_pieces(5.0, 99)) == 1


@pytest.mark.parametrize("duration, interval, words", [(10, 0.01, "at least 0.1"), (100000, 0.1, "pieces")])
def test_slice_refuses_silly_sizes(duration, interval, words):
    with pytest.raises(EditForgeError) as e:
        slice_pieces(duration, interval)
    assert words in e.value.message and e.value.fix


def test_slice_at_beat_times():
    p = slice_at(10.0, [2.5, 4.5, 4.52, 6.5, 9.98, 30])
    assert [(x["start"], x["end"]) for x in p] == [(0, 2.5), (2.5, 4.5), (4.5, 6.5), (6.5, 10)]


def test_clean_project_drops_bad_pieces_and_bad_values():
    raw = {"name": "  my   <b>video</b>  ", "source": "u/x", "settings": {"shape": "hexagon", "transition": "explode", "quality": "ultra",
                                                                           "music_volume": "loud", "fade_in": -3, "duck": 0},
           "pieces": [{"start": 0, "end": 2, "speed": 999, "fx": ["hflip", "hflip", "rm -rf", 7], "zoom": {"mode": "spin", "amount": 50},
                       "color": {"brightness": "x", "contrast": 99}, "volume": -1},
                      {"start": 5, "end": 4}, {"start": "a", "end": 2}, "nope", {"end": 3}, {"start": float("nan"), "end": 3},
                      {"start": -4, "end": 50}]}
    p = clean_project(raw, 10.0)
    assert p["name"] == "my <b>video</b>" and len(p["pieces"]) == 2          # the page shows names as text, never as HTML
    a, b = p["pieces"]
    assert a["speed"] == 16 and a["fx"] == ["hflip"] and a["zoom"] == {"mode": "none", "amount": 4.0}
    assert a["color"] == {"brightness": 0.0, "contrast": 3.0, "saturation": 1.0} and a["volume"] == 0
    assert (b["start"], b["end"]) == (0, 10)
    s = p["settings"]
    assert (s["shape"], s["transition"], s["quality"], s["music_volume"], s["fade_in"], s["duck"]) == ("original", "none", "best", 0.25, 0, False)


def test_clean_project_limits():
    with pytest.raises(EditForgeError):
        clean_project({"pieces": [{"start": 0, "end": 1}] * (MAX_PIECES + 1)}, 10)
    with pytest.raises(EditForgeError):
        clean_project(["not", "a", "project"], 10)


# ---------------------------------------------------------------------------- rendering
def test_removed_pieces_are_left_out(media, out):
    pieces = slice_pieces(6.0, 1.0)                      # colours: 2 s red, 2 s green, 2 s blue
    pieces[2]["off"] = pieces[3]["off"] = True          # remove all the green
    plan, res = make(project(pieces), media["scenes"], out())
    assert plan.timeline.total_frames == 100 and count_frames(res.output_path) == 100 and res.verify.ok
    assert is_red(color_at(res.output_path, 1.0)) and is_blue(color_at(res.output_path, 2.5))


def test_pieces_can_be_moved_and_repeated(media, out):
    red, green, blue = slice_pieces(6.0, 2.0)
    plan, res = make(project([blue, red, dict(red), green]), media["scenes"], out())
    assert count_frames(res.output_path) == 200
    got = [color_at(res.output_path, t) for t in (1.0, 3.0, 5.0, 7.0)]
    assert is_blue(got[0]) and is_red(got[1]) and is_red(got[2]) and is_green(got[3])


def test_a_piece_can_be_made_longer_into_its_neighbour(media, out):
    red, green, blue = slice_pieces(6.0, 2.0)
    red["end"] = 3.0                                     # the red piece now also shows 1 s of green ...
    plan, res = make(project([red, green, blue]), media["scenes"], out())
    assert count_frames(res.output_path) == 175          # ... and the green piece still plays in full
    assert is_red(color_at(res.output_path, 1.0)) and is_green(color_at(res.output_path, 2.5)) and is_green(color_at(res.output_path, 4.0))
    assert is_blue(color_at(res.output_path, 6.0))


def test_speed_changes_the_length(media, out):
    a, b, c = slice_pieces(6.0, 2.0)
    a["speed"], c["speed"] = 2, 0.5
    plan, res = make(project([a, b, c]), media["scenes"], out())
    assert count_frames(res.output_path) == 25 + 50 + 100
    assert is_green(color_at(res.output_path, 1.5)) and is_blue(color_at(res.output_path, 5.0))


def test_flip_and_black_and_white_only_touch_their_piece(media, out):
    from helpers import frame_rgb
    from tools.make_samples import run
    two = out("two_halves.mp4")                          # left half red, right half blue
    run(["-f", "lavfi", "-i", "color=c=red:size=80x90:rate=25:duration=4", "-f", "lavfi", "-i", "color=c=blue:size=80x90:rate=25:duration=4",
         "-filter_complex", "[0][1]hstack", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", two])
    a, b = slice_pieces(4.0, 2.0)
    a["fx"] = ["hflip"]
    plan, res = make(project([a, b]), two, out())
    flipped, plain = frame_rgb(res.output_path, 1.0, 4, 1), frame_rgb(res.output_path, 3.0, 4, 1)     # 4 samples across
    assert is_blue(flipped[0:3]) and is_red(flipped[9:12])       # the flipped piece has its halves swapped ...
    assert is_red(plain[0:3]) and is_blue(plain[9:12])           # ... and the other piece is unchanged
    red, rest = slice_pieces(6.0, 2.0)[0], slice_pieces(6.0, 2.0)[1]
    red["fx"] = ["grayscale"]
    plan, res = make(project([red, rest]), media["scenes"], out("g.mp4"))
    r, g, bl = color_at(res.output_path, 1.0)
    assert abs(r - g) < 25 and abs(g - bl) < 25 and is_green(color_at(res.output_path, 3.0))


def test_backwards_piece_plays_in_reverse(media, out):
    pieces = [{"start": 1.0, "end": 5.0, "reverse": True}]        # red-ish end first: source is red, green, blue
    plan, res = make(project(pieces), media["scenes"], out())
    assert count_frames(res.output_path) == 100
    assert is_blue(color_at(res.output_path, 0.5)) and is_green(color_at(res.output_path, 2.0)) and is_red(color_at(res.output_path, 3.5))


def test_mute_and_original_sound_off(media, out):
    a, b = slice_pieces(6.0, 3.0)
    a["mute"] = True
    plan, res = make(project([a, b]), media["tone"], out())
    assert audio_level(res.output_path, 0.5, 2.0)[0] < -60 and audio_level(res.output_path, 3.5, 2.0)[0] > -30
    plan, res = make(project(slice_pieces(6.0, 3.0), original_sound=False), media["tone"], out("s.mp4"))
    assert audio_level(res.output_path, 0.5, 5.0)[0] < -60


def test_music_is_mixed_in_and_can_replace_the_sound(media, out):
    proj = project(slice_pieces(4.0, 2.0), original_sound=False, music_volume=1.0)
    plan, res = make(proj, media["silent"], out(), music_path=media["music_tone"])
    assert stream_info(res.output_path, "a") and audio_level(res.output_path, 1.0, 2.0)[0] > -30
    assert count_frames(res.output_path) == 100


def test_transitions_shorten_the_video_and_blend_colours(media, out):
    proj = project(slice_pieces(6.0, 2.0), transition="fade", transition_s=0.4)
    plan, res = make(proj, media["scenes"], out())
    assert plan.timeline.total_frames == 150 - 2 * 10 == count_frames(res.output_path)
    r, g, b = color_at(res.output_path, 1.8)             # the middle of the first crossfade: red and green mixed
    assert r > 60 and g > 60 and b < 90


@pytest.mark.parametrize("shape, size", [("vertical", (1080, 1920)), ("vertical_blur", (1080, 1920)), ("square", (1080, 1080)), ("original", (320, 180))])
def test_shapes(media, out, shape, size):
    plan, res = make(project(slice_pieces(2.0, 1.0), shape=shape), media["tone"], out())
    assert (res.verify.width, res.verify.height) == size and count_frames(res.output_path) == 50


def test_preview_is_small_but_the_same_length_and_shape(media, out):
    proj = project(slice_pieces(4.0, 1.0), shape="vertical")
    full = plan_project(proj, media["tone"], output_path=out())
    plan, res = make(proj, media["tone"], out("p.mp4"), preview=True)
    assert plan.timeline.total_frames == full.timeline.total_frames == 100
    assert res.verify.height == 854 and res.verify.width == 480


def test_two_different_projects_never_share_a_result(media, out):
    """The stale-cache failure of an older app: edit B must not return edit A's video."""
    pieces = slice_pieces(6.0, 2.0)
    one, two = out("same.mp4"), out("same.mp4")
    _, a = make(project(pieces[:1]), media["scenes"], one)
    assert count_frames(one) == 50
    _, b = make(project(pieces[1:]), media["scenes"], two)
    assert count_frames(two) == 100 and is_green(color_at(two, 0.5)) and not b.cache_hit


def test_everything_removed_is_a_plain_error(media, out):
    pieces = slice_pieces(6.0, 2.0)
    for p in pieces:
        p["off"] = True
    with pytest.raises(EditForgeError) as e:
        plan_project(project(pieces), media["scenes"], output_path=out())
    assert "no video left" in e.value.message and e.value.fix


def test_follow_faces_without_the_add_on_says_how_to_fix_it(media, out, monkeypatch):
    import editforge.project as pm
    monkeypatch.setattr(pm, "make_face_provider", lambda analyzer: None)
    with pytest.raises(EditForgeError) as e:
        plan_project(project(slice_pieces(2.0, 1.0), shape="vertical_follow"), media["tone"], output_path=out())
    assert "switched off" in e.value.message and "pip install" in e.value.fix


# ---------------------------------------------------------------------------- face path (regression)
def test_a_piece_inside_a_long_camera_move_keeps_moving():
    """Found with a real face moving across the frame: pieces between two far-apart path points stood still."""
    path = [(0.0, 0.2), (0.5, 0.2), (1.25, 0.26), (7.75, 0.76)]
    w = window_of_path(path, 2.5, 7.5)                   # a piece from 4 s to 6 s (with 1.5 s of margin)
    assert w[0][0] == 2.5 and w[-1][0] == 7.5 and len(w) == 2
    assert abs(w[0][1] - path_value(path, 2.5)) < 1e-4 and w[-1][1] > w[0][1] + 0.3
    assert window_of_path(path, -1.5, 3.5)[1:4] == path[:3]
    assert path_value(path, -5) == 0.2 and path_value(path, 99) == 0.76 and abs(path_value(path, 4.5) - 0.51) < 1e-6
    assert window_of_path([], 0, 1) == []


# ---------------------------------------------------------------------------- combining two videos
def test_combine_options_fall_back_to_safe_values():
    o = clean_options({"mode": "explode", "transition": "x", "size": 99, "opacity": "a", "sound": 1, "quality": None, "corner": "middle"})
    assert (o["mode"], o["transition"], o["size"], o["opacity"], o["sound"], o["quality"], o["corner"]) == \
        ("sequence", "none", 0.9, 0.5, "both", "best", "bottom_right")


def test_combine_one_after_the_other(media, out):
    r = combine(media["scenes"], media["tall"], out(), {"mode": "sequence", "quality": "small"})
    assert abs(r["duration"] - 10.0) < 0.1 and (r["verify"]["width"], r["verify"]["height"]) == (160, 90) and r["verify"]["has_audio"]
    assert is_red(color_at(out(), 1.0)) and is_blue(color_at(out(), 5.0))
    r = combine(media["scenes"], media["tall"], out("x.mp4"), {"mode": "sequence", "transition": "fade", "transition_s": 1, "quality": "small"})
    assert abs(r["duration"] - 9.0) < 0.1


@pytest.mark.parametrize("opts, size, length", [
    ({"mode": "side"}, (320, 90), 6.0),                               # 160x90 next to a 320x180 video resized to the same height
    ({"mode": "stack", "sound": "none"}, (160, 180), 6.0),
    ({"mode": "side", "length": "shortest"}, (160 + 50, 90), 4.0),    # the tall 4 s video is resized to the same height
    ({"mode": "side", "length": "longest", "sound": "b"}, (160 + 50, 90), 6.0),
    ({"mode": "corner", "corner": "top_left"}, (160, 90), 6.0),
    ({"mode": "blend", "opacity": 0.5, "sound": "a"}, (160, 90), 6.0),
])
def test_combine_layouts(media, out, opts, size, length):
    second = media["tone"] if opts == {"mode": "side"} or opts["mode"] == "stack" else media["tall"]
    r = combine(media["scenes"], second, out(), dict(opts, quality="small"))
    v = r["verify"]
    assert (v["width"], v["height"]) == size and abs(v["duration"] - length) < 0.1
    assert v["has_audio"] == (opts.get("sound") != "none")


def test_combine_side_by_side_shows_both(media, out):
    from helpers import frame_rgb
    combine(media["scenes"], media["black"], out(), {"mode": "side", "quality": "small"})
    px = frame_rgb(out(), 1.0, 8, 1)                      # 8 samples across the picture
    assert is_red(px[3:6]) and max(px[18:21]) < 40        # red on the left, black on the right
    combine(media["black"], media["scenes"], out("b.mp4"), {"mode": "side", "quality": "small"})
    px = frame_rgb(out("b.mp4"), 1.0, 8, 1)
    assert max(px[3:6]) < 40 and is_red(px[18:21])


def test_combine_works_when_one_video_has_no_sound(media, out):
    r = combine(media["black"], media["tone"], out(), {"mode": "sequence", "quality": "small"})
    assert r["verify"]["has_audio"] and abs(r["duration"] - 10.0) < 0.1
    assert audio_level(out(), 0.5, 3.0)[0] < -60 and audio_level(out(), 5.0, 3.0)[0] > -30


def test_combine_needs_two_pictures(media, out):
    with pytest.raises(EditForgeError) as e:
        combine(media["scenes"], media["tone_wav"], out(), {"mode": "side"})
    assert "picture" in e.value.message and e.value.fix
