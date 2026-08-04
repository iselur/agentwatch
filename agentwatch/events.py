"""Turn one line of an agent's session log into events.

Two log formats are read: Claude Code (``~/.claude/projects/**/*.jsonl``) and
Codex (``~/.codex/sessions/**/*.jsonl``).  Both are append-only JSONL, which is
what makes tailing them possible at all — a line, once written, never changes.

Only *activity* is extracted: the commands the agent ran, the files it wrote,
and the calls that came back as errors.  Message text is never read.  That is
the same promise the rest of this family makes, and here it is also what keeps
the output narrow enough to watch in real time.

An event is a small dict:

    at       datetime | None  — when it happened
    kind     str              — 'turn' | 'cmd' | 'write' | 'read' | 'error'
    text     str              — the command, the path, or the failed call
    session  str              — session id (short form of the filename)
    source   str              — 'claude' | 'codex'
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

# Reads are excluded from the default view: an agent reads far more than it
# writes, and a stream that is 90% reads is a stream nobody watches.
KINDS = ("turn", "cmd", "write", "read", "error")

_CLAUDE_WRITE_TOOLS = {"Write", "Edit", "MultiEdit"}
_CODEX_WORK_CALLS = {"exec_command", "apply_patch"}

# A patch marker can sit at the start of its own line, or halfway through a
# line of JavaScript — see _patched_files.
_PATCH_LINE = re.compile(r"\*\*\* (?:Update|Add|Delete) File:[ \t]*([^\n]+)")

# Current Codex runs the shell by sending a snippet of JavaScript, so the
# command is a string literal inside somebody else's source code rather than a
# field of the record.  Reading it back out is the only way to see it at all.
#
#   const r = await tools.exec_command({"cmd":"pytest -x","workdir":"/p"});
#
# The word boundary matters: `exec_command` also ends in `command`, and without
# it the function name matches its own argument.
_JS_COMMAND = re.compile(r'["\']?\b(?:cmd|command)\b["\']?\s*:\s*"((?:[^"\\]|\\.)*)"')

# The directory the same snippet says to run in, which is what a relative path
# in a patch envelope is relative to.
_JS_WORKDIR = re.compile(r'["\']?\bworkdir\b["\']?\s*:\s*"((?:[^"\\]|\\.)*)"')

_SCRIPT_FAILED = "script failed"

# How close together two reports of the same file have to be to be one write.
_WRITE_ECHO = timedelta(seconds=30)


def parse_time(raw: str) -> Optional[datetime]:
    """ISO 8601 to an aware datetime, or None if it is not one.

    Aware for every input, which this used to only promise.  Every real record
    ends in ``Z``, but the file is written by another program, and one that
    dropped its offset came back naive — and then `Watcher.poll` compared it
    against an aware ``--since`` and raised, taking the watcher down with a
    traceback and an exit 1 the README says cannot happen.

    A naive stamp is read as UTC, which is the offset the format is written in
    and the same reading agentlog takes.  The alternative, letting Python
    resolve it as local, put the same log line nine hours from where agentlog
    put it when read in Tokyo: two tools in one family disagreeing about one
    line, quietly, and differently on each machine.  Assuming UTC can still be
    wrong, but it is wrong by the same amount everywhere.
    """
    if not raw:
        return None
    try:
        at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return None
    return at if at.tzinfo is not None else at.replace(tzinfo=timezone.utc)


def _js_unescape(text: str) -> str:
    """Best-effort: a JavaScript string literal's contents, read as text.

    The whole snippet is not valid JSON, so it cannot simply be parsed.  Only
    the two escapes that matter for finding a patch envelope are undone; being
    wrong about the rest costs nothing, because all that is read back out of
    the result is the file paths.
    """
    if "\\" not in text:
        return text
    return text.replace("\\n", "\n").replace('\\"', '"')


def _unquote(raw: str) -> str:
    """A JSON string body, decoded — or returned as it stands if it will not."""
    try:
        value = json.loads('"' + raw + '"')
    except (json.JSONDecodeError, ValueError):
        return raw
    return value if isinstance(value, str) else raw


def _patched_files(text: str) -> List[str]:
    """File paths named in an ``apply_patch`` envelope.

    Codex has no structured file-write field — it edits by handing an envelope
    like ``*** Update File: src/app.py`` to a patch tool — so the only record of
    which file changed is the text of the call itself.

    The marker is not required to start its line: in a current session the
    envelope is embedded in a line of JavaScript, and insisting on a line start
    there finds nothing at all.
    """
    if not text or "*** " not in text:
        return []
    out: List[str] = []
    for found in _PATCH_LINE.findall(_js_unescape(text)):
        # Whatever follows the path is the rest of somebody's source line.
        path = found.strip().rstrip("\\").strip().strip("'\"")
        path = path.rstrip(" \t\\'\");,")
        if path:
            out.append(path)
    return out


class Tracker:
    """Per-file state that single lines cannot carry on their own.

    A failed call arrives as its own record and names only the id of the call
    that failed, so the command has to be remembered from when it was issued.
    The same object also holds the working directory, which appears once near
    the top of the file and never again.
    """

    def __init__(self, session: str, source: str, project: str = "") -> None:
        self.session = session
        self.source = source
        self.project = project
        self._labels: Dict[str, str] = {}
        # Bounded: a long session issues thousands of calls, and a watcher that
        # grows without limit is a watcher that gets killed overnight.
        self._order: List[str] = []
        self._max_labels = 2000
        # One patch is announced twice — once by the call that sends it, once by
        # the result that says it applied.  See ``already_written``.
        self._writes: Dict[str, datetime] = {}
        self._write_order: List[str] = []

    def remember(self, call_id: str, label: str) -> None:
        if not call_id or not label:
            return
        if call_id not in self._labels:
            self._order.append(call_id)
        self._labels[call_id] = label
        while len(self._order) > self._max_labels:
            self._labels.pop(self._order.pop(0), None)

    def recall(self, call_id: str) -> str:
        return self._labels.get(call_id, "")

    def already_written(self, path: str, at: Optional[datetime]) -> bool:
        """Have we just reported this exact file being written?

        Codex names a patched file twice: in the call that sends the envelope,
        and again in the result that confirms it applied.  Both are worth
        reading — the call is the earliest sight of it, the result is the only
        sight of it when the envelope was built somewhere we cannot follow — so
        both are parsed and the second one is dropped here instead.

        Bounded by time, not by count: two writes of one file a minute apart are
        two edits and both belong in the stream.
        """
        if not path or at is None:
            return False
        seen = self._writes.get(path)
        if seen is not None and timedelta(0) <= (at - seen) <= _WRITE_ECHO:
            return True
        if path not in self._writes:
            self._write_order.append(path)
        self._writes[path] = at
        while len(self._write_order) > self._max_labels:
            self._writes.pop(self._write_order.pop(0), None)
        return False

    def _event(self, at, kind: str, text: str) -> Dict:
        return {
            "at": at,
            "kind": kind,
            "text": text,
            "session": self.session,
            "source": self.source,
            "project": self.project,
        }


def events_from_line(raw: str, tracker: Tracker) -> List[Dict]:
    """Zero or more events from one JSONL line.

    Anything unparseable yields nothing.  A log being written to right now will
    hand us half a line eventually; that is expected, not an error.
    """
    raw = raw.strip()
    if not raw:
        return []
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(obj, dict):
        return []
    try:
        if tracker.source == "codex":
            return _codex_events(obj, tracker)
        return _claude_events(obj, tracker)
    except Exception:
        # These formats are written by other programs and change without
        # notice, so a record shaped in a way no branch here expects is a
        # question of when.  Losing that one line is a fair price; taking the
        # watcher down in the middle of a run is not.
        return []


# ---------------------------------------------------------------------------
# Claude Code
# ---------------------------------------------------------------------------

def _claude_events(obj: Dict, tr: Tracker) -> List[Dict]:
    at = parse_time(obj.get("timestamp", ""))
    kind = obj.get("type", "")
    out: List[Dict] = []

    if kind == "user":
        cwd = obj.get("cwd")
        if not tr.project and isinstance(cwd, str) and cwd:
            tr.project = cwd
        msg = obj.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        saw_result = False
        for item in content if isinstance(content, list) else []:
            if not isinstance(item, dict) or item.get("type") != "tool_result":
                continue
            saw_result = True
            if item.get("is_error"):
                label = tr.recall(item.get("tool_use_id", ""))
                out.append(tr._event(at, "error", label))
        # A user record carrying tool results is the agent's own loop feeding
        # itself, not a person typing.  Only the latter is a turn.
        if not saw_result:
            out.append(tr._event(at, "turn", ""))

    elif kind == "assistant":
        msg = obj.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        for item in content if isinstance(content, list) else []:
            if not isinstance(item, dict) or item.get("type") != "tool_use":
                continue
            call_id = item.get("id", "")
            name = item.get("name", "")
            inp = item.get("input")
            if not isinstance(inp, dict):
                inp = {}
            path = inp.get("file_path", "")
            if name == "Bash":
                cmd = inp.get("command", "")
                if isinstance(cmd, str) and cmd:
                    tr.remember(call_id, cmd)
                    out.append(tr._event(at, "cmd", cmd))
            elif name in _CLAUDE_WRITE_TOOLS and isinstance(path, str) and path:
                tr.remember(call_id, "edit " + os.path.basename(path))
                out.append(tr._event(at, "write", path))
            elif name == "Read" and isinstance(path, str) and path:
                tr.remember(call_id, "read " + os.path.basename(path))
                out.append(tr._event(at, "read", path))
            elif name:
                tr.remember(call_id, name)

    return out


# ---------------------------------------------------------------------------
# Codex
# ---------------------------------------------------------------------------

def _codex_events(obj: Dict, tr: Tracker) -> List[Dict]:
    at = parse_time(obj.get("timestamp", ""))
    kind = obj.get("type", "")
    payload = obj.get("payload")
    if not isinstance(payload, dict):
        return []
    ptype = payload.get("type", "")
    out: List[Dict] = []

    if kind in ("session_meta", "turn_context"):
        cwd = payload.get("cwd")
        if isinstance(cwd, str) and cwd and not tr.project:
            tr.project = cwd

    elif kind == "event_msg":
        if ptype == "user_message":
            out.append(tr._event(at, "turn", ""))
        elif ptype == "patch_apply_end":
            out.extend(_codex_patch_result(payload, tr, at))

    elif kind == "response_item":
        if ptype == "custom_tool_call":
            out.extend(_codex_script(payload, tr, at))
        elif ptype == "function_call" and payload.get("name") in _CODEX_WORK_CALLS:
            out.extend(_codex_call(payload, tr, at))
        elif ptype == "custom_tool_call_output":
            # A script that only sent a patch has already had its failure
            # reported, in more detail, by patch_apply_end.  With no command to
            # name, this line would be a bare second "something failed".
            named = tr.recall(payload.get("call_id") or "")
            if named and _script_failed(payload.get("output")):
                out.append(tr._event(at, "error", named))
        elif ptype == "function_call_output":
            output = payload.get("output")
            if isinstance(output, dict):
                meta = output.get("metadata")
                if isinstance(meta, dict) and meta.get("exit_code", 0) not in (0, None):
                    out.append(tr._event(
                        at, "error", tr.recall(payload.get("call_id") or "")))

    return out


def _codex_writes(paths, root, tr: Tracker, at) -> List[Dict]:
    """Write events for patched paths, made absolute and de-echoed."""
    out: List[Dict] = []
    for path in paths:
        if not os.path.isabs(path) and isinstance(root, str) and root:
            path = os.path.normpath(os.path.join(root, path))
        if tr.already_written(path, at):
            continue
        out.append(tr._event(at, "write", path))
    return out


def _codex_script(payload: Dict, tr: Tracker, at) -> List[Dict]:
    """A ``custom_tool_call`` — how current Codex runs everything.

    The call carries a snippet of JavaScript rather than arguments, so the
    command and any patch envelope have to be read back out of it.  A build
    that stops sending these will simply stop matching; nothing here assumes
    the snippet is well formed.
    """
    raw = payload.get("input")
    if not isinstance(raw, str) or not raw:
        return []
    call_id = payload.get("call_id") or ""
    out: List[Dict] = []

    for found in _JS_COMMAND.findall(raw):
        cmd = _unquote(found).strip()
        if not cmd:
            continue
        # The first command in a snippet is the one the failure is named after:
        # by the time the result arrives, several may have run.
        if not tr.recall(call_id):
            tr.remember(call_id, cmd)
        out.append(tr._event(at, "cmd", cmd))

    # Codex does not always announce a cwd, but every exec snippet carries a
    # workdir.  Without reading it, the project column stays empty for a whole
    # session that was never in doubt.
    workdir = _JS_WORKDIR.search(raw)
    if workdir and not tr.project:
        found = _unquote(workdir.group(1)).strip()
        if found:
            tr.project = found

    # Deliberately no write here.  The envelope in the snippet only proves one
    # was sent; a patch_apply_end follows within a fraction of a second, says
    # whether it applied, and names the files absolutely.  Reporting from the
    # call instead would announce edits that failed.
    return out


def _codex_call(payload: Dict, tr: Tracker, at) -> List[Dict]:
    """The older ``function_call`` shape, still on disk in older sessions."""
    try:
        args = json.loads(payload.get("arguments", "{}"))
    except (json.JSONDecodeError, ValueError, TypeError):
        args = {}
    if not isinstance(args, dict):
        args = {}
    call_id = payload.get("call_id") or ""
    cmd = args.get("cmd") or args.get("command") or ""
    patch = args.get("patch") or args.get("input") or ""
    if not isinstance(cmd, str):
        cmd = ""
    if not isinstance(patch, str):
        patch = ""
    out: List[Dict] = []
    if cmd:
        tr.remember(call_id, cmd)
        out.append(tr._event(at, "cmd", cmd))
    root = args.get("workdir") or tr.project or ""
    out.extend(_codex_writes(_patched_files(patch or cmd), root, tr, at))
    return out


def _codex_patch_result(payload: Dict, tr: Tracker, at) -> List[Dict]:
    """``patch_apply_end`` — the only place a failed patch is ever admitted."""
    changes = payload.get("changes")
    paths = sorted(changes) if isinstance(changes, dict) else []
    if payload.get("success", True):
        return _codex_writes(paths, tr.project or "", tr, at)
    names = ", ".join(os.path.basename(p) for p in paths[:3])
    return [tr._event(at, "error", "patch did not apply" + (": " + names if names else ""))]


def _script_failed(output) -> bool:
    """Did a script call come back as a failure?

    Codex says so in the first line of the output — ``Script failed`` against
    ``Script completed`` — and nowhere else in the record.
    """
    if isinstance(output, str):
        text = output
    elif isinstance(output, list):
        parts = [item.get("text", "") for item in output
                 if isinstance(item, dict) and isinstance(item.get("text"), str)]
        text = "\n".join(parts)
    else:
        return False
    return text.strip()[:40].lower().startswith(_SCRIPT_FAILED)
