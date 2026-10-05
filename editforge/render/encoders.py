"""Video encoders: find the ones that really work here, and build their command-line options.

Hardware encoders (NVENC, QuickSync, AMF, VideoToolbox) are only used after a tiny test-encode
succeeds. If the test fails, EditForge falls back to the software encoder libx264 automatically.
"""

from __future__ import annotations

import json
import platform
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional

from ..core.cache import atomic_write_json, home_dir
from ..core.errors import EditForgeError
from ..core.ffmpeg import get_tools
from ..core.model import OutputSpec
from ..core.resources import x264_tuning

SOFTWARE = "libx264"
HARDWARE = ("h264_videotoolbox", "h264_nvenc", "h264_qsv", "h264_amf")
LABELS = {"libx264": "Software (libx264, works everywhere)", "h264_nvenc": "NVIDIA NVENC", "h264_qsv": "Intel Quick Sync",
          "h264_amf": "AMD AMF", "h264_videotoolbox": "Apple VideoToolbox"}


@dataclass
class EncoderInfo:
    name: str
    label: str
    in_build: bool
    works: bool
    error: str = ""

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def hardware_args(name: str, crf: int) -> List[str]:
    """Quality options for each hardware encoder (quality is mapped from the CRF number)."""
    if name == "h264_nvenc":
        return ["-c:v", name, "-preset", "p4", "-rc", "vbr", "-cq", str(min(51, crf + 3)), "-b:v", "0", "-bf", "2"]
    if name == "h264_qsv":
        return ["-c:v", name, "-preset", "medium", "-global_quality", str(min(51, crf + 3))]
    if name == "h264_amf":
        return ["-c:v", name, "-quality", "balanced", "-rc", "cqp", "-qp_i", str(crf + 2), "-qp_p", str(crf + 4), "-qp_b", str(crf + 6)]
    if name == "h264_videotoolbox":
        return ["-c:v", name, "-q:v", str(max(20, min(90, 100 - 2 * crf))), "-allow_sw", "1"]
    raise ValueError(name)


def software_args(out: OutputSpec, threads: int, gop: int) -> List[str]:
    refs, bf, la = x264_tuning(out.width or 640, out.height or 360, out.x264_preset)
    return ["-c:v", SOFTWARE, "-preset", out.x264_preset, "-crf", str(out.crf), "-profile:v", "high", "-bf", str(bf),
            "-refs", str(refs), "-g", str(gop), "-threads", str(max(1, threads)),
            "-x264-params", f"rc-lookahead={la}:sync-lookahead=0"]


def video_encoder_args(name: str, out: OutputSpec, threads: int) -> List[str]:
    """Full encoder options for one piece."""
    gop = max(2, int(round(float(out.fps) * 2)))
    if name == SOFTWARE:
        return software_args(out, threads, gop) + ["-pix_fmt", "yuv420p"]
    return hardware_args(name, out.crf) + ["-g", str(gop), "-pix_fmt", "yuv420p"]


def test_encoder(name: str, timeout: float = 20.0) -> "tuple[bool, str]":
    """Try to encode 8 tiny frames. Returns (worked, error text)."""
    tools = get_tools()
    if not tools.has_encoder(name):
        return False, "This FFmpeg was built without it."
    spec = OutputSpec(crf=23, x264_preset="ultrafast")
    args = video_encoder_args(name, spec, 1)
    cmd = [tools.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
           "testsrc2=size=256x144:rate=25:duration=0.4", "-frames:v", "8", *args, "-f", "null", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return False, "The test took too long."
    except OSError as exc:
        return False, str(exc)
    if proc.returncode == 0:
        return True, ""
    text = proc.stderr.decode("utf-8", "replace").strip().splitlines()
    return False, (text[-1] if text else "The test encode failed.")[:200]


def _cache_file():
    return home_dir() / "encoders.json"


def detect_encoders(force: bool = False) -> List[EncoderInfo]:
    """List every encoder EditForge knows, testing the ones this FFmpeg has (results are cached)."""
    tools = get_tools()
    key = f"{tools.ffmpeg}|{tools.version}|{platform.node()}"
    cache: Dict = {}
    try:
        cache = json.loads(_cache_file().read_text())
    except (OSError, ValueError):
        cache = {}
    if not force and cache.get("key") == key:
        return [EncoderInfo(**e) for e in cache["items"]]
    infos: List[EncoderInfo] = []
    for name in (SOFTWARE,) + HARDWARE:
        ok, err = test_encoder(name)
        infos.append(EncoderInfo(name, LABELS[name], tools.has_encoder(name), ok, err))
    try:
        atomic_write_json(_cache_file(), {"key": key, "items": [i.to_dict() for i in infos]})
    except OSError:
        pass
    return infos


def choose_encoder(preference: str = "auto") -> str:
    """Pick the encoder to use: 'auto', 'software' or an encoder name. Falls back to libx264."""
    pref = (preference or "auto").lower()
    if pref in ("software", "cpu", "libx264", "x264"):
        return SOFTWARE
    infos = {i.name: i for i in detect_encoders()}
    if pref != "auto":
        alias = {"nvenc": "h264_nvenc", "qsv": "h264_qsv", "amf": "h264_amf", "videotoolbox": "h264_videotoolbox"}
        name = alias.get(pref, pref)
        if name in infos and infos[name].works:
            return name
        raise EditForgeError(f"The encoder '{preference}' is not available here"
                             + (f" ({infos[name].error})" if name in infos and infos[name].error else "") + ".",
                             "Run 'editforge encoders' to see what works, or use --encoder auto (falls back to software).")
    order = list(HARDWARE)
    if platform.system() != "Darwin":
        order = [n for n in order if n != "h264_videotoolbox"]
    for name in order:
        if name in infos and infos[name].works:
            return name
    return SOFTWARE
