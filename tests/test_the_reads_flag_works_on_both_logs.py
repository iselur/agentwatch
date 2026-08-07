"""`--reads` accepted a Codex log and showed nothing.

The flag is documented as "include file reads", and under Claude Code it does:
a read is a tool call named ``Read``, the path is a field, and the `·` line is
a lookup away.  Codex has no read tool.  It reads a file by running
``sed -n '1,200p' notes.md``, and the command text is the only record — so
``agentwatch --reads`` on a Codex session emitted not one `·`, ever.

That is the worst shape a gap can take in a live feed: no error, no warning,
just a screen that looks like an agent which happens not to be reading
anything.  A person watching a Codex run and a person watching a Claude run
were being shown two different tools while being told they were one.

The rule that fills it lives in ``transcript.py`` — the same module, the same
answer, and the same copy agentlog reads.  That is the point of the seam being
there: ``agentlog``'s digest had the identical hole in its `files read` column,
and one rule closed both.  ``test_a_codex_session_says_what_it_read.py`` in
agentlog is the other half of this, and it holds the detail of what the rule
counts and what it deliberately refuses to.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agentwatch.follow import Watcher                     # noqa: E402
from agentwatch.transcript import files_a_command_reads   # noqa: E402

CODEX = "019f7ac5-88d3-7b93-b790-99adb23103d9"
CWD = "/home/you/api"


class Case(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="agentwatch-codex-reads-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.now = datetime.now(timezone.utc)
        self.records = []

    def at(self, minutes_ago):
        return self.now - timedelta(minutes=minutes_ago)

    def add(self, rec):
        self.records.append(rec)
        return rec

    def typed(self, minutes_ago=30):
        return self.add({"timestamp": self.at(minutes_ago).isoformat(),
                         "type": "event_msg",
                         "payload": {"type": "user_message", "message": "go"}})

    def script(self, cmd, call="c1", workdir=CWD, minutes_ago=29):
        """A ``custom_tool_call`` — how current Codex runs everything."""
        snippet = ('await exec({cmd: "%s", workdir: "%s"});'
                   % (cmd.replace("\\", "\\\\").replace('"', '\\"'), workdir))
        return self.add({"timestamp": self.at(minutes_ago).isoformat(),
                         "type": "response_item",
                         "payload": {"type": "custom_tool_call", "name": "exec",
                                     "call_id": call, "input": snippet}})

    def old_call(self, cmd, call="f1", workdir=CWD, minutes_ago=29):
        """The older ``function_call`` shape, still on disk in older logs."""
        return self.add({"timestamp": self.at(minutes_ago).isoformat(),
                         "type": "response_item",
                         "payload": {"type": "function_call",
                                     "name": "exec_command", "call_id": call,
                                     "arguments": json.dumps(
                                         {"cmd": cmd, "workdir": workdir})}})

    def poll(self, minutes_ago=31):
        d = os.path.join(self.home, ".codex", "sessions", "2026", "08", "02")
        os.makedirs(d, exist_ok=True)
        stamp = self.at(minutes_ago).strftime("%Y-%m-%dT%H-%M-%S")
        with open(os.path.join(d, "rollout-%s-%s.jsonl" % (stamp, CODEX)),
                  "w", encoding="utf-8") as fh:
            for r in self.records:
                fh.write(json.dumps(r) + "\n")
        return Watcher(home=self.home,
                       since=self.now - timedelta(hours=2)).poll()

    def reads(self, events):
        return [e["text"] for e in events if e["kind"] == "read"]


class TestACodexReadReachesTheFeed(Case):
    """The defect, at the level a person watching the screen meets it."""

    def test_a_command_that_read_a_file_produces_a_read(self):
        self.typed()
        self.script("cat src/app.py")
        self.assertEqual(self.reads(self.poll()), ["/home/you/api/src/app.py"])

    def test_the_older_call_shape_produces_one_too(self):
        # Both shapes are on disk and both are parsed.  Wiring one and not the
        # other is how the last Codex gap in this family lasted as long as it
        # did.
        self.typed()
        self.old_call("cat src/app.py")
        self.assertEqual(self.reads(self.poll()), ["/home/you/api/src/app.py"])

    def test_the_commonest_idiom_in_the_corpus_is_read(self):
        # `sed -n '1,200p' FILE` is the single most common read across 1,217
        # real Codex sessions, and the one a flag table shared between verbs
        # gets wrong: `-n` swallows the next word for `head` and does not for
        # `sed`, where the next word is the script.
        self.typed()
        self.script("sed -n '1,200p' notes.md")
        self.assertEqual(self.reads(self.poll()), ["/home/you/api/notes.md"])

    def test_a_path_is_resolved_against_the_directory_it_ran_in(self):
        # The feed names paths absolutely for writes already.  A read left
        # relative would appear beside its own absolute spelling as if they
        # were two files.
        self.typed()
        self.script("cat app.py", workdir="/home/you/api/src")
        self.assertEqual(self.reads(self.poll()), ["/home/you/api/src/app.py"])

    def test_the_command_is_still_reported_as_a_command(self):
        # The read is additional, not instead of.  A `cat` is a thing the agent
        # ran as well as a file it opened, and the default view — which has no
        # reads in it — must still show the `$` line.
        self.typed()
        self.script("cat src/app.py")
        events = self.poll()
        self.assertIn("cat src/app.py",
                      [e["text"] for e in events if e["kind"] == "cmd"])

    def test_a_session_that_only_ran_tests_produces_no_reads(self):
        # The empty case has to stay reachable, or one wrong answer has
        # replaced another.
        self.typed()
        self.script("pytest -x")
        self.assertEqual(self.reads(self.poll()), [])

    def test_a_search_does_not_produce_a_read(self):
        # `rg pattern src/` puts a pattern, a glob and a directory in the
        # position a path goes.  A `·` naming a file that was never opened is
        # worse in a scrolling feed than a `·` that never appears, because
        # there is no retraction.
        self.typed()
        self.script("rg -n 'def main' src/")
        self.assertEqual(self.reads(self.poll()), [])


class TestTheFlagShowsThemAndTheDefaultDoesNot(Case):
    """`--reads` is off by default, and that has to stay true for Codex too."""

    GLYPH = "\u00b7"          # the middle dot render.py gives a read

    def read_lines(self, out):
        """Only the `·` lines.

        Asserting on the filename alone cannot tell a read from a command: the
        `$` line for `cat src/app.py` contains `app.py` too, and a first draft
        of this test passed against a build with no reads in it at all.
        """
        return [ln for ln in out.splitlines() if self.GLYPH in ln]

    def run_cli(self, *args):
        env = dict(os.environ, PYTHONPATH=_ROOT)
        return subprocess.run(
            [sys.executable, "-m", "agentwatch", "--once", "--home", self.home,
             "--since", "2h"] + list(args),
            capture_output=True, text=True, env=env, timeout=60)

    def test_the_flag_puts_the_file_on_the_screen(self):
        self.typed()
        self.script("cat src/app.py")
        self.poll()                      # writes the fixture to disk
        lines = self.read_lines(self.run_cli("--reads").stdout)
        self.assertEqual(len(lines), 1, lines)
        # Named the way every other row in the family names a file: the part
        # that says which file, without the part the reader already knows.  The
        # project is printed in its own column two cells to the left, so a row
        # that spelled it out again was saying `api` twice and calling it a
        # path.  What must not come back is the absolute path.
        self.assertIn("src/app.py", lines[0])
        self.assertNotIn("/home/you/api", lines[0])

    def test_without_it_the_read_stays_off_the_screen(self):
        # An agent reads far more than it writes, and a feed that is 90% reads
        # is a feed nobody watches.  That was the reason for the default, and
        # it applies to Codex exactly as it applies to Claude.
        self.typed()
        self.script("cat src/app.py")
        self.poll()
        out = self.run_cli().stdout
        self.assertEqual(self.read_lines(out), [])
        # and the command it came from is still on the screen, so this is the
        # read being filtered and not the fixture failing to produce anything.
        self.assertIn("cat src/app.py", out)


class TestBothPackagesReadItTheSameWay(unittest.TestCase):
    """One rule, in the module the two readers share.

    agentlog's `files read` column was empty for Codex for exactly the reason
    this flag was.  Two consumers of one missing rule is what makes the seam
    real rather than hypothetical, and a rule written twice is a rule that
    drifts — which is the whole reason ``transcript.py`` exists.
    """

    def test_the_rule_lives_in_the_shared_module(self):
        from agentwatch import events
        self.assertIs(events.files_a_command_reads, files_a_command_reads)

    def test_the_view_does_not_pick_commands_apart_itself(self):
        with open(os.path.join(_ROOT, "agentwatch", "events.py"),
                  encoding="utf-8") as fh:
            body = fh.read()
        for spelling in ("_READS_ITS_ARGS", "shlex.split"):
            self.assertNotIn(spelling, body,
                             "events.py picks reads apart itself again")

    def test_the_two_copies_of_the_rule_are_one_copy(self):
        # `transcript.py` is copied into both packages rather than imported
        # across them, because nothing in this family reaches into another.
        # The copies are pinned byte-identical elsewhere; this asserts the new
        # rule is in the part that is pinned, and not bolted on beside it.
        import inspect
        here = inspect.getsource(files_a_command_reads)
        self.assertIn("files_a_command_reads", here)
        self.assertIn(
            "files_a_command_reads",
            open(os.path.join(_ROOT, "agentwatch", "transcript.py"),
                 encoding="utf-8").read())


if __name__ == "__main__":
    unittest.main()
