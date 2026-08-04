"""Command line for agentwatch.

    agentwatch                     # follow every active session, live
    agentwatch --since 10m         # replay the last ten minutes, then follow
    agentwatch --once              # print what is there and exit
    agentwatch --project relay     # one project only
    agentwatch --only cmd,error    # just the commands and the failures
    agentwatch --json              # one JSON object per line

Exit codes: 0 normal (Ctrl-C included), 2 usage error.  There is deliberately
no exit 1 — agentwatch reports what an agent is doing, it does not judge it.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from . import __version__
from .events import KINDS
from .follow import DEFAULT_STALE_S, Watcher
from .render import (
    format_event, format_json, marks_for, terminal_width, use_color, write_line,
)

DEFAULT_KINDS = ("cmd", "write", "error", "turn")
_OFFSET = re.compile(r"^(\d+)\s*([mhdw])$", re.IGNORECASE)
_UNITS = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}


def parse_since(raw: str, now: Optional[datetime] = None) -> datetime:
    """A cutoff from ``10m`` / ``2h`` / ``3d`` / ``1w`` or an ISO date.

    Raises ``ValueError`` with a message meant for the person who typed it.
    """
    now = now or datetime.now(timezone.utc)
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty; try --since 10m or --since 2026-08-03")
    match = _OFFSET.match(text)
    if match:
        amount = int(match.group(1))
        if amount <= 0:
            raise ValueError("{!r} is not a length of time; use 10m, 2h, 3d".format(raw))
        try:
            return now - timedelta(**{_UNITS[match.group(2).lower()]: amount})
        except OverflowError:
            raise ValueError("{!r} is further back than time goes".format(raw))
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(
            "{!r} is not a time; use 10m, 2h, 3d, 1w or 2026-08-03".format(raw))
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def parse_kinds(raw: str) -> Tuple[str, ...]:
    """The ``--only`` list, validated against what actually exists."""
    wanted = [part.strip().lower() for part in (raw or "").split(",") if part.strip()]
    if not wanted:
        raise ValueError("empty; try --only cmd,error")
    bad = [k for k in wanted if k not in KINDS]
    if bad:
        raise ValueError("unknown: {}. Known kinds: {}".format(
            ", ".join(sorted(set(bad))), ", ".join(KINDS)))
    return tuple(dict.fromkeys(wanted))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agentwatch",
        description="Tail what your coding agent is doing, right now.",
        epilog="Reads Claude Code and Codex session logs. Never reads message "
               "text, never writes to them, sends nothing anywhere.",
    )
    p.add_argument("--version", action="version",
                   version="agentwatch {}".format(__version__))
    p.add_argument("--since", metavar="WHEN",
                   help="replay activity since 10m / 2h / 3d / 1w / 2026-08-03")
    p.add_argument("--once", action="store_true",
                   help="print what is there and exit, instead of following")
    p.add_argument("--project", metavar="NAME", default="",
                   help="only projects whose name contains NAME")
    p.add_argument("--only", metavar="KINDS",
                   help="comma-separated: {}".format(",".join(KINDS)))
    p.add_argument("--reads", action="store_true",
                   help="include file reads (an agent reads a great deal)")
    p.add_argument("--json", action="store_true",
                   help="one JSON object per line, for scripts")
    p.add_argument("--interval", metavar="SECONDS", type=float, default=1.0,
                   help="how often to look for new activity (default 1.0)")
    p.add_argument("--claude", action="store_true", help="Claude Code logs only")
    p.add_argument("--codex", action="store_true", help="Codex logs only")
    p.add_argument("--stale", metavar="SECONDS", type=float, default=DEFAULT_STALE_S,
                   help="ignore logs untouched for this long (default 900)")
    p.add_argument("--home", metavar="DIR",
                   help="override the home directory; used by tests and CI")
    p.add_argument("--no-color", action="store_true", help="never colourise")
    return p


def _sources(args) -> Tuple[str, ...]:
    if args.claude and not args.codex:
        return ("claude",)
    if args.codex and not args.claude:
        return ("codex",)
    return ("claude", "codex")


def _resolve_home(args, parser) -> str:
    home = args.home or os.environ.get("AGENTWATCH_HOME") or os.path.expanduser("~")
    if not os.path.isdir(home):
        parser.error("no such directory: {}".format(home))
    return home


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    kinds = DEFAULT_KINDS
    if args.only:
        try:
            kinds = parse_kinds(args.only)
        except ValueError as exc:
            parser.error("--only {}".format(exc))
    elif args.reads:
        kinds = DEFAULT_KINDS + ("read",)

    since = None
    if args.since:
        try:
            since = parse_since(args.since)
        except ValueError as exc:
            parser.error("--since {}".format(exc))

    if args.interval <= 0:
        parser.error("--interval must be greater than zero")
    if args.stale <= 0:
        parser.error("--stale must be greater than zero")

    home = _resolve_home(args, parser)

    watcher = Watcher(
        home=home,
        sources=_sources(args),
        since=since,
        stale_s=args.stale,
        project=args.project,
    )

    marks = marks_for(sys.stdout)
    color = use_color(sys.stdout, False if args.no_color else None)
    width = terminal_width()
    wanted = set(kinds)

    def emit(events) -> int:
        shown = 0
        for event in events:
            if event["kind"] not in wanted:
                continue
            write_line(format_json(event) if args.json
                       else format_event(event, marks, color, width))
            shown += 1
        return shown

    try:
        if args.once:
            first = watcher.poll()
            shown = emit(first)
            if not args.json and shown == 0:
                _note(_nothing_message(watcher, since, args.project))
            return 0
        return _follow(watcher, args, emit)
    except KeyboardInterrupt:
        return 0
    except BrokenPipeError:
        # Someone closed the pipe — `agentwatch | head` is a normal thing to do.
        try:
            sys.stdout.close()
        except (OSError, ValueError):
            pass
        return 0


def _nothing_message(watcher: Watcher, since, project: str) -> str:
    if watcher.watched() == 0:
        if project:
            return "no recent session logs for a project matching {!r}".format(project)
        # Logs exist, they are just all outside the window — which is a different
        # thing to be told than "you have never run an agent here".
        if watcher.found() and since is not None:
            return "nothing has happened in that window"
        return "no session logs have been written to recently"
    if since is not None:
        return "nothing has happened in that window"
    return "nothing new yet"


def _note(text: str) -> None:
    """Context goes to stderr, so `--json` on stdout stays machine-clean."""
    try:
        sys.stderr.write("  " + text + "\n")
        sys.stderr.flush()
    except (OSError, ValueError):
        pass


def _follow(watcher: Watcher, args, emit) -> int:
    first = watcher.poll()          # adopts the files; also replays --since
    count = watcher.watched()
    _note("watching {} session log{} · Ctrl-C to stop".format(
        count, "" if count == 1 else "s")
        if count else "waiting for a session to start · Ctrl-C to stop")
    emit(first)
    while True:
        time.sleep(args.interval)
        emit(watcher.poll())


if __name__ == "__main__":       # pragma: no cover
    sys.exit(main())
