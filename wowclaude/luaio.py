"""Read and write World of Warcraft SavedVariables files.

The game client stores addon data as a small subset of Lua: top-level
``Name = <value>`` statements whose values are tables, strings, numbers,
booleans or nil. This module parses that subset into plain Python objects
(``dict``, ``list``, ``str``, ``int``, ``float``, ``bool``, ``None``) and writes
Python objects back out in the client's own style, so the companion can read
requests the addon saved and write a data file the addon loads on reload.

Standard library only. Nothing here touches any path except the ones the
caller passes in.
"""

from __future__ import annotations

import math
import os
import re
import tempfile
import time
from pathlib import Path

__all__ = [
    "LuaParseError",
    "SAMPLE_SAVED_VARIABLES",
    "parse_saved_variables",
    "read_saved_variables",
    "to_lua",
    "write_lua_file",
]

_MAX_DEPTH = 200
_REPLACE_RETRIES = 5
_REPLACE_DELAY = 0.2

_SKIP = re.compile(r"(?:[ \t\r\n\f\v]+|--[^\n]*)*")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER = re.compile(
    r"0[xX][0-9a-fA-F]+|(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"
)
_DECIMAL_ESCAPE = re.compile(r"[0-9]{1,3}")
_STRING_CHUNK = {
    '"': re.compile(r'[^\\\r\n"]+'),
    "'": re.compile(r"[^\\\r\n']+"),
}
_SIMPLE_ESCAPES = {
    "n": 10,
    "r": 13,
    "t": 9,
    "a": 7,
    "b": 8,
    "f": 12,
    "v": 11,
    "\\": 92,
    '"': 34,
    "'": 39,
}


class LuaParseError(ValueError):
    """Raised when text is not valid SavedVariables syntax.

    Attributes: ``line`` and ``col`` (both 1-based) and ``msg``.
    """

    def __init__(self, msg: str, line: int, col: int) -> None:
        super().__init__(f"line {line}, column {col}: {msg}")
        self.msg = msg
        self.line = line
        self.col = col

    def __str__(self) -> str:
        return f"line {self.line}, column {self.col}: {self.msg}"


class _Parser:
    """Recursive-descent parser over one SavedVariables text."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0

    def error(self, msg: str, pos: int | None = None) -> LuaParseError:
        """Build a LuaParseError for ``pos`` (default: the current position)."""
        if pos is None:
            pos = self.pos
        line = self.text.count("\n", 0, pos) + 1
        col = pos - (self.text.rfind("\n", 0, pos) + 1) + 1
        return LuaParseError(msg, line, col)

    def skip(self) -> None:
        """Advance past whitespace and ``--`` comments."""
        self.pos = _SKIP.match(self.text, self.pos).end()  # type: ignore[union-attr]

    def peek(self) -> str:
        """Return the current character, or an empty string at end of input."""
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def expect(self, char: str) -> None:
        """Consume ``char`` (after whitespace) or raise."""
        self.skip()
        if self.peek() != char:
            raise self.error(f"expected {char!r}")
        self.pos += 1

    def parse_file(self) -> dict[str, object]:
        """Parse every top-level ``Name = value`` statement."""
        result: dict[str, object] = {}
        while True:
            self.skip()
            if self.pos >= len(self.text):
                return result
            m = _IDENT.match(self.text, self.pos)
            if m is None:
                raise self.error("expected a variable name")
            self.pos = m.end()
            self.expect("=")
            result[m.group()] = self.value(0)
            self.skip()
            if self.peek() == ";":
                self.pos += 1

    def value(self, depth: int) -> object:
        """Parse one value at the current position."""
        self.skip()
        c = self.peek()
        if c == "":
            raise self.error("unexpected end of input")
        if c == "{":
            return self.table(depth + 1)
        if c in "\"'":
            return self.string()
        if c == "-" or c == "." or (c.isascii() and c.isdigit()):
            return self.number()
        m = _IDENT.match(self.text, self.pos)
        if m is None:
            raise self.error(f"unexpected character {c!r}")
        word = m.group()
        if word == "true":
            self.pos = m.end()
            return True
        if word == "false":
            self.pos = m.end()
            return False
        if word == "nil":
            self.pos = m.end()
            return None
        raise self.error(
            f"unsupported identifier {word!r} (only true, false and nil are allowed)"
        )

    def number(self) -> int | float:
        """Parse a numeral, including the ``1/0``, ``-1/0`` and ``0/0`` forms."""
        start = self.pos
        negative = False
        if self.peek() == "-":
            negative = True
            self.pos += 1
            self.skip()
        m = _NUMBER.match(self.text, self.pos)
        if m is None:
            raise self.error("invalid number", start)
        token = m.group()
        end = m.end()
        if end < len(self.text) and (self.text[end].isalnum() or self.text[end] in "_."):
            raise self.error("malformed number", start)
        is_hex = token[:2] in ("0x", "0X")
        try:
            if is_hex:
                number: int | float = int(token, 16)
            elif token.isdigit():
                number = int(token)
            else:
                number = float(token)
        except ValueError:
            raise self.error("invalid number", start) from None
        self.pos = end
        if negative:
            number = -number

        self.skip()
        if self.peek() != "/":
            return number
        slash = self.pos
        self.pos += 1
        self.skip()
        bad = "operator '/' is only supported as 1/0, -1/0 or 0/0"
        d = _NUMBER.match(self.text, self.pos)
        if d is None or d.group()[:2] in ("0x", "0X") or float(d.group()) != 0:
            raise self.error(bad, slash)
        after = d.end()
        if after < len(self.text) and (self.text[after].isalnum() or self.text[after] in "_."):
            raise self.error("malformed number", self.pos)
        if is_hex or abs(number) not in (0, 1):
            raise self.error(bad, slash)
        self.pos = after
        if number == 0:
            return math.nan
        return math.inf if number > 0 else -math.inf

    def string(self) -> str:
        """Parse a quoted string and decode its escapes."""
        text = self.text
        start = self.pos
        quote = text[start]
        chunk = _STRING_CHUNK[quote]
        self.pos += 1
        buf = bytearray()
        while True:
            m = chunk.match(text, self.pos)
            if m is not None:
                buf += m.group().encode("utf-8", "surrogatepass")
                self.pos = m.end()
            c = self.peek()
            if c == "":
                raise self.error("unterminated string", start)
            if c == quote:
                self.pos += 1
                return buf.decode("utf-8", "replace")
            if c in "\r\n":
                raise self.error("unterminated string (newline in string)", start)
            # c is a backslash
            self.pos += 1
            e = self.peek()
            if e == "":
                raise self.error("unterminated string", start)
            if e in _SIMPLE_ESCAPES:
                buf.append(_SIMPLE_ESCAPES[e])
                self.pos += 1
            elif e == "\n":
                buf.append(10)
                self.pos += 1
            elif e == "\r":
                buf.append(10)
                self.pos += 1
                if self.peek() == "\n":
                    self.pos += 1
            elif e.isascii() and e.isdigit():
                d = _DECIMAL_ESCAPE.match(text, self.pos)
                assert d is not None
                code = int(d.group())
                if code > 255:
                    raise self.error("decimal escape too large", self.pos - 1)
                buf.append(code)
                self.pos = d.end()
            else:
                raise self.error(f"invalid escape sequence '\\{e}'", self.pos - 1)

    def table(self, depth: int) -> dict | list:
        """Parse a table; return a list when it is a gapless 1..n sequence."""
        if depth > _MAX_DEPTH:
            raise self.error("tables nested too deeply")
        self.pos += 1  # the opening brace
        entries: dict[object, object] = {}
        count = 0
        while True:
            self.skip()
            c = self.peek()
            if c == "":
                raise self.error("unterminated table")
            if c == "}":
                self.pos += 1
                break
            if c == "[":
                key_pos = self.pos
                self.pos += 1
                key = self.value(depth)
                self.expect("]")
                self.expect("=")
                if key is None or isinstance(key, (dict, list)) or (
                    isinstance(key, float) and key != key
                ):
                    raise self.error("invalid table key", key_pos)
                val = self.value(depth)
                if val is None:
                    entries.pop(key, None)
                else:
                    entries[key] = val
            else:
                m = _IDENT.match(self.text, self.pos)
                named = False
                if m is not None:
                    probe = _SKIP.match(self.text, m.end()).end()  # type: ignore[union-attr]
                    named = self.text.startswith("=", probe) and not self.text.startswith(
                        "==", probe
                    )
                if named:
                    assert m is not None
                    self.pos = m.end()
                    self.expect("=")
                    val = self.value(depth)
                    if val is None:
                        entries.pop(m.group(), None)
                    else:
                        entries[m.group()] = val
                else:
                    count += 1
                    entries[count] = self.value(depth)
            self.skip()
            c = self.peek()
            if c in (",", ";") and c != "":
                self.pos += 1
            elif c != "}":
                raise self.error("expected ',' or '}' in table")
        if entries and all(type(k) is int and k == i for i, k in enumerate(entries, 1)):
            return list(entries.values())
        return entries


def parse_saved_variables(text: str) -> dict[str, object]:
    """Parse a SavedVariables file body into ``{Name: value}``."""
    return _Parser(text).parse_file()


def read_saved_variables(path: Path | str) -> dict[str, object]:
    """Read a SavedVariables file (UTF-8, BOM tolerated) and parse it."""
    data = Path(path).read_bytes()
    return parse_saved_variables(data.decode("utf-8-sig", errors="replace"))


_STRING_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "|": "\\124",
}


def _quote(s: str) -> str:
    """Return ``s`` as a double-quoted Lua string in the client's escape style."""
    out = []
    for ch in s:
        esc = _STRING_ESCAPES.get(ch)
        if esc is not None:
            out.append(esc)
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\{ord(ch):03d}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _scalar(value: object) -> str:
    """Serialize a non-table value."""
    if value is None:
        return "nil"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ValueError(f"cannot write non-finite float {value!r} as Lua")
        if value.is_integer() and abs(value) < 1e15:
            return str(int(value))
        return repr(value)
    if isinstance(value, str):
        return _quote(value)
    raise TypeError(f"cannot write value of type {type(value).__name__} as Lua")


def _emit(value: object, depth: int) -> str:
    """Serialize ``value``; tables span lines and close at ``depth`` tabs."""
    if not isinstance(value, (dict, list)):
        return _scalar(value)
    if depth > _MAX_DEPTH:
        raise ValueError("value nested too deeply (or contains a cycle)")
    inner = "\t" * (depth + 1)
    lines = ["{\n"]
    if isinstance(value, list):
        for i, item in enumerate(value, 1):
            lines.append(f"{inner}{_emit(item, depth + 1)}, -- [{i}]\n")
    else:
        for k in value:
            if isinstance(k, bool) or not isinstance(k, (str, int)):
                raise TypeError(f"table keys must be str or int, not {type(k).__name__}")
        int_keys = sorted(k for k in value if isinstance(k, int))
        str_keys = sorted(k for k in value if isinstance(k, str))
        for k in int_keys:
            if value[k] is not None:
                lines.append(f"{inner}[{k}] = {_emit(value[k], depth + 1)},\n")
        for k in str_keys:
            if value[k] is not None:
                lines.append(f"{inner}[{_quote(k)}] = {_emit(value[k], depth + 1)},\n")
    lines.append("\t" * depth + "}")
    return "".join(lines)


def to_lua(value: object, name: str) -> str:
    """Return ``name = <value>`` in the client's style, ending with a newline."""
    if not _IDENT.fullmatch(name):
        raise ValueError(f"invalid Lua variable name: {name!r}")
    return f"{name} = {_emit(value, 0)}\n"


def write_lua_file(path: Path | str, value: object, name: str) -> None:
    """Atomically write ``value`` as ``name = ...`` to ``path`` (UTF-8, ``\\n``).

    The text goes to a temp file beside the target and is moved into place with
    ``os.replace``. A ``PermissionError`` (the game may hold the file open) is
    retried 5 times, 0.2 s apart, then re-raised.
    """
    text = to_lua(value, name)
    target = Path(path)
    fd, tmp_name = tempfile.mkstemp(
        dir=target.parent, prefix=target.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        for attempt in range(_REPLACE_RETRIES + 1):
            try:
                os.replace(tmp_name, target)
                return
            except PermissionError:
                if attempt == _REPLACE_RETRIES:
                    raise
                time.sleep(_REPLACE_DELAY)
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass


SAMPLE_SAVED_VARIABLES = r'''ClaudeCodeDB = {
	["version"] = 1,
	["outbox"] = {
		{
			["id"] = "1762300000-1a2b",
			["kind"] = "prompt",
			["session_key"] = "s-1762300000",
			["project"] = "example",
			["backend"] = "claude-code",
			["prompt"] = "line one\nline two with \"quotes\", a \\ backslash, a tab\t, and a \124cffff0000pipe\124r",
			["created_at"] = 1762300000,
		}, -- [1]
	},
	["settings"] = {
		["auto_reload"] = true,
		["scale"] = 1.25,
		["offset"] = -3,
		["big"] = 1e+20,
	},
	["sparse"] = {
		[3] = "three",
		[7] = "seven",
	},
}
GlobalBlizzardSavedSets = {
}
'''
"""A realistic file exactly as the client writes it."""
