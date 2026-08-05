"""Shared test fixtures: timestamps, subprocess environments, session text.

Three helpers were written out once per file and then copied.  `_ago` existed
three times, byte for byte.  `_env` existed twice, and the two disagreed about
whether to clear `AGENTWATCH_HOME` — so one file's subprocesses were hermetic
and another's inherited whatever the machine happened to have set.  `_records`
existed three times, each a slightly different two-record session, which meant
that a change to what a Claude Code record looks like had three separate places
to be made and no way to notice the ones that were missed.

None of that is worth a module on its own.  What is, is that agentwatch reads
somebody else's file format: the shape of a record is the one fact the whole
tool depends on, and a fixture that drifts from it tests a format that no agent
writes.  Keeping the shape in one place is the point; the timestamp and
environment helpers just live next to it.

No real home directory is ever touched — every helper here writes into a
temporary directory the caller owns, and `env()` clears `AGENTWATCH_HOME` so a
subprocess cannot reach one either.
"""

from __future__ import annotations

import itertools
import json
import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional

# The repo root, which is what a subprocess needs on PYTHONPATH to import the
# package under test rather than an installed copy of it.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Every record gets its own id.  A helper that stamps a whole file with one
# literal builds a log that a tool is entitled to read as the same event
# arriving sixty times, which is not the log the test meant to write.
_ids = itertools.count(1)


def now() -> datetime:
    """The current moment, aware.  Every stamp in a real log carries an offset."""
    return datetime.now(timezone.utc)


def ago(seconds: float) -> str:
    """An ISO 8601 stamp that many seconds in the past.

    Most of these tests are about a window — what is recent enough to show, what
    has gone quiet — so they need a time relative to the run rather than a fixed
    one.
    """
    return (now() - timedelta(seconds=seconds)).isoformat()


def midnight_today(now: datetime = None) -> datetime:
    """The start of the local day the tests are running in."""
    now = now or datetime.now().astimezone()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def a_now_that_keeps(minutes_of_history: float, now: datetime = None) -> datetime:
    """A "now" with that many minutes of *today* behind it.

    Fixtures write records "fifty minutes ago" and mean "earlier today".  For
    the first fifty minutes of a day those are two different things: the
    records land in yesterday, the renderer correctly draws a day rule between
    them, and a suite that passed all evening counts one line too many until
    00:50.

    Pinning the clock is not on offer here — some of these fixtures are read by
    subprocess runs of the real command, which reads the real one — so the
    fixture's clock is moved forward off midnight instead, by however far back
    it needs to reach.  Anchor once per run and subtract the offsets from what
    comes back: anchoring each record separately would flatten them all onto
    midnight and collapse the span being measured.

    The real clock is handed back untouched for the rest of the day, which is
    to say almost always.

    agentlog carries the same two functions, word for word.  The family
    forbids one package importing another, and these are test fixtures rather
    than shipped code, so the copy stays a copy — see
    tests/test_the_clock_the_fixtures_write_with.py in either repo.
    """
    now = now or datetime.now().astimezone()
    return max(now, midnight_today(now) + timedelta(minutes=minutes_of_history))


def env(**overrides) -> dict:
    """The environment for a subprocess that should read only what it is given.

    `PYTHONPATH` points at this tree so the child imports this agentwatch.
    `AGENTWATCH_HOME` is cleared, because a test that inherits it reads the
    developer's own logs and passes or fails on their contents.  An override of
    None removes a variable.
    """
    child = dict(os.environ)
    child["PYTHONPATH"] = ROOT
    child.pop("AGENTWATCH_HOME", None)
    for key, value in overrides.items():
        if value is None:
            child.pop(key, None)
        else:
            child[key] = value
    return child


def user(sid: str = "s1", when: Optional[str] = None, cwd: str = "/tmp/p",
         text: str = "hi") -> dict:
    """A Claude Code user record — the one that carries `cwd` and starts a turn."""
    return {
        "type": "user",
        "timestamp": when or now().isoformat(),
        "sessionId": sid,
        "cwd": cwd,
        "message": {"role": "user", "content": text},
    }


def bash(sid: str = "s1", when: Optional[str] = None,
         command: str = "echo hi") -> dict:
    """An assistant record whose content is one Bash call.

    The tool call is nested three deep — message, content, input — and that
    nesting is exactly what a hand-written fixture gets subtly wrong.
    """
    nth = next(_ids)
    return {
        "type": "assistant",
        "timestamp": when or now().isoformat(),
        "sessionId": sid,
        "message": {
            "role": "assistant",
            "id": "m-{}-{}".format(sid, nth),
            "content": [{"type": "tool_use", "id": "t-{}-{}".format(sid, nth),
                         "name": "Bash", "input": {"command": command}}],
        },
    }


def jsonl(records: List[dict]) -> str:
    """Records as the file holds them: one JSON object per line, newline-ended."""
    return "".join(json.dumps(rec) + "\n" for rec in records)


def session(sid: str = "s1", when: Optional[str] = None, cwd: str = "/tmp/p",
            command: str = "echo hi", text: str = "hi") -> str:
    """The text of the smallest session that has anything in it to watch.

    A user turn and one command, which between them carry every field the
    watcher reads: the session id, the working directory it groups by, the
    stamp it windows on, and a tool call to report.
    """
    stamp = when or now().isoformat()
    return jsonl([user(sid, stamp, cwd, text), bash(sid, stamp, command)])


def write_session(path: str, text: str) -> str:
    """Write session text to `path`, making its directory.  Returns the path."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path
