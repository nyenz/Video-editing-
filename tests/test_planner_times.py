"""Every time format must work on every instruction that takes a time (regression for failure 4)."""

import pytest

from helpers import plan_only

# 2 s and 4 s written in eight different ways (the test video runs at 25 frames per second)
FORMATS = [("2", "4"), ("2s", "4s"), ("0:02", "0:04"), ("00:00:02", "00:00:04"), ("00:00:02.000", "00:00:04.000"),
           ("00:00:02:00", "00:00:04:00"), ("f50", "f100"), ("2000ms", "4000ms")]

TEMPLATES = {
    "keep": "keep {a}-{b}",
    "keep_to": "keep from {a} to {b}",
    "cut": "cut {a} to {b}",
    "speed": "speed 2 from {a} to {b}",
    "speed_ramp": "speed_ramp 1 to 2 from {a} to {b}",
    "reverse": "reverse from {a} to {b}",
    "zoom": "zoom 1.5 from {a} to {b}",
    "pan": "pan from_x=0.2 to_x=0.8 from {a} to {b}",
    "crop": "crop 1:1 from {a} to {b}",
    "color": "color contrast=1.2 from {a} to {b}",
    "effect": "effect mirror from {a} to {b}",
    "mute": "mute from {a} to {b}",
    "volume": "volume 0.5 from {a} to {b}",
    "text": 'text "Hi" start={a} end={b}',
    "text_duration": 'text "Hi" start={a} duration={b}',
    "image": "image logo.png start={a} end={b}",
    "freeze": "freeze at={a} duration={b}",
    "pattern_keep": "pattern_keep 1 every 2 start={a} end={b}",
    "pattern_remove": "pattern_remove 0.5 every 2 start={a} end={b}",
    "transition": "keep 0-2, 3-5\ntransition fade {a}",
    "fade": "fade in={a} out={b}",
    "music": "music tone.wav start={a} end={b}",
    "beat_cut": "beat_cut source=bpm bpm=120 every=2 start={a} end={b}",
    "scene_cut": "scene_cut start={a} end={b}",
    "silence_remove": "silence_remove start={a} end={b}",
    "reframe": "reframe 9:16 mode=center from {a} to {b}",
    "beat_effect": "beat_effect flash source=bpm bpm=120 from {a} to {b}",
}


def _digest(script, media):
    return plan_only(script, media["speech"]).digest


@pytest.mark.parametrize("fmt", range(len(FORMATS)), ids=[f[0] for f in FORMATS])
@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_every_time_format_on_every_instruction(media, name, fmt):
    import shutil
    shutil.copy(media["tone_wav"], media["dir"] / "tone.wav") if not (media["dir"] / "tone.wav").exists() else None
    tpl = TEMPLATES[name]
    base = _digest(tpl.format(a="2", b="4"), media)
    a, b = FORMATS[fmt]
    assert _digest(tpl.format(a=a, b=b), media) == base


def test_time_formats_in_all_three_languages(media):
    line = _digest("cut 2 to 4\ntext Hi start=1 end=3", media)
    yaml = _digest("steps:\n  - cut: {start: 00:00:02, end: 00:00:04}\n  - text: {text: Hi, start: 00:00:01, end: f75}", media)
    js = _digest('{"steps":[{"cut":{"start":"0:02","end":"4s"}},{"text":{"text":"Hi","start":1,"end":"3000ms"}}]}', media)
    assert line == yaml == js
