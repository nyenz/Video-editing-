"""Version 1.7: three phases. Clip projects (take many clips out of a movie), Edit, Final."""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
import urllib.parse
from pathlib import Path

import pytest

from editforge import clipsets as CS
from editforge.core import ffmpeg as ff
from editforge.core.media import probe_media
from editforge.web.server import make_server
from test_web_workshop import Client

FFMPEG = ff.find_executable("ffmpeg")


def q(ident: str) -> str:
    return urllib.parse.quote(ident, safe="")


@pytest.fixture()
def web(tmp_path):
    srv, state = make_server("127.0.0.1", 0, home=tmp_path / "home")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield Client(state.port, state.token), state
    state.manager.shutdown()
    state.previews.shutdown()
    srv.shutdown()
    srv.server_close()


def choose(state, path):
    state.chooser = lambda kind, start: str(path)


def wait_made(c, cid, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        _, v = c.json("GET", f"/api/clipsets/{cid}")
        states = [s["state"] for s in v["states"].values()]
        if states and all(s in ("done", "failed", "old", "none") for s in states):
            return v
        time.sleep(0.3)
    raise AssertionError(v["states"])


# ------------------------------------------------------------------ cleaning (pure)
def test_clips_are_cleaned_parts_kept_in_order_and_made_is_never_taken_from_the_page():
    raw = {"name": "  Movie  ", "source": "m/x.mp4", "clips": [
        {"name": "fight", "parts": [{"start": 50, "end": 55}, {"start": 10, "end": 12}, {"start": 3, "end": 3.01}],
         "speed": 9, "volume": -1, "color": {"brightness": 5}, "settings": {"fade_in": 1, "shape": "vertical", "music": "evil"},
         "made": {"job": "abcdefabcdef", "key": "x"}},
        {"name": "", "parts": []},                                     # no usable part: dropped
        {"parts": [{"start": 90, "end": 200}]}]}                       # clamped to the movie length
    cs = CS.clean_clipset(raw, 100.0)
    assert cs["name"] == "Movie" and len(cs["clips"]) == 2
    a, b = cs["clips"]
    assert a["parts"] == [{"start": 50.0, "end": 55.0}, {"start": 10.0, "end": 12.0}]       # the order the person chose
    assert a["speed"] == 4.0 and a["volume"] == 0.0 and a["color"]["brightness"] == 1.0
    assert a["settings"]["fade_in"] == 1.0 and a["settings"]["shape"] == "vertical" and "music" not in a["settings"]
    assert b["parts"] == [{"start": 90.0, "end": 100.0}] and b["name"] == "clip03"
    assert len(a["id"]) == 8 and a["id"] != b["id"]
    assert abs(CS.clip_length(a) - 7 / 4) < 1e-9
    k = CS.clip_key("m/x.mp4", a)
    assert k == CS.clip_key("m/x.mp4", dict(a, name="other")) and k != CS.clip_key("m/x.mp4", dict(a, speed=1.0))


def test_a_clip_becomes_a_project_with_its_parts_and_look():
    cs = CS.clean_clipset({"source": "m/x.mp4", "clips": [{"parts": [{"start": 5, "end": 6}, {"start": 1, "end": 2}], "mute": True,
                                                           "color": {"saturation": 0}, "settings": {"fade_out": 0.5}}]}, 10)
    proj = CS.clip_project(cs, cs["clips"][0], 10)
    assert [(p["start"], p["end"]) for p in proj["pieces"]] == [(5, 6), (1, 2)]
    assert all(p["mute"] and p["color"]["saturation"] == 0 for p in proj["pieces"])
    assert proj["settings"]["fade_out"] == 0.5


# ------------------------------------------------------------------ scenes
def test_scenes_are_found_in_the_background_and_kept(web, media):
    c, state = web
    choose(state, media["scenes"])                                    # red, green, blue: 2 s each
    _, f = c.json("POST", "/api/choose", {"kind": "video"})
    status, st = c.json("POST", "/api/scenes", {"id": f["id"]})
    assert status == 200 and st["state"] in ("running", "ready")
    end = time.time() + 30
    while st["state"] != "ready" and time.time() < end:
        time.sleep(0.2)
        _, st = c.json("GET", "/api/scenes?id=" + q(f["id"]))
    assert st["state"] == "ready", st
    assert len(st["scenes"]) == 2 and abs(st["scenes"][0] - 2.0) < 0.1 and abs(st["scenes"][1] - 4.0) < 0.1
    assert CS.scene_ranges(st["scenes"], 6.0)[1][0] == st["scenes"][0]
    assert list((state.home / "scenes").glob("*.json"))


# ------------------------------------------------------------------ the whole phase 1
def test_clip_project_make_clips_from_two_places_and_free_the_movie(web, media, tmp_path):
    c, state = web
    movie = tmp_path / "movie.mp4"
    shutil.copy(media["scenes"], movie)
    choose(state, movie)
    _, f = c.json("POST", "/api/choose", {"kind": "video"})
    status, v = c.json("POST", "/api/clipsets", {"source": f["id"]})
    assert status == 201 and v["clipset"]["kind"] == "clips" and v["info"]["duration"] > 5
    cid = v["clipset"]["id"]
    body = dict(v["clipset"], clips=[
        {"name": "red and blue", "parts": [{"start": 0.2, "end": 1.2}, {"start": 4.5, "end": 5.5}], "settings": {"fade_in": 0.2}},
        {"name": "green", "parts": [{"start": 2.5, "end": 3.5}], "color": {"saturation": 0}, "volume": 0.5}])
    status, v = c.json("PUT", f"/api/clipsets/{cid}", body)
    assert status == 200 and len(v["clipset"]["clips"]) == 2 and all(s["state"] == "none" for s in v["states"].values())
    _, home = c.json("GET", "/api/clipsets")
    card = home["clipsets"][0]
    assert card["clips"] == 2 and card["made"] == 0 and abs(card["length"] - 3.0) < 0.01

    status, err = c.json("POST", f"/api/clipsets/{cid}/free", {})
    assert status == 400 and "Make every clip first" in err["error"]

    status, v = c.json("POST", f"/api/clipsets/{cid}/make", {})
    assert status == 200 and len(v["started"]) == 2
    v = wait_made(c, cid)
    assert all(s["state"] == "done" for s in v["states"].values()), v["states"]
    a, b = v["clipset"]["clips"]
    fa = state.resolve_input(v["states"][a["id"]]["file"])
    assert abs(probe_media(fa).duration - 2.0) < 0.15                        # two parts of 1 s from two places
    assert "red_and_blue" in Path(fa).name or "red and blue" in Path(fa).name
    # the first frame is red, the last is blue: the parts are in the order chosen
    def color_at(path, t):
        raw = subprocess.run([FFMPEG, "-v", "error", "-ss", str(t), "-i", path, "-frames:v", "1", "-vf", "scale=1:1", "-f", "rawvideo",
                              "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
        return tuple(raw[:3])
    r, g, bl = color_at(fa, 0.5)
    assert r > 150 and bl < 80
    r, g, bl = color_at(fa, 1.6)
    assert bl > 150 and r < 80
    r, g, bl = color_at(state.resolve_input(v["states"][b["id"]]["file"]), 0.5)
    assert abs(r - g) < 25 and abs(g - bl) < 25                              # saturation 0: grey, not green

    # make again: nothing to do. Change a clip: it is out of date until made again.
    _, again = c.json("POST", f"/api/clipsets/{cid}/make", {})
    assert again["started"] == []
    body = dict(v["clipset"]); body["clips"] = [dict(a, speed=2.0), b]
    _, v2 = c.json("PUT", f"/api/clipsets/{cid}", body)
    assert v2["states"][a["id"]]["state"] == "old" and v2["states"][b["id"]]["state"] == "done"
    _, v3 = c.json("POST", f"/api/clipsets/{cid}/make", {"ids": [a["id"]]})
    assert v3["started"] == [a["id"]]
    v3 = wait_made(c, cid)
    assert abs(probe_media(state.resolve_input(v3["states"][a["id"]]["file"])).duration - 1.0) < 0.15

    # made clips are not "Final" videos
    _, fin = c.json("GET", "/api/final")
    assert fin["videos"] == []

    # free the movie: a file used where it is is only deleted when the person says so
    _, ask = c.json("POST", f"/api/clipsets/{cid}/free", {})
    assert ask["ask"] and Path(ask["path"]) == movie.resolve() and movie.exists()
    _, done = c.json("POST", f"/api/clipsets/{cid}/free", {"delete_original": True})
    assert done["freed"] >= movie.stat().st_size if movie.exists() else done["freed"] > 0
    assert not movie.exists()
    _, v4 = c.json("GET", f"/api/clipsets/{cid}")
    assert v4["info"] is None and v4["clipset"]["freed"] and all(s["state"] == "done" for s in v4["states"].values())
    _, home = c.json("GET", "/api/clipsets")
    assert home["clipsets"][0]["missing"] and home["clipsets"][0]["thumb_file"]
    # the movie is gone: renaming a clip still works, its parts cannot change
    body = dict(v4["clipset"]); body["clips"] = [dict(body["clips"][0], name="renamed", parts=[{"start": 0, "end": 1}]), body["clips"][1]]
    _, v5 = c.json("PUT", f"/api/clipsets/{cid}", body)
    assert v5["clipset"]["clips"][0]["name"] == "renamed" and v5["clipset"]["clips"][0]["parts"] == v4["clipset"]["clips"][0]["parts"]
    assert v5["states"][a["id"]]["state"] == "done"

    # phase 2: a made clip opens in the Editor; its export is a Final video
    clip_file = v5["states"][b["id"]]["file"]
    status, pv = c.json("POST", "/api/projects", {"source": clip_file, "interval": 0})
    assert status == 201
    pid = pv["project"]["id"]
    status, job = c.json("POST", f"/api/projects/{pid}/render", {"preview": False})
    assert status in (200, 201)
    end = time.time() + 60
    while time.time() < end:
        _, fin = c.json("GET", "/api/final")
        if fin["videos"]:
            break
        time.sleep(0.3)
    assert fin["videos"][0]["project_id"] == pid and fin["videos"][0]["duration"] > 0.5


def test_a_sent_in_copy_is_freed_without_asking_and_trash_restore_delete(web, media):
    c, state = web
    fid = c.upload(media["colors"])
    _, v = c.json("POST", "/api/clipsets", {"source": fid, "name": "Colours"})
    cid = v["clipset"]["id"]
    c.json("PUT", f"/api/clipsets/{cid}", dict(v["clipset"], clips=[{"parts": [{"start": 0, "end": 1}]}]))
    c.json("POST", f"/api/clipsets/{cid}/make", {})
    wait_made(c, cid)
    folder = state.uploads / fid.split("/")[1]
    assert folder.exists()
    _, r = c.json("POST", f"/api/clipsets/{cid}/free", {})
    assert r["freed"] > 0 and not folder.exists()
    c.json("POST", f"/api/clipsets/{cid}/rename", {"name": "New name"})
    c.json("POST", f"/api/clipsets/{cid}/trash", {})
    _, home = c.json("GET", "/api/clipsets")
    assert home["clipsets"][0]["trashed"] and home["clipsets"][0]["name"] == "New name"
    c.json("POST", f"/api/clipsets/{cid}/restore", {})
    status, r = c.json("DELETE", f"/api/clipsets/{cid}")
    assert r["ok"]
    _, home = c.json("GET", "/api/clipsets")
    assert home["clipsets"] == []
    status, _ = c.json("GET", "/api/clipsets/zzz")
    assert status == 404


def test_another_project_on_the_same_movie_blocks_freeing_it(web, media):
    c, state = web
    fid = c.upload(media["colors"])
    _, v = c.json("POST", "/api/clipsets", {"source": fid})
    cid = v["clipset"]["id"]
    c.json("PUT", f"/api/clipsets/{cid}", dict(v["clipset"], clips=[{"parts": [{"start": 0, "end": 1}]}]))
    c.json("POST", f"/api/clipsets/{cid}/make", {})
    wait_made(c, cid)
    c.json("POST", "/api/projects", {"source": fid, "interval": 2})
    status, err = c.json("POST", f"/api/clipsets/{cid}/free", {})
    assert status == 400 and "Another project" in err["error"]
    assert (state.uploads / fid.split("/")[1]).exists()
    # and the storage clean-up never deletes a movie a clip project uses
    c.json("DELETE", "/api/projects/" + c.json("GET", "/api/projects")[1]["projects"][0]["id"])
    state.clean_storage("uploads")
    assert (state.uploads / fid.split("/")[1]).exists()


# ------------------------------------------------------------------ phase 2: keep the first pattern cut
def test_new_video_from_step_one_keeps_the_first_cut(web, media):
    c, state = web
    fid = c.upload(media["speech"])
    _, v = c.json("POST", "/api/projects", {"source": fid, "interval": 1})
    proj = v["project"]
    first = [dict(p, off=(i % 2 == 1)) for i, p in enumerate(proj["pieces"])]
    later = [{"start": 0.0, "end": 0.5}]
    body = dict(proj, steps=[{"pieces": first, "rule": {"interval": 1, "group": 2, "picks": [2], "action": "remove"}}], pieces=later)
    _, saved = c.json("PUT", "/api/projects/" + proj["id"], body)
    status, r = c.json("POST", f"/api/projects/{proj['id']}/fork", {"step": 1})
    assert status == 201
    new = r["project"]
    assert new["id"] != proj["id"] and new["steps"] == [] and "from step 1" in new["name"]
    assert len(new["pieces"]) == len(first) and [p["off"] for p in new["pieces"]] == [p["off"] for p in saved["steps"][0]["pieces"]]
    assert new["rule"]["action"] == "remove"
    _, again = c.json("GET", "/api/projects/" + proj["id"])
    assert len(again["project"]["steps"]) == 1 and len(again["project"]["pieces"]) == 1           # the old project is untouched


# ------------------------------------------------------------------ 1.8: the frame (canvas, background layer, transform, mask)
def _gray(path, t, w, h):
    raw = subprocess.run([FFMPEG, "-v", "error", "-ss", str(t), "-i", path, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    return [raw[i * w:(i + 1) * w] for i in range(h)]


def _detail(rows, y0, y1):
    """How much the picture changes from pixel to pixel in rows y0..y1 (sharp = high, blurred = low)."""
    tot = n = 0
    for r in rows[y0:y1]:
        for x in range(1, len(r)):
            tot += abs(r[x] - r[x - 1]); n += 1
    return tot / max(1, n)


def _render(c, fid, frame, tmp=None):
    _, v = c.json("POST", "/api/projects", {"source": fid, "interval": 0})
    proj = v["project"]
    proj["settings"] = dict(proj["settings"], frame=frame)
    _, saved = c.json("PUT", "/api/projects/" + proj["id"], proj)
    status, job = c.json("POST", f"/api/projects/{proj['id']}/render", {"preview": False})
    assert status in (200, 201), job
    end = time.time() + 90
    while time.time() < end:
        _, jobs = c.json("GET", "/api/jobs")
        j = next(x for x in jobs["jobs"] if x["id"] == job["id"])
        if j["status"] in ("done", "failed"):
            break
        time.sleep(0.3)
    assert j["status"] == "done", j
    return saved, j["output_path"]


def test_frame_tall_canvas_blurred_background_and_a_sharp_masked_strip(web, tmp_path):
    c, state = web
    src = tmp_path / "busy.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=2", "-f", "lavfi", "-i",
                    "sine=duration=2", "-vf", "noise=alls=60:allf=t", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(src)], check=True)
    fid = c.upload(str(src))
    frame = {"ratio": "9:16", "bg": "blur", "blur": 80, "scale": 2.0, "mask": {"shape": "strip", "y": 0.5, "h": 0.3, "feather": 0}}
    saved, out = _render(c, fid, frame)
    assert saved["settings"]["frame"]["ratio"] == "9:16"
    info = probe_media(out)
    assert (info.width, info.height) == (360, 640)
    rows = _gray(out, 1.0, 360, 640)
    middle, top = _detail(rows, 280, 360), _detail(rows, 20, 120)
    assert middle > 3 * top, (middle, top)              # the strip is sharp, the rest is the blurred layer
    # a plain move: with a black background the left edge is black when the picture moves right
    saved, out2 = _render(c, fid, {"ratio": "16:9", "bg": "black", "scale": 0.5, "x": 0.5})
    rows = _gray(out2, 1.0, 640, 360)
    assert max(rows[180][:150]) < 30 and max(rows[180][330:600]) > 60
    # turned by 90 degrees with a circle mask and soft edges: it renders, the size is the canvas
    saved, out3 = _render(c, fid, {"ratio": "1:1", "rotate": 90, "mask": {"shape": "circle", "w": 0.6, "h": 0.6, "feather": 40, "invert": True}})
    i3 = probe_media(out3)
    assert (i3.width, i3.height) == (640, 640) and abs(i3.duration - 2.0) < 0.15
    # nothing changed: no frame is stored (the original shape is used)
    _, v = c.json("POST", "/api/projects", {"source": fid, "interval": 0})
    p = v["project"]; p["settings"]["frame"] = {"ratio": "original"}
    _, s = c.json("PUT", "/api/projects/" + p["id"], p)
    assert s["settings"]["frame"] is None
