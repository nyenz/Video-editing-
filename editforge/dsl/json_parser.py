"""A small JSON reader that remembers line numbers (the standard library's does not)."""

from __future__ import annotations

from typing import List, Optional, Tuple

from ..core.errors import ScriptError
from .nodes import Map, Node, Scalar, Seq


class _Reader:
    def __init__(self, text: str):
        self.s = text
        self.i = 0
        self.line = 1

    def fail(self, message: str) -> ScriptError:
        return ScriptError(f"The JSON is not valid here: {message}.",
                           "Check for a missing comma, quote or bracket on this line or the one above.", self.line)

    def ws(self) -> None:
        while self.i < len(self.s) and self.s[self.i] in " \t\r\n":
            if self.s[self.i] == "\n":
                self.line += 1
            self.i += 1

    def parse(self) -> Optional[Node]:
        self.ws()
        if self.i >= len(self.s):
            return None
        node = self.value()
        self.ws()
        if self.i < len(self.s):
            raise self.fail("there is extra text after the end")
        return node

    def value(self) -> Node:
        self.ws()
        if self.i >= len(self.s):
            raise self.fail("the text ends too early")
        ch = self.s[self.i]
        line = self.line
        if ch == "{":
            return self.obj()
        if ch == "[":
            return self.arr()
        if ch == '"':
            return Scalar(self.string(), line, True)
        for word, val in (("true", "true"), ("false", "false"), ("null", None)):
            if self.s.startswith(word, self.i):
                self.i += len(word)
                return Scalar(val, line, False)
        start = self.i
        while self.i < len(self.s) and self.s[self.i] in "+-0123456789.eE":
            self.i += 1
        if start == self.i:
            raise self.fail(f"unexpected character '{ch}'")
        return Scalar(self.s[start:self.i], line, False)

    def string(self) -> str:
        assert self.s[self.i] == '"'
        self.i += 1
        out: List[str] = []
        while self.i < len(self.s):
            ch = self.s[self.i]
            if ch == '"':
                self.i += 1
                return "".join(out)
            if ch == "\\":
                self.i += 1
                esc = self.s[self.i] if self.i < len(self.s) else ""
                if esc == "u":
                    try:
                        out.append(chr(int(self.s[self.i + 1:self.i + 5], 16)))
                    except ValueError:
                        raise self.fail("a \\u escape is not valid")
                    self.i += 5
                    continue
                mapping = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "/": "/", "\\": "\\", '"': '"'}
                if esc not in mapping:
                    raise self.fail(f"the escape \\{esc} is not allowed")
                out.append(mapping[esc])
                self.i += 1
                continue
            if ch == "\n":
                self.line += 1
            out.append(ch)
            self.i += 1
        raise self.fail("a text string is never closed")

    def arr(self) -> Seq:
        line = self.line
        self.i += 1
        items: List[Node] = []
        self.ws()
        if self.i < len(self.s) and self.s[self.i] == "]":
            self.i += 1
            return Seq(items, line)
        while True:
            items.append(self.value())
            self.ws()
            if self.i >= len(self.s):
                raise self.fail("a list is never closed")
            if self.s[self.i] == ",":
                self.i += 1
                continue
            if self.s[self.i] == "]":
                self.i += 1
                return Seq(items, line)
            raise self.fail("expected a comma or ]")

    def obj(self) -> Map:
        line = self.line
        self.i += 1
        pairs: List[Tuple[Scalar, Node]] = []
        self.ws()
        if self.i < len(self.s) and self.s[self.i] == "}":
            self.i += 1
            return Map(pairs, line)
        while True:
            self.ws()
            if self.i >= len(self.s) or self.s[self.i] != '"':
                raise self.fail("expected a quoted name")
            kline = self.line
            key = self.string()
            self.ws()
            if self.i >= len(self.s) or self.s[self.i] != ":":
                raise self.fail("expected a colon after the name")
            self.i += 1
            pairs.append((Scalar(key, kline, True), self.value()))
            self.ws()
            if self.i >= len(self.s):
                raise self.fail("an object is never closed")
            if self.s[self.i] == ",":
                self.i += 1
                continue
            if self.s[self.i] == "}":
                self.i += 1
                return Map(pairs, line)
            raise self.fail("expected a comma or }")


def parse_json_nodes(text: str) -> Optional[Node]:
    """Parse JSON text into nodes carrying line numbers."""
    return _Reader(text).parse()
