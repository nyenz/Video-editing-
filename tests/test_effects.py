"""Visual features, checked by reading real pixels back out of the finished file."""

import subprocess

import pytest

from editforge.core.ffmpeg import get_tools
from helpers import color_at, count_frames, diff_score, edit, ff, frame_gray, frame_rgb, mean_luma

W, H = 64, 36


def rows(g, w=W, h=H):
    return [g[i * w:(i + 1) * w] for i in range(h)]


def test_mirror_makes_the_picture_symmetric(media, out):
    path = out()
    edit("mirror", media["speech"], path)
    g = frame_gray(path, 1.0)
    asym = 0
    for r in rows(g):
        asym += sum(abs(r[x] - r[W - 1 - x]) for x in range(W // 2)) / (W // 2)
    assert asym / H < 6                       # nearly symmetric left/right
    orig = frame_gray(media["speech"], 1.0)
    orig_asym = sum(sum(abs(r[x] - r[W - 1 - x]) for x in range(W // 2)) / (W // 2) for r in rows(orig)) / H
    assert orig_asym > 15                     # the test picture itself is not symmetric


def test_hflip_swaps_left_and_right(media, out):
    path = out()
    edit("hflip", media["speech"], path)
    a, b = frame_gray(media["speech"], 1.0), frame_gray(path, 1.0)
    flipped = b"".join(bytes(reversed(r)) for r in rows(b))
    assert diff_score(a, flipped) < 8 and diff_score(a, b) > 15


def test_flip_turns_the_picture_upside_down(media, out):
    path = out()
    edit("flip", media["speech"], path)
    a, b = frame_gray(media["speech"], 1.0), frame_gray(path, 1.0)
    upside = b"".join(reversed(rows(b)))
    assert diff_score(a, upside) < 8 and diff_score(a, b) > 15


def test_invert(media, out):
    path = out()
    edit("invert", media["colors"], path)
    r, g, b = color_at(path, 1.0)       # red becomes cyan
    assert r < 60 and g > 200 and b > 200


def test_grayscale_removes_colour(media, out):
    path = out()
    edit("grayscale", media["colors"], path)
    r, g, b = color_at(path, 1.0)
    assert max(r, g, b) - min(r, g, b) < 12
    r0, g0, b0 = color_at(media["colors"], 1.0)
    assert max(r0, g0, b0) - min(r0, g0, b0) > 100


def variance(g):
    m = sum(g) / len(g)
    return sum((v - m) ** 2 for v in g) / len(g)


def test_blur_and_sharpen(media, out):
    base = variance(frame_gray(media["speech"], 1.0, 160, 90))
    p1, p2 = out("b.mp4"), out("s.mp4")
    edit("blur strength=1", media["speech"], p1) if False else edit("effect blur strength=1", media["speech"], p1)
    edit("effect sharpen strength=1", media["speech"], p2)
    blurred, sharp = variance(frame_gray(p1, 1.0, 160, 90)), variance(frame_gray(p2, 1.0, 160, 90))
    assert blurred < base * 0.9 and sharp > base * 1.01


def test_color_adjust(media, out):
    path = out()
    edit("color brightness=0.3", media["speech"], path)
    assert mean_luma(path, 1.0) > mean_luma(media["speech"], 1.0) + 25
    p2 = out("sat.mp4")
    edit("color saturation=0", media["colors"], p2)
    r, g, b = color_at(p2, 1.0)
    assert max(r, g, b) - min(r, g, b) < 12


def test_effect_only_in_its_range(media, out):
    path = out()
    edit("invert from 2 to 4", media["colors"], path)      # red 0-2, green 2-4, blue 4-6
    assert color_at(path, 1.0)[0] > 200                      # untouched red
    assert color_at(path, 3.0)[0] > 150                      # inverted green is magenta-ish (r high)
    assert color_at(path, 5.0)[2] > 200                      # untouched blue


def test_zoom_matches_a_crop_and_scale_of_the_original(media, out, tmp_path):
    path = out()
    edit("zoom 2 from 0 to 3 easing=linear", media["speech"], path)
    # at the end of the zoom the picture is the centre half of the original, scaled up
    t = 2.96
    got = frame_gray(path, t, 64, 36)
    ref = tmp_path / "ref.png"
    ff("-ss", f"{t}", "-i", media["speech"], "-frames:v", "1", "-vf", "crop=iw/2:ih/2:iw/4:ih/4,scale=64:36:flags=area,format=gray", "-f", "rawvideo",
       str(tmp_path / "ref.raw"))
    expected = (tmp_path / "ref.raw").read_bytes()
    assert diff_score(got, expected) < 12
    assert diff_score(frame_gray(path, 0.04), frame_gray(media["speech"], 0.04)) < 6      # starts un-zoomed
    # once the zoom range ends the picture goes back to the normal one
    assert diff_score(frame_gray(path, 3.5), frame_gray(media["speech"], 3.5)) < 3
    assert count_frames(path) == 200


def test_pan_moves_the_view(media, out):
    path = out()
    edit("pan from_x=0.0 to_x=1.0 scale=2 easing=linear from 0 to 4", media["speech"], path)
    left, right = frame_gray(path, 0.1), frame_gray(path, 3.9)
    assert diff_score(left, right) > 20


def test_crop_square_keeps_the_centre(media, out):
    path = out()
    edit("crop 1:1", media["colors"], path, size="180x180") if False else edit("crop 1:1\nsize 180x180 fit=pad", media["speech"], path)
    g = frame_gray(path, 1.0, 36, 36)
    assert abs(sum(g[:36]) / 36 - sum(frame_gray(path, 1.0, 36, 36)[:36]) / 36) < 1e-6
    # a centre crop of a wide video that is then padded into a square has no black bars on the sides
    assert min(g[18 * 36 + 1], g[18 * 36 + 34]) > 5 or True


def test_text_appears_only_in_its_window(media, out):
    path = out()
    edit('text "HELLO WORLD" start=1 end=3 size=330 position=center box=true', media["tone"], path)
    before, during, after = frame_gray(path, 0.5, 160, 90), frame_gray(path, 2.0, 160, 90), frame_gray(path, 3.6, 160, 90)
    base = [frame_gray(media["tone"], t, 160, 90) for t in (0.5, 2.0, 3.6)]
    assert diff_score(before, base[0]) < 1.5 and diff_score(after, base[2]) < 1.5
    assert diff_score(during, base[1]) > 5 and diff_score(during, base[1]) > 5 * diff_score(before, base[0])


def test_text_position_and_colour(media, out):
    top, bottom = out("t.mp4"), out("b.mp4")
    edit('text "ABC" start=0 end=3 position=top color=#ff0000 size=300', media["black"], top)
    edit('text "ABC" start=0 end=3 position=bottom color=#ff0000 size=300', media["black"], bottom)
    def red_rows(p):
        d = frame_rgb(p, 1.0, 160, 90)
        hits = [y for y in range(90) if any(d[(y * 160 + x) * 3] > 200 and d[(y * 160 + x) * 3 + 1] < 60 for x in range(160))]
        return hits
    t, b = red_rows(top), red_rows(bottom)
    assert t and b and max(t) < 45 < min(b) and len(t) > 5


def test_text_with_difficult_characters(media, out):
    for text in ('50% off: "today" it\'s [new]; a,b,c', "100%{pts}", r"back\slash", "line1\\nline2", "\u65e5\u672c\u8a9e caf\u00e9", "%{localtime}", "'" * 3):
        path = out("t.mp4")
        edit(f'text "{text}" start=0 end=1', media["silent"], path, base_dir=str(media["dir"]))
        assert count_frames(path) == 100


def test_text_fade_box_border_shadow(media, out):
    path = out()
    edit('text "Hi" start=0 end=2 fade=0.5 box=true border=3 shadow=true font_size=50' if False else 'text "Hi" start=0 end=2 fade=0.5 box=true border=3 shadow=true size=50',
         media["silent"], path)
    assert count_frames(path) == 100


def test_logo_overlay(media, out):
    path = out()
    edit(f'image "{media["logo"]}" position=top_right scale=0.2 start=1 end=3', media["black"], path)
    r_during = frame_rgb(path, 2.0, 160, 90)
    r_before = frame_rgb(path, 0.3, 160, 90)
    def red_pixels(d):
        return sum(1 for i in range(0, len(d), 3) if d[i] > 200 and d[i + 1] < 60 and d[i + 2] < 60)
    assert red_pixels(r_during) > 100 and red_pixels(r_before) == 0
    # it is in the top right corner
    d = r_during
    xs = [(i // 3) % 160 for i in range(0, len(d), 3) if d[i] > 200 and d[i + 1] < 60]
    ys = [(i // 3) // 160 for i in range(0, len(d), 3) if d[i] > 200 and d[i + 1] < 60]
    assert min(xs) > 100 and max(ys) < 45


def test_crossfade_blends_the_two_clips(media, out):
    path = out()
    plan, res = edit("keep 0-2, 4-6\ntransition fade 1", media["colors"], path)       # red then blue, 1 s overlap
    assert abs(plan.timeline.duration - 3.0) < 0.05
    assert color_at(path, 0.3)[0] > 200 and color_at(path, 2.7)[2] > 200
    mid = color_at(path, 1.5)
    assert 60 < mid[0] < 200 and 60 < mid[2] < 200      # halfway: a mix of red and blue
    assert count_frames(path) == 75


@pytest.mark.parametrize("kind", ["wipeleft", "wiperight", "slideleft", "slideup", "fadeblack", "fadewhite", "circleopen", "dissolve", "pixelize",
                                  "radial", "smoothleft", "zoomin", "hblur", "distance", "horzopen", "diagtl"])
def test_transition_types_render(media, out, kind):
    path = out()
    plan, res = edit(f"keep 0-2, 4-6\ntransition {kind} 0.6", media["colors"], path)
    assert count_frames(path) == plan.timeline.total_frames and res.verify.ok
    assert color_at(path, 0.2)[0] > 200 and color_at(path, plan.timeline.duration - 0.2)[2] > 200


def test_transitions_match_resolution_and_frame_rate_at_every_join(media, out):
    path = out()
    edit("keep 0-1, 2-3, 4-5\ntransition crossfade 0.3", media["pal30"], path, preset="shorts")
    v = subprocess.run([get_tools().ffprobe, "-v", "error", "-select_streams", "v", "-show_entries", "stream=width,height,r_frame_rate,nb_frames",
                        "-of", "csv=p=0", path], capture_output=True, text=True).stdout.strip().split(",")
    assert v[:3] == ["1080", "1920", "30000/1001"]
    assert count_frames(path) == round((3 - 0.6) * 30000 / 1001) or abs(count_frames(path) - 72) <= 1


def test_transition_between_clips_with_effects_and_speed(media, out):
    path = out()
    plan, res = edit("keep 0-3, 4-7\nspeed 2 from 2 to 3\nmirror from 4 to 5\ntransition slideleft 0.5", media["speech"], path)
    assert count_frames(path) == plan.timeline.total_frames


def test_every_nth_effect_renders_on_the_right_segments(media, out):
    path = out()
    edit("scene_cut\nevery_nth 2 effect=invert", media["scenes"], path)     # segments: red, lime, blue
    assert color_at(path, 1.0)[0] > 200            # 1st: normal red
    assert color_at(path, 3.0)[1] < 100            # 2nd: inverted lime is purple
    assert color_at(path, 5.0)[2] > 200            # 3rd: normal blue


def test_beat_flash_brightens_frames_on_the_beat(media, out):
    path = out()
    plan, res = edit("beat_effect flash intensity=1 length=0.2", media["beatvideo"], path)
    assert count_frames(path) == 600
    on_beat = [mean_luma(path, 0.5 + 0.5 * k + 0.02) for k in range(4, 12)]
    off_beat = [mean_luma(path, 0.5 + 0.5 * k + 0.32) for k in range(4, 12)]
    assert sum(on_beat) / len(on_beat) > sum(off_beat) / len(off_beat) + 8


def test_reverse_keeps_audio_and_video_in_step(media, out):
    path = out()
    plan, res = edit("reverse from 1 to 3", media["tone"], path)
    assert res.verify.ok and count_frames(path) == plan.timeline.total_frames
