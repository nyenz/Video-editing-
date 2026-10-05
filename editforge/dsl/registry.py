"""The list of every instruction, its aliases and its parameters.

All three script languages (line, YAML, JSON) are read against this one table, so
they always have identical features.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class Param:
    """One named option of an instruction."""

    name: str
    kind: str
    aliases: Tuple[str, ...] = ()
    default: Any = None
    required: bool = False
    choices: Tuple[str, ...] = ()
    enum_alias: Dict[str, str] = field(default_factory=dict)
    lo: Optional[float] = None
    hi: Optional[float] = None
    doc: str = ""


@dataclass
class Spec:
    """One instruction."""

    name: str
    group: str
    summary: str
    example: str
    params: Tuple[Param, ...]
    positional: Tuple[str, ...] = ()
    aliases: Tuple[str, ...] = ()
    presets: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    greedy: Optional[str] = None

    def param_map(self) -> Dict[str, Param]:
        out: Dict[str, Param] = {}
        for p in self.params:
            out[p.name] = p
            for a in p.aliases:
                out.setdefault(norm(a), p)
        return out

    def get(self, name: str) -> Optional[Param]:
        return self.param_map().get(norm(name))


def norm(text: str) -> str:
    """Normalise a name: lower case, hyphens and spaces become underscores."""
    return re.sub(r"[\s\-]+", "_", str(text).strip().lower()).strip(":")


def P(name: str, kind: str, *aliases: str, default: Any = None, req: bool = False, choices: Tuple[str, ...] = (),
      ea: Optional[Dict[str, str]] = None, lo: Optional[float] = None, hi: Optional[float] = None, doc: str = "") -> Param:
    return Param(name, kind, tuple(aliases), default, req, tuple(choices), ea or {}, lo, hi, doc)


# Reusable parameter groups ---------------------------------------------------
START = P("start", "time", "from", "begin", "since", "in", doc="Where the instruction starts (source time).")
END = P("end", "time", "to", "until", "stop", "out", doc="Where the instruction stops (source time).")
RANGE = (START, END)
EASING = P("easing", "enum", "ease", "curve", default="ease_in_out",
           choices=("linear", "ease_in", "ease_out", "ease_in_out"),
           ea={"easein": "ease_in", "easeout": "ease_out", "easeinout": "ease_in_out", "smooth": "ease_in_out",
               "in": "ease_in", "out": "ease_out", "in_out": "ease_in_out"})
EFFECT_NAMES = ("mirror", "hflip", "flip", "vflip", "invert", "grayscale", "blur", "sharpen", "sepia", "vignette")
EFFECT_ALIASES = {"flip_h": "hflip", "flop": "hflip", "flip_horizontal": "hflip", "flip_v": "vflip",
                  "flip_vertical": "vflip", "upside_down": "vflip", "negate": "invert", "negative": "invert",
                  "gray": "grayscale", "grey": "grayscale", "greyscale": "grayscale", "bw": "grayscale",
                  "black_and_white": "grayscale", "mirror_h": "mirror", "kaleidoscope": "mirror",
                  "unsharp": "sharpen", "gaussian_blur": "blur", "soften": "blur"}
XFADE = ("fade", "dissolve", "fadeblack", "fadewhite", "wipeleft", "wiperight", "wipeup", "wipedown", "slideleft",
         "slideright", "slideup", "slidedown", "smoothleft", "smoothright", "circleopen", "circleclose", "radial",
         "pixelize", "hblur", "zoomin", "distance", "horzopen", "vertopen", "diagtl")
XFADE_ALIASES = {"crossfade": "fade", "cross_fade": "fade", "blend": "fade", "mix": "fade", "cross_dissolve": "dissolve",
                 "black": "fadeblack", "dip_to_black": "fadeblack", "white": "fadewhite", "dip_to_white": "fadewhite",
                 "wipe": "wipeleft", "slide": "slideleft", "push": "slideleft", "circle": "circleopen",
                 "wipe_left": "wipeleft", "wipe_right": "wiperight", "wipe_up": "wipeup", "wipe_down": "wipedown",
                 "slide_left": "slideleft", "slide_right": "slideright", "slide_up": "slideup",
                 "slide_down": "slidedown"}
BEAT_EFFECTS = ("flash", "zoom_pulse", "invert", "mirror", "grayscale", "blur", "sharpen", "shake", "contrast")
POSITIONS = ("top", "bottom", "center", "top_left", "top_right", "bottom_left", "bottom_right", "left", "right")
POS_ALIASES = {"middle": "center", "centre": "center", "mid": "center", "upper": "top", "lower": "bottom",
               "topleft": "top_left", "topright": "top_right", "bottomleft": "bottom_left",
               "bottomright": "bottom_right", "top_center": "top", "bottom_center": "bottom", "centered": "center"}


def _pos(default: str, *aliases: str) -> Param:
    return P("position", "pos", *aliases, default=default, choices=POSITIONS, ea=POS_ALIASES,
             doc="top, bottom, center, top_left ... or x,y")


SPECS: List[Spec] = [
    # ---------------------------------------------------------------- setup
    Spec("input", "setup", "Choose the video or audio file to edit.", 'input "my video.mp4"',
         (P("file", "file", "path", "video", "source", "src", "media", req=True),), ("file",), ("source", "open", "load")),
    Spec("output", "setup", "Choose the name of the finished file.", "output result.mp4",
         (P("file", "file", "path", "save_as", "name", "to_file", req=True),), ("file",), ("save", "save_to", "out_file")),
    Spec("preset", "setup", "Pick a ready-made output style (see 'editforge presets').", "preset shorts",
         (P("name", "str", "preset", "profile", "style", req=True),), ("name",), ("output_preset", "profile")),
    Spec("size", "setup", "Set the picture size and how the video fits into it.", "size 1080x1920 fit=blur",
         (P("size", "size", "resolution", "dimensions", "wh", req=True),
          P("fit", "enum", "mode", "fill", default="pad", choices=("pad", "crop", "stretch", "blur"),
            ea={"letterbox": "pad", "contain": "pad", "cover": "crop", "fill": "crop", "zoom": "crop",
                "scale": "stretch", "background_blur": "blur", "blurred": "blur"})),
         ("size", "fit"), ("resolution", "dimensions")),
    Spec("fps", "setup", "Set the frame rate of the finished video.", "fps 30",
         (P("fps", "num", "rate", "frame_rate", "framerate", req=True, lo=1, hi=240),), ("fps",), ("frame_rate", "framerate")),
    Spec("quality", "setup", "Set the picture quality: low, medium, high, max or a CRF number.", "quality high",
         (P("level", "quality", "crf", "value", req=True),), ("level",), ("crf",)),
    Spec("fast_cuts", "setup", "Cut on keyframes without re-encoding: very fast, but cuts are not frame-exact.",
         "fast_cuts on", (P("enabled", "bool", "on", "value", default=True),), ("enabled",),
         ("fast_cut", "keyframe_cuts", "copy_cuts")),
    # --------------------------------------------------------------- select
    Spec("keep", "select", "Keep only these parts of the video (everything else is dropped).", "keep 0:10-0:40, 1:00-1:30",
         (P("ranges", "ranges", "range", "ranges", "parts"),) + RANGE, ("ranges",), ("include", "only", "keep_range", "select")),
    Spec("cut", "select", "Remove these parts of the video.", "cut 0:20-0:25",
         (P("ranges", "ranges", "range", "parts"),) + RANGE, ("ranges",),
         ("remove", "delete", "drop", "exclude", "cut_out", "trim_out", "cut_range")),
    Spec("pattern_keep", "select", "Keep a short piece out of every cycle (for example 2 s out of every 10 s).",
         "pattern_keep 2 every 10",
         (P("take", "dur", "keep", "length", "duration", "on", "play", req=True),
          P("every", "dur", "cycle", "period", "interval", req=True),
          P("offset", "dur", "shift", "delay", "phase", default="0")) + RANGE,
         ("take",), ("keep_pattern", "pattern_include")),
    Spec("pattern_remove", "select", "Remove a short piece out of every cycle (for example 1 s out of every 5 s).",
         "pattern_remove 1 every 5",
         (P("take", "dur", "remove", "cut", "length", "duration", "off", req=True),
          P("every", "dur", "cycle", "period", "interval", req=True),
          P("offset", "dur", "shift", "delay", "phase", default="0")) + RANGE,
         ("take",), ("pattern_cut", "remove_pattern", "cut_pattern", "pattern_delete", "pattern_drop")),
    Spec("silence_remove", "select", "Cut out silent parts (with a little padding so speech is not clipped).",
         "silence_remove threshold=-35 min_silence=0.5 padding=0.1",
         (P("threshold", "db", "noise", "level", "db", "thresh", default=-35.0, lo=-90, hi=0),
          P("min_silence", "dur", "min", "min_duration", "minimum", "min_length", "duration", "longer_than", default="0.5"),
          P("padding", "dur", "pad", "keep_padding", "margin", default="0.1")) + RANGE,
         ("threshold",), ("silence_removal", "remove_silence", "cut_silence", "silence_cut", "silence_remover",
                          "trim_silence", "skip_silence", "silenceremove")),
    Spec("scene_cut", "select", "Split the video at scene changes (and optionally keep or drop chosen scenes).",
         "scene_cut threshold=0.3 keep=1,3-4",
         (P("threshold", "num", "score", "sensitivity", default=0.3, lo=0.01, hi=1.0),
          P("min_scene", "dur", "min", "min_length", "min_duration", default="0.8"),
          P("keep", "intlist", "keep_scenes", "only"),
          P("remove", "intlist", "cut", "drop", "skip", "remove_scenes")) + RANGE,
         (), ("scene_cuts", "split_scenes", "scene_split", "scenes")),
    Spec("scene_snap", "select", "Move cut points onto the nearest scene change.", "scene_snap tolerance=0.5",
         (P("tolerance", "dur", "within", "distance", "max_shift", default="0.5"),
          P("threshold", "num", "score", "sensitivity", default=0.3, lo=0.01, hi=1.0)), ("tolerance",),
         ("snap_to_scenes", "snap_scenes", "snap_cuts_to_scenes")),
    Spec("beat_cut", "select", "Cut the video on the beat of its own sound (or a fixed tempo).",
         "beat_cut every=4 take=0.5",
         (P("every", "int", "beats", "per", "n", default=4, lo=1, hi=64),
          P("take", "num", "keep", "fraction", "portion", default=1.0, lo=0.05, hi=1.0),
          P("source", "enum", "use", default="audio", choices=("audio", "bpm"), ea={"video": "audio", "sound": "audio", "tempo": "bpm"}),
          P("bpm", "num", "tempo", lo=30, hi=300),
          P("offset", "dur", "shift", "delay", default="0"),
          P("downbeats", "bool", "on_downbeats", "bars", default=False),
          START, END), ("every",), ("beat_sync", "beat_cuts", "cut_on_beats", "cut_to_beat", "beat_split")),
    Spec("beat_snap", "select", "Nudge cut points so they land on the beats of your background music.",
         "beat_snap tolerance=0.25",
         (P("tolerance", "dur", "within", "distance", default="0.25"),
          P("source", "enum", "use", default="music", choices=("music", "bpm"), ea={"tempo": "bpm", "song": "music"}),
          P("bpm", "num", "tempo", lo=30, hi=300),
          P("offset", "dur", "shift", "delay", default="0"),
          P("downbeats", "bool", "on_downbeats", "bars", default=False)), ("tolerance",),
         ("snap_to_beats", "snap_beats", "align_to_beats", "beat_align")),
    Spec("beat_effect", "select", "Trigger a short effect on every beat (or every downbeat).", "beat_effect flash on=downbeat",
         (P("effect", "enum", "name", "fx", "type", default="flash", choices=BEAT_EFFECTS,
            ea={"pulse": "zoom_pulse", "zoom": "zoom_pulse", "punch": "zoom_pulse", "negate": "invert",
                "gray": "grayscale", "grey": "grayscale", "white_flash": "flash", "bounce": "zoom_pulse"}),
          P("on", "enum", "trigger", "at", default="beat", choices=("beat", "downbeat"),
            ea={"beats": "beat", "downbeats": "downbeat", "bar": "downbeat", "bars": "downbeat"}),
          P("every", "int", "nth", default=1, lo=1, hi=64),
          P("length", "dur", "hold", "duration", "pulse_length", default="0.12"),
          P("intensity", "num", "strength", "amount", default=0.5, lo=0.05, hi=1.0),
          P("source", "enum", "use", default="audio", choices=("audio", "music", "bpm"),
            ea={"video": "audio", "sound": "audio", "song": "music", "tempo": "bpm"}),
          P("bpm", "num", "tempo", lo=30, hi=300),
          P("offset", "dur", "shift", "delay", default="0"), START, END), ("effect",),
         ("beat_fx", "on_beat", "beats_effect", "beat_flash")),
    # ---------------------------------------------------------------- clip
    Spec("speed", "clip", "Play faster (2) or slower (0.5).", "speed 2 from 10 to 20",
         (P("factor", "factor", "rate", "x", "multiplier", "speed", "amount", "times", req=True, lo=0.05, hi=50),
          P("pitch", "bool", "keep_pitch", "preserve_pitch", default=True),
          P("mute", "bool", "silent", "mute_audio", default=False)) + RANGE, ("factor",),
         ("speedup", "speed_up", "slowmo", "slow_motion", "slow_down", "slowdown"),
         presets={"slowmo": {"factor": "0.5"}, "slow_motion": {"factor": "0.5"}, "slow_down": {"factor": "0.5"},
                  "slowdown": {"factor": "0.5"}}),
    Spec("speed_ramp", "clip", "Gradually change speed from one value to another.", "speed_ramp 1 to 3 from 5 to 8",
         (P("from_speed", "factor", "start_speed", "speed_from", "initial", "s0", req=True, lo=0.05, hi=50),
          P("to_speed", "factor", "end_speed", "speed_to", "final", "s1", req=True, lo=0.05, hi=50),
          START, END, EASING,
          P("steps", "int", "pieces", default=0, lo=0, hi=200),
          P("mute", "bool", "silent", default=False)), ("from_speed", "to_speed"),
         ("ramp", "speedramp", "speed_curve", "ramp_speed")),
    Spec("reverse", "clip", "Play a part backwards.", "reverse from 5 to 8",
         (P("mute", "bool", "silent", default=False),) + RANGE, (), ("rewind", "backwards", "backward")),
    Spec("freeze", "clip", "Hold one frame still for a while.", "freeze at=4.5 duration=2",
         (P("at", "time", "time", "frame_at", "position", "when", req=True),
          P("duration", "dur", "for", "length", "hold", "dur", default="2")), ("at", "duration"),
         ("freeze_frame", "hold", "hold_frame", "pause")),
    Spec("zoom", "clip", "Slowly zoom in (or out) with smooth easing.", "zoom 1.5 from 5 to 10",
         (P("scale", "num", "zoom", "amount", "level", "factor", "end_scale", "to_scale", req=True, lo=0.5, hi=8),
          P("from_scale", "num", "start_scale", "scale_from", "initial", default=1.0, lo=0.5, hi=8),
          P("x", "num", "cx", "center_x", "focus_x", default=0.5, lo=0, hi=1),
          P("y", "num", "cy", "center_y", "focus_y", default=0.5, lo=0, hi=1), EASING) + RANGE,
         ("scale",), ("zoom_in", "punch_in", "camera_zoom")),
    Spec("pan", "clip", "Slide the view across the picture (zoomed in a little).", "pan from_x=0.2 to_x=0.8 scale=1.4",
         (P("from_x", "num", "x_from", "x0", default=0.2, lo=0, hi=1), P("to_x", "num", "x_to", "x1", default=0.8, lo=0, hi=1),
          P("from_y", "num", "y_from", "y0", default=0.5, lo=0, hi=1), P("to_y", "num", "y_to", "y1", default=0.5, lo=0, hi=1),
          P("scale", "num", "zoom", "level", default=1.3, lo=1.0, hi=8), EASING) + RANGE,
         (), ("camera_pan", "pan_across")),
    Spec("crop", "clip", "Cut the picture down to an aspect ratio (9:16, 1:1 ...) or a box.", "crop 9:16",
         (P("aspect", "aspect", "ratio", "shape"),
          P("box", "box", "rect", "area", doc="x,y,width,height in pixels (or with % signs)"),
          P("position", "enum", "anchor", "align", default="center",
            choices=("center", "left", "right", "top", "bottom"), ea={"middle": "center", "centre": "center"})) + RANGE,
         ("aspect",), ("crop_to", "trim_frame")),
    Spec("reframe", "clip", "Re-frame 16:9 video to 9:16 or 1:1, following faces when available.", "reframe 9:16 mode=face",
         (P("aspect", "aspect", "ratio", "shape", default="9:16"),
          P("mode", "enum", "follow", default="auto", choices=("auto", "center", "face", "speaker"),
            ea={"faces": "face", "centre": "center", "active_speaker": "speaker"}),
          P("smooth", "num", "smoothing", default=0.7, lo=0, hi=1)) + RANGE, ("aspect",),
         ("auto_reframe", "autoreframe", "smart_crop", "reframe_face")),
    Spec("color", "clip", "Adjust brightness, contrast, saturation, gamma and hue.", "color contrast=1.2 saturation=1.3",
         (P("brightness", "num", "light", default=0.0, lo=-1, hi=1), P("contrast", "num", default=1.0, lo=0, hi=4),
          P("saturation", "num", "sat", default=1.0, lo=0, hi=4), P("gamma", "num", default=1.0, lo=0.1, hi=10),
          P("hue", "num", "tint", default=0.0, lo=-180, hi=180)) + RANGE, (),
         ("colour", "color_adjust", "colour_adjust", "grade", "adjust", "color_grade")),
    Spec("effect", "clip", "Apply a look: mirror, hflip, flip, invert, grayscale, blur, sharpen, sepia, vignette.",
         "effect mirror from 5 to 10",
         (P("name", "enum", "effect", "type", "fx", req=True, choices=EFFECT_NAMES, ea=EFFECT_ALIASES),
          P("strength", "num", "amount", "level", default=0.5, lo=0, hi=1)) + RANGE, ("name",),
         ("fx", "filter", "video_effect", "look", "mirror", "hflip", "flip", "vflip", "invert", "negate", "grayscale",
          "greyscale", "blur", "sharpen", "sepia", "vignette"),
         presets={k: {"name": k} for k in ("mirror", "hflip", "flip", "vflip", "invert", "grayscale", "blur", "sharpen",
                                           "sepia", "vignette")} | {"negate": {"name": "invert"}, "greyscale": {"name": "grayscale"}}),
    Spec("every_nth", "clip", "Apply an effect to every 2nd, 3rd ... segment.", "every_nth 2 effect=mirror",
         (P("n", "int", "every", "step", "nth", req=True, lo=1, hi=1000),
          P("effect", "enum", "name", "fx", "type", req=True, choices=EFFECT_NAMES, ea=EFFECT_ALIASES),
          P("strength", "num", "amount", "level", default=0.5, lo=0, hi=1),
          P("offset", "int", "first", "skip", "start_at", default=0, lo=0, hi=999)), ("n",),
         ("every_other", "nth", "every_n", "alternate")),
    # ----------------------------------------------------------- transitions
    Spec("transition", "transition", "Blend between clips (crossfade, wipe, slide ...).", "transition fade 0.5",
         (P("type", "enum", "style", "kind", "effect", default="fade", choices=XFADE, ea=XFADE_ALIASES),
          P("duration", "dur", "dur", "length", "for", "seconds", default="0.5"),
          P("at", "time", "when", "near", "around"),
          P("every", "int", "nth", default=1, lo=1, hi=1000),
          P("contiguous", "bool", "include_contiguous", "force", default=False)), ("type", "duration"),
         ("crossfade", "xfade", "join", "transitions", "dissolve", "wipe"),
         presets={"crossfade": {"type": "fade", "_positional": ["duration"]},
                  "dissolve": {"type": "dissolve", "_positional": ["duration"]},
                  "wipe": {"type": "wipeleft", "_positional": ["duration"]}}),
    # ----------------------------------------------------------------- audio
    Spec("loudnorm", "audio", "Make loudness match a standard (two-pass EBU R128) with a clipping guard.", "loudnorm -16",
         (P("target", "db", "i", "lufs", "level", "loudness", default=-16.0, lo=-70, hi=-5),
          P("true_peak", "db", "tp", "peak", "ceiling", default=-1.5, lo=-9, hi=0),
          P("lra", "num", "range", default=11.0, lo=1, hi=50),
          P("enabled", "bool", "on", default=True)), ("target",),
         ("normalize", "normalise", "loudness", "normalize_audio", "normalise_audio", "loudness_normalize", "ebu_r128", "r128")),
    Spec("music", "audio", "Add background music (it gets quieter when someone talks).", 'music "song.mp3" volume=0.2',
         (P("file", "file", "path", "track", "song", req=True),
          P("volume", "gain", "level", "gain", "vol", default=0.25),
          P("duck", "bool", "ducking", "auto_duck", default=True),
          P("start", "time", "at", "from", "begin", default="0"),
          P("end", "time", "to", "until", "stop"),
          P("loop", "bool", "repeat", default=True),
          P("fade_in", "dur", "fadein", "in_fade", default="1"),
          P("fade_out", "dur", "fadeout", "out_fade", default="2")), ("file",),
         ("background_music", "bgm", "add_music", "soundtrack")),
    Spec("fade", "audio", "Fade the picture and sound in at the start and out at the end.", "fade in=1 out=2",
         (P("fade_in", "dur", "in", "fadein", "start_fade", default="0"),
          P("fade_out", "dur", "out", "fadeout", "end_fade", default="0"),
          P("audio", "bool", "sound", default=True), P("video", "bool", "picture", default=True),
          P("color", "enum", "colour", default="black", choices=("black", "white"))), ("fade_in", "fade_out"),
         ("fades", "fade_in_out", "fade_in", "fade_out"),
         presets={"fade_in": {"_positional": ["fade_in"]}, "fade_out": {"_positional": ["fade_out"]}}),
    Spec("denoise", "audio", "Reduce background noise (free FFmpeg filters).", "denoise 12",
         (P("amount", "num", "strength", "level", "nr", "db", default=12.0, lo=1, hi=60),
          P("highpass", "bool", "rumble", "hp", default=True), P("enabled", "bool", "on", default=True)),
         ("amount",), ("noise_reduction", "noise_reduce", "reduce_noise", "clean_audio", "remove_noise", "noise_removal")),
    Spec("volume", "audio", "Make the sound louder or quieter (2, 0.5, -6dB, 150%).", "volume -6dB",
         (P("gain", "gain", "level", "amount", req=True),) + RANGE, ("gain",), ("gain", "audio_volume")),
    Spec("mute", "audio", "Silence the sound (whole video, or a range).", "mute from 3 to 5", RANGE, (), ("silence_audio", "mute_audio")),
    # -------------------------------------------------------------- overlays
    Spec("text", "overlay", "Put text on the picture.", 'text "Hello" start=00:00:01 end=00:00:04 position=bottom',
         (P("text", "str", "message", "words", "label", req=True),
          P("start", "time", "from", "begin", "at", "in", default="0"), P("end", "time", "to", "until", "stop", "out"),
          P("duration", "dur", "for", "length", "dur"),
          P("position", "pos", "pos", "align", "placement", default="bottom", choices=POSITIONS, ea=POS_ALIASES),
          P("x", "str", "left_px"), P("y", "str", "top_px"),
          P("size", "num", "font_size", "fontsize", default=48.0, lo=6, hi=600),
          P("color", "color", "colour", "font_color", "fill", default="white"),
          P("font", "str", "font_file", "typeface", "fontfile"),
          P("box", "bool", "background", "bg", default=False),
          P("box_color", "color", "box_colour", "bg_color", default="black@0.5"),
          P("box_padding", "num", "padding", "pad", default=12.0, lo=0, hi=200),
          P("border", "num", "outline", "stroke", default=0.0, lo=0, hi=40),
          P("border_color", "color", "outline_color", "stroke_color", default="black"),
          P("shadow", "bool", default=False), P("fade", "dur", "fade_in_out", default="0"),
          P("time", "enum", "timeline", "clock", default="output", choices=("output", "source"),
            ea={"final": "output", "result": "output", "original": "source"})), ("text",),
         ("overlay_text", "caption_text", "title", "add_text", "label", "drawtext"), greedy="text"),
    Spec("image", "overlay", "Put a picture or logo on the video.", 'image "logo.png" position=top_right scale=0.15',
         (P("file", "file", "path", "logo", "picture", req=True),
          _pos("top_right"),
          P("x", "str"), P("y", "str"),
          P("scale", "num", "size", "width_fraction", default=0.15, lo=0.01, hi=1.0),
          P("width", "num", "px", lo=4, hi=8000),
          P("opacity", "num", "alpha", default=1.0, lo=0, hi=1),
          P("margin", "num", "gap", default=24.0, lo=0, hi=500),
          P("start", "time", "from", "begin", "at", "in", default="0"), P("end", "time", "to", "until", "stop", "out"),
          P("duration", "dur", "for", "length", "dur"),
          P("time", "enum", "timeline", "clock", default="output", choices=("output", "source"),
            ea={"final": "output", "result": "output", "original": "source"})), ("file",),
         ("logo", "overlay_image", "watermark", "add_image", "add_logo", "picture")),
    Spec("captions", "overlay", "Make captions with local Whisper (or use your own transcript) and add them.",
         "captions style=karaoke mode=burn",
         (P("mode", "enum", "how", "where", "embed", default="burn", choices=("burn", "soft", "both", "files"),
            ea={"hard": "burn", "burned": "burn", "burn_in": "burn", "hardcode": "burn", "track": "soft",
                "soft_subtitles": "soft", "sidecar": "files", "srt": "files"}),
          P("style", "enum", "look", default="karaoke", choices=("karaoke", "plain", "boxed")),
          P("language", "str", "lang", default="auto"),
          P("model", "str", "whisper_model", default="base"),
          P("transcript", "file", "from_file", "srt", "subtitles_file", "import", "words"),
          P("words_per_line", "int", "max_words", default=6, lo=1, hi=30),
          P("max_chars", "int", "chars", default=32, lo=8, hi=100),
          P("font_size", "num", "size", default=56.0, lo=8, hi=400),
          P("color", "color", "colour", default="white"),
          P("highlight", "color", "highlight_color", "active_color", default="yellow"),
          P("outline", "color", "outline_color", default="black"),
          _pos("bottom", "pos", "align"),
          P("margin", "num", default=60.0, lo=0, hi=800),
          P("font", "str", "font_file", "typeface"),
          P("files", "bool", "write_files", "sidecar", default=True),
          P("device", "enum", default="auto", choices=("auto", "cpu", "cuda"))), (),
         ("caption", "subtitles", "subtitle", "auto_captions", "autocaption", "auto_caption", "transcribe", "whisper",
          "add_captions")),
]

SETTINGS_NAMES = ("input", "output", "preset", "size", "fps", "quality", "fast_cuts")

_BY_NAME: Dict[str, Spec] = {s.name: s for s in SPECS}
_ALIAS: Dict[str, Tuple[str, Optional[str]]] = {}
for _s in SPECS:
    for _a in _s.aliases:
        # An alias never overrides a real instruction name and the first spec wins.
        if norm(_a) not in _BY_NAME:
            _ALIAS.setdefault(norm(_a), (_s.name, norm(_a)))


def all_names() -> List[str]:
    """Every accepted instruction word (names and aliases)."""
    return sorted(set(_BY_NAME) | set(_ALIAS))


def canonical_names() -> List[str]:
    return [s.name for s in SPECS]


def resolve_instruction(word: str) -> Tuple[Optional[Spec], Optional[str]]:
    """Return (spec, alias_used) for a written instruction name, or (None, None)."""
    w = norm(word)
    if w in _BY_NAME:
        return _BY_NAME[w], None
    if w in _ALIAS:
        name, alias = _ALIAS[w]
        return _BY_NAME[name], alias
    return None, None


def suggest(word: str, choices: List[str], n: int = 3) -> List[str]:
    """Close matches for a mistyped word."""
    return difflib.get_close_matches(norm(word), choices, n=n, cutoff=0.6)


def suggest_instruction(word: str) -> List[str]:
    """Close instruction names, shown as canonical names."""
    out: List[str] = []
    for m in suggest(word, all_names()):
        spec, _ = resolve_instruction(m)
        if spec and spec.name not in out:
            out.append(spec.name)
    return out


def spec_for(name: str) -> Spec:
    return _BY_NAME[name]
