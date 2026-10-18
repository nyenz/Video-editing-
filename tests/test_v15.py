"""Version 1.5: faster loading, videos used where they are (no copies), the Home screen's projects,
stacked steps on the same parent video, storage clean-up and automatic updates."""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from editforge import project as P
from editforge.core import ffmpeg as ff
from editforge.web.server import make_server
from test_web_workshop import Client

ROOT = Path(__file__).resolve().parent.parent
STEPS_JS = ROOT / "editforge" / "web" / "static" / "steps.js"


def q(ident: str) -> str:
    return urllib.parse.quote(ident, safe="")


@pytest.fixture(scope="module")
def web(tmp_path_factory):
    srv, state = make_server("127.0.0.1", 0, home=tmp_path_factory.mktemp("v15_home"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield Client(state.port, state.token), state
    state.manager.shutdown()
    state.previews.shutdown()
    srv.shutdown()
    srv.server_close()


def link(state, path):
    """Pretend the person chose ``path`` in the computer's file window."""
    state.chooser = lambda kind, start: str(path)


# ------------------------------------------------------------------ A: faster loading
def test_static_files_are_gzipped_versioned_and_cached(web):
    c, _ = web
    status, _, html = c.call("GET", "/edit", token=False)
    refs = {r.decode() for r in __import__("re").findall(rb'/static/[\w.]+\?v=\w+', html)}
    assert any("editor.js?v=" in r for r in refs) and any("steps.js?v=" in r for r in refs)
    ref = next(r for r in refs if "editor.js" in r)
    status, h, body = c.call("GET", ref, token=False, headers={"Accept-Encoding": "gzip"})
    assert status == 200 and h["Content-Encoding"] == "gzip" and "immutable" in h["Cache-Control"]
    plain = (ROOT / "editforge/web/static/editor.js").read_bytes()
    assert gzip.decompress(body) == plain and len(body) < len(plain) / 2
    status, h2, _ = c.call("GET", "/static/editor.js", token=False, headers={"If-None-Match": h["ETag"]})
    assert status == 304
    status, h3, body = c.call("GET", "/static/editor.js", token=False)       # no version: must ask again next time
    assert status == 200 and h3["Cache-Control"] == "no-cache" and body == plain


def test_features_check_does_not_load_the_add_ons():
    code = ("import sys; sys.path.insert(0, %r); from editforge.web.server import quick_features; f = quick_features(); "
            "print(int('cv2' in sys.modules), int('faster_whisper' in sys.modules), sorted(f))") % str(ROOT)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60).stdout.split()
    assert out[:2] == ["0", "0"], out


def test_video_facts_are_remembered(media, monkeypatch):
    calls = []
    real = ff._run_ffprobe_json
    monkeypatch.setattr(ff, "_run_ffprobe_json", lambda path, extra=(): calls.append(path) or real(path, extra))
    from editforge.core.media import probe_media
    a = probe_media(media["odd"])
    for _ in range(5):
        b = probe_media(media["odd"])
    assert a.duration == b.duration and len(calls) <= 1
    ff._PROBE_MEMO.clear()                                    # a new start of the app: read from the disk cache
    probe_media(media["odd"])
    assert len(calls) <= 1


def test_quick_copy_plays_first_then_the_light_copy_replaces_it(web, tmp_path, monkeypatch):
    c, state = web
    film = tmp_path / "film.mkv"
    subprocess.run([ff.find_executable("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=3",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-ac", "6", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "ac3", "-shortest", str(film)], check=True)
    link(state, film)
    fid = c.json("POST", "/api/choose", {"kind": "video"})[1]["id"]
    pm = state.previews
    seen = {}
    real_make = pm._make

    def slow_make(source, out, duration, has_audio):
        q_path = pm.quick_path_for(source)
        seen["quick_ready_before_light"] = q_path.is_file()
        seen["status"] = c.json("GET", f"/api/preview?id={q(fid)}")[1]
        seen["quick_served"] = c.call("GET", f"/media?id={q(fid)}&preview=q")[0]
        real_make(source, out, duration, has_audio)

    monkeypatch.setattr(pm, "_make", slow_make)
    assert c.json("POST", "/api/preview", {"input": fid})[0] == 202
    for _ in range(120):
        st = c.json("GET", f"/api/preview?id={q(fid)}")[1]
        if st["state"] in ("ready", "failed"):
            break
        time.sleep(0.25)
    assert st["state"] == "ready"
    assert seen["quick_ready_before_light"] and seen["status"]["quick"] is True and seen["quick_served"] == 200
    assert not pm.quick_path_for(str(film)).exists()           # the light copy replaced it (disk space)


# ------------------------------------------------------------------ B: videos used where they are
def test_choose_links_the_file_where_it_is(web, media, tmp_path):
    c, state = web
    src = tmp_path / "big film.mp4"
    shutil.copy(media["tone"], src)
    before = sum(1 for _ in state.uploads.rglob("*"))
    link(state, src)
    status, d = c.json("POST", "/api/choose", {"kind": "video"})
    assert status == 200 and d["id"].startswith("l/") and d["linked"] and d["where"] == str(src.resolve())
    assert sum(1 for _ in state.uploads.rglob("*")) == before            # nothing copied
    assert c.json("GET", f"/api/info?id={q(d['id'])}")[1]["duration"] > 5
    status, _, body = c.call("GET", f"/media?id={q(d['id'])}")
    assert status == 200 and body == src.read_bytes()
    assert any(f["id"] == d["id"] and f["linked"] for f in c.json("GET", "/api/files")[1]["files"])
    link(state, src)                                                       # the same file again: the same id
    assert c.json("POST", "/api/choose", {"kind": "video"})[1]["id"] == d["id"]
    # only exactly the linked file may be read: other ids and names are refused
    lid = d["id"].split("/")[1]
    for bad in (f"l/{lid}/other.mp4", "l/000000000000/x.mp4", f"l/{lid}/..", "l/../../etc/passwd"):
        assert c.call("GET", f"/media?id={q(bad)}")[0] == 404
    # the job runner may read the linked file (clips are made from it), but nothing else outside the folders
    jobs = c.json("POST", "/api/clips", {"input": d["id"], "clips": [{"start": 1, "end": 2}]})[1]["jobs"]
    assert c.wait_jobs(jobs)[0]["status"] == "done"
    assert str(tmp_path) not in state.allowed_roots and str(src.resolve()) in state.allowed_roots
    # links are remembered after a restart
    srv2, st2 = make_server("127.0.0.1", 0, home=state.home)
    try:
        assert st2.resolve_input(d["id"]) == str(src.resolve())
    finally:
        st2.manager.shutdown(); st2.previews.shutdown(); srv2.server_close()


def test_choose_cancel_and_no_file_window(web):
    c, state = web
    state.chooser = lambda kind, start: None
    assert c.json("POST", "/api/choose", {"kind": "video"})[1] == {"cancelled": True}
    from editforge.web import choose

    def none(kind, start):
        raise choose.NoDialog()
    state.chooser = none
    assert c.json("POST", "/api/choose", {"kind": "video"})[1] == {"no_dialog": True}


# ------------------------------------------------------------------ B2: Home screen projects
def test_projects_list_rename_duplicate_trash_find_again(web, media, tmp_path):
    c, state = web
    src = tmp_path / "match.mp4"
    shutil.copy(media["colors"], src)
    link(state, src)
    fid = c.json("POST", "/api/choose", {"kind": "video"})[1]["id"]
    p = c.json("POST", "/api/projects", {"source": fid, "interval": 2})[1]["project"]
    card = next(x for x in c.json("GET", "/api/projects")[1]["projects"] if x["id"] == p["id"])
    assert card["size"] == src.stat().st_size and abs(card["duration"] - 6) < 0.1 and card["linked"] and not card["missing"]
    assert card["length"] == pytest.approx(6, abs=0.1) and card["where"] == str(src.resolve()) and card["thumb_at"] > 0
    assert c.json("POST", f"/api/projects/{p['id']}/rename", {"name": "  My   cut "})[1]["name"] == "My cut"
    dup = c.json("POST", f"/api/projects/{p['id']}/duplicate", {})[1]
    assert dup["id"] != p["id"] and dup["name"] == "My cut copy" and dup["pieces"] == p["pieces"]
    c.json("POST", f"/api/projects/{dup['id']}/trash", {})
    cards = {x["id"]: x for x in c.json("GET", "/api/projects")[1]["projects"]}
    assert cards[dup["id"]]["trashed"] > 0 and cards[p["id"]]["trashed"] == 0
    c.json("POST", f"/api/projects/{dup['id']}/restore", {})
    assert not {x["id"]: x for x in c.json("GET", "/api/projects")[1]["projects"]}[dup["id"]]["trashed"]
    # opening a project moves it to the front
    c.json("GET", f"/api/projects/{p['id']}")
    assert c.json("GET", "/api/projects")[1]["projects"][0]["id"] == p["id"]
    # the video is moved: "Video not found", then "Find it again"
    moved = tmp_path / "moved" / "match.mp4"
    moved.parent.mkdir()
    src.rename(moved)
    card = next(x for x in c.json("GET", "/api/projects")[1]["projects"] if x["id"] == p["id"])
    assert card["missing"] and card["size"] > 0 and card["duration"] > 0           # the card still shows what it was
    assert c.json("GET", f"/api/projects/{p['id']}")[0] == 400
    link(state, moved)
    r = c.json("POST", f"/api/projects/{p['id']}/relink", {})[1]
    assert r["project"]["source"].startswith("l/") and r["note"] == "" and len(r["project"]["pieces"]) == 3
    assert not next(x for x in c.json("GET", "/api/projects")[1]["projects"] if x["id"] == p["id"])["missing"]
    # empty the trash: only trashed projects go
    c.json("POST", f"/api/projects/{dup['id']}/trash", {})
    assert c.json("POST", "/api/trash/empty", {})[1]["deleted"] == 1
    ids = {x["id"] for x in c.json("GET", "/api/projects")[1]["projects"]}
    assert p["id"] in ids and dup["id"] not in ids and moved.exists()


def test_deleting_a_project_deletes_its_sent_in_copy_only_when_unused(web, media):
    c, state = web
    fid = c.upload(media["tone"])
    folder = state.uploads / fid.split("/")[1]
    a = c.json("POST", "/api/projects", {"source": fid, "interval": 2})[1]["project"]
    b = c.json("POST", "/api/projects", {"source": fid, "interval": 3})[1]["project"]
    assert c.json("DELETE", f"/api/projects/{a['id']}")[1]["ok"] and folder.exists()      # b still uses it
    assert c.json("DELETE", f"/api/projects/{b['id']}")[1]["ok"] and not folder.exists()


def test_storage_panel_and_free_up(web, media):
    c, state = web
    fid = c.upload(media["tone"])                       # a sent-in copy that no project uses
    c.call("GET", f"/thumb?id={q(fid)}&at=1")
    st = c.json("GET", "/api/storage")[1]
    assert st["sizes"]["uploads"] > 0 and st["sizes"]["thumbs"] > 0 and st["loose_uploads"] > 0 and st["free"] > 0
    r = c.json("POST", "/api/storage/clean", {"what": "thumbs"})[1]
    assert r["removed"] >= 1 and r["storage"]["sizes"]["thumbs"] == 0
    r = c.json("POST", "/api/storage/clean", {"what": "uploads"})[1]
    assert r["removed"] >= 1 and not (state.uploads / fid.split("/")[1]).exists()
    assert c.json("POST", "/api/storage/clean", {"what": "everything"})[0] == 400


# ------------------------------------------------------------------ B3: stacked steps on the same parent
def whole(duration, sec):
    return P.slice_pieces(duration, sec)


def pin(pieces):
    return [dict(p, off=False) for p in pieces if not p.get("off")]


def test_three_stacked_steps_keep_parent_times():
    D = 60.0
    s1 = whole(D, 2.0)                                            # 30 pieces of 2 s
    P.apply_pattern(s1, 3, [1], "remove")                         # remove 1st of every 3 -> 20 left, 40 s
    assert P.step_length(s1) == pytest.approx(40.0)
    s2 = P.recut(pin(s1), 3.0)                                    # 40 s cut into 3 s pieces (some cross a removed gap)
    assert P.step_length(s2) == pytest.approx(40.0)
    assert len(set(P.units(s2))) == 14                            # 13 full 3 s pieces + 1 s at the end
    P.apply_pattern(s2, 2, [2], "remove")                         # every 2nd new piece goes
    s3 = P.recut(pin(s2), 1.0)
    P.apply_pattern(s3, 4, [1, 2], "keep")
    kept_before = [p for p in s1 if not p["off"]]
    for p in s3:                                                  # every piece is still a range of footage step 1 kept
        assert any(k["start"] - 1e-6 <= p["start"] and p["end"] <= k["end"] + 1e-6 for k in kept_before), p
    assert P.step_length(s3) == pytest.approx(sum(P.out_len(p) for p in s3 if not p["off"]))
    # a 3 s piece that spans a removed gap is two parts with one unit, counted as one by patterns
    u = P.units([p for p in s2])
    assert len(u) > len(set(u))


def test_extending_reaches_footage_an_earlier_step_removed():
    D = 12.0
    s1 = whole(D, 2.0)                                            # 0-2 2-4 4-6 6-8 8-10 10-12
    P.apply_pattern(s1, 2, [2], "remove")                         # keep 0-2, 4-6, 8-10
    s2 = P.recut(pin(s1), 1.0)                                    # 0-1 1-2 4-5 5-6 8-9 9-10
    i = next(k for k, p in enumerate(s2) if p["start"] == 4.0)
    lo, hi = P.extend_limits(s2, i, D)
    assert lo == 2.0 and hi == 5.0                               # back into the removed 2-4, stops at its neighbour 5-6
    s2[i + 1]["off"] = True                                       # remove the neighbour: now it may grow up to 8 (the next kept)
    assert P.extend_limits(s2, i, D) == (2.0, 8.0)


def test_export_uses_the_last_steps_pieces_from_the_parent(web, media):
    c, state = web
    fid = c.upload(media["colors"])                               # 2 s red, 2 s green, 2 s blue
    p = c.json("POST", "/api/projects", {"source": fid, "interval": 1})[1]["project"]
    s1 = p["pieces"]
    P.apply_pattern(s1, 2, [2], "remove")                         # keep 0-1, 2-3, 4-5
    s2 = P.recut(pin(s1), 0.5)
    P.apply_pattern(s2, 3, [3], "remove")
    body = dict(p, pieces=s2, steps=[{"pieces": s1, "rule": {"interval": 1, "group": 2, "picks": [2], "action": "remove"}}],
                rule={"interval": 0.5, "group": 3, "picks": [3], "action": "remove"})
    saved = c.json("PUT", f"/api/projects/{p['id']}", body)[1]
    assert len(saved["steps"]) == 1 and saved["steps"][0]["length"] == pytest.approx(3.0) and saved["rule"]["action"] == "remove"
    assert all("unit" in x for x in saved["pieces"])
    want = P.step_length(saved["pieces"])
    jid = c.json("POST", f"/api/projects/{p['id']}/render", {"preview": True})[1]["id"]
    job = c.wait_jobs([jid], 180)[0]
    assert job["status"] == "done", job
    assert abs(job["result"]["duration"] - want) < 0.1


def test_saved_steps_recipe(web):
    c, _ = web
    rec = [{"name": "two steps", "type": "steps", "steps": [{"interval": 2, "group": 3, "picks": [1], "action": "remove"},
                                                            {"interval": 1, "group": 2, "picks": [2], "action": "keep"}]},
           {"name": "x", "type": "steps", "steps": []}]
    out = c.json("PUT", "/api/recipes", rec)[1]["recipes"]
    assert len(out) == 1 and out[0]["type"] == "steps" and [s["interval"] for s in out[0]["steps"]] == [2, 1]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_steps_js_matches_python():
    D = 30.0
    s1 = whole(D, 2.0)
    s1[3]["speed"] = 2.0
    s1[5]["reverse"] = True
    P.apply_pattern(s1, 3, [2], "remove")
    data = json.dumps({"s1": s1})
    script = (f"const S = require({json.dumps(str(STEPS_JS))}); const d = {data};"
              "const s1 = d.s1; const base = s1.filter(p => !p.off).map(p => Object.assign({}, p, {off: false}));"
              "const s2 = S.recut(base, 1.7); const hits = S.patternHits(s2.filter(p => !p.off), 3, [1]);"
              "const lim = S.extendLimits(s2, s2[4], 30);"
              "console.log(JSON.stringify({s2, units: S.units(s2), hits: hits.map(h => s2.indexOf(h)), lim, len: S.stepLength(s2)}));")
    out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60, check=True).stdout)
    s2 = P.recut(pin(s1), 1.7)
    assert [(p["start"], p["end"], p["unit"]) for p in s2] == [(p["start"], p["end"], p["unit"]) for p in out["s2"]]
    assert P.units(s2) == out["units"]
    kept = [p for p in s2 if not p.get("off")]
    hits = [k for k, (p, u) in enumerate(zip(kept, P.units(kept))) if (u % 3) + 1 == 1]
    assert hits == out["hits"]
    assert list(P.extend_limits(s2, 4, 30)) == [out["lim"]["lo"], out["lim"]["hi"]]
    assert P.step_length(s2) == pytest.approx(out["len"])


# ------------------------------------------------------------------ B4: automatic updates
def release(version, data=b"", url="https://github.com/nyenz/Video-editing-/releases/download/x/EditForge-Setup.exe", digest=True):
    asset = {"name": "EditForge-Setup.exe", "size": len(data), "browser_download_url": url}
    if digest:
        asset["digest"] = "sha256:" + hashlib.sha256(data).hexdigest()
    return json.dumps({"tag_name": "v" + version, "assets": [asset], "body": "notes"}).encode()


def test_update_check_once_a_day_skip_and_offline(tmp_path):
    from editforge.updater import Updater, is_newer, parse_version
    assert parse_version("v1.10.2") == (1, 10, 2) and is_newer("1.10.0", "1.9.9") and not is_newer("1.5.0", "1.5.0")
    asked = []
    u = Updater(tmp_path, fetch=lambda url: asked.append(url) or release("99.0.0", b"x"))
    st = u.check()
    assert st["available"] and st["latest"] == "99.0.0" and len(asked) == 1
    u.check()
    assert len(asked) == 1                                      # at most once a day
    u.skip("99.0.0")
    assert not u.status()["available"]
    old = Updater(tmp_path / "b", fetch=lambda url: release("0.0.1"))
    assert not old.check()["available"]

    def offline(url):
        raise OSError("no internet")
    st = Updater(tmp_path / "c", fetch=offline).check()
    assert not st["available"] and st["job"]["state"] == "idle"


def serve_bytes(data: bytes):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/EditForge-Setup.exe"


def wait_job(u):
    for _ in range(100):
        j = u.status()["job"]
        if j["state"] not in ("downloading",):
            return j
        time.sleep(0.05)
    return u.status()["job"]


def test_update_download_is_checked_before_use(tmp_path):
    from editforge.updater import Updater
    data = b"installer" * 1000
    srv, url = serve_bytes(data)
    try:
        good = Updater(tmp_path / "ok", fetch=lambda u: release("99.0.0", data, url))
        good.download_prefix = "http://127.0.0.1:"
        good.check()
        good.download()
        j = wait_job(good)
        assert j["state"] == "ready" and Path(j["path"]).read_bytes() == data
        bad = Updater(tmp_path / "bad", fetch=lambda u: release("99.0.0", b"something else" * 100, url))
        bad.download_prefix = "http://127.0.0.1:"
        bad.check()
        bad.download()
        j = wait_job(bad)
        assert j["state"] == "failed" and not list((tmp_path / "bad" / "updates").glob("*.exe"))
        elsewhere = Updater(tmp_path / "far", fetch=lambda u: release("99.0.0", data, url))     # not from GitHub: never used
        elsewhere.check()
        elsewhere.download()
        assert wait_job(elsewhere)["state"] == "failed"
    finally:
        srv.shutdown()


def test_update_endpoints(web):
    c, state = web
    st = c.json("GET", "/api/update")[1]
    assert st["current"] and st["available"] is False and st["can_install"] is False
    status, err = c.json("POST", "/api/update/install", {})
    assert status == 400 and "installed app" in err["error"]


# ------------------------------------------------------------------ sound clean-up
def loudness(path) -> float:
    out = subprocess.run([ff.find_executable("ffmpeg"), "-v", "info", "-i", str(path), "-vn", "-af", "volumedetect", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    import re
    return float(re.search(r"mean_volume: (-?[\d.]+) dB", out).group(1))


def test_sound_cleanup_settings_and_vocal_removal(web, tmp_path):
    c, state = web
    clip = tmp_path / "centre.mp4"              # the same tone on both sides, like a voice in the middle of a stereo mix
    subprocess.run([ff.find_executable("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=3",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-filter_complex", "[1:a]pan=stereo|c0=c0|c1=c0[a]", "-map", "0:v",
                    "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(clip)], check=True)
    link(state, clip)
    fid = c.json("POST", "/api/choose", {"kind": "video"})[1]["id"]
    p = c.json("POST", "/api/projects", {"source": fid, "interval": 0})[1]["project"]
    s = c.json("PUT", f"/api/projects/{p['id']}", dict(p, settings={"noise": "medium", "vocals": "remove", "voice_boost": True,
                                                                     "compress": True, "bass": 40, "treble": -3}))[1]["settings"]
    assert (s["noise"], s["vocals"], s["voice_boost"], s["compress"], s["bass"], s["treble"]) == ("medium", "remove", True, True, 12.0, -3.0)
    outs = {}
    for vocals in ("keep", "remove"):
        c.json("PUT", f"/api/projects/{p['id']}", dict(p, settings={"vocals": vocals}))
        jid = c.json("POST", f"/api/projects/{p['id']}/render", {"preview": True})[1]["id"]
        job = c.wait_jobs([jid], 180)[0]
        assert job["status"] == "done", job
        outs[vocals] = loudness(job["output_path"])
    assert outs["remove"] < outs["keep"] - 25          # the middle (the "voice") is gone
    # every option together still makes a video
    c.json("PUT", f"/api/projects/{p['id']}", dict(p, settings={"noise": "strong", "vocals": "voice", "voice_boost": True, "compress": True,
                                                                "bass": 4, "treble": 2}))
    jid = c.json("POST", f"/api/projects/{p['id']}/render", {"preview": True})[1]["id"]
    assert c.wait_jobs([jid], 180)[0]["status"] == "done"


def test_vocal_removal_on_mono_sound_is_skipped_with_a_note(media):
    from editforge.core.media import probe_media
    src = media["tone"]
    info = probe_media(src)
    proj = P.clean_project({"name": "x", "source": "s", "pieces": [{"start": 0, "end": 2}], "settings": {"vocals": "remove"}}, info.duration)
    plan = P.plan_project(proj, src, preview=True)
    if info.channels < 2:
        assert plan.timeline.audio.voice is None and any("mono" in w for w in plan.warnings)
    else:
        assert plan.timeline.audio.voice["vocals"] == "remove"
