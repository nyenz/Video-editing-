"""Measure how fast the app starts and opens a video (used for BENCHMARKS.md).

    python tools/bench_app.py path/to/video.mkv

Starts the server in a fresh process with an empty home folder, then times: server ready, first page
(with its scripts and styles), the features check, opening the video (facts, new project, open, save)
and the first playable picture (light copy, or the quick copy when there is one).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def get(url: str, token: str = "", data=None, method=None, gzip=False):
    req = urllib.request.Request(url, data=data, method=method)
    if token:
        req.add_header("X-EditForge-Token", token)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if gzip:
        req.add_header("Accept-Encoding", "gzip")
    with urllib.request.urlopen(req, timeout=600) as r:
        return r.read(), dict(r.headers)


def main() -> int:
    video = Path(sys.argv[1]).resolve()
    home = Path(tempfile.mkdtemp(prefix="ef-bench-"))
    media = home / "media"
    media.mkdir()
    link = media / video.name
    try:
        os.link(video, link)          # the server refuses links that point outside its folders
    except OSError:
        shutil.copy2(video, link)
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
    env = dict(os.environ, EDITFORGE_HOME=str(home), PYTHONPATH=str(ROOT))
    code = ("import sys;from editforge.web.server import make_server;"
            f"s,st=make_server('127.0.0.1',0,media_dir={str(media)!r});print(st.port,flush=True);s.serve_forever()")
    t0 = time.perf_counter()
    proc = subprocess.Popen([sys.executable, "-c", code], env=env, stdout=subprocess.PIPE, text=True)
    port = int(proc.stdout.readline())
    out = {"server ready": time.perf_counter() - t0}
    base = f"http://127.0.0.1:{port}"
    try:
        t = time.perf_counter()
        html, _ = get(base + "/")
        token = re.search(rb'name="ef-token" content="([0-9a-f]+)"', html).group(1).decode()
        size = len(html)
        for ref in re.findall(rb'(/static/[\w.]+(?:\?v=\w+)?)', html):
            body, _ = get(base + ref.decode(), gzip=True)
            size += len(body)
        out["first page + scripts"] = time.perf_counter() - t
        out["first page bytes"] = size
        t = time.perf_counter()
        get(base + "/api/state", token)
        out["features check"] = time.perf_counter() - t
        fid = "m/" + video.name
        q = urllib.parse.quote(fid)
        t = time.perf_counter()
        get(f"{base}/api/info?id={q}", token)
        out["video facts (first time)"] = time.perf_counter() - t
        t = time.perf_counter()
        get(f"{base}/api/info?id={q}", token)
        out["video facts (again)"] = time.perf_counter() - t
        t = time.perf_counter()
        body, _ = get(base + "/api/projects", token, json.dumps({"source": fid, "interval": 2}).encode(), "POST")
        proj = json.loads(body)["project"]
        out["new project"] = time.perf_counter() - t
        t = time.perf_counter()
        get(f"{base}/api/projects/{proj['id']}", token)
        out["open project"] = time.perf_counter() - t
        t = time.perf_counter()
        get(f"{base}/api/projects/{proj['id']}", token, json.dumps(proj).encode(), "PUT")
        out["autosave"] = time.perf_counter() - t
        t = time.perf_counter()
        get(base + "/api/preview", token, json.dumps({"input": fid}).encode(), "POST")
        first = None
        while True:
            st = json.loads(get(f"{base}/api/preview?id={q}", token)[0])
            if first is None and (st.get("quick") or st["state"] == "ready"):
                first = time.perf_counter() - t
            if st["state"] in ("ready", "failed"):
                break
            time.sleep(0.2)
        out["first playable copy"] = first
        out["light copy ready"] = time.perf_counter() - t
    finally:
        proc.terminate()
        shutil.rmtree(home, ignore_errors=True)
    for k, v in out.items():
        print(f"{k:28s} {v:,.0f} bytes" if "bytes" in k else f"{k:28s} {v:.3f} s")
    return 0


if __name__ == "__main__":
    import urllib.parse  # noqa: F401
    sys.exit(main())
