"""The four lines under ## Privacy, held to end-to-end.

    - Never reads prompt or response text — only tool activity.
    - Never sends anything anywhere. There is no network code in this package.
    - Never writes to a session log.
    - No API key, no account, no config file.

Those are the reason somebody points this tool at their own transcripts, so
they are a contract and not a description.  `test_events.py` already checks
the first one at the parser, one record shape at a time.  This file checks it
where a user would: a home directory whose logs are full of a marker word,
every output mode run over it, and the marker looked for in everything that
comes back.

The marker is put only in fields that are message text.  What *must* come out
is the activity beside it — the command, the path — so a run that printed
nothing at all cannot pass this file by having no output to search.

The reads-what-it-must half matters as much as the reads-nothing half: a
tailer that silently stopped reporting would satisfy every assertion about
secrecy and be worthless, which is the shape of vacuous pass this project has
already been caught by once (see `test_unreadable_logs.py`).
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# A word that occurs nowhere in this package, so finding it anywhere in the
# output means it came out of a log.
SECRET = "SQUIRRELPLUM"

# The activity around it, which is what agentwatch exists to show.
COMMAND = "pytest -x"
WRITTEN = "/home/you/api/src/app.py"

# Every stdlib module that can open a socket, plus the popular third-party
# clients.  A dependency-free tool that grew one of these would be the first
# place a reviewer looks, and nobody re-reads the imports of five packages by
# hand every release.
NETWORK_MODULES = {
    "asyncore", "ftplib", "http", "httplib", "httpx", "imaplib", "nntplib",
    "poplib", "requests", "smtplib", "socket", "socketserver", "ssl",
    "telnetlib", "urllib", "urllib2", "urllib3", "webbrowser", "xmlrpc",
    "aiohttp", "websockets",
}


def claude_log():
    """A Claude Code session with a secret in every field that is message text."""
    return "\n".join(json.dumps(r) for r in [
        {"type": "user", "timestamp": "2026-08-04T09:00:00Z",
         "cwd": "/home/you/api",
         "message": {"role": "user", "content": [
             {"type": "text", "text": "deploy with token " + SECRET}]}},
        {"type": "assistant", "timestamp": "2026-08-04T09:00:02Z",
         "message": {"role": "assistant", "content": [
             {"type": "thinking", "thinking": "they said " + SECRET},
             {"type": "text", "text": "I will use " + SECRET}]}},
        {"type": "assistant", "timestamp": "2026-08-04T09:00:05Z",
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "id": "t1", "name": "Bash",
              # `description` is prose the model wrote, sitting inside the one
              # record agentwatch does read fields out of.
              "input": {"command": COMMAND,
                        "description": "run the tests for " + SECRET}}]}},
        {"type": "user", "timestamp": "2026-08-04T09:00:19Z",
         # A command's output is where a real secret actually lives: a printed
         # environment, a connection string in a traceback.
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "t1",
              "content": "AWS_SECRET_ACCESS_KEY=" + SECRET}]}},
        {"type": "assistant", "timestamp": "2026-08-04T09:00:30Z",
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "id": "t2", "name": "Write",
              "input": {"file_path": WRITTEN,
                        "content": "PASSWORD = '" + SECRET + "'\n"}}]}},
        # Two record types agentwatch has no branch for.  They are in the fixture
        # because that is exactly why they are worth having: a `user` record is
        # obviously message text and gets handled carefully, and these carry the
        # same thing somewhere nobody is looking.  See TestTheRecordsNobodyReads.
        {"type": "queue-operation", "operation": "enqueue",
         "timestamp": "2026-08-04T09:00:40Z", "sessionId": "aaa",
         "content": "then rotate the key " + SECRET},
        {"type": "frame-link", "sessionId": "aaa",
         "timestamp": "2026-08-04T09:00:50Z",
         "path": "/home/you/api/out/report.html",
         "frameUrl": "https://example.invalid/artifact/1",
         "title": "How do I revoke " + SECRET + "?"},
    ]) + "\n"


def codex_log():
    """The same, in the other format."""
    patch = ("*** Begin Patch\\n*** Update File: " + WRITTEN + "\\n"
             "+password = '" + SECRET + "'\\n*** End Patch")
    return "\n".join(json.dumps(r) for r in [
        {"timestamp": "2026-08-04T09:10:00Z", "type": "session_meta",
         "payload": {"cwd": "/home/you/api"}},
        {"timestamp": "2026-08-04T09:10:01Z", "type": "event_msg",
         "payload": {"type": "user_message", "message": "the key is " + SECRET}},
        {"timestamp": "2026-08-04T09:10:02Z", "type": "response_item",
         "payload": {"type": "reasoning",
                     "summary": [{"type": "summary_text",
                                  "text": "recalling " + SECRET}]}},
        {"timestamp": "2026-08-04T09:10:05Z", "type": "response_item",
         "payload": {"type": "custom_tool_call", "name": "exec", "call_id": "c1",
                     "input": 'tools.exec_command({"cmd":"%s"});' % COMMAND}},
        {"timestamp": "2026-08-04T09:10:20Z", "type": "response_item",
         # The patch body is read — that is how the path is found — so what
         # gets *kept* out of it is the whole question.
         "payload": {"type": "function_call", "name": "apply_patch",
                     "call_id": "c2",
                     "arguments": json.dumps({"input": patch})}},
    ]) + "\n"


class Case(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="aw-privacy-")
        self.addCleanup(_rmtree, self.home)
        c = os.path.join(self.home, ".claude", "projects", "-home-you-api")
        x = os.path.join(self.home, ".codex", "sessions", "2026", "08", "04")
        os.makedirs(c)
        os.makedirs(x)
        self.write(os.path.join(c, "aaa.jsonl"), claude_log())
        self.write(os.path.join(
            x, "rollout-2026-08-04T09-10-00-4ef1361b-07e4-4bc9-bb29-1783b761d677.jsonl"),
            codex_log())

    def write(self, path, text):
        with open(path, "w") as fh:
            fh.write(text)

    def watch(self, *argv):
        # --since reaches back past the staleness cutoff, so the fixture does
        # not have to be dated today to be seen.
        return subprocess.run(
            [sys.executable, "-m", "agentwatch", "--home", self.home,
             "--once", "--since", "3650d", *argv],
            capture_output=True, text=True,
            env=dict(os.environ, PYTHONPATH=_ROOT))


def _rmtree(path):
    import shutil
    shutil.rmtree(path, ignore_errors=True)


def _tree_digest(root):
    """Path, size and contents of everything under a directory."""
    out = {}
    for dirpath, _, names in os.walk(root):
        for name in sorted(names):
            full = os.path.join(dirpath, name)
            with open(full, "rb") as fh:
                out[os.path.relpath(full, root)] = hashlib.sha256(
                    fh.read()).hexdigest()
    return out


class TestMessageTextNeverReachesTheOutput(Case):

    MODES = (
        (),
        ("--json",),
        ("--reads",),
        ("--claude",),
        ("--codex",),
        ("--only", "cmd,write,error,turn"),
        ("--project", "api"),
    )

    def test_no_mode_prints_it(self):
        for mode in self.MODES:
            with self.subTest(mode=mode or ("default",)):
                p = self.watch(*mode)
                said = p.stdout + p.stderr
                self.assertNotIn(SECRET, said,
                                 "message text reached the screen:\n" + said)

    def test_the_activity_beside_it_does_come_out(self):
        # Otherwise every assertion above passes on an empty stream.
        p = self.watch()
        self.assertIn(COMMAND, p.stdout, p.stdout + p.stderr)
        self.assertIn(os.path.basename(WRITTEN), p.stdout, p.stdout + p.stderr)

    def test_both_formats_are_actually_being_read(self):
        # One command from each log, so neither half of the fixture is silently
        # contributing nothing to the runs above.
        for flag in ("--claude", "--codex"):
            with self.subTest(source=flag):
                p = self.watch(flag, "--json")
                lines = [json.loads(l) for l in p.stdout.splitlines() if l.strip()]
                self.assertTrue(lines, "no events at all from " + flag)

    def test_a_turn_carries_no_text_at_all(self):
        # The mark says a turn began.  What was said in it is not agentwatch's
        # to show, and an empty string is the only version of that.
        p = self.watch("--json")
        turns = [json.loads(l) for l in p.stdout.splitlines() if l.strip()]
        turns = [e for e in turns if e["kind"] == "turn"]
        self.assertTrue(turns, p.stdout)
        for e in turns:
            self.assertEqual(e["text"], "", e)

    def test_every_json_field_is_searched_not_just_the_rendering(self):
        # `--json` is the mode a script pipes somewhere else, so a secret
        # surviving in an unrendered field would travel further, not less far.
        p = self.watch("--json")
        for line in p.stdout.splitlines():
            if line.strip():
                self.assertNotIn(SECRET, json.dumps(json.loads(line)), line)


class TestTheRecordsNobodyReads(Case):
    """The promise has to hold for records no branch was written for.

    Claude Code writes more into a session file than the conversation.  Two of
    those types carry message text, and neither is a `user` record, so neither
    passes any of the care that one gets:

      - `queue-operation` (4983 on this machine) carries, in `content`, the
        whole of a prompt somebody typed while the agent was busy.
      - `frame-link` (104) carries, in `title`, a question of theirs turned
        into a heading.

    agentwatch has no branch for either, so today the marker cannot come out.
    That is the point of pinning it: "we never wrote the code to read that" is
    a fact about this version, and the sentence in the README is a promise
    about every version.  Anyone adding a branch — a queued-work indicator is
    the obvious one — meets these tests before a user does.
    """

    def test_a_queued_prompt_is_not_shown(self):
        for mode in ((), ("--json",), ("--reads",), ("--claude",)):
            with self.subTest(mode=mode or ("default",)):
                p = self.watch(*mode)
                self.assertNotIn(SECRET, p.stdout + p.stderr)

    def test_a_queued_prompt_is_not_a_turn(self):
        # Tempting, because an enqueue is the most literal record there is of a
        # person typing.  It is still wrong twice over: the prompt is written
        # again as a `user` record when it is sent, and more than half are never
        # sent at all — 2494 enqueues on this machine against 1121 dequeues and
        # 1358 removes.  Counting them would mark turns that never happened and
        # count the rest twice.
        p = self.watch("--json")
        events = [json.loads(l) for l in p.stdout.splitlines() if l.strip()]
        turns = [e for e in events if e["kind"] == "turn"]
        self.assertEqual(len(turns), 2, turns)

    def test_a_frame_link_title_is_not_shown(self):
        # A title is a question somebody asked, edited into a heading.  The url
        # and the artifact path beside it are metadata and may be shown; the
        # sentence is not.
        p = self.watch("--json")
        for line in p.stdout.splitlines():
            if line.strip():
                self.assertNotIn(SECRET, json.dumps(json.loads(line)), line)

    def test_the_fixture_really_contains_them(self):
        # Without this the two tests above pass on a fixture that lost the
        # records in an edit, which is the same shape of vacuous pass this file
        # was written to avoid.
        log = claude_log()
        types = [json.loads(l)["type"] for l in log.splitlines()]
        self.assertIn("queue-operation", types)
        self.assertIn("frame-link", types)
        self.assertEqual(log.count(SECRET), 8, "the marker moved")


class TestTheSessionLogsAreNotWrittenTo(Case):

    def test_nothing_under_the_home_changes(self):
        before = _tree_digest(self.home)
        for mode in ((), ("--json",), ("--reads",)):
            self.watch(*mode)
        self.assertEqual(_tree_digest(self.home), before,
                         "a read-only tool changed something it read")

    def test_no_file_is_created_beside_them(self):
        # No cache, no offset file, no config: `--home` is somebody's real
        # `~`, and the tool's own state has no business being written into it.
        before = set(_tree_digest(self.home))
        self.watch()
        self.assertEqual(set(_tree_digest(self.home)), before)


class TestThereIsNoNetworkCode(unittest.TestCase):
    """Read the package's own imports, rather than trusting the sentence."""

    def _sources(self):
        pkg = os.path.join(_ROOT, "agentwatch")
        for dirpath, _, names in os.walk(pkg):
            for name in sorted(names):
                if name.endswith(".py"):
                    yield os.path.join(dirpath, name)

    def test_nothing_that_can_open_a_socket_is_imported(self):
        for path in self._sources():
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    top = name.split(".")[0]
                    self.assertNotIn(
                        top, NETWORK_MODULES,
                        "{}:{} imports {}".format(
                            os.path.basename(path), node.lineno, name))

    def test_no_import_is_hidden_behind_a_string(self):
        # The check above reads import statements, so a module named by a
        # string would walk straight past it.
        for path in self._sources():
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                name = getattr(fn, "id", None) or getattr(fn, "attr", None)
                self.assertNotIn(
                    name, ("__import__", "import_module"),
                    "{}:{} imports by name at runtime".format(
                        os.path.basename(path), node.lineno))

    def test_the_readme_still_makes_the_claim(self):
        # If the promise is ever dropped from the README, these tests should be
        # revisited rather than left guarding a sentence nobody makes any more.
        with open(os.path.join(_ROOT, "README.md"), encoding="utf-8") as fh:
            text = fh.read()
        for claim in ("Never reads prompt or response text",
                      "There is no network code in this package",
                      "Never writes to a session log"):
            self.assertIn(claim, text)


if __name__ == "__main__":
    unittest.main()
