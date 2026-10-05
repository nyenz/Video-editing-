"""Caching, resume, cancel, atomic writes, encoder fallback, fast cuts and memory."""

import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from editforge.api import prepare, read_script
from editforge.core.errors import Cancelled, EditForgeError
from editforge.core.resources import (PeakMemoryMonitor, memory_measurement_available, plan_resources)
from editforge.jobs.manager import JobManager
from editforge.jobs.store import JobStore
from editforge.render import engine as engine_mod
from editforge.render.engine import RenderOptions, Renderer, render, render_job_key
from helpers import color_at, count_frames, edit, ff, plan_only, stream_info

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def long_video(tmp_path_factory):
    from tools.make_samples import make_video
    d = tmp_path_factory.mktemp("long")
    return make_video(str(d / "long.mp4"), 120, "640x360", "25", "speech", gop=50)


# ---- failure 3: stale results must never come back --------------------------------------------------
def test_back_to_back_different_edits_give_different_correct_outputs(media, tmp_path):
    out = str(tmp_path / "result.mp4")        # the SAME output name each time
    for script, seconds in (("keep 0-2", 2.0), ("keep 0-5", 5.0), ("keep 0-2", 2.0), ("keep 1-4", 3.0), ("keep 0-5\nspeed 2", 2.5)):
        plan, res = edit(script, media["speech"], out)
        assert not res.cache_hit or script == "keep 0-2"
        assert stream_info(out, "v")["duration"] and float(stream_info(out, "v")["duration"]) == pytest.approx(seconds, abs=0.05)
        assert count_frames(out) == plan.timeline.total_frames


def test_same_edit_twice_reuses_the_finished_file(media, tmp_path):
    out = str(tmp_path / "r.mp4")
    _, first = edit("keep 0-3", media["speech"], out)
    mtime = os.stat(out).st_mtime_ns
    _, second = edit("keep 0-3", media["speech"], out)
    assert second.cache_hit and os.stat(out).st_mtime_ns == mtime and second.verify.ok
    # the same edit written differently is also recognised
    _, third = edit("# again\nkeep from 0s to 00:00:03", media["speech"], out)
    assert third.cache_hit


def test_cache_is_not_used_when_the_output_was_changed_or_removed(media, tmp_path):
    out = str(tmp_path / "r.mp4")
    edit("keep 0-3", media["speech"], out)
    with open(out, "ab") as fh:
        fh.write(b"x")
    _, again = edit("keep 0-3", media["speech"], out)
    assert not again.cache_hit and count_frames(out) == 75
    os.unlink(out)
    _, third = edit("keep 0-3", media["speech"], out)
    assert not third.cache_hit


def test_changed_source_with_same_name_is_not_confused(media, tmp_path):
    src = str(tmp_path / "input.mp4")
    out = str(tmp_path / "o.mp4")
    shutil.copy(media["colors"], src)
    edit("keep 0-1", src, out)
    assert color_at(out, 0.5)[0] > 200                       # red
    from tools.make_samples import make_color_video
    os.unlink(src)
    make_color_video(src, ["blue", "red", "green"], 2.0)      # same name, same length, different picture
    plan, res = edit("keep 0-1", src, out)
    assert not res.cache_hit and color_at(out, 0.5)[2] > 200  # blue now


def test_job_key_contains_every_ingredient(media, monkeypatch):
    base = plan_only("keep 0-3", media["speech"])
    k = render_job_key(base, "libx264")
    assert k == render_job_key(plan_only("keep 0-3", media["speech"]), "libx264")
    assert k != render_job_key(plan_only("keep 0-4", media["speech"]), "libx264")             # the edit
    assert k != render_job_key(plan_only("keep 0-3", media["speech"], preset="shorts"), "libx264")   # output settings
    assert k != render_job_key(plan_only("keep 0-3", media["speech"], quality="max"), "libx264")
    assert k != render_job_key(base, "h264_nvenc")                                             # the encoder
    assert k != render_job_key(plan_only("keep 0-3", media["tone"]), "libx264")               # the source
    monkeypatch.setattr(engine_mod, "__version__", "9.9.9")
    assert k != render_job_key(base, "libx264")                                                # the app version


def test_changed_source_content_changes_the_fingerprint(media, tmp_path):
    from editforge.core.fingerprint import file_fingerprint
    a, b = tmp_path / "a.bin", tmp_path / "b.bin"
    a.write_bytes(os.urandom(300000))
    shutil.copy(a, b)
    assert file_fingerprint(str(a)) == file_fingerprint(str(b))
    data = bytearray(b.read_bytes())
    data[150000] ^= 0xFF
    b.write_bytes(bytes(data))
    assert file_fingerprint(str(a)) != file_fingerprint(str(b)) or len(data) > 24 * 65536   # sampled for huge files only


def test_analysis_cache_is_shared_between_plan_and_render(media, tmp_path):
    from editforge.core.cache import AnalysisCache
    cache = AnalysisCache(tmp_path / "ac")
    p1 = prepare(read_script("silence_remove"), input_path=media["speech"], cache=cache)
    assert "silence" in p1.analyzer.ran
    p2 = prepare(read_script("silence_remove"), input_path=media["speech"], cache=cache)
    assert "silence" not in p2.analyzer.ran and any("reused" in n for n in p2.analysis_notes)
    assert p1.digest == p2.digest


def test_analysis_cache_changes_with_parameters_and_clears(tmp_path):
    from editforge.core.cache import AnalysisCache
    c = AnalysisCache(tmp_path)
    c.put("x", "fp1", {"a": 1}, [1, 2])
    assert c.get("x", "fp1", {"a": 1}) == [1, 2] and c.get("x", "fp1", {"a": 2}) is None and c.get("x", "fp2", {"a": 1}) is None
    assert c.clear() == 1 and c.get("x", "fp1", {"a": 1}) is None


# ---- output safety ------------------------------------------------------------------------------------
def test_existing_output_needs_overwrite(media, tmp_path):
    out = str(tmp_path / "r.mp4")
    edit("keep 0-1", media["speech"], out)
    with pytest.raises(EditForgeError) as e:
        edit("keep 0-2", media["speech"], out, overwrite=False)
    assert "--overwrite" in e.value.fix
    assert count_frames(out) == 25         # the old file is untouched


def test_output_cannot_replace_the_input(media, tmp_path):
    src = str(tmp_path / "in.mp4")
    shutil.copy(media["speech"], src)
    with pytest.raises(EditForgeError) as e:
        edit("keep 0-1", src, src)
    assert "original" in e.value.message and os.path.getsize(src) == os.path.getsize(media["speech"])


def test_unwritable_output_folder_is_explained(media):
    with pytest.raises(EditForgeError) as e:
        edit("keep 0-1", media["speech"], "/proc/nope/out.mp4")
    assert "write" in e.value.message


def test_output_appears_only_when_complete(media, tmp_path):
    out = str(tmp_path / "r.mp4")
    seen = []
    plan, _ = None, None

    def on_progress(p):
        seen.append((p.fraction, os.path.exists(out)))

    edit("keep 0-6\nloudnorm", media["speech"], out, render_opts={"on_progress": on_progress})
    assert seen and all(not exists for frac, exists in seen if frac < 0.98)
    leftovers = [f for f in os.listdir(tmp_path) if f.endswith(".part")]
    assert leftovers == []


# ---- cancel and resume --------------------------------------------------------------------------------
MANY_CUTS = "keep " + ", ".join(f"{i * 2.4:.1f}-{i * 2.4 + 1.2:.1f}" for i in range(40))


def test_cancel_stops_cleanly_and_resume_finishes(long_video, tmp_path, monkeypatch):
    out = str(tmp_path / "r.mp4")
    monkeypatch.setenv("EDITFORGE_HOME", str(tmp_path / "home"))
    plan = prepare(read_script(MANY_CUTS), input_path=long_video, output_path=out)
    cancel = threading.Event()
    done_pieces = []

    def on_progress(p):
        if p.pieces_done >= 2 and not cancel.is_set():
            cancel.set()
        done_pieces.append(p.pieces_done)

    opts = RenderOptions(overwrite=True, cancel=cancel, on_progress=on_progress, keep_temp=True, workdir_root=str(tmp_path / "work"),
                         workers=1, max_memory_mb=300)
    with pytest.raises(Cancelled):
        render(plan, opts)
    assert not os.path.exists(out) and not [f for f in os.listdir(tmp_path) if f.endswith(".part")]
    work = list((tmp_path / "work").iterdir())[0]
    assert not list(work.glob("*.part")) and list(work.glob("*.ts"))
    res = render(plan, RenderOptions(overwrite=True, workdir_root=str(tmp_path / "work")))
    assert res.pieces_reused >= 2 and res.verify.ok and count_frames(out) == plan.timeline.total_frames


def test_resume_after_the_program_is_killed(long_video, tmp_path):
    out = str(tmp_path / "r.mp4")
    home = tmp_path / "home"
    script = tmp_path / "s.txt"
    script.write_text(MANY_CUTS)
    env = dict(os.environ, EDITFORGE_HOME=str(home), PYTHONPATH=str(ROOT))
    cmd = [sys.executable, "-m", "editforge", "edit", str(script), "-i", long_video, "-o", out, "--workers", "1", "-v"]
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    deadline = time.time() + 120
    work = home / "work"
    while time.time() < deadline:
        if work.exists() and any(len(list(d.glob("*.ts"))) >= 2 for d in work.iterdir() if d.is_dir()):
            break
        time.sleep(0.2)
    proc.send_signal(signal.SIGKILL)
    proc.wait()
    assert not os.path.exists(out)
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-800:]
    assert "Resuming" in r.stderr
    assert count_frames(out) == 40 * 30 or abs(count_frames(out) - 1200) <= 2
    assert not [f for f in os.listdir(tmp_path) if f.endswith(".part")]


def test_resume_does_not_mix_pieces_of_a_different_edit(long_video, tmp_path):
    out = str(tmp_path / "r.mp4")
    root = str(tmp_path / "w")
    opts = dict(workdir_root=root, render_opts=None)
    plan_a = prepare(read_script("keep 0-5\nmirror from 1 to 2"), input_path=long_video, output_path=out)
    render(plan_a, RenderOptions(overwrite=True, keep_temp=True, workdir_root=root))
    plan_b = prepare(read_script("keep 0-5\ninvert from 1 to 2"), input_path=long_video, output_path=out)
    res = render(plan_b, RenderOptions(overwrite=True, workdir_root=root))
    assert res.pieces_reused == 0 and not res.cache_hit
    assert len(list(Path(root).iterdir())) == 1      # plan A's temporary folder was kept (different key), B's was cleaned


def test_temp_files_are_cleaned_unless_asked_to_keep(media, tmp_path):
    root = tmp_path / "w"
    edit("keep 0-2", media["speech"], str(tmp_path / "a.mp4"), render_opts={"workdir_root": str(root)})
    assert list(root.iterdir()) == []
    edit("keep 0-3", media["speech"], str(tmp_path / "b.mp4"), render_opts={"workdir_root": str(root), "keep_temp": True})
    assert len(list(root.iterdir())) == 1


# ---- encoders -----------------------------------------------------------------------------------------
def test_encoder_detection_and_choice():
    from editforge.render.encoders import choose_encoder, detect_encoders, test_encoder
    infos = {i.name: i for i in detect_encoders(force=True)}
    assert infos["libx264"].works and choose_encoder("software") == "libx264"
    ok, err = test_encoder("h264_amf" if not infos["h264_amf"].in_build else "libx264")
    assert isinstance(ok, bool)
    broken = [n for n, i in infos.items() if i.in_build and not i.works]
    for name in broken:
        with pytest.raises(EditForgeError) as e:
            choose_encoder(name)
        assert "not available" in e.value.message
    assert choose_encoder("auto") in infos and infos[choose_encoder("auto")].works


def test_broken_hardware_encoder_falls_back_to_software(media, tmp_path, monkeypatch):
    from editforge.render.encoders import detect_encoders
    infos = {i.name: i for i in detect_encoders()}
    if not infos["h264_nvenc"].in_build or infos["h264_nvenc"].works:
        pytest.skip("this FFmpeg has no NVENC (or it really works), so the failure cannot be shown")
    monkeypatch.setattr(engine_mod, "choose_encoder", lambda pref="auto": "h264_nvenc")
    out = str(tmp_path / "r.mp4")
    plan, res = edit("keep 0-2", media["speech"], out)
    assert res.encoder == "libx264" and any("switched to the software encoder" in n for n in res.notes) and count_frames(out) == 50


def test_hardware_encoder_options_are_built_for_each_vendor():
    from editforge.core.model import OutputSpec
    from editforge.render.encoders import video_encoder_args
    spec = OutputSpec(width=1280, height=720, crf=20)
    for name, needle in (("h264_nvenc", "-cq"), ("h264_qsv", "-global_quality"), ("h264_amf", "-qp_i"), ("h264_videotoolbox", "-q:v")):
        a = video_encoder_args(name, spec, 2)
        assert a[:2] == ["-c:v", name] and needle in a and "yuv420p" in a
    a = video_encoder_args("libx264", spec, 2)
    assert "-crf" in a and "20" in a


# ---- fast cuts and stream copy -----------------------------------------------------------------------
def test_fast_cuts_are_fast_copies_and_say_how_they_differ(media, tmp_path):
    fast, exact = str(tmp_path / "f.mp4"), str(tmp_path / "e.mp4")
    pf, rf = edit("keep 1.3-3.7, 5.2-6.8", media["speech"], fast, fast_cuts=True)
    pe, re_ = edit("keep 1.3-3.7, 5.2-6.8", media["speech"], exact)
    assert pf.timeline.video_mode == "copy_cuts" and pe.timeline.video_mode == "encode"
    assert rf.verify.ok and abs(count_frames(fast) - pf.timeline.total_frames) <= 2
    assert pf.timeline.duration > pe.timeline.duration            # fast cuts keep a little extra up to the keyframes
    assert stream_info(fast, "v")["codec_name"] == "h264" and stream_info(fast, "a")
    a, b = float(stream_info(fast, "v")["duration"]), float(stream_info(fast, "a")["duration"])
    assert abs(a - b) < 0.1


def test_nothing_changing_the_picture_copies_it_bit_for_bit(media, tmp_path):
    out = str(tmp_path / "c.mp4")
    plan, res = edit("loudnorm", media["tone"], out)
    assert plan.timeline.video_mode == "copy_all"

    def md5(p):
        r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", p, "-map", "0:v:0", "-c", "copy", "-f", "md5", "-"], capture_output=True, text=True)
        return r.stdout.strip()

    assert md5(out) == md5(media["tone"])


# ---- resources and memory ---------------------------------------------------------------------------
def test_resource_planning_respects_the_limits():
    r = plan_resources(1920, 1080, 1920, 1080, cpu=16, avail_mb=64000, limit_mb=300)
    assert 62 + r.workers * r.est_worker_mb <= 300 + 1
    r = plan_resources(1920, 1080, 1920, 1080, cpu=16, avail_mb=64000, limit_mb=3000)
    assert r.workers > 1
    r = plan_resources(640, 360, 640, 360, cpu=4, avail_mb=500)         # little free RAM -> smaller budget
    assert 62 + r.workers * r.est_worker_mb <= 500 * 0.6 + 1 or r.workers == 1
    r = plan_resources(3840, 2160, 3840, 2160, cpu=2, avail_mb=8000)
    assert r.workers == 1 and r.notes
    r = plan_resources(640, 360, 640, 360, cpu=8, avail_mb=8000, requested_workers=2)
    assert r.workers <= 2
    h = plan_resources(1920, 1080, 1920, 1080, cpu=16, avail_mb=64000, limit_mb=3000, hardware=True)
    assert h.workers <= 2


def test_many_workers_give_the_same_result(media, tmp_path):
    a, b = str(tmp_path / "a.mp4"), str(tmp_path / "b.mp4")
    pa, _ = edit("keep 0-1, 2-3, 4-5, 6-7", media["speech"], a, render_opts={"workers": 1})
    pb, rb = edit("keep 0-1, 2-3, 4-5, 6-7", media["speech"], b, render_opts={"workers": 3, "max_memory_mb": 2000})
    assert count_frames(a) == count_frames(b) == pa.timeline.total_frames and rb.workers >= 1


def test_hundreds_of_tiny_cuts(media, tmp_path):
    out = str(tmp_path / "r.mp4")
    script = "pattern_keep 0.2 every 0.4"
    plan, res = edit(script, media["tone"], out)
    assert len(plan.timeline.segments) >= 14 and count_frames(out) == plan.timeline.total_frames and res.verify.ok


def test_a_single_frame_segment(media, tmp_path):
    out = str(tmp_path / "r.mp4")
    plan, res = edit("keep 1-1.04", media["speech"], out)
    assert count_frames(out) == 1


@pytest.mark.skipif(not memory_measurement_available(), reason="memory cannot be measured on this computer")
def test_memory_stays_flat_with_many_cuts(long_video, tmp_path):
    """A 2-minute source with 40 cuts must stay under 300 MB for the whole process tree, and 6 cuts must not use much less."""
    peaks = {}
    for n in (6, 40):
        script = tmp_path / f"s{n}.txt"
        script.write_text("keep " + ", ".join(f"{i * 2.8:.1f}-{i * 2.8 + 1.2:.1f}" for i in range(n)))
        out = str(tmp_path / f"o{n}.mp4")
        env = dict(os.environ, EDITFORGE_HOME=str(tmp_path / f"h{n}"), PYTHONPATH=str(ROOT))
        proc = subprocess.Popen([sys.executable, "-m", "editforge", "edit", str(script), "-i", long_video, "-o", out, "--no-resume"],
                                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        mon = PeakMemoryMonitor(proc.pid, interval=0.03).start()
        assert proc.wait(timeout=600) == 0
        peaks[n] = mon.stop()
        assert count_frames(out) > 0
    assert peaks[40] < 300 and peaks[6] < 300, peaks
    assert peaks[40] < peaks[6] + 40, peaks            # more cuts must not mean more memory


# ---- job store and manager --------------------------------------------------------------------------
def test_job_store_survives_a_restart(tmp_path):
    db = tmp_path / "jobs.sqlite3"
    s1 = JobStore(db)
    jid = s1.create("keep 0-1", "/a.mp4", "/o.mp4", {"preset": "shorts"})
    s1.update(jid, status="running", progress=0.4, message="working")
    s2 = JobStore(db)                     # a "new program run"
    job = s2.get(jid)
    assert job["status"] == "running" and job["progress"] == 0.4 and job["options"] == {"preset": "shorts"}
    assert s2.mark_interrupted() == 1 and s2.get(jid)["status"] == "interrupted"
    assert [j["id"] for j in s2.list()] == [jid]
    s2.delete(jid)
    assert s2.get(jid) is None


def test_job_store_is_safe_with_many_threads(tmp_path):
    s = JobStore(tmp_path / "j.sqlite3")
    ids = [s.create("keep 0-1", "/a", "/o", {}) for _ in range(5)]

    def work(i):
        for k in range(30):
            s.update(ids[i], progress=k / 30.0, message=f"m{k}")

    ts = [threading.Thread(target=work, args=(i,)) for i in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert all(abs(s.get(i)["progress"] - 29 / 30.0) < 1e-9 for i in ids)


def test_manager_runs_cancels_and_resumes_jobs(media, long_video, tmp_path):
    store = JobStore(tmp_path / "j.sqlite3")
    mgr = JobManager(store)
    out = str(tmp_path / "o.mp4")
    jid = mgr.submit("keep 0-2", media["speech"], out, {})
    job = mgr.wait(jid, 120)
    assert job["status"] == "done" and count_frames(out) == 50
    bad = mgr.submit("nonsense 1", media["speech"], str(tmp_path / "x.mp4"), {})
    job = mgr.wait(bad, 60)
    assert job["status"] == "failed" and "don't know" in job["error"] and job["error_fix"]
    out2 = str(tmp_path / "o2.mp4")
    jid2 = mgr.submit(MANY_CUTS, long_video, out2, {})
    for _ in range(300):
        j = store.get(jid2)
        if j["status"] == "running" and j["progress"] > 0.05:
            break
        time.sleep(0.1)
    assert mgr.cancel(jid2)
    job = mgr.wait(jid2, 120)
    assert job["status"] == "cancelled"
    assert mgr.resume(jid2)
    job = mgr.wait(jid2, 300)
    assert job["status"] == "done" and count_frames(out2) > 1000
    mgr.shutdown()
