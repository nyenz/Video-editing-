"""The Editor and Combine pages: projects, thumbnails, beats, saved patterns and combine jobs over real HTTP."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from editforge.web.server import make_server
from helpers import audio_level, color_at, count_frames
from test_web_workshop import Client, q


@pytest.fixture(scope="module")
def web(tmp_path_factory):
    srv, state = make_server("127.0.0.1", 0, home=tmp_path_factory.mktemp("editor_home"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield Client(state.port, state.token), state
    state.manager.shutdown()
    state.previews.shutdown()
    srv.shutdown()
    srv.server_close()


def new_project(c, fid, interval=2):
    status, data = c.json("POST", "/api/projects", {"source": fid, "interval": interval})
    assert status == 201, data
    return data["project"]


def test_pages_are_served(web):
    c, _ = web
    for path, word in (("/edit", b'id="grid"'), ("/combine", b'id="fileB"')):
        status, _, body = c.call("GET", path, token=False)
        assert status == 200 and word in body
    for name in ("common.js", "editor.js", "editor.css", "combine.js"):
        assert c.call("GET", f"/static/{name}", token=False)[0] == 200


def test_new_project_is_sliced_listed_and_reopened(web, media):
    c, _ = web
    fid = c.upload(media["scenes"])
    p = new_project(c, fid, 1.5)
    assert [round(x["end"] - x["start"], 2) for x in p["pieces"]] == [1.5, 1.5, 1.5, 1.5] and p["name"] == "scenes"
    whole = new_project(c, fid, 0)
    assert len(whole["pieces"]) == 1 and whole["pieces"][0]["end"] == 6.0
    listed = c.json("GET", "/api/projects")[1]["projects"]
    assert {x["id"] for x in listed} >= {p["id"], whole["id"]} and all(x["source_name"] == "scenes.mp4" for x in listed)
    status, view = c.json("GET", f"/api/projects/{p['id']}")
    assert status == 200 and view["project"]["pieces"] == p["pieces"] and view["info"]["duration"] == 6.0 and "path" not in view["info"]
    assert c.json("DELETE", f"/api/projects/{whole['id']}")[1]["ok"]
    assert c.json("GET", f"/api/projects/{whole['id']}")[0] == 400


def test_saving_cleans_the_project_and_keeps_its_video(web, media):
    c, _ = web
    p = new_project(c, c.upload(media["scenes"]))
    body = {"name": "My <i>edit</i>", "source": "m/../../etc/passwd", "settings": {"transition": "fade", "shape": "nonsense"},
            "pieces": [dict(p["pieces"][0], fx=["hflip", "evil"], speed=2), {"start": 4, "end": 99}, {"start": 3, "end": 1}]}
    status, saved = c.json("PUT", f"/api/projects/{p['id']}", body)
    assert status == 200 and saved["source"] == p["source"] and saved["name"] == "My <i>edit</i>"
    assert len(saved["pieces"]) == 2 and saved["pieces"][0]["fx"] == ["hflip"] and saved["pieces"][1]["end"] == 6.0
    assert saved["settings"]["transition"] == "fade" and saved["settings"]["shape"] == "original"
    assert c.json("GET", f"/api/projects/{p['id']}")[1]["project"]["pieces"] == saved["pieces"]


@pytest.mark.parametrize("method, path", [("GET", "/api/projects"), ("POST", "/api/projects"), ("GET", "/api/recipes"), ("PUT", "/api/recipes"),
                                           ("POST", "/api/beats"), ("POST", "/api/combine"), ("GET", "/thumb?id=x&at=1")])
def test_new_routes_need_the_key(web, method, path):
    c, _ = web
    assert c.json(method, path, {} if method != "GET" else None, token=False)[0] == 403


def test_bad_project_ids_and_missing_video(web, media):
    c, state = web
    for bad in ("../../etc", "zzzzzzzzzzzz", "0123456789ab"):
        assert c.json("GET", f"/api/projects/{bad}")[0] in (400, 404)
    assert c.json("POST", "/api/projects", {"source": "m/../../etc/passwd"})[0] == 400
    assert c.json("POST", "/api/projects", {})[0] == 400
    fid = c.upload(media["tone"])
    p = new_project(c, fid)
    Path(state.resolve_input(fid)).unlink()                       # the video is deleted behind the project's back
    status, data = c.json("GET", f"/api/projects/{p['id']}")
    assert status == 400 and "gone" in data["error"] and data["fix"]


def test_make_a_project_video_with_a_pattern(web, media):
    """Remove the 1st of every 3 pieces and flip nothing: the result is exactly the kept pieces."""
    c, _ = web
    p = new_project(c, c.upload(media["scenes"]), 1)              # 6 pieces: red red, lime lime, blue blue
    for i, piece in enumerate(p["pieces"]):
        piece["off"] = i % 3 == 0                                 # removes piece 1 (red) and piece 4 (lime)
    p["settings"]["quality"] = "small"
    assert c.json("PUT", f"/api/projects/{p['id']}", p)[0] == 200
    ids = [c.json("POST", f"/api/projects/{p['id']}/render", {"preview": pv})[1]["id"] for pv in (True, False)]
    preview, full = c.wait_jobs(ids)
    assert preview["status"] == full["status"] == "done", (preview, full)
    assert preview["kind"] == "project" and preview["preview"] and not full["preview"] and "script" not in full
    assert Path(preview["output_path"]).name == "scenes_preview.mp4" and Path(full["output_path"]).name == "scenes.mp4"
    out = full["output_path"]
    assert count_frames(out) == 100 == count_frames(preview["output_path"])
    r, g, b = color_at(out, 0.5)
    assert r > 150 and g < 90
    assert color_at(out, 1.5)[1] > 150 and color_at(out, 2.5)[2] > 150 and color_at(out, 3.5)[2] > 150
    names = [f["name"] for f in c.json("GET", "/api/files")[1]["files"]]
    assert "scenes.mp4" in names and "scenes_preview.mp4" not in names          # quick previews are not offered for editing
    assert full["file_id"].startswith("o/") and new_project(c, full["file_id"], 1)["pieces"][-1]["end"] == 4.0   # edit the result again


def test_render_refuses_an_empty_project_and_missing_music(web, media):
    c, _ = web
    p = new_project(c, c.upload(media["tone"]))
    for piece in p["pieces"]:
        piece["off"] = True
    c.json("PUT", f"/api/projects/{p['id']}", p)
    status, data = c.json("POST", f"/api/projects/{p['id']}/render", {})
    assert status == 400 and "no video left" in data["error"]
    p["pieces"][0]["off"] = False
    p["settings"]["music"] = "u/000000000000/gone.mp3"
    c.json("PUT", f"/api/projects/{p['id']}", p)
    status, data = c.json("POST", f"/api/projects/{p['id']}/render", {})
    assert status == 400 and "music" in data["error"] and data["fix"]


def test_music_from_the_page_ends_up_in_the_video(web, media):
    c, _ = web
    p = new_project(c, c.upload(media["silent"]), 2)
    music = c.upload(media["music_tone"])
    files = {f["id"]: f for f in c.json("GET", "/api/files")[1]["files"]}
    assert files[music]["audio"] and not files[p["source"]]["audio"]
    p["settings"].update({"music": music, "music_volume": 1.0, "quality": "small"})
    c.json("PUT", f"/api/projects/{p['id']}", p)
    job = c.wait_jobs([c.json("POST", f"/api/projects/{p['id']}/render", {})[1]["id"]])[0]
    assert job["status"] == "done", job
    assert audio_level(job["output_path"], 1.0, 2.0)[0] > -35


def test_beats_of_a_music_file(web, media):
    c, _ = web
    status, b = c.json("POST", "/api/beats", {"input": c.upload(media["click4"])})
    assert status == 200 and 110 <= b["tempo"] <= 130 and len(b["beats"]) > 20 and b["duration"] > 10
    gaps = [y - x for x, y in zip(b["beats"], b["beats"][1:])]
    assert all(abs(g - 0.5) < 0.06 for g in gaps[2:-2])
    status, data = c.json("POST", "/api/beats", {"input": c.upload(media["black"])})
    assert status == 400 and "no sound" in data["error"]


def test_thumbnails(web, media):
    c, state = web
    fid = c.upload(media["scenes"])
    status, headers, body = c.call("GET", f"/thumb?id={q(fid)}&at=1.0&t={state.token}", token=False)
    assert status == 200 and headers["Content-Type"] == "image/jpeg" and body[:2] == b"\xff\xd8" and "max-age" in headers["Cache-Control"]
    assert len(list(state.thumbs.glob("*.jpg"))) >= 1 and not list(state.thumbs.glob("*.part"))
    assert c.call("GET", f"/thumb?id={q(fid)}&at=1.0")[2] == body                    # second time comes from the saved copy
    for bad in (f"/thumb?id={q(fid)}&at=abc", f"/thumb?id={q(fid)}&at=-3", f"/thumb?id={q('m/../x')}&at=1", f"/thumb?id={q(fid)}&at=nan"):
        assert c.call("GET", bad)[0] == 404, bad
    assert c.call("GET", f"/thumb?id={q(c.upload(media['tone_wav']))}&at=1")[0] == 404   # sound only: no picture


def test_saved_patterns_are_cleaned_and_kept(web):
    c, state = web
    raw = [{"name": "  mirror   2 of 3 ", "group": 3, "picks": [2, 2, 9, 0], "look": {"fx": ["hflip", "bad"], "speed": 2, "start": 5, "end": 6}},
           {"name": "", "group": 2, "picks": [1]}, {"name": "no picks", "group": 2, "picks": []}, "junk",
           {"name": "pattern only", "group": 500, "picks": [100]}]
    status, data = c.json("PUT", "/api/recipes", raw)
    assert status == 200 and [r["name"] for r in data["recipes"]] == ["mirror 2 of 3", "pattern only"]
    first, second = data["recipes"]
    assert first["group"] == 3 and first["picks"] == [2] and first["look"]["fx"] == ["hflip"] and first["look"]["speed"] == 2
    assert "start" not in first["look"] and second == {"name": "pattern only", "group": 100, "picks": [100], "look": None}
    assert c.json("GET", "/api/recipes")[1] == data
    assert c.json("PUT", "/api/recipes", [{"name": "x", "group": 1, "picks": [1]}] * 101)[0] == 400
    assert c.json("PUT", "/api/recipes", {"not": "a list"})[0] == 400
    assert c.json("GET", "/api/recipes")[1] == data                                  # a refused save changes nothing


def test_combine_job(web, media):
    c, _ = web
    a, b = c.upload(media["scenes"]), c.upload(media["tall"])
    status, data = c.json("POST", "/api/combine", {"a": a, "b": b, "options": {"mode": "side", "length": "shortest", "quality": "small"}})
    assert status == 201
    job = c.wait_jobs([data["id"]])[0]
    assert job["status"] == "done" and job["kind"] == "combine", job
    assert Path(job["output_path"]).name == "scenes_side_tall.mp4" and job["result"]["verify"]["width"] == 210
    assert abs(job["result"]["duration"] - 4.0) < 0.1 and job["file_id"].startswith("o/")
    assert c.json("POST", "/api/combine", {"a": a})[0] == 400
    status, data = c.json("POST", "/api/combine", {"a": a, "b": c.upload(media["tone_wav"])})
    assert status == 400 and "picture" in data["error"]
    assert c.json("POST", "/api/combine", {"a": a, "b": "m/../../etc/passwd"})[0] == 400
