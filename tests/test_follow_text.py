"""Following a face or an object, words on the picture, captions in projects, and the tempo fixes."""

from __future__ import annotations

import math
import os
import threading

import pytest

from editforge.analysis import beats as B
from editforge.analysis import follow as F
from editforge.analysis.facepath import Face
from editforge.analysis.faces import opencv_available
from editforge.analysis.speech import Word
from editforge.core.errors import EditForgeError
from editforge.core.model import Segment
from editforge.project import clean_piece, clean_project, clean_texts, plan_project, remap_words, slice_pieces
from editforge.render.engine import RenderOptions, render
from editforge.render.filters import ItemBuilder
from editforge.web.server import make_server
from helpers import count_frames, frame_rgb
from test_web_workshop import Client
from tools.make_samples import run, write_click_track

needs_cv = pytest.mark.skipif(not F.object_tracking_available(), reason="the OpenCV add-on is not installed")


def project(pieces, texts=None, **settings):
    return clean_project({"name": "t", "source": "x", "pieces": pieces, "texts": texts or [], "settings": dict({"quality": "high"}, **settings)}, 1e9)


def make(proj, src, out, **kw):
    plan = plan_project(proj, src, output_path=out, **kw)
    return plan, render(plan, RenderOptions(overwrite=True))


def box_x(t):            # where the moving box is in the test video (left edge, pixels of 640)
    return 40 + 60 * t


def box_y(t):
    return 60 + 120 * abs(math.sin(t * 0.9))


@pytest.fixture(scope="module")
def moving(tmp_path_factory):
    """A grey 640x360 video with one 80x80 patterned box that moves right and bobs up and down for 8 seconds."""
    out = str(tmp_path_factory.mktemp("follow") / "moving.mp4")
    run(["-f", "lavfi", "-i", "color=c=0x606060:size=640x360:rate=25:duration=8", "-f", "lavfi", "-i", "testsrc=size=80x80:rate=25:duration=8",
         "-filter_complex", "[0][1]overlay=x='40+60*t':y='60+120*abs(sin(t*0.9))'", "-c:v", "libx264", "-preset", "ultrafast", "-g", "25",
         "-pix_fmt", "yuv420p", out])
    return out


def is_background(px):                      # the plain grey (0x60 = 96) behind the moving box
    return all(abs(v - 96) < 12 for v in px)


# ---------------------------------------------------------------------------- path maths
def test_view_position_puts_the_subject_in_the_middle():
    assert F.view_position(0.5, 2.0) == 0.5
    assert F.view_position(0.25, 2.0) == 0.0 and F.view_position(0.75, 2.0) == 1.0     # the view cannot leave the picture
    assert F.view_position(0.1, 2.0) == 0.0 and F.view_position(0.9, 4.0) == 1.0
    assert abs(F.view_position(0.6, 2.0) - 0.7) < 1e-9 and F.view_position(0.3, 1.0) == 0.5


def test_path_window_and_value():
    path = [(0.0, 0.2, 0.5), (4.0, 0.6, 0.5), (8.0, 0.6, 0.9)]
    assert F.value_at(path, 2.0) == pytest.approx((0.4, 0.5)) and F.value_at(path, -3) == (0.2, 0.5) and F.value_at(path, 99) == (0.6, 0.9)
    w = F.window(path, 1.0, 6.0)
    assert w[0] == (1.0, 0.3, 0.5) and w[1] == (4.0, 0.6, 0.5) and w[-1] == (6.0, 0.6, 0.7) and len(w) == 3
    many = [(i * 0.1, i / 1000.0, 0.5) for i in range(1000)]
    assert len(F.window(many, 10.0, 90.0, max_points=24)) == 24 and F.window([], 0, 1) == []


def test_calm_removes_shake_without_lagging():
    shaky = [(i * 0.1, 0.5 + 0.2 * i / 50 + (0.03 if i % 2 else -0.03), 0.5) for i in range(51)]
    smooth = F.calm(shaky, 0.5)
    steps = [abs(b[1] - a[1]) for a, b in zip(smooth, smooth[1:])]
    assert max(steps) < 0.02 and abs(smooth[25][1] - 0.6) < 0.02          # calm, and still on the true line in the middle
    assert F.calm(shaky[:2]) == shaky[:2]


def test_face_points_follow_the_most_seen_face_and_wait_when_it_is_hidden():
    samples = [(t / 4.0, [Face(0.1 + 0.05 * t / 4.0, 0.3, 0.1, 0.1)] if not 8 <= t <= 10 else []) for t in range(32)]
    pts = F.face_points(samples)
    assert len(pts) == 32 and pts[0][1] < pts[-1][1] and all(0 <= p[1] <= 1 and 0 <= p[2] <= 1 for p in pts)
    assert abs(pts[-1][1] - (0.1 + 0.05 * 31 / 4 + 0.05)) < 0.05 and pts[0][2] > 0.35      # a bit below the middle of the face
    assert F.face_points([(0.0, []), (0.25, [])]) == []


def test_follow_expression_glides_between_keyframes():
    expr = ItemBuilder.follow_expr([(2.0, 0.2, 0.9), (4.0, 0.6, 0.9), (4.0, 0.99, 0.9), (6.0, 0.6, 0.1)], 1, "T")
    val = lambda t: eval(expr, {"clip": lambda x, lo, hi: max(lo, min(hi, x)), "T": t})
    assert val(0) == pytest.approx(0.2) and val(3) == pytest.approx(0.4) and val(5) == pytest.approx(0.6) and val(60) == pytest.approx(0.6)
    y = ItemBuilder.follow_expr([(2.0, 0.2, 0.9), (4.0, 0.6, 0.9), (6.0, 0.6, 0.1)], 2, "T")
    assert eval(y, {"clip": lambda x, lo, hi: max(lo, min(hi, x)), "T": 5}) == pytest.approx(0.5)
    assert ItemBuilder.follow_expr([(1.0, 7.0, -3.0)], 1, "T") == "(1.00000)"            # one point, clamped into 0..1


def test_clean_piece_follow():
    good = clean_piece({"start": 0, "end": 2, "follow": {"mode": "object", "zoom": 99, "path": [[1, 0.5, 0.5], [0, 2, -1], "x", [float("nan"), 0, 0], [3]]}}, 10)
    assert good["follow"] == {"mode": "object", "zoom": 4.0, "path": [[0.0, 1.0, 0.0], [1.0, 0.5, 0.5]]}
    assert clean_piece({"start": 0, "end": 2, "follow": {"mode": "object", "path": [[1, 0.5, 0.5]]}}, 10)["follow"]["mode"] == "none"   # one point is no path
    assert clean_piece({"start": 0, "end": 2, "follow": {"mode": "face", "path": [[1, 2, 3]]}}, 10)["follow"] == {"mode": "face", "zoom": 1.6, "path": []}
    assert clean_piece({"start": 0, "end": 2, "follow": "yes"}, 10)["follow"]["mode"] == "none"


# ---------------------------------------------------------------------------- following an object
@needs_cv
def test_object_is_followed_along_a_curved_path(moving):
    at = 3.0
    box = [box_x(at) / 640, box_y(at) / 360, 80 / 640, 80 / 360]
    path, found = F.track_object(moving, at, box, 0.0, 8.0, 640, 360)
    assert found > 0.95 and len(path) >= 78
    for t, x, y in path[5:-5:6]:
        assert abs(x - (box_x(t) + 40) / 640) < 0.03 and abs(y - (box_y(t) + 40) / 360) < 0.07, (t, x, y)


@needs_cv
def test_object_tracking_refuses_what_it_cannot_do(moving):
    with pytest.raises(EditForgeError) as e:
        F.track_object(moving, 3.0, [0.8, 0.8, 0.1, 0.1], 0.0, 8.0, 640, 360)             # plain grey: nothing to recognise
    assert "plain" in e.value.message and e.value.fix
    with pytest.raises(EditForgeError) as e:
        F.track_object(moving, 7.5, [0.1, 0.1, 0.1, 0.1], 0.0, 4.0, 640, 360)             # the box was drawn outside the picked pieces
    assert "not inside" in e.value.message
    with pytest.raises(EditForgeError) as e:
        F.track_object(moving, 1.0, [0.1, 0.1, 0.1, 0.1], 0.0, 10000.0, 640, 360)
    assert "the most is" in e.value.message
    stop = threading.Event()
    stop.set()
    from editforge.core.errors import Cancelled
    with pytest.raises(Cancelled):
        F.track_object(moving, 3.0, [0.3, 0.3, 0.2, 0.2], 0.0, 8.0, 640, 360, cancel=stop)


@needs_cv
def test_followed_object_stays_in_the_middle_of_the_finished_video(moving, out):
    at = 3.0
    path, _ = F.track_object(moving, at, [box_x(at) / 640, box_y(at) / 360, 80 / 640, 80 / 360], 1.0, 7.0, 640, 360)
    follow = {"mode": "object", "zoom": 2.0, "path": [list(p) for p in path]}
    pieces = [{"start": 1.0, "end": 4.0, "follow": follow}, {"start": 4.0, "end": 7.0, "follow": follow, "fx": ["hflip"]},
              {"start": 1.0, "end": 4.0}]
    plan, res = make(project(pieces), moving, out())
    assert count_frames(res.output_path) == 225 and (res.verify.width, res.verify.height) == (640, 360)
    for t in (0.5, 1.5, 2.5, 3.5, 4.5, 5.5):                    # followed (also in the flipped piece): the box fills the middle
        px = frame_rgb(res.output_path, t, 5, 5)
        middle, corner = px[12 * 3:13 * 3], px[0:3]
        assert is_background(corner) and not is_background(middle), (t, tuple(middle), tuple(corner))
    px = frame_rgb(res.output_path, 6.5 + 0.5, 5, 5)             # the last piece is not followed: at 1.5 s the box is top left
    assert is_background(px[12 * 3:13 * 3]) and is_background(px[24 * 3:25 * 3])


@needs_cv
def test_follow_is_left_out_with_a_tall_shape_and_says_so(moving, out):
    pieces = [{"start": 0.0, "end": 2.0, "follow": {"mode": "object", "zoom": 2.0, "path": [[0, 0.2, 0.3], [2, 0.4, 0.5]]}}]
    plan = plan_project(project(pieces, shape="vertical"), moving, output_path=out())
    assert any("original shape" in w for w in plan.warnings) and all(s.camera is None for s in plan.timeline.segments)


def _face_photo():
    try:
        import matplotlib
        p = os.path.join(matplotlib.get_data_path(), "sample_data", "grace_hopper.jpg")
        return p if os.path.exists(p) else None
    except Exception:
        return None


@pytest.mark.skipif(not opencv_available() or not _face_photo(), reason="OpenCV 4 or a sample face photo is not available")
def test_zoom_and_follow_a_real_face_even_a_small_one(tmp_path, out):
    """A real photo of a face, small in a wide picture, sliding to the right: the zoomed view must go with it."""
    src = str(tmp_path / "face.mp4")
    run(["-f", "lavfi", "-i", "color=c=0x305070:size=1280x720:rate=25:duration=6", "-loop", "1", "-i", _face_photo(), "-filter_complex",
         "[1]scale=-2:260[f];[0][f]overlay=x='60+150*t':y=200:shortest=1", "-t", "6", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", src])
    pieces = [dict(p, follow={"mode": "face", "zoom": 2.0}) for p in slice_pieces(6.0, 2.0)]
    plan, res = make(project(pieces), src, out(), preview=True)
    assert not plan.warnings and all(s.camera is not None and len(s.camera.path) >= 2 for s in plan.timeline.segments)
    xs = [s.camera.path[len(s.camera.path) // 2][1] for s in plan.timeline.segments]
    assert xs[0] < xs[1] < xs[2]                                 # the view moves right with the face
    for t in (1.0, 3.0, 5.0):                                    # the middle of the picture is the photo, not the blue background
        r, g, b = frame_rgb(res.output_path, t, 3, 3)[4 * 3:5 * 3]
        assert not (b > r + 25 and b > g + 10), (t, r, g, b)


def test_follow_face_without_the_add_on_says_how_to_fix_it(media, out, monkeypatch):
    import editforge.project as pm
    monkeypatch.setattr(pm, "make_face_provider", lambda analyzer: None)
    pieces = [{"start": 0, "end": 2, "follow": {"mode": "face"}}]
    with pytest.raises(EditForgeError) as e:
        plan_project(project(pieces), media["tone"], output_path=out())
    assert "switched off" in e.value.message and "pip install" in e.value.fix


# ---------------------------------------------------------------------------- words on the picture
def test_clean_texts():
    t = clean_texts([{"text": "  Hi <b>there</b>  ", "start": 1, "end": 4, "position": "top", "size": "huge", "color": "red", "box": False},
                     {"text": ""}, "junk", {"text": "x" * 500, "start": 5, "end": 2, "position": "moon", "size": 7, "color": "#fff"}])
    assert t[0] == {"text": "Hi <b>there</b>", "start": 1.0, "end": 4.0, "position": "top", "size": "huge", "color": "red", "box": False}
    assert len(t) == 2 and len(t[1]["text"]) == 200 and t[1]["end"] == 0.0 and (t[1]["position"], t[1]["size"], t[1]["color"]) == ("bottom", "medium", "white")
    assert len(clean_texts([{"text": "a"}] * 80)) == 50 and clean_texts(None) == []


def test_words_show_only_between_their_times(media, out):
    texts = [{"text": "HELLO: it's 100% 'ok' \\ {x} %{n}", "start": 1, "end": 3, "position": "center", "size": "huge", "color": "yellow", "box": False},
             {"text": "too late", "start": 50}]
    plan, res = make(project(slice_pieces(4.0, 2.0), texts), media["black"], out())
    assert len(plan.timeline.texts) == 1 and any("too late" in w for w in plan.warnings)
    def bright(t):
        px = frame_rgb(res.output_path, t, 32, 18)
        return max(px)
    assert bright(0.4) < 40 and bright(2.0) > 120 and bright(3.6) < 40          # black video: only the words are bright


# ---------------------------------------------------------------------------- captions
def seg(start_f, frames, out_start, speed=1.0, **kw):
    s = Segment("clip", start_f, int(frames * speed), frames, **kw)
    s.out_start = out_start
    return s


def test_caption_words_follow_moved_repeated_and_sped_up_pieces():
    words = [Word("one", 0.2, 0.6), Word("two", 1.2, 1.6), Word("three", 2.2, 2.6)]
    segs = [seg(50, 25, 0), seg(0, 25, 25), seg(0, 25, 50), seg(25, 25, 75, mute=True), seg(50, 10, 100, speed=2.5), seg(0, 25, 110, reverse=True)]
    got = remap_words(words, segs, 25.0)
    assert [w[0] for w in got] == ["three", "one", "one", "three"]              # moved first, repeated, muted and backwards pieces are silent
    assert got[0][1] == pytest.approx(0.2) and got[1][1] == pytest.approx(1.2) and got[2][1] == pytest.approx(2.2)
    assert got[3][1] == pytest.approx(4.0 + 0.2 / 2.5) and got[3][2] - got[3][1] == pytest.approx(0.4 / 2.5)
    assert remap_words([Word("cut", 0.9, 1.5)], [seg(0, 25, 0)], 25.0) == []    # less than 40% of the word is in the piece


def test_captions_from_a_subtitle_file_are_burned_in(media, out):
    pieces = [{"start": 2.0, "end": 4.0}, {"start": 0.0, "end": 2.0}]          # the subtitle file says "Hello brave new world" from 0.5 to 2.0
    plan, res = make(project(pieces, captions="u/x/talk.srt", caption_style="plain", caption_size="large"), media["black"], out(),
                     captions_path=media["srt"])
    lines = plan.timeline.captions.lines
    assert [ln.text for ln in lines] == ["Hello brave new world"] and lines[0].start == pytest.approx(2.5, abs=0.05)   # moved with its piece
    bottom = lambda t: max(frame_rgb(res.output_path, t, 64, 36)[64 * 3 * 24:])
    assert bottom(1.0) < 30 and bottom(3.2) > 60                               # black video: words only while they are spoken
    plan = plan_project(project([{"start": 2.2, "end": 3.8}], captions="u/x/talk.srt"), media["black"], output_path=out("n.mp4"), captions_path=media["srt"])
    assert plan.timeline.captions is None and any("No speech" in w for w in plan.warnings)


def test_automatic_captions_need_sound(media, out):
    with pytest.raises(EditForgeError) as e:
        plan_project(project(slice_pieces(4.0, 2.0), captions="auto"), media["black"], output_path=out())
    assert "no sound" in e.value.message and e.value.fix


# ---------------------------------------------------------------------------- over HTTP
@pytest.fixture(scope="module")
def web(tmp_path_factory):
    srv, state = make_server("127.0.0.1", 0, home=tmp_path_factory.mktemp("follow_home"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield Client(state.port, state.token), state
    state.manager.shutdown()
    state.previews.shutdown()
    srv.shutdown()
    srv.server_close()


def test_subtitle_upload_and_file_kinds(web, media, tmp_path):
    c, _ = web
    sid = c.upload(media["srt"])
    listed = {f["id"]: f for f in c.json("GET", "/api/files")[1]["files"]}
    assert listed[sid]["subtitles"] and not listed[sid]["audio"]
    bad = tmp_path / "empty.srt"
    bad.write_text("this is not a subtitle file\n")
    status, data = c.json("POST", "/api/upload?name=empty.srt", bad.read_bytes())
    assert status == 400 and data["fix"]
    assert c.json("POST", "/api/projects", {"source": sid})[0] == 400              # a subtitle file is not a video


def test_project_with_words_and_captions_over_http(web, media):
    c, _ = web
    fid, sid = c.upload(media["black"]), c.upload(media["srt"])
    p = c.json("POST", "/api/projects", {"source": fid, "interval": 2})[1]["project"]
    p["texts"] = [{"text": "TITLE", "start": 0, "end": 1, "size": "huge"}]
    p["settings"].update({"captions": sid, "quality": "small"})
    saved = c.json("PUT", f"/api/projects/{p['id']}", p)[1]
    assert saved["texts"][0]["text"] == "TITLE" and saved["settings"]["captions"] == sid
    job = c.wait_jobs([c.json("POST", f"/api/projects/{p['id']}/render", {})[1]["id"]])[0]
    assert job["status"] == "done" and job["warnings"] == [], job
    assert max(frame_rgb(job["output_path"], 0.5, 64, 36)) > 50 and max(frame_rgb(job["output_path"], 1.5, 64, 36)[:64 * 3 * 20]) < 30
    p["settings"]["captions"] = "u/000000000000/gone.srt"
    c.json("PUT", f"/api/projects/{p['id']}", p)
    status, data = c.json("POST", f"/api/projects/{p['id']}/render", {})
    assert status == 400 and "subtitle file" in data["error"]


def test_automatic_captions_without_the_add_on_is_a_plain_error(web, media, monkeypatch):
    import editforge.analysis.speech as speech
    monkeypatch.setattr(speech, "whisper_available", lambda: False)
    c, _ = web
    p = c.json("POST", "/api/projects", {"source": c.upload(media["tone"]), "interval": 2})[1]["project"]
    p["settings"]["captions"] = "auto"
    c.json("PUT", f"/api/projects/{p['id']}", p)
    status, data = c.json("POST", f"/api/projects/{p['id']}/render", {})
    assert status == 400 and "faster-whisper" in data["error"] and "subtitle file" in data["fix"]


@needs_cv
def test_track_over_http(web, moving):
    c, _ = web
    fid = c.upload(moving)
    body = {"input": fid, "at": 3.0, "start": 2.0, "end": 6.0, "box": {"x": box_x(3) / 640, "y": box_y(3) / 360, "w": 80 / 640, "h": 80 / 360}}
    status, data = c.json("POST", "/api/track", body)
    assert status == 200 and data["found"] > 0.9 and 38 <= len(data["path"]) <= 42 and data["path"][0][0] == 2.0
    mid = min(data["path"], key=lambda p: abs(p[0] - 4.0))
    assert abs(mid[1] - (box_x(4) + 40) / 640) < 0.03
    for broken in ({**body, "box": {"x": 0.1}}, {**body, "box": {"x": 0, "y": 0, "w": 0, "h": 0.1}}, {**body, "start": 3.0, "end": 3.05},
                   {**body, "at": "soon"}, {**body, "input": "m/../../etc/passwd"}):
        status, data = c.json("POST", "/api/track", broken)
        assert status == 400 and data["error"], broken
    assert c.json("POST", "/api/track", body, token=False)[0] == 403


def test_job_warnings_reach_the_page(web, media):
    c, _ = web
    p = c.json("POST", "/api/projects", {"source": c.upload(media["scenes"]), "interval": 2})[1]["project"]
    p["texts"] = [{"text": "never shown", "start": 500}]
    p["settings"]["quality"] = "small"
    c.json("PUT", f"/api/projects/{p['id']}", p)
    job = c.wait_jobs([c.json("POST", f"/api/projects/{p['id']}/render", {"preview": True})[1]["id"]])[0]
    assert job["status"] == "done" and any("never shown" in w for w in job["warnings"])


# ---------------------------------------------------------------------------- tempo fixes
@pytest.mark.parametrize("bpm", [80, 97, 128, 140, 160, 173, 185, 195])
def test_tempo_is_not_half_double_or_two_thirds(tmp_path, bpm):
    """These tempos used to come out as another multiple, or with every beat on the off-beat hi-hat."""
    p = str(tmp_path / "c.wav")
    truth = write_click_track(p, bpm, 4, 28, accent=2.0, seed=bpm)
    info = B.analyze_beats(p, duration=28)
    assert info.tempo == pytest.approx(bpm, rel=0.02)
    on_beat = sum(1 for b in info.beats if min(abs(b - t) for t in truth) < 0.06)
    assert on_beat >= len(info.beats) - 1 and len(info.beats) >= len(truth) - 1


def test_refine_tempo_keeps_a_good_guess_and_fixes_a_bad_one():
    onset = [0.0] * 3000
    for i in range(0, 3000, 40):                # a beat every 0.4 s = 150 BPM
        onset[i] = 1.0
    assert B.refine_tempo(onset, 150.0) == 150.0
    assert B.refine_tempo(onset, 75.0) == pytest.approx(150.0) and B.refine_tempo(onset, 100.0) == pytest.approx(150.0)
    assert B.refine_tempo([0.0] * 500, 120.0) == 120.0 and B.refine_tempo(onset, 0.0) == 0.0


def test_beats_in_the_silence_before_and_after_the_music_are_dropped():
    onset = [0.0] * 2000
    for i in range(500, 1500, 50):
        onset[i] = 1.0
    frames = list(range(0, 2000, 50))
    kept = B._trim_unsupported(frames, onset)
    assert kept[0] == 500 and kept[-1] == 1450 and len(kept) == 20
    assert B._trim_unsupported([10, 20, 30], onset) == [10, 20, 30]
