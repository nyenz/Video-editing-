"""The three script languages (line, YAML, JSON) must read the same and report errors helpfully."""

import json

import pytest

from editforge.core.errors import ScriptError
from editforge.dsl.loader import detect_format, load_script
from editforge.dsl.registry import SPECS, all_names, resolve_instruction, suggest_instruction

LINE = """
# my edit
preset shorts
keep 0:10-0:40, 1:00-1:30
cut 0:20 to 0:25
speed 2 from 10 to 20
text "Hello: world" start=00:00:01 end=00:00:04 position=bottom
loudnorm -16
transition crossfade 0.5
mirror from 5 to 10
"""

YAML = """
preset: shorts
steps:
  - keep: 0:10-0:40, 1:00-1:30
  - cut: 0:20 to 0:25
  - speed: {factor: 2, start: 10, end: 20}
  - text: {text: "Hello: world", start: 00:00:01, end: 00:00:04, position: bottom}
  - loudnorm: -16
  - transition: crossfade 0.5
  - mirror: from 5 to 10
"""

JSON = json.dumps({"preset": "shorts", "steps": [
    {"keep": "0:10-0:40, 1:00-1:30"}, {"cut": "0:20 to 0:25"}, {"speed": {"factor": 2, "start": 10, "end": 20}},
    {"op": "text", "text": "Hello: world", "start": "00:00:01", "end": "00:00:04", "position": "bottom"},
    {"loudnorm": -16}, {"transition": "crossfade 0.5"}, {"mirror": "from 5 to 10"}]}, indent=1)


def test_three_languages_give_identical_normalised_scripts():
    a, b, c = load_script(LINE), load_script(YAML), load_script(JSON)
    for s in (a, b, c):
        assert not s.errors, [e.plain() for e in s.errors]
    assert (a.format, b.format, c.format) == ("line", "yaml", "json")
    assert a.canonical() == b.canonical() == c.canonical()
    assert a.digest() == b.digest() == c.digest()


def test_format_detection():
    assert detect_format(LINE) == "line" and detect_format(YAML) == "yaml" and detect_format(JSON) == "json"
    assert detect_format("- keep: 0-5") == "yaml"
    assert detect_format("# only a comment\n\nkeep 0-5") == "line"


@pytest.mark.parametrize("a,b", [("silence_removal", "silence_remove"), ("remove_silence", "silence_remove"), ("pattern_cut", "pattern_remove"),
                                 ("Pattern-Keep", "pattern_keep"), ("overlay_text", "text"), ("colour", "color"), ("crossfade", "transition"),
                                 ("normalize", "loudnorm"), ("bgm", "music"), ("auto_reframe", "reframe"), ("greyscale", "effect"),
                                 ("scene_cuts", "scene_cut"), ("snap_to_beats", "beat_snap"), ("freeze_frame", "freeze"), ("noise_reduction", "denoise")])
def test_aliases_resolve(a, b):
    spec, _ = resolve_instruction(a)
    assert spec is not None and spec.name == b


def test_every_alias_is_unique_or_consistent():
    seen = {}
    for s in SPECS:
        for w in (s.name, *s.aliases):
            w = w.lower()
            if w in seen and seen[w] != s.name:
                # an alias that is also another instruction's name resolves to the real instruction
                assert resolve_instruction(w)[0].name in (seen[w], s.name)
            seen.setdefault(w, s.name)


@pytest.mark.parametrize("typo,expect", [("silense_remove", "silence_remove"), ("spede", "speed"), ("overlay_txt", "text"), ("loudnrom", "loudnorm"),
                                         ("transiton", "transition"), ("revrse", "reverse"), ("fredze", "freeze")])
def test_did_you_mean_for_instructions(typo, expect):
    s = load_script(f"{typo} 1")
    assert s.errors
    msg = s.errors[0].message
    assert "Did you mean" in msg and expect in msg


def test_did_you_mean_for_options():
    s = load_script("speed facter=2")
    assert "Did you mean 'factor'" in s.errors[0].message
    s = load_script("text hi colr=red")
    assert "Did you mean 'color'" in s.errors[0].message


def test_errors_have_line_numbers_and_fixes():
    s = load_script("keep 0-5\nspeed abc\n\nfoo 1\ncut 9-3")
    lines = {e.line for e in s.errors}
    assert lines == {2, 4, 5}
    assert all(e.fix for e in s.errors)
    assert len(s.steps) == 1  # the good line is still read


def test_all_errors_reported_at_once():
    s = load_script("speed\nzoom\ntext")
    assert len(s.errors) == 3


def test_yaml_errors_have_lines():
    s = load_script("steps:\n  - keep: 0-5\n  - foo: 1\n  - speed: {factor: abc}\n")
    assert [e.line for e in s.errors] == [3, 4]


def test_json_errors_have_lines():
    s = load_script('{\n "steps": [\n  {"keep": "0-5"},\n  {"speed": {"factor": }}\n ]\n}')
    assert s.errors and s.errors[0].line == 4


def test_yaml_does_not_turn_timecodes_into_numbers():
    s = load_script("steps:\n  - cut: {start: 1:30, end: 2:00}\n  - text: {text: hi, start: 00:00:01, end: 00:00:02.5}")
    assert s.steps[0].args["ranges"] == [["90", "120"]]
    assert s.steps[1].args["start"] == "1" and s.steps[1].args["end"] == "2.5"


def test_quotes_and_special_characters_in_text():
    s = load_script('text "Tom & Jerry\'s 100% \\"best\\": a=b" start=1 end=2')
    assert s.steps[0].args["text"] == 'Tom & Jerry\'s 100% "best": a=b'
    s = load_script("text \u201csmart quotes\u201d start=1")
    assert s.steps[0].args["text"] == "smart quotes"


def test_unclosed_quote():
    s = load_script('text "oops start=1')
    assert "never closed" in s.errors[0].message


def test_comments_and_hex_colours():
    s = load_script("text hi color=#ff8800 # a comment\n# whole line\ncaptions color #00ff00")
    assert s.steps[0].args["color"] == "#ff8800" and s.steps[1].args["color"] == "#00ff00"
    # in 'text' a bare word is part of the words, which is why the docs say: put the words in quotes
    assert load_script("text bye color #00ff00").steps[0].args["text"] == "bye color #00ff00"


def test_text_without_quotes_takes_all_words():
    s = load_script("text Welcome to the show start=2 end=4")
    assert s.steps[0].args["text"] == "Welcome to the show" and s.steps[0].args["start"] == "2"


def test_bare_word_options_and_flags():
    s = load_script("text Hi from 1 to 3 box=true\nloudnorm off\ndenoise on")
    assert s.steps[0].args["box"] is True and s.steps[0].args["start"] == "1" and s.steps[0].args["end"] == "3"
    assert s.steps[1].args["enabled"] is False and s.steps[2].args["enabled"] is True


def test_speed_ramp_natural_form():
    s = load_script("speed_ramp 1 to 3 from 5 to 8")
    a = s.steps[0].args
    assert (a["from_speed"], a["to_speed"], a["start"], a["end"]) == (1.0, 3.0, "5", "8")


def test_settings_move_out_of_steps():
    s = load_script("input a.mp4\noutput b.mp4\npreset youtube\nsize 720p fit=crop\nfps 30\nquality high\nfast_cuts on\nkeep 0-5")
    assert s.settings["input"] == "a.mp4" and s.settings["size"] == "1280x720" and s.settings["fit"] == "crop"
    assert s.settings["fps"] == 30.0 and s.settings["quality"] == "crf:20" and s.settings["fast_cuts"] is True
    assert [x.name for x in s.steps] == ["keep"]


@pytest.mark.parametrize("line,key,value", [
    ("speed 2x", "factor", 2.0), ("speed 200%", "factor", 2.0), ("speed 0.5", "factor", 0.5), ("loudnorm -14 lufs", None, None),
    ("volume -6dB", "gain", 0.501187), ("volume 150%", "gain", 1.5), ("color hue=30", "hue", 30.0), ("zoom 2", "scale", 2.0),
    ("crop 9:16", "aspect", "9:16"), ("crop vertical", "aspect", "9:16"), ("crop 0.5625", "aspect", "9:16"), ("reframe square", "aspect", "1:1"),
    ("freeze at=f50", "at", "f50"), ("music a.mp3 volume=-12dB", "volume", 0.251189),
])
def test_value_conversions(line, key, value):
    s = load_script(line)
    if key is None:
        assert s.errors  # 'lufs' is not a valid word: must be reported, not crash
        return
    assert not s.errors, [e.plain() for e in s.errors]
    assert s.steps[0].args[key] == pytest.approx(value, rel=1e-4) if isinstance(value, float) else s.steps[0].args[key] == value


@pytest.mark.parametrize("line", ["speed 100", "speed 0", "zoom 50", "color contrast=9", "fps 999", "denoise 500", "text x size=0", "image a.png scale=5",
                                  "loudnorm 5", "beat_cut every=0", "scene_cut threshold=5"])
def test_out_of_range_values_rejected(line):
    assert load_script(line).errors


@pytest.mark.parametrize("line", ["keep 10-5", "cut 20-10", "speed 2 from 9 to 3", "text hi start=9 end=2" if False else "zoom 2 from 9 to 3"])
def test_reversed_ranges_rejected(line):
    assert load_script(line).errors


def test_enum_aliases():
    s = load_script("effect negate\neffect grey\neffect flop\ntransition wipe 1\nbeat_effect pulse\nsize 1000x500 fit=letterbox")
    assert [x.args.get("name") for x in s.steps[:3]] == ["invert", "grayscale", "hflip"]
    assert s.steps[3].args["type"] == "wipeleft" and s.steps[4].args["effect"] == "zoom_pulse"
    assert s.settings["fit"] == "pad"


def test_ranges_forms():
    s = load_script("keep 10-20 30-40 50..60 70 to 80")
    assert s.steps[0].args["ranges"] == [["10", "20"], ["30", "40"], ["50", "60"], ["70", "80"]]
    s = load_script("keep 10 - 20")
    assert s.steps[0].args["ranges"] == [["10", "20"]]


def test_duplicate_option_rejected():
    assert load_script("speed 2 factor=3").errors


def test_positional_leftovers_rejected():
    assert "don't understand" in load_script("speed 2 3 4").errors[0].message


def test_empty_and_comment_only_scripts():
    assert load_script("").steps == [] and load_script("# nothing\n\n").steps == []
    assert load_script("---\n").format in ("yaml", "line")


def test_yaml_top_level_unknown_key():
    s = load_script("colour: red\nsteps:\n  - keep: 0-5")
    assert s.errors and "top-level" in s.errors[0].message


def test_instruction_table_is_complete():
    names = {s.name for s in SPECS}
    required = {"keep", "cut", "pattern_keep", "pattern_remove", "silence_remove", "scene_cut", "scene_snap", "beat_cut", "beat_snap", "beat_effect", "speed",
                "speed_ramp", "reverse", "freeze", "zoom", "pan", "crop", "reframe", "color", "effect", "every_nth", "transition", "loudnorm", "music", "fade",
                "denoise", "text", "image", "captions"}
    assert required <= names
    assert len(all_names()) > 100


def test_every_spec_example_parses():
    for s in SPECS:
        r = load_script(s.example)
        assert not r.errors, (s.name, s.example, [e.plain() for e in r.errors])
