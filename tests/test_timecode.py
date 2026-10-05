"""Every time format must be accepted everywhere (regression for failure 4)."""

import pytest

from editforge.core.errors import ScriptError
from editforge.core.timecode import (format_ass, format_srt, format_time, looks_like_time, parse_range, parse_time, parse_time_value)

FPS = 25

GOOD = [
    (12.5, 12.5), (12, 12.0), ("12.5", 12.5), ("12", 12.0), ("12.5s", 12.5), ("12s", 12.0), ("12.5S", 12.5), ("250ms", 0.25),
    ("1.5m", 90.0), ("2m30s", 150.0), ("1h2m3.5s", 3723.5), ("1h", 3600.0), ("2min", 120.0), ("0:30", 30.0), ("1:30", 90.0),
    ("01:30", 90.0), ("1:30.5", 90.5), ("90:00", 5400.0), ("00:00:01", 1.0), ("00:01:30", 90.0), ("01:02:03", 3723.0),
    ("00:00:01.500", 1.5), ("00:00:01,500", 1.5), ("1:02:03.250", 3723.25), ("f120", 4.8), ("F120", 4.8), ("120f", 4.8),
    ("f0", 0.0), ("00:00:01:12", 1.48), ("0:0:0:0", 0.0), (".5", 0.5), ("0", 0.0),
]


@pytest.mark.parametrize("text,expected", GOOD)
def test_time_formats(text, expected):
    assert parse_time(text, FPS) == pytest.approx(expected, abs=1e-6)


@pytest.mark.parametrize("a,b", [("12.5", "12.5s"), ("90", "1:30"), ("90", "00:01:30"), ("1.5", "00:00:01.500"), ("1.52", "00:00:01:13"),
                                 ("4.8", "f120"), ("0.25", "250ms")])
def test_equivalent_spellings_have_one_canonical_form(a, b):
    assert parse_time_value(a).to_seconds(FPS) == pytest.approx(parse_time_value(b).to_seconds(FPS))


def test_canonical_text_is_identical_for_equal_seconds():
    assert parse_time_value("90").canon == parse_time_value("1:30").canon == parse_time_value("00:01:30.000").canon == "90"


@pytest.mark.parametrize("bad", ["abc", "", "  ", "1:2:3:4:5", "1:75", "00:61:00", "12.5.5", "--3", "s12", "12x", "1::2", "f", "fx12", None, [1], True])
def test_bad_times_raise_plain_errors(bad):
    with pytest.raises(ScriptError) as e:
        parse_time(bad, FPS)
    assert e.value.fix  # always says how to fix


@pytest.mark.parametrize("neg", ["-5", -5, "-0:30"])
def test_negative_times_refused(neg):
    with pytest.raises(ScriptError):
        parse_time(neg)


def test_frames_need_frame_rate():
    with pytest.raises(ScriptError) as e:
        parse_time("f120")
    assert "frame rate" in e.value.message
    with pytest.raises(ScriptError):
        parse_time("00:00:01:12")


@pytest.mark.parametrize("text", ["10-20", "0:10-0:20", "00:00:10..00:00:20", "10 to 20", "10s-20s", "10->20", "f250-f500", "00:00:10,500-00:00:20,250"])
def test_range_formats(text):
    a, b = parse_range(text)
    assert a is not None and b is not None and looks_like_time(a.canon) and looks_like_time(b.canon)


def test_range_values():
    a, b = parse_range("0:10-0:20")
    assert (a.seconds, b.seconds) == (10.0, 20.0)
    a, b = parse_range("10-")
    assert a.seconds == 10.0 and b is None
    a, b = parse_range("00:00:01,500-00:00:02,500")
    assert (a.seconds, b.seconds) == (1.5, 2.5)


def test_bad_range():
    with pytest.raises(ScriptError):
        parse_range("hello")


def test_formatters():
    assert format_time(3723.5) == "01:02:03.500"
    assert format_time(0.0004) == "00:00:00.000"
    assert format_srt(1.5) == "00:00:01,500"
    assert format_ass(3723.456) == "1:02:03.46"
    assert format_ass(0) == "0:00:00.00"
