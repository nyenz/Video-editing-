"""The clip workshop web page: real server, real FFmpeg, real HTTP requests."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from editforge.web.server import make_server
from helpers import color_at, count_frames


class Client:
    def __init__(self, port: int, token: str):
        self.base, self.token = f"http://127.0.0.1:{port}", token

    def call(self, method: str, path: str, body=None, headers=None, token: bool = True):
        h = dict(headers or {})
        if token:
            h["X-EditForge-Token"] = self.token
        data = None
        if isinstance(body, (bytes, bytearray)):
            data = bytes(body)
            h.setdefault("Content-Type", "application/octet-stream")
        elif body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def json(self, method: str, path: str, body=None, **kw):
        status, _, raw = self.call(method, path, body, **kw)
        return status, json.loads(raw or b"{}")

    def upload(self, path: str) -> str:
        name = urllib.parse.quote(Path(path).name)
        status, data = self.json("POST", f"/api/upload?name={name}", Path(path).read_bytes())
        assert status == 201, data
        return data["id"]

    def wait_jobs(self, ids, timeout: float = 120.0):
        end = time.time() + timeout
        while time.time() < end:
            jobs = {j["id"]: j for j in self.json("GET", "/api/jobs")[1]["jobs"]}
            if all(jobs[i]["status"] in ("done", "failed", "cancelled") for i in ids):
                return [jobs[i] for i in ids]
            time.sleep(0.1)
        raise TimeoutError("jobs did not finish")


@pytest.fixture(scope="module")
def web(tmp_path_factory):
    home = tmp_path_factory.mktemp("web_home")
    srv, state = make_server("127.0.0.1", 0, home=home)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield Client(state.port, state.token), state
    state.manager.shutdown()
    state.previews.shutdown()
    srv.shutdown()
    srv.server_close()


def q(ident: str) -> str:
    return urllib.parse.quote(ident, safe="")


def test_pages_and_static_files(web):
    c, _ = web
    status, headers, body = c.call("GET", "/", token=False)
    assert status == 200 and b"Clip workshop" in body and b'id="player"' in body
    assert "media-src 'self'" in headers["Content-Security-Policy"]
    status, _, body = c.call("GET", "/advanced", token=False)
    assert status == 200 and b'id="script"' in body
    for name in ("workshop.js", "workshop.css", "app.js", "style.css"):
        assert c.call("GET", f"/static/{name}", token=False)[0] == 200
    assert c.call("GET", "/static/../server.py", token=False)[0] == 404


def test_info_reports_the_video(web, media):
    c, _ = web
    fid = c.upload(media["tone"])
    status, info = c.json("GET", f"/api/info?id={q(fid)}")
    assert status == 200 and info["has_video"] and abs(info["duration"] - 6.0) < 0.1
    assert info["width"] == 320 and info["fps_float"] == 25.0 and "path" not in info
    assert info["preview"]["state"] == "none"


def test_media_needs_the_key_and_a_valid_file(web, media):
    c, state = web
    fid = c.upload(media["tone"])
    assert c.call("GET", f"/media?id={q(fid)}", token=False)[0] == 403
    assert c.call("GET", f"/media?id={q(fid)}&t=wrong", token=False)[0] == 403
    assert c.call("GET", f"/media?id={q(fid)}&t={state.token}", token=False)[0] == 200  # the <video> tag sends the key in the address
    for bad in ("m/../../etc/passwd", "u/000000000000/nope.mp4", "/etc/passwd", "o/000000000000/x.mp4", ""):
        assert c.call("GET", f"/media?id={q(bad)}")[0] == 404, bad
    assert c.call("GET", f"/media?id={q(fid)}", headers={"Host": "evil.example"})[0] == 403


def test_media_supports_jumping_around(web, media):
    c, _ = web
    fid = c.upload(media["tone"])
    whole = Path(media["tone"]).read_bytes()
    status, headers, body = c.call("GET", f"/media?id={q(fid)}")
    assert status == 200 and body == whole and headers["Accept-Ranges"] == "bytes" and headers["Content-Type"] == "video/mp4"
    status, headers, body = c.call("GET", f"/media?id={q(fid)}", headers={"Range": "bytes=100-199"})
    assert status == 206 and body == whole[100:200] and headers["Content-Range"] == f"bytes 100-199/{len(whole)}"
    status, headers, body = c.call("GET", f"/media?id={q(fid)}", headers={"Range": "bytes=1000-"})
    assert status == 206 and body == whole[1000:]
    status, _, body = c.call("GET", f"/media?id={q(fid)}", headers={"Range": "bytes=-50"})
    assert status == 206 and body == whole[-50:]
    status, _, body = c.call("GET", f"/media?id={q(fid)}", headers={"Range": f"bytes=10-{len(whole) + 999}"})
    assert status == 206 and body == whole[10:]
    for bad in (f"bytes={len(whole)}-", "bytes=50-10", "bytes=-", "chunks=1-2"):
        assert c.call("GET", f"/media?id={q(fid)}", headers={"Range": bad})[0] == 416, bad


def test_one_video_per_clip_is_frame_exact(web, media):
    c, state = web
    fid = c.upload(media["colors"])      # 2 s red, 2 s green, 2 s blue at 25 fps
    status, data = c.json("POST", "/api/clips", {"input": fid, "quality": "best", "clips": [
        {"start": 0.4, "end": 1.6, "name": "red part"}, {"start": 4.2, "end": 5.0, "name": "../../evil<b>"}]})
    assert status == 201 and len(data["jobs"]) == 2
    jobs = c.wait_jobs(data["jobs"])
    assert [j["status"] for j in jobs] == ["done", "done"], jobs
    first, second = (Path(j["output_path"]) for j in jobs)
    assert first.name == "colors_red part.mp4" and count_frames(str(first)) == 30
    assert count_frames(str(second)) == 20
    for p in (first, second):                      # names can never leave the outputs folder
        assert p.resolve().is_relative_to(state.outputs) and ".." not in p.name and "<" not in p.name and "/" not in p.name
    r, g, b = color_at(str(first), 0.5)
    assert r > 150 and g < 90 and b < 90
    r, g, b = color_at(str(second), 0.3)
    assert b > 150 and r < 90


def test_join_puts_clips_in_one_video_in_list_order(web, media):
    c, _ = web
    fid = c.upload(media["colors"])
    status, data = c.json("POST", "/api/clips", {"input": fid, "join": True, "quality": "high",
                                                 "clips": [{"start": 4.0, "end": 5.0}, {"start": 0.0, "end": 1.0}]})
    assert status == 201 and len(data["jobs"]) == 1
    job = c.wait_jobs(data["jobs"])[0]
    assert job["status"] == "done" and Path(job["output_path"]).name == "colors_joined.mp4"
    assert count_frames(job["output_path"]) == 50
    assert color_at(job["output_path"], 0.5)[2] > 150 and color_at(job["output_path"], 1.5)[0] > 150     # blue first, as listed


def test_a_finished_clip_can_be_opened_and_cut_again(web, media):
    c, _ = web
    fid = c.upload(media["tone"])
    job = c.wait_jobs(c.json("POST", "/api/clips", {"input": fid, "clips": [{"start": 1, "end": 4}]})[1]["jobs"])[0]
    assert job["status"] == "done" and job["file_id"].startswith("o/")
    files = c.json("GET", "/api/files")[1]["files"]
    assert any(f["id"] == job["file_id"] and f["where"] == "made here" for f in files)
    assert abs(c.json("GET", f"/api/info?id={q(job['file_id'])}")[1]["duration"] - 3.0) < 0.05
    assert c.call("GET", f"/media?id={q(job['file_id'])}", headers={"Range": "bytes=0-9"})[0] == 206
    again = c.wait_jobs(c.json("POST", "/api/clips", {"input": job["file_id"], "clips": [{"start": 0.5, "end": 1.5}]})[1]["jobs"])[0]
    assert again["status"] == "done" and count_frames(again["output_path"]) == 25
    assert c.json("DELETE", f"/api/jobs/{job['id']}")[1]["ok"]
    assert c.call("GET", f"/media?id={q(job['file_id'])}")[0] == 404


@pytest.mark.parametrize("clips, words", [
    ([], "no clips"),
    ([{"start": 3, "end": 2}], "too short"),
    ([{"start": 2, "end": 2}], "too short"),
    ([{"start": 50, "end": 60}], "too short"),          # after the end of a 6 s video
    ([{"start": "abc", "end": 2}], "not a time"),
    ([{"end": 2}], "not a time"),
    ([{"start": 0, "end": float("nan")}], "not a time"),
    ([{"start": 0, "end": 1}] * 201, "more than 200"),
])
def test_bad_clips_get_a_plain_message(web, media, clips, words):
    c, _ = web
    fid = c.upload(media["tone"])
    status, data = c.json("POST", "/api/clips", {"input": fid, "clips": clips})
    assert status == 400 and words in data["error"] and data["fix"], data


def test_clips_need_a_video_and_the_key(web, media):
    c, _ = web
    assert c.json("POST", "/api/clips", {"clips": [{"start": 0, "end": 1}]})[0] == 400
    assert c.json("POST", "/api/clips", {"input": "m/../x", "clips": [{"start": 0, "end": 1}]})[0] == 400
    assert c.json("POST", "/api/clips", {"input": "x", "clips": [{"start": 0, "end": 1}]}, token=False)[0] == 403


def test_end_past_the_video_is_shortened_not_refused(web, media):
    c, _ = web
    fid = c.upload(media["tone"])
    job = c.wait_jobs(c.json("POST", "/api/clips", {"input": fid, "clips": [{"start": 5, "end": 99}]})[1]["jobs"])[0]
    assert job["status"] == "done" and count_frames(job["output_path"]) == 25


def test_preview_copy_is_made_and_served(web, media):
    c, state = web
    fid = c.upload(media["tall"])
    assert c.call("GET", f"/media?id={q(fid)}&preview=1")[0] == 404          # not made yet
    status, st = c.json("POST", "/api/preview", {"input": fid})
    assert status == 202 and st["state"] in ("running", "ready")
    end = time.time() + 60
    while st["state"] == "running" and time.time() < end:
        time.sleep(0.1)
        st = c.json("GET", f"/api/preview?id={q(fid)}")[1]
    assert st == {"state": "ready", "progress": 1.0, "error": ""}
    status, headers, body = c.call("GET", f"/media?id={q(fid)}&preview=1", headers={"Range": "bytes=0-99"})
    assert status == 206 and len(body) == 100 and headers["Content-Type"] == "video/mp4"
    assert c.json("GET", f"/api/info?id={q(fid)}")[1]["preview"]["state"] == "ready"
    assert not list(state.previews.folder.glob("*.part"))
    assert c.json("POST", "/api/preview", {"input": fid})[1]["state"] == "ready"   # asking again does not redo it


def test_preview_is_refused_for_sound_only(web, media):
    c, _ = web
    fid = c.upload(media["tone_wav"])
    status, data = c.json("POST", "/api/preview", {"input": fid})
    assert status == 400 and "no picture" in data["error"]
    assert c.json("GET", f"/api/info?id={q(fid)}")[1]["preview"]["state"] == "none"
