"""One event, one line, narrow enough to read while it scrolls.

The layout is fixed-width on purpose.  A live stream is read by glancing at it,
and a glance needs the marks in the same column every time — so the project
column never resizes, even when a longer name shows up later.
"""

from __future__ import annotations

import json
import os
import sys
import unicodedata
from typing import Dict, Optional

PROJECT_WIDTH = 12
MIN_TEXT = 20

# Everything on the line that is not the project or the text: the clock, the two
# gaps around the project column, the mark, and the space after it.
_FIXED = 8 + 2 + 2 + 1 + 1

# The mark says what happened; it is the only thing scanned at speed.
MARKS = {
    "cmd": "$",
    "write": "✎",   # pencil
    "read": "·",    # middle dot
    "error": "✗",   # ballot X
    "turn": "»",    # right guillemet
}

ASCII_MARKS = {
    "cmd": "$",
    "write": "w",
    "read": ".",
    "error": "!",
    "turn": ">",
}

_COLORS = {
    "cmd": "\033[36m",      # cyan
    "write": "\033[32m",    # green
    "read": "\033[90m",     # grey
    "error": "\033[31m",    # red
    "turn": "\033[35m",     # magenta
}
_DIM = "\033[90m"
_RESET = "\033[0m"


def marks_for(stream) -> Dict[str, str]:
    """Unicode marks, unless this stream cannot carry them.

    Falling back is not cosmetic: on a terminal claiming ASCII, an unencodable
    glyph raises mid-write and takes the watcher down with it.
    """
    encoding = getattr(stream, "encoding", None) or ""
    try:
        "".join(MARKS.values()).encode(encoding or "ascii")
    except (LookupError, UnicodeEncodeError, TypeError):
        return dict(ASCII_MARKS)
    return dict(MARKS)


def use_color(stream, force: Optional[bool] = None) -> bool:
    """Colour only where it will be seen and is not unwelcome."""
    if force is not None:
        return force
    if os.environ.get("NO_COLOR"):
        return False
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def terminal_width(default: int = 100) -> int:
    try:
        return max(40, min(200, os.get_terminal_size().columns))
    except OSError:
        pass
    try:
        return max(40, min(200, int(os.environ.get("COLUMNS", "") or default)))
    except ValueError:
        return default


def _clock(event: Dict) -> str:
    at = event.get("at")
    if at is None:
        return "  --:--"
    try:
        return at.astimezone().strftime("%H:%M:%S")
    except (ValueError, OSError):
        return "  --:--"


def _clean(text: str) -> str:
    """Text that cannot do anything to the terminal it is printed on.

    Everything here is text an agent chose, arriving from a file some other
    program wrote.  Printed as-is, an escape sequence in a command would clear
    the screen, retitle the window, or leave every later line coloured — and a
    right-to-left override would let a command read as something it is not.  So
    both control characters (Cc) and formatting characters (Cf, which is where
    the bidi overrides live) become spaces, and the caller's whitespace collapse
    then removes them.  Cf also holds the joiners inside some emoji, which is a
    price worth paying to make this whole class of problem impossible.
    """
    if not any(ord(c) < 32 or ord(c) == 127 or ord(c) > 126 for c in text):
        return text                     # the overwhelmingly common case
    return "".join(
        " " if unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") else c
        for c in text)


def _fit(text: str, width: int) -> str:
    """One line, at most ``width`` columns, with a visible cut."""
    text = " ".join(_clean(text).split())   # newlines in a stream ruin the layout
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: width - 1] + "…"


def _shorten_path(path: str, project: str) -> str:
    """A written file, said the way the person watching would say it."""
    if not path:
        return ""
    if project:
        marker = os.sep + project + os.sep
        idx = path.find(marker)
        if idx >= 0:
            return path[idx + len(marker):]
    home = os.path.expanduser("~")
    if home and home != os.sep and path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


def format_event(event: Dict, marks: Dict[str, str], color: bool = False,
                 width: int = 100) -> str:
    """The whole line for one event."""
    kind = event.get("kind", "")
    mark = marks.get(kind, "?")
    # On a narrow terminal something has to give, and it is the project column:
    # holding it at full width would push the line past the edge and wrap it,
    # which costs the fixed layout far more than a truncated name does.
    room = max(4, min(PROJECT_WIDTH, width - _FIXED - MIN_TEXT))
    project = _fit(event.get("project") or "-", room).ljust(room)
    text = event.get("text") or ""
    if kind == "write":
        text = _shorten_path(text, (event.get("project") or ""))
    elif kind == "turn":
        text = "you"
    elif kind == "error" and not text:
        text = "(a call failed)"
    text = _fit(text, max(MIN_TEXT, width - _FIXED - room))
    stamp = _clock(event)
    if color:
        return "{}{}{}  {}  {}{}{} {}".format(
            _DIM, stamp, _RESET, project,
            _COLORS.get(kind, ""), mark, _RESET, text)
    return "{}  {}  {} {}".format(stamp, project, mark, text)


def format_json(event: Dict) -> str:
    """One JSON object per line, for anything downstream of this."""
    at = event.get("at")
    return _one_line(json.dumps({
        "at": at.isoformat() if at is not None else None,
        "kind": event.get("kind", ""),
        "text": event.get("text", ""),
        "project": event.get("project", ""),
        "session": event.get("session", ""),
        "source": event.get("source", ""),
    }, ensure_ascii=False, sort_keys=True))


def _one_line(row: str) -> str:
    """Escape the two characters that are a newline to a reader but not to us.

    ``json.dumps`` escapes every control character, but U+2028 and U+2029 are
    not control characters — they pass through, and JSON-lines output that is
    one object per line silently becomes two.  Both are legal inside a JSON
    string as an escape, so this stays valid JSON and round-trips unchanged.
    """
    if "\u2028" in row or "\u2029" in row:
        row = row.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return row


def write_line(line: str, stream=None) -> None:
    """Print a line now, not when the buffer feels like it.

    A watcher whose output is piped into ``less`` or ``tee`` and appears in
    4 KB bursts is not a live view of anything.
    """
    stream = stream or sys.stdout
    try:
        stream.write(line + "\n")
        stream.flush()
    except UnicodeEncodeError:
        stream.write(line.encode("ascii", "replace").decode("ascii") + "\n")
        stream.flush()
    except (BrokenPipeError, ValueError):
        raise
