"""Speech transcripts: import your own (SRT / VTT / JSON) or make one with local Whisper.

Automatic transcription uses the free ``faster-whisper`` add-on if it is installed. It runs on your
own computer. The first time a model name such as ``base`` is used, faster-whisper downloads the
model file once (about 150 MB for ``base``); after that it works offline. You can also point
``model=`` at a folder that already holds a model.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..core.errors import Cancelled, FeatureUnavailable, MediaError, ScriptError
from ..core.timecode import parse_time_value


@dataclass
class Word:
    """One spoken word with its time in the ORIGINAL (source) video, in seconds."""

    text: str
    start: float
    end: float

    def to_list(self) -> list:
        return [self.text, round(self.start, 3), round(self.end, 3)]


def whisper_available() -> bool:
    """True if faster-whisper can be imported."""
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:
        return False


_TIME_LINE = re.compile(r"(\d+:\d{2}:\d{2}[.,]\d{1,3}|\d{1,2}:\d{2}[.,]\d{1,3})\s*-->\s*(\d+:\d{2}:\d{2}[.,]\d{1,3}|\d{1,2}:\d{2}[.,]\d{1,3})")


def _t(text: str) -> float:
    return parse_time_value(text.replace(",", ".")).to_seconds()


def parse_cues(text: str) -> List[Tuple[float, float, str]]:
    """Read SRT or WebVTT text into (start, end, text) cues."""
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    cues: List[Tuple[float, float, str]] = []
    blocks = re.split(r"\n\s*\n", text)
    for block in blocks:
        lines = [ln for ln in block.split("\n") if ln.strip() != ""]
        for i, ln in enumerate(lines):
            m = _TIME_LINE.search(ln)
            if m:
                body = " ".join(re.sub(r"<[^>]+>|\{[^}]*\}", "", x).strip() for x in lines[i + 1:]).strip()
                if body:
                    cues.append((_t(m.group(1)), _t(m.group(2)), body))
                break
    return cues


def cues_to_words(cues: List[Tuple[float, float, str]]) -> List[Word]:
    """Spread each cue's time over its words (longer words get more time)."""
    words: List[Word] = []
    for start, end, body in cues:
        parts = body.split()
        if not parts or end <= start:
            continue
        weights = [max(1, len(p)) for p in parts]
        total = float(sum(weights))
        t = start
        for p, w in zip(parts, weights):
            d = (end - start) * w / total
            words.append(Word(p, round(t, 3), round(t + d, 3)))
            t += d
    return words


def _words_from_json(data: Any) -> List[Word]:
    words: List[Word] = []

    def add(item: Dict[str, Any]) -> None:
        text = str(item.get("text", item.get("word", ""))).strip()
        if text and "start" in item and "end" in item:
            words.append(Word(text, float(item["start"]), float(item["end"])))

    if isinstance(data, dict):
        if isinstance(data.get("words"), list):
            for w in data["words"]:
                add(w) if isinstance(w, dict) else None
        for seg in data.get("segments", []) or []:
            if isinstance(seg, dict) and isinstance(seg.get("words"), list) and seg["words"]:
                for w in seg["words"]:
                    add(w)
            elif isinstance(seg, dict) and "start" in seg and "end" in seg:
                words.extend(cues_to_words([(float(seg["start"]), float(seg["end"]), str(seg.get("text", "")))]))
    elif isinstance(data, list):
        for w in data:
            if isinstance(w, dict):
                add(w)
            elif isinstance(w, (list, tuple)) and len(w) == 3:
                words.append(Word(str(w[0]), float(w[1]), float(w[2])))
    return words


def import_transcript(path: str) -> List[Word]:
    """Read a transcript file (.srt, .vtt or .json) into words with times."""
    p = Path(path)
    if not p.exists():
        raise ScriptError(f"I can't find the transcript file '{path}'.", "Check the name and folder.")
    try:
        text = p.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        raise ScriptError(f"'{path}' is not a text file I can read.", "Save it as plain text (UTF-8).")
    ext = p.suffix.lower()
    if ext == ".json" or text.lstrip().startswith(("{", "[")):
        try:
            words = _words_from_json(json.loads(text))
        except ValueError:
            raise ScriptError(f"The transcript file '{path}' is not valid JSON.", "Check the file, or use an .srt file.")
    else:
        words = cues_to_words(parse_cues(text))
    if not words:
        raise ScriptError(f"I found no timed words in '{path}'.",
                          "Use an .srt or .vtt file with time lines like 00:00:01,000 --> 00:00:03,000, or a JSON file with words and times.")
    return sorted(words, key=lambda w: w.start)


def transcribe(path: str, *, language: str = "auto", model: str = "base", device: str = "auto",
               cancel: Optional[threading.Event] = None) -> Tuple[List[Word], str]:
    """Transcribe with faster-whisper (word-level timing). Returns (words, language)."""
    if not whisper_available():
        raise FeatureUnavailable(
            "Automatic captions are switched off because the free 'faster-whisper' add-on is not installed.",
            "Install it with:  pip install faster-whisper   -- or give your own transcript: captions transcript=my.srt")
    from faster_whisper import WhisperModel  # type: ignore
    dev = "cpu" if device in ("auto", "cpu") else device
    if device == "auto":
        try:
            import ctranslate2  # type: ignore
            if ctranslate2.get_cuda_device_count() > 0:
                dev = "cuda"
        except Exception:
            dev = "cpu"
    try:
        wm = WhisperModel(model, device=dev, compute_type="float16" if dev == "cuda" else "int8")
    except Exception as exc:
        raise MediaError(f"Whisper could not load the model '{model}' ({exc}).",
                         "The first use of a model name downloads it once, which needs internet. "
                         "Connect once and try again, or give model=<folder that holds a downloaded model>.")
    segments, info = wm.transcribe(path, language=None if language == "auto" else language, word_timestamps=True,
                                   vad_filter=True)
    words: List[Word] = []
    for seg in segments:
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        for w in (seg.words or []):
            text = (w.word or "").strip()
            if text:
                words.append(Word(text, float(w.start), float(w.end)))
    return words, getattr(info, "language", language)
