"""The desktop app launcher and the 'can a browser play this?' check (films with Dolby sound)."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from editforge import desktop
from editforge.core import ffmpeg as ff
from editforge.web.server import make_server, playback_check
from test_web_workshop import Client, q


def test_selftest_passes(capsys):
    assert desktop.main(["--selftest"]) == 0
    out = capsys.readouterr().out
    assert "ALL OK" in out and "FAIL" not in out


def test_version_flag(capsys):
    assert desktop.main(["--version"]) == 0
    assert capsys.readouterr().out.strip().count(".") == 2


def test_app_prefers_the_ffmpeg_shipped_next_to_it(tmp_path, monkeypatch):
    exe = "ffmpeg" + (".exe" if sys.platform == "win32" else "")
    (tmp_path / "ffmpeg").mkdir()
    fake = tmp_path / "ffmpeg" / exe
    fake.write_text("")
    fake.chmod(0o755)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / ("EditForge" + (".exe" if sys.platform == "win32" else ""))))
    monkeypatch.delenv("EDITFORGE_FFMPEG", raising=False)
    assert ff.find_executable("ffmpeg") == str(fake)


def test_playback_check():
    good = {"has_video": True, "has_audio": True, "video_codec": "h264", "pix_fmt": "yuv420p", "audio_codec": "aac"}
    assert playback_check("a.mp4", good) == (True, "")
    ok, why = playback_check("film.mkv", dict(good, audio_codec="ac3"))
    assert not ok and why
    ok, why = playback_check("film.mp4", dict(good, audio_codec="eac3"))
    assert not ok and "EAC3" in why
    assert not playback_check("a.mp4", dict(good, pix_fmt="yuv420p10le"))[0]
    assert playback_check("song.mp3", {"has_video": False, "has_audio": True, "audio_codec": "mp3"})[0]


@pytest.fixture(scope="module")
def web(tmp_path_factory):
    srv, state = make_server("127.0.0.1", 0, home=tmp_path_factory.mktemp("desk_home"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield Client(state.port, state.token), state
    state.manager.shutdown()
    state.previews.shutdown()
    srv.shutdown()
    srv.server_close()


def test_dolby_film_gets_a_copy_with_normal_sound(web, tmp_path):
    c, _ = web
    film = tmp_path / "film.mkv"
    subprocess.run([ff.find_executable("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=3",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-ac", "6", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "ac3", "-shortest", str(film)], check=True)
    fid = c.upload(str(film))
    status, info = c.json("GET", f"/api/info?id={q(fid)}")
    assert status == 200 and info["plays_in_browser"] is False and "AC3" in info["playback_note"]
    assert c.json("POST", "/api/preview", {"input": fid})[0] in (200, 202)
    for _ in range(120):
        st = c.json("GET", f"/api/preview?id={q(fid)}")[1]
        if st["state"] in ("ready", "failed"):
            break
        time.sleep(0.5)
    assert st["state"] == "ready"
    status, headers, body = c.call("GET", f"/media?id={q(fid)}&preview=1")
    assert status == 200
    copy = tmp_path / "copy.mp4"
    copy.write_bytes(body)
    probe = subprocess.run([ff.find_executable("ffprobe"), "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_name,channels",
                            "-of", "csv=p=0", str(copy)], capture_output=True, text=True).stdout.strip()
    assert probe.startswith("aac") and probe.endswith("2")
