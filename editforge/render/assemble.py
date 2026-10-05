"""The final step: join the pieces, mix the sound, add captions, and write the finished file."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..core.errors import RenderError
from ..core.ffmpeg import escape_concat_path, get_tools
from ..core.model import AudioSpec, MusicSpec, Timeline
from .filters import Graph
from .pieces import filter_script_args

LIMITER = "alimiter=limit=0.891:attack=5:release=60:level=disabled"


def concat_list(names: Sequence[str]) -> str:
    return "".join(f"file '{escape_concat_path(n)}'\n" for n in names)


def music_chain(m: MusicSpec, total_s: float, sr: int, layout: str) -> Optional[str]:
    """Filters that prepare one music track: length, volume, fades and start delay."""
    length = (min(m.end_s, total_s) if m.end_s is not None else total_s) - m.start_s
    if length <= 0.05:
        return None
    parts = [f"aformat=sample_fmts=fltp:sample_rates={sr}:channel_layouts={layout}",
             f"atrim=duration={length:.4f}", "asetpts=PTS-STARTPTS", f"volume={m.volume:.5f}"]
    if m.fade_in > 0:
        parts.append(f"afade=t=in:st=0:d={min(m.fade_in, length):.4f}")
    if m.fade_out > 0 and length > 0.1:
        fo = min(m.fade_out, length)
        parts.append(f"afade=t=out:st={length - fo:.4f}:d={fo:.4f}")
    if m.start_s > 0:
        ms = int(round(m.start_s * 1000))
        parts.append(f"adelay={ms}" if layout == "mono" else f"adelay={ms}|{ms}")
    return ",".join(parts)


def audio_graph(tl: Timeline, sr: int, layout: str, speech: str, music_idx: List[int], total_s: float,
                loudnorm: Optional[str] = None, guard: bool = True, fades: bool = True,
                denoise_nf: float = -50.0) -> Tuple[str, str, List[str]]:
    """Speech -> denoise -> (music with ducking) -> mix -> loudnorm -> clipping guard -> fades.

    Returns (graph text, output label, warnings).
    """
    g = Graph("m")
    warnings: List[str] = []
    a: AudioSpec = tl.audio
    cur = speech
    if a.denoise:
        parts = ["highpass=f=80"] if a.denoise.get("highpass") else []
        parts.append(f"afftdn=nr={a.denoise['amount']:g}:nf={denoise_nf:.1f}:tn=1")
        cur = g.add(cur, ",".join(parts), "dn")
    tracks: List[Tuple[MusicSpec, str]] = []
    for m, idx in zip(a.music, music_idx):
        chain = music_chain(m, total_s, sr, layout)
        if chain is None:
            warnings.append(f"The music starts after the end of the video (line {m.line}), so it was not added.")
            continue
        tracks.append((m, g.add(f"{idx}:a:0", chain, "mu")))
    ducked = [t for t in tracks if t[0].duck]
    keys: List[str] = []
    if ducked:
        outs = [g.label("sp") for _ in range(len(ducked) + 1)]
        g.add_raw(f"[{cur}]asplit={len(ducked) + 1}" + "".join(f"[{o}]" for o in outs))
        cur, keys = outs[0], outs[1:]
    mix_in = [cur]
    ki = 0
    for m, lab in tracks:
        if m.duck:
            out = g.label("md")
            g.add_raw(f"[{lab}][{keys[ki]}]sidechaincompress=threshold=0.03:ratio=10:attack=20:release=400:makeup=1[{out}]")
            ki += 1
            lab = out
        mix_in.append(lab)
    if len(mix_in) > 1:
        out = g.label("mx")
        norm = ":normalize=0" if get_tools().amix_normalize else ""
        g.add_raw("".join(f"[{x}]" for x in mix_in) + f"amix=inputs={len(mix_in)}:duration=first:dropout_transition=0{norm}[{out}]")
        cur = out
        if not norm:
            cur = g.add(cur, f"volume={len(mix_in)}", "mv")
    if loudnorm:
        cur = g.add(cur, loudnorm, "ln")
    if guard and (loudnorm or tracks):
        cur = g.add(cur, LIMITER, "lm")
    if fades and a.fade_audio:
        if a.fade_in > 0:
            cur = g.add(cur, f"afade=t=in:st=0:d={a.fade_in:.4f}", "fi")
        if a.fade_out > 0:
            cur = g.add(cur, f"afade=t=out:st={max(0.0, total_s - a.fade_out):.4f}:d={a.fade_out:.4f}", "fo")
    cur = g.add(cur, f"aformat=sample_fmts=fltp:sample_rates={sr}:channel_layouts={layout}", "fz")
    return g.text(), cur, warnings


def loudnorm_filter(target: Dict[str, float], measured: Optional[Dict[str, float]] = None) -> str:
    base = f"loudnorm=I={target['target']:g}:TP={target['true_peak']:g}:LRA={target['lra']:g}"
    if measured is None:
        return base + ":print_format=json"
    return (base + f":measured_I={measured['input_i']:.2f}:measured_TP={measured['input_tp']:.2f}:measured_LRA={measured['input_lra']:.2f}"
            f":measured_thresh={measured['input_thresh']:.2f}:offset={measured['target_offset']:.2f}:linear=true:print_format=summary")


def parse_loudnorm_json(stderr: str) -> Optional[Dict[str, float]]:
    """Read the measurement block that loudnorm prints. Returns None if the sound was silent."""
    blocks = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr, re.S)
    if not blocks:
        return None
    try:
        raw = json.loads(blocks[-1])
        vals = {k: float(raw[k]) for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")}
    except (ValueError, KeyError):
        return None
    if vals["input_i"] < -69.9 or any(v != v or abs(v) == float("inf") for v in vals.values()):
        return None
    return vals


def container_args(tl: Timeline, sr: int, layout: str) -> Tuple[List[str], str]:
    """(audio/container options, muxer name) for the chosen container."""
    out = tl.output
    ch = "1" if layout == "mono" else "2"
    if out.container in ("mp4", "mov"):
        fmt = "mp4" if out.container == "mp4" else "mov"
        return ["-c:a", "aac", "-b:a", out.audio_bitrate, "-ar", str(sr), "-ac", ch, "-movflags", "+faststart"], fmt
    if out.container == "mkv":
        return ["-c:a", "aac", "-b:a", out.audio_bitrate, "-ar", str(sr), "-ac", ch], "matroska"
    if out.container == "m4a":
        return ["-c:a", "aac", "-b:a", out.audio_bitrate, "-ar", str(sr), "-ac", ch, "-movflags", "+faststart"], "ipod"
    if out.container == "mp3":
        return ["-c:a", "libmp3lame", "-b:a", out.audio_bitrate, "-ar", str(sr), "-ac", ch], "mp3"
    if out.container == "wav":
        return ["-c:a", "pcm_s16le", "-ar", str(sr), "-ac", ch], "wav"
    if out.container == "flac":
        return ["-c:a", "flac", "-ar", str(sr), "-ac", ch], "flac"
    raise RenderError(f"The container '{out.container}' is not supported.", "Use mp4, mkv, mov, m4a, mp3, wav, flac or gif.")


def final_command(tl: Timeline, *, src: str, video_mode: str, has_video_pieces: bool, has_audio: bool, sr: int, layout: str,
                  total_s: float, loudnorm: Optional[str], soft_subs: Optional[str], out_name: str,
                  denoise_nf: float = -50.0) -> Tuple[List[str], Dict[str, str]]:
    """The one FFmpeg command that writes the finished file. Returns (arguments, files to write first)."""
    out = tl.output
    args: List[str] = ["-y"]
    idx = 0
    video_idx: Optional[int] = None
    if out.has_video:
        if video_mode == "copy_all":
            args += ["-i", src]
        else:
            args += ["-f", "concat", "-safe", "0", "-i", "vlist.txt"]
        video_idx, idx = 0, 1
    audio_idx: Optional[int] = None
    if has_audio:
        args += ["-f", "concat", "-safe", "0", "-i", "alist.txt"]
        audio_idx, idx = idx, idx + 1
    music_idx: List[int] = []
    for m in tl.audio.music:
        args += (["-stream_loop", "-1"] if m.loop else []) + ["-i", m.file]
        music_idx.append(idx)
        idx += 1
    subs_idx: Optional[int] = None
    if soft_subs and out.has_video:
        args += ["-i", soft_subs]
        subs_idx = idx
        idx += 1
    files: Dict[str, str] = {}
    out_args: List[str] = []
    if video_idx is not None:
        out_args += ["-map", f"{video_idx}:v:0", "-c:v", "copy"]
    else:
        out_args += ["-vn"]
    a_opts, muxer = container_args(tl, sr, layout)
    if has_audio and audio_idx is not None:
        text, label, warns = audio_graph(tl, sr, layout, f"{audio_idx}:a:0", music_idx, total_s, loudnorm, denoise_nf=denoise_nf)
        files["final_graph.txt"] = text
        args += filter_script_args("final_graph.txt")
        out_args += ["-map", f"[{label}]"] + a_opts
    else:
        out_args += ["-an"]
        if out.container in ("mp4", "mov"):
            out_args += ["-movflags", "+faststart"]
    if subs_idx is not None:
        out_args += ["-map", f"{subs_idx}:0", "-c:s", "mov_text" if out.container in ("mp4", "mov") else "srt",
                     "-metadata:s:s:0", "language=eng"]
    out_args += ["-t", f"{total_s:.6f}", "-map_metadata", "-1", "-f", muxer, out_name]
    return args + out_args, files


def measure_command(tl: Timeline, *, has_audio: bool, sr: int, layout: str, total_s: float, loudnorm: str,
                    denoise_nf: float = -50.0) -> Tuple[List[str], Dict[str, str]]:
    """First loudness pass: run the sound chain up to loudnorm and print what it measured."""
    args: List[str] = ["-y", "-f", "concat", "-safe", "0", "-i", "alist.txt"]
    idx = 1
    music_idx: List[int] = []
    for m in tl.audio.music:
        args += (["-stream_loop", "-1"] if m.loop else []) + ["-i", m.file]
        music_idx.append(idx)
        idx += 1
    text, label, _ = audio_graph(tl, sr, layout, "0:a:0", music_idx, total_s, loudnorm, guard=False, fades=False, denoise_nf=denoise_nf)
    args += filter_script_args("measure_graph.txt") + ["-map", f"[{label}]", "-t", f"{total_s:.6f}", "-f", "null", "-"]
    return args, {"measure_graph.txt": text}


def gif_commands(tl: Timeline) -> Tuple[List[str], List[str]]:
    """Two passes for a good animated GIF: make a palette, then use it (memory stays flat)."""
    p1 = ["-y", "-f", "concat", "-safe", "0", "-i", "vlist.txt", "-vf", f"palettegen=max_colors={tl.output.gif_colors}:stats_mode=diff",
          "-frames:v", "1", "-update", "1", "palette.png"]
    p2 = ["-y", "-f", "concat", "-safe", "0", "-i", "vlist.txt", "-i", "palette.png", "-lavfi",
          "paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle", "-loop", "0", "-f", "gif", "final.gif.part"]
    return p1, p2


def noise_floor_from_levels(levels: Sequence[float]) -> float:
    """Estimate the background noise level (dB) from short-window loudness values.

    It is the 5th percentile of the windows that are not digital silence, plus 3 dB, kept between
    -60 and -25 dB. Quiet recordings get a low floor; a track that never goes quiet gets a high one.
    """
    vals = sorted(v for v in levels if v > -85.0)
    if not vals:
        return -50.0
    p5 = vals[int(0.05 * (len(vals) - 1))]
    return max(-60.0, min(-25.0, p5 + 3.0))
