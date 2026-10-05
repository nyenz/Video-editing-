"""Beat and downbeat detection (synthetic music with known structure) and face tracking."""

import math
import os
import subprocess

import pytest

from editforge.analysis import beats as B
from editforge.analysis import facepath as FP
from editforge.analysis.faces import opencv_available
from editforge.core.errors import FeatureUnavailable
from tools.make_samples import write_click_track
from helpers import count_frames, edit, plan_only


def truth_match(info, truth, tol=0.06):
    return sum(1 for b in info.beats if min(abs(b - t) for t in truth) < tol)


@pytest.mark.parametrize("bpm,meter", [(90, 4), (100, 3), (110, 5), (120, 4), (128, 2), (140, 4)])
def test_tempo_beats_and_metre_of_synthetic_music(tmp_path, bpm, meter):
    p = str(tmp_path / "c.wav")
    truth = write_click_track(p, bpm, meter, 28, accent=2.0, seed=bpm)
    info = B.analyze_beats(p, duration=28)
    assert info.tempo == pytest.approx(bpm, rel=0.02)
    assert truth_match(info, truth) >= len(info.beats) - 1 and len(info.beats) >= len(truth) - 1
    assert info.meter == meter                               # NOT just "every 4th beat"
    true_down = [truth[i] for i in range(0, len(truth), meter)]
    assert len(info.downbeats) >= len(true_down) - 1
    assert all(min(abs(d - t) for t in true_down) < 0.06 for d in info.downbeats)
    assert info.meter_confidence > 0.5


@pytest.mark.parametrize("bpm", [100, 120, 140])
def test_music_without_accents_has_no_downbeats(tmp_path, bpm):
    p = str(tmp_path / "flat.wav")
    write_click_track(p, bpm, 4, 28, accent=1.0, seed=bpm + 1)
    info = B.analyze_beats(p, duration=28)
    assert info.tempo == pytest.approx(bpm, rel=0.02)
    assert info.meter is None and info.downbeats == []
    assert any("No clear accent pattern" in n for n in info.notes)


def test_beats_of_a_video_file_and_short_or_silent_input(media, tmp_path):
    info = B.analyze_beats(media["beatvideo"], duration=24)
    assert info.tempo == pytest.approx(120, rel=0.02) and info.meter == 4
    from tools.make_samples import make_audio
    quiet = make_audio(str(tmp_path / "q.wav"), "0*t", 12)
    assert B.analyze_beats(quiet, duration=12).beats == []
    short = make_audio(str(tmp_path / "s.wav"), "0.5*sin(2*PI*300*t)", 2)
    assert B.analyze_beats(short, duration=2).downbeats == []


def test_fixed_tempo_grid():
    g = B.bpm_grid(120, 0.25, 0, 5)
    assert g.beats[:3] == [0.25, 0.75, 1.25] and g.tempo == 120 and len(g.downbeats) == len(g.beats[::4])
    assert "assume" in g.notes[0]


def test_tempo_estimator_on_a_plain_pulse_train():
    onset = [0.0] * 3000
    for i in range(0, 3000, 50):
        onset[i] = 1.0
    bpm, conf = B.estimate_tempo(onset)
    assert bpm == pytest.approx(120, rel=0.01) and conf > 0.3
    frames = B.track_beats(onset, 50)
    assert len(frames) >= 55 and all(abs((b - a) - 50) <= 1 for a, b in zip(frames, frames[1:]))
    assert B.estimate_tempo([0.0] * 100) == (0.0, 0.0)


@pytest.mark.parametrize("m", [2, 3, 4, 5, 6, 7])
def test_metre_finder_on_ideal_accents(m):
    acc = [(1.0 if i % m == 2 % m else 0.35) for i in range(m * 12)]    # accent on the third beat (the first, when a bar has 2)
    meter, phase, contrast = B.find_meter(acc)
    assert phase == 2 % m or m == 6
    assert meter in (m, m // 2 if m % 2 == 0 else m) or (m == 6 and meter == 3)


def test_metre_finder_refuses_noise():
    import random
    r = random.Random(4)
    for _ in range(5):
        meter, _, _ = B.find_meter([1.0 + r.random() * 0.15 for _ in range(60)])
        assert meter is None
    assert B.find_meter([1.0] * 5)[0] is None


def test_double_tempo_correction():
    frames = list(range(0, 1000, 25))                       # 40 beats at 25-frame spacing
    acc = [1.0 if i % 2 == 0 else 0.3 for i in range(len(frames))]
    f2, a2, bpm = B._halve_if_alternating(frames, acc, 200.0)
    assert bpm == 100.0 and len(f2) == 20
    f3, a3, bpm3 = B._halve_if_alternating(frames, [1.0] * len(frames), 200.0)
    assert bpm3 == 200.0 and len(f3) == 40


def test_beat_cut_and_effects_render(media, tmp_path):
    out = str(tmp_path / "b.mp4")
    plan, res = edit("beat_cut every=4 take=0.5\nbeat_effect zoom_pulse", media["beatvideo"], out)
    assert count_frames(out) == plan.timeline.total_frames and res.verify.ok


# ---- faces: pure logic ------------------------------------------------------------------------------
def face(cx, cy=0.4, size=0.12, mouth=0.0):
    return FP.Face(cx - size / 2, cy - size / 2, size, size, mouth)


def test_tracker_keeps_ids_stable_for_two_people_who_move():
    samples = []
    for k in range(40):
        t = k * 0.25
        samples.append((t, [face(0.25 + 0.003 * k), face(0.75 - 0.003 * k)]))
    tracks = FP.track_all(samples)
    assert len(tracks) == 2 and all(len(tr.samples) == 40 for tr in tracks)


def test_tracker_ids_survive_close_passing_and_missed_detections():
    samples = []
    for k in range(60):
        t = k * 0.25
        a, b = 0.2 + 0.01 * k, 0.8 - 0.01 * k                  # they walk through each other
        faces = [face(a), face(b)]
        if k in (25, 26):
            faces = faces[:1]                                   # one detection is missed for a moment
        samples.append((t, faces))
    tracks = FP.track_all(samples)
    big = [tr for tr in tracks if len(tr.samples) > 20]
    assert len(big) == 2
    for tr in big:        # each track keeps moving the same way (no identity swap at the crossing)
        xs = [f.cx for _, f in tr.samples]
        assert abs(xs[-1] - xs[0]) > 0.4 and all(abs(b - a) < 0.08 for a, b in zip(xs, xs[1:]))


def test_new_face_after_a_long_absence_gets_a_new_id():
    tr = FP.FaceTracker(max_gap=1.0)
    first = tr.update(0.0, [face(0.5)])[0][0]
    again = tr.update(0.5, [face(0.5)])[0][0]
    later = tr.update(5.0, [face(0.5)])[0][0]
    assert first == again and later != first


def test_active_speaker_follows_mouth_movement_with_hysteresis():
    times = [k * 0.25 for k in range(80)]
    samples = []
    for k, t in enumerate(times):
        left_talks = t < 10
        samples.append((t, [face(0.25, mouth=0.5 if left_talks else 0.02), face(0.75, mouth=0.02 if left_talks else 0.5)]))
    tracks = FP.track_all(samples)
    chosen = FP.active_speaker(tracks, times)
    left = min(tracks, key=lambda tr: tr.samples[0][1].cx).id
    right = max(tracks, key=lambda tr: tr.samples[0][1].cx).id
    assert chosen[2] == left and chosen[-2] == right
    switches = sum(1 for a, b in zip(chosen, chosen[1:]) if a != b)
    assert switches == 1                                                    # one clean hand-over, no flicker between speakers


def test_active_speaker_ignores_motion_while_nobody_talks():
    times = [k * 0.25 for k in range(40)]
    samples = [(t, [face(0.25, mouth=0.4), face(0.75, mouth=0.02)]) for t in times]
    tracks = FP.track_all(samples)
    chosen = FP.active_speaker(tracks, times, speech=[(0.0, 1.0)])
    assert len(set(chosen[6:])) == 1


def test_smooth_path_has_no_flicker():
    import random
    r = random.Random(7)
    times = [k * 0.25 for k in range(120)]
    noisy = [0.5 + r.uniform(-0.02, 0.02) for _ in times]             # a still person, jittery detections
    path = FP.smooth_path(times, noisy, smooth=0.7)
    vals = [v for _, v in path]
    assert max(vals) - min(vals) < 0.001                              # inside the dead zone: the camera does not move at all
    moving = [0.3 + 0.004 * k for k in range(120)]
    path = FP.smooth_path(times, moving, smooth=0.7)
    steps = [b[1] - a[1] for a, b in zip(path, path[1:])]
    assert all(s >= -1e-9 for s in steps) and max(steps) <= 0.35 * 0.25 * 1.3 + 1e-6
    jump = FP.smooth_path([0, 0.25, 0.5, 0.75, 1.0], [0.2, 0.2, 0.9, 0.9, 0.9], smooth=0.7)
    assert max(b[1] - a[1] for a, b in zip(jump, jump[1:])) < 0.2     # a sudden jump becomes a glide
    held = FP.smooth_path([0, 1, 2], [0.4, None, None])
    assert [v for _, v in held] == [0.4, 0.4, 0.4]


def test_path_simplifying_keeps_the_shape():
    pts = [(k * 0.25, 0.3 + 0.2 * (k / 100.0)) for k in range(101)]    # a straight glide
    s = FP.simplify(pts, 0.004)
    assert len(s) == 2
    curve = [(k * 0.25, 0.5 + 0.2 * math.sin(k / 10.0)) for k in range(120)]
    s = FP.simplify(curve, 0.004)
    assert 5 < len(s) < 80
    for t, v in curve:
        seg = max((p for p in s if p[0] <= t), key=lambda p: p[0])
        nxt = min((p for p in s if p[0] >= t), key=lambda p: p[0])
        interp = seg[1] if nxt[0] == seg[0] else seg[1] + (nxt[1] - seg[1]) * (t - seg[0]) / (nxt[0] - seg[0])
        assert abs(interp - v) < 0.0045


def test_crop_expression_follows_the_path(media):
    from editforge.core.model import Segment
    from editforge.render.filters import Item, ItemBuilder
    from editforge.planner.planner import Planner
    plan = plan_only("keep 0-6", media["speech"])
    tl = plan.timeline
    b = ItemBuilder(tl, plan.media, 48000, "stereo", True, True)
    seg = tl.segments[0]
    path = [(0.0, 0.2), (2.0, 0.2), (4.0, 0.8), (6.0, 0.8)]
    expr = b.path_expr(Item(seg, 0, seg.frames, 0), path, 320, 100)
    ev = lambda t: eval(expr, {"clip": lambda x, lo, hi: max(lo, min(hi, x)), "t": t})
    wanted = lambda c: max(0, min(220, c * 320 - 50))
    for t, c in ((0.5, 0.2), (2.0, 0.2), (3.0, 0.5), (4.0, 0.8), (5.5, 0.8)):
        assert ev(t) == pytest.approx(wanted(c), abs=1.5)
    # an item that starts in the middle of the path (e.g. after a cut) is offset correctly
    item = Item(seg, 75, 50, 75)
    expr2 = b.path_expr(item, path, 320, 100)
    ev2 = lambda t: eval(expr2, {"clip": lambda x, lo, hi: max(lo, min(hi, x)), "t": t})
    assert ev2(0.0) == pytest.approx(wanted(0.5), abs=2.0)           # frame 75 of 25 fps = 3 s -> halfway through the glide


# ---- faces: real detection on a real photograph (needs OpenCV and a sample picture) -----------------
def _face_photo():
    try:
        import matplotlib
        p = os.path.join(matplotlib.get_data_path(), "sample_data", "grace_hopper.jpg")
        return p if os.path.exists(p) else None
    except Exception:
        return None


needs_faces = pytest.mark.skipif(not opencv_available() or not _face_photo(), reason="OpenCV or a sample face photo is not available")


@pytest.fixture(scope="module")
def moving_face_video(tmp_path_factory):
    d = tmp_path_factory.mktemp("face")
    out = str(d / "face.mp4")
    from helpers import ff
    ff("-f", "lavfi", "-i", "color=c=0x404040:size=640x360:rate=25:duration=8", "-loop", "1", "-i", _face_photo(), "-f", "lavfi", "-i", "sine=frequency=300:duration=8",
       "-filter_complex", "[1:v]scale=-2:200[f];[0:v][f]overlay=x='60+430*t/8':y=80:shortest=1[v]", "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-preset",
       "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-t", "8", out)
    return out


@needs_faces
def test_real_faces_are_found_and_followed(moving_face_video, tmp_path):
    res = FP.detect_faces(moving_face_video, 0, 8, 640, 360)
    assert sum(1 for _, f in res if f) >= len(res) - 4
    path, info = FP.build_path(res, "face", None, 0.7)
    assert info["faces"] == 1 and path[0][1] < 0.35 and path[-1][1] > 0.7
    assert all(b[1] >= a[1] - 1e-9 for a, b in zip(path, path[1:]))     # moves steadily one way, no jitter back


@needs_faces
def test_face_following_reframe_keeps_the_face_in_the_vertical_crop(moving_face_video, tmp_path):
    out = str(tmp_path / "v.mp4")
    plan, res = edit("reframe 9:16 mode=face", moving_face_video, out, preset="shorts")
    from helpers import frame_gray
    assert count_frames(out) == plan.timeline.total_frames and any("face track" in n for n in plan.notes)
    probe = FP.detect_faces(out, 0, 8, 1080, 1920, sample_fps=2)
    found = [f for _, f in probe if f]
    assert len(found) >= 6                                    # a face is in the picture the whole time
    assert all(0.15 < f[0].cx < 0.85 for f in found)           # and stays near the middle of the vertical frame


@needs_faces
def test_speaker_mode_runs_and_is_labelled_a_heuristic(moving_face_video, tmp_path):
    plan = plan_only("reframe 9:16 mode=speaker", moving_face_video)
    assert any("heuristic" in n for n in plan.notes)


def test_face_features_off_without_opencv(media, monkeypatch):
    monkeypatch.setattr("editforge.analysis.faces.opencv_available", lambda: False)
    with pytest.raises(FeatureUnavailable) as e:
        plan_only("reframe 9:16 mode=face", media["speech"])
    assert "opencv" in e.value.message and "mode=center" in e.value.fix
    p = plan_only("reframe 9:16 mode=auto", media["speech"])
    assert any("face tracking is off" in n for n in p.notes)
    p = plan_only("reframe 9:16 mode=center", media["speech"])
    assert p.timeline.segments[0].reframe is not None
