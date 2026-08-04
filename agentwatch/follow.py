"""Find agent session logs and follow them as they are written.

The whole tool rests on one property of these logs: they are append-only JSONL.
So following them needs no inotify, no daemon and no dependency — remember a
byte offset per file, read from there, repeat.  That is why this works
identically on Linux, macOS and a network share.

Files are read as bytes and split on newlines by hand.  A log being appended to
right now will hand back a half-written final line; keeping it in a buffer until
its newline arrives is the difference between a watcher and a line-mangler.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from .events import Tracker, events_from_line

CLAUDE_SUBDIR = os.path.join(".claude", "projects")
CODEX_SUBDIR = os.path.join(".codex", "sessions")

# How far back a file's mtime may be before it is assumed finished.  Generous:
# an agent that spends ten minutes on one tool call is thinking, not gone.
DEFAULT_STALE_S = 900.0

# A tree of session logs can hold tens of thousands of files; walking it every
# second would cost more than the watching does.
RESCAN_MIN_S = 2.0

_MAX_WALK_ENTRIES = 200000


def _walk_jsonl(root: str) -> List[str]:
    """Every ``.jsonl`` under a root, symlinks never followed.

    Not following links is a correctness choice, not just a safety one: a
    session log reached by two paths would be tailed twice and every event
    printed twice.
    """
    out: List[str] = []
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            if name.endswith(".jsonl"):
                out.append(os.path.join(dirpath, name))
        if len(out) > _MAX_WALK_ENTRIES:
            break
    return out


def discover(home: str, sources: Tuple[str, ...] = ("claude", "codex")) -> List[Tuple[str, str]]:
    """(path, source) for every session log under a home directory."""
    found: List[Tuple[str, str]] = []
    if "claude" in sources:
        for path in _walk_jsonl(os.path.join(home, CLAUDE_SUBDIR)):
            found.append((path, "claude"))
    if "codex" in sources:
        for path in _walk_jsonl(os.path.join(home, CODEX_SUBDIR)):
            found.append((path, "codex"))
    return found


def session_id_for(path: str, source: str) -> str:
    """A short, stable id for a session, taken from its filename."""
    base = os.path.splitext(os.path.basename(path))[0]
    if source == "codex":
        # rollout-<date>-<uuid>.jsonl — the uuid is the last five dash-parts.
        parts = base.split("-")
        if len(parts) >= 5:
            base = "-".join(parts[-5:])
    return base


def decode_claude_project(path: str) -> str:
    """Claude Code's encoded project directory, decoded back to a path.

    It stores the project's absolute path as a directory name with ``/``
    replaced by ``-``, which is ambiguous the moment the path itself contains a
    dash.  Treat the result as a label of last resort; a ``cwd`` seen in the log
    always wins.
    """
    name = os.path.basename(os.path.dirname(path))
    if name.startswith("-"):
        # The leading dash is the root slash.  Dropping it leaves a relative
        # path, which then fails to shorten anything in the output.
        return "/" + name[1:].replace("-", "/")
    return name


def _probe_project(path: str, source: str, max_lines: int = 60) -> str:
    """The working directory a log names near its top, if it names one.

    Worth the extra read: in live mode we seek straight to the end of the file,
    and the record that says which project this is went past long ago.
    """
    from .events import Tracker as _T, events_from_line as _ev
    tracker = _T("", source)
    try:
        with open(path, "rb") as fh:
            for _ in range(max_lines):
                line = fh.readline()
                if not line:
                    break
                _ev(line.decode("utf-8", "replace"), tracker)
                if tracker.project:
                    return tracker.project
    except OSError:
        return ""
    return ""


class _FileState:
    __slots__ = ("offset", "tracker", "buf", "inode")

    def __init__(self, offset: int, tracker: Tracker, inode: int) -> None:
        self.offset = offset
        self.tracker = tracker
        self.buf = b""
        self.inode = inode


class Watcher:
    """Follows every session log under a home directory.

    ``poll()`` returns the events appended since the previous call, oldest
    first.  It never blocks and never raises for a file that vanished, was
    truncated, or cannot be read — a watcher that dies when a log rotates is
    worse than no watcher.
    """

    def __init__(
        self,
        home: str,
        sources: Tuple[str, ...] = ("claude", "codex"),
        since: Optional[datetime] = None,
        stale_s: float = DEFAULT_STALE_S,
        project: str = "",
        clock=None,
    ) -> None:
        self.home = home
        self.sources = sources
        self.since = since
        self.stale_s = stale_s
        self.project = (project or "").lower()
        self._clock = clock or (lambda: datetime.now(timezone.utc).timestamp())
        self._files: Dict[str, _FileState] = {}
        self._first_scan = True
        self._found = 0
        self._last_scan = 0.0
        self._project_names: Dict[str, str] = {}

    # -- scanning ---------------------------------------------------------

    def _fresh(self, path: str) -> bool:
        """Is this log recent enough to be worth following?"""
        try:
            mtime = os.stat(path).st_mtime
        except OSError:
            return False
        if self.since is not None:
            return mtime >= self.since.timestamp()
        return (self._clock() - mtime) <= self.stale_s

    def _adopt(self, path: str, source: str) -> None:
        try:
            st = os.stat(path)
        except OSError:
            return
        project = _probe_project(path, source)
        if not project and source == "claude":
            project = decode_claude_project(path)
        name = os.path.basename(project.rstrip("/")) if project else ""
        if self.project and self.project not in name.lower():
            # Remember the decision, so a filtered-out log is not re-probed on
            # every rescan for as long as the watcher runs.
            self._files[path] = _FileState(-1, Tracker("", source), st.st_ino)
            return
        tracker = Tracker(session_id_for(path, source), source, project)
        # A log that existed before we started is joined at its end; one that
        # appeared since is read whole, because all of it is new to the user.
        # With --since, everything is read whole and filtered by timestamp.
        start_at_end = self._first_scan and self.since is None
        self._files[path] = _FileState(
            st.st_size if start_at_end else 0, tracker, st.st_ino)
        self._project_names[path] = name

    def _scan(self) -> None:
        found = 0
        for path, source in discover(self.home, self.sources):
            found += 1
            if path in self._files:
                continue
            if not self._fresh(path):
                continue
            self._adopt(path, source)
        self._found = found
        self._first_scan = False
        self._last_scan = self._clock()

    # -- reading ----------------------------------------------------------

    def _read_new(self, path: str, state: _FileState) -> List[Dict]:
        try:
            st = os.stat(path)
        except OSError:
            self._files.pop(path, None)
            return []
        if st.st_ino != state.inode or st.st_size < state.offset:
            # Rotated or truncated: the bytes we were pointing at are gone.
            state.offset = 0
            state.buf = b""
            state.inode = st.st_ino
        if st.st_size == state.offset:
            return []
        try:
            with open(path, "rb") as fh:
                fh.seek(state.offset)
                chunk = fh.read()
        except OSError:
            return []
        state.offset += len(chunk)
        data = state.buf + chunk
        # Everything after the last newline is a line still being written.
        cut = data.rfind(b"\n")
        if cut < 0:
            state.buf = data
            return []
        state.buf = data[cut + 1:]
        out: List[Dict] = []
        for line in data[:cut].split(b"\n"):
            if not line:
                continue
            out.extend(events_from_line(line.decode("utf-8", "replace"), state.tracker))
        # The project can turn up mid-stream, on the first record we happen to
        # read; backfill it so early events are not labelled blank.
        name = self._project_names.get(path) or (
            os.path.basename(state.tracker.project.rstrip("/"))
            if state.tracker.project else "")
        for event in out:
            event["project"] = name
        return out

    def poll(self) -> List[Dict]:
        """Events appended since the last call, oldest first."""
        now = self._clock()
        if self._first_scan or (now - self._last_scan) >= RESCAN_MIN_S:
            self._scan()
        events: List[Dict] = []
        for path in list(self._files):
            state = self._files[path]
            if state.offset < 0:      # filtered out by --project
                continue
            events.extend(self._read_new(path, state))
        if self.since is not None:
            events = [e for e in events
                      if e["at"] is None or e["at"] >= self.since]
        # Timestamps come from several files; None sorts first so an undated
        # record never jumps to the end of the stream.
        events.sort(key=lambda e: (e["at"] is not None,
                                   e["at"].timestamp() if e["at"] else 0.0))
        return events

    def watched(self) -> int:
        """How many logs are actually being followed right now."""
        return sum(1 for s in self._files.values() if s.offset >= 0)

    def found(self) -> int:
        """How many session logs exist at all, before any filter.

        The difference between this and ``watched`` is the difference between
        "you have never run an agent" and "nothing happened in the window you
        asked for" — two messages a person needs told apart.
        """
        return self._found
