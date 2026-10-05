"""Combine two videos into one: one after the other, side by side, stacked, one in a corner, or blended.

The first video ("A") sets the picture size and frame rate. The second ("B") is resized to match.
Both inputs are streamed by FFmpeg; nothing is loaded into memory.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

from .core.errors import Cancelled, EditForgeError
from .core.ffmpeg import raise_for_result, run_ffmpeg
from .core.media import MediaInfo, probe_media
from .dsl.registry import XFADE

MODES = ("sequence", "side", "stack", "corner", "blend")
CORNERS = ("top_left", "top_right", "bottom_left", "bottom_right")
SOUNDS = ("both", "a", "b", "none")
QUALITY_CRF = {"best": 17, "high": 20, "small": 23}
MAX_SIDE = 3840


def _even(x: float) -> int:
    return max(2, int(round(x / 2.0)) * 2)


def _fps(info: MediaInfo) -> str:
    return f"{info.fps.numerator}/{info.fps.denominator}"


def clean_options(raw: Any) -> Dict[str, Any]:
    """Validate the options that come from the page; anything odd falls back to a safe default."""
    raw = raw if isinstance(raw, dict) else {}

    def num(key: str, default: float, lo: float, hi: float) -> float:
        try:
            v = float(raw.get(key, default))
        except (TypeError, ValueError):
            return default
        return default if v != v else max(lo, min(hi, v))

    mode = raw.get("mode") if raw.get("mode") in MODES else "sequence"
    transition = raw.get("transition") if raw.get("transition") in XFADE else "none"
    return {
        "mode": mode, "transition": transition, "transition_s": num("transition_s", 0.5, 0.1, 5.0),
        "corner": raw.get("corner") if raw.get("corner") in CORNERS else "bottom_right",
        "size": num("size", 0.3, 0.1, 0.9), "opacity": num("opacity", 0.5, 0.05, 1.0),
        "sound": raw.get("sound") if raw.get("sound") in SOUNDS else "both",
        "length": raw.get("length") if raw.get("length") in ("shortest", "longest") else "longest",
        "quality": raw.get("quality") if raw.get("quality") in QUALITY_CRF else "best",
    }


def expected_duration(a: MediaInfo, b: MediaInfo, o: Dict[str, Any]) -> float:
    if o["mode"] == "sequence":
        cut = _transition_seconds(a, b, o)
        return a.duration + b.duration - cut
    if o["mode"] in ("corner", "blend"):
        return a.duration
    return min(a.duration, b.duration) if o["length"] == "shortest" else max(a.duration, b.duration)


def _transition_seconds(a: MediaInfo, b: MediaInfo, o: Dict[str, Any]) -> float:
    if o["transition"] == "none":
        return 0.0
    return max(0.0, min(o["transition_s"], a.duration / 2.0, b.duration / 2.0))


def _audio(index: int, info: MediaInfo, seconds: float, label: str) -> str:
    """A filter chain giving exactly ``seconds`` of 48 kHz stereo sound for one input (silence if it has none)."""
    if info.has_audio:
        return (f"[{index}:a:0]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,apad,"
                f"atrim=duration={seconds:.4f},asetpts=PTS-STARTPTS[{label}]")
    return f"anullsrc=r=48000:cl=stereo,atrim=duration={seconds:.4f},asetpts=PTS-STARTPTS[{label}]"


def build_command(a: MediaInfo, b: MediaInfo, o: Dict[str, Any], out_path: str) -> Tuple[List[str], float]:
    """Return (ffmpeg arguments, expected length in seconds)."""
    if not a.has_video or not b.has_video:
        raise EditForgeError("Both files need a picture to be combined.", "Choose two videos. To add music, use the editor's Music box.")
    W, H, fps = _even(a.width), _even(a.height), _fps(a)
    if max(W, H) > MAX_SIDE:
        raise EditForgeError("The first video is larger than 4K, which is too big to combine here.", "Make a smaller copy first.")
    mode, total = o["mode"], expected_duration(a, b, o)
    fit = f"scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
    f: List[str] = []
    if mode == "sequence":
        d = _transition_seconds(a, b, o)
        f.append(f"[0:v:0]fps={fps},{fit},format=yuv420p,trim=duration={a.duration:.4f},setpts=PTS-STARTPTS[va]")
        f.append(f"[1:v:0]fps={fps},{fit},format=yuv420p,trim=duration={b.duration:.4f},setpts=PTS-STARTPTS[vb]")
        f.append(_audio(0, a, a.duration, "aa"))
        f.append(_audio(1, b, b.duration, "ab"))
        if d >= 0.05:
            f.append(f"[va][vb]xfade=transition={o['transition']}:duration={d:.4f}:offset={a.duration - d:.4f}[v]")
            f.append(f"[aa][ab]acrossfade=d={d:.4f}[asum]")
        else:
            f.append("[va][vb]concat=n=2:v=1:a=0[v]")
            f.append("[aa][ab]concat=n=2:v=0:a=1[asum]")
        sound = "asum"
    else:
        pad_a = max(0.0, total - a.duration)
        pad_b = max(0.0, total - b.duration)
        if mode == "side":
            bw = _even(H * b.width / b.height)
            if W + bw > 2 * MAX_SIDE:
                raise EditForgeError("Side by side, these two videos are too wide.", "Make smaller copies first.")
            f.append(f"[0:v:0]fps={fps},scale={W}:{H}:flags=lanczos,setsar=1,tpad=stop_mode=clone:stop_duration={pad_a:.4f}[va]")
            f.append(f"[1:v:0]fps={fps},scale={bw}:{H}:flags=lanczos,setsar=1,tpad=stop_mode=clone:stop_duration={pad_b:.4f}[vb]")
            f.append(f"[va][vb]hstack=inputs=2,trim=duration={total:.4f},format=yuv420p[v]")
        elif mode == "stack":
            bh = _even(W * b.height / b.width)
            if H + bh > 2 * MAX_SIDE:
                raise EditForgeError("Stacked, these two videos are too tall.", "Make smaller copies first.")
            f.append(f"[0:v:0]fps={fps},scale={W}:{H}:flags=lanczos,setsar=1,tpad=stop_mode=clone:stop_duration={pad_a:.4f}[va]")
            f.append(f"[1:v:0]fps={fps},scale={W}:{bh}:flags=lanczos,setsar=1,tpad=stop_mode=clone:stop_duration={pad_b:.4f}[vb]")
            f.append(f"[va][vb]vstack=inputs=2,trim=duration={total:.4f},format=yuv420p[v]")
        elif mode == "corner":
            sw = _even(W * o["size"])
            sh = _even(sw * b.height / b.width)
            if sh > H:
                sh = _even(H * o["size"])
                sw = _even(sh * b.width / b.height)
            m = _even(min(W, H) * 0.03)
            x = str(m) if "left" in o["corner"] else f"W-w-{m}"
            y = str(m) if "top" in o["corner"] else f"H-h-{m}"
            f.append(f"[0:v:0]fps={fps},scale={W}:{H}:flags=lanczos,setsar=1[va]")
            f.append(f"[1:v:0]fps={fps},scale={sw}:{sh}:flags=lanczos,setsar=1[vb]")
            f.append(f"[va][vb]overlay={x}:{y}:eof_action=pass,trim=duration={total:.4f},format=yuv420p[v]")
        else:  # blend: B lies over A, partly see-through
            f.append(f"[0:v:0]fps={fps},scale={W}:{H}:flags=lanczos,setsar=1[va]")
            f.append(f"[1:v:0]fps={fps},{fit},format=yuva420p,colorchannelmixer=aa={o['opacity']:.3f}[vb]")
            f.append(f"[va][vb]overlay=0:0:eof_action=pass,trim=duration={total:.4f},format=yuv420p[v]")
        sound = {"both": "asum", "a": "aa", "b": "ab", "none": ""}[o["sound"]]
        if sound in ("asum", "aa"):       # only build the sound that is used (FFmpeg refuses unused outputs)
            f.append(_audio(0, a, total, "aa"))
        if sound in ("asum", "ab"):
            f.append(_audio(1, b, total, "ab"))
        if sound == "asum":
            f.append("[aa][ab]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.95[asum]")
    args = ["-y", "-i", a.path, "-i", b.path, "-filter_complex", ";".join(f), "-map", "[v]"]
    if sound:
        args += ["-map", f"[{sound}]", "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    else:
        args += ["-an"]
    args += ["-c:v", "libx264", "-preset", "medium" if o["quality"] == "best" else "veryfast", "-crf", str(QUALITY_CRF[o["quality"]]),
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-t", f"{total:.4f}", "-f", "mp4", out_path]
    return args, total


def combine(a_path: str, b_path: str, out_path: str, options: Any = None, *, cancel: Optional[threading.Event] = None,
            on_progress: Optional[Callable[[float], None]] = None) -> Dict[str, Any]:
    """Make the combined video at ``out_path`` (written to a .part file first, then renamed)."""
    o = clean_options(options)
    a, b = probe_media(a_path), probe_media(b_path)
    part = out_path + ".part"
    args, total = build_command(a, b, o, part)

    def prog(d: Dict[str, str]) -> None:
        if on_progress:
            try:
                on_progress(max(0.0, min(0.99, float(d.get("out_time_us", "0")) / 1e6 / max(total, 0.001))))
            except ValueError:
                pass

    try:
        res = run_ffmpeg(args, on_progress=prog, cancel=cancel)
        if res.cancelled or (cancel is not None and cancel.is_set()):
            raise Cancelled()
        raise_for_result(res, "combining the two videos")
        os.replace(part, out_path)
    finally:
        if os.path.exists(part):
            try:
                os.remove(part)
            except OSError:
                pass
    info = probe_media(out_path)
    return {"output_path": out_path, "duration": info.duration, "expected": total,
            "verify": {"ok": True, "width": info.width, "height": info.height, "duration": info.duration,
                       "has_video": info.has_video, "has_audio": info.has_audio, "size_bytes": info.size_bytes}}
