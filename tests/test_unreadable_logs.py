"""A session log agentwatch could not open counted as one it was watching.

Two log files with identical contents, one of them chmod 000:

    $ agentwatch --once --since 1h
    10:53:32  p             » you
    10:53:32  p             $ echo hello

    $ agentwatch
      watching 2 session logs · Ctrl-C to stop

Half the activity, and a header that says otherwise.  The failure mode is
the one this tool exists to prevent: somebody watches a quiet screen and
concludes the agent is idle, when what is actually true is that the file
carrying its work cannot be opened.  Over-claiming coverage is worse here
than in a digest, because a digest is read once and a watch window is
believed continuously.

What is deliberately *not* reported: a file that opens fine and whose lines
produce no events.  Most records in a session log are not events — that is
the ordinary case, on every file, all the time, and warning about it would
make the note meaningless.  agentwatch only reports what it could not read.

Recovery matters too.  A tail runs for hours; permissions get fixed while it
is running.  The note is a live property of the watcher, not a verdict
stamped once at startup.
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
sys.path.insert(0, _ROOT)

from agentwatch.follow import Watcher  # noqa: E402


def _records(sid="s"):
    now = datetime.now(timezone.utc).isoformat()
    return "\n".join([
        json.dumps({"type": "user", "timestamp": now, "sessionId": sid,
                    "cwd": "/tmp/p",
                    "message": {"role": "user", "content": "hi"}}),
        json.dumps({"type": "assistant", "timestamp": now, "sessionId": sid,
                    "message": {"role": "assistant", "id": "m-" + sid,
                                "content": [{"type": "tool_use",
                                             "id": "t-" + sid, "name": "Bash",
                                             "input": {"command": "echo hi"}}]}}),
    ]) + "\n"


class Case(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="aw-unread-")
        self.addCleanup(self._cleanup)
        self.proj = os.path.join(self.home, ".claude", "projects", "p")
        os.makedirs(self.proj)
        self.good = self.write("aaa-good.jsonl", _records("s1"))

    def _cleanup(self):
        for dirpath, _, names in os.walk(self.home):
            for name in names:
                try:
                    os.chmod(os.path.join(dirpath, name), 0o644)
                except OSError:
                    pass
        shutil.rmtree(self.home, ignore_errors=True)

    def write(self, name, text, mode=0o644):
        path = os.path.join(self.proj, name)
        with open(path, "w") as fh:
            fh.write(text)
        os.chmod(path, mode)
        return path

    def watcher(self):
        return Watcher(self.home,
                       since=datetime.now(timezone.utc) - timedelta(hours=1))

    def run_cli(self, *argv):
        return subprocess.run(
            [sys.executable, "-m", "agentwatch", *argv, "--home", self.home],
            capture_output=True, text=True,
            env=dict(os.environ, PYTHONPATH=_ROOT))


class TestALogThatCannotBeOpened(Case):

    def setUp(self):
        super().setUp()
        self.locked = self.write("bbb-locked.jsonl", _records("s2"), mode=0o000)

    def test_the_watcher_knows_which_files_it_could_not_read(self):
        w = self.watcher()
        w.poll()
        self.assertEqual(w.unreadable(), [self.locked])

    def test_it_is_not_counted_as_a_log_being_watched(self):
        # `watching N` is a claim about coverage.  A file that cannot be opened
        # is not being watched in any sense the word is used on that line.
        w = self.watcher()
        w.poll()
        self.assertEqual(w.watched(), 1)

    def test_a_run_says_so(self):
        p = self.run_cli("--once", "--since", "1h")
        self.assertIn("could not be read", p.stderr.lower(), p.stderr)

    def test_it_says_how_many(self):
        self.write("ccc-locked.jsonl", _records("s3"), mode=0o000)
        p = self.run_cli("--once", "--since", "1h")
        self.assertIn("2 session logs", p.stderr, p.stderr)

    def test_the_note_is_on_stderr_so_json_stays_clean(self):
        p = self.run_cli("--once", "--since", "1h", "--json")
        for line in p.stdout.splitlines():
            if line.strip():
                json.loads(line)
        self.assertIn("could not be read", p.stderr.lower(), p.stderr)

    def test_the_events_from_the_readable_file_still_arrive(self):
        p = self.run_cli("--once", "--since", "1h")
        self.assertIn("echo hi", p.stdout, p.stdout)

    def test_the_exit_code_is_still_zero(self):
        # agentwatch shows what is happening; it does not gate anything, and a
        # tail that exits non-zero because one file is locked would break every
        # script that pipes it.
        p = self.run_cli("--once", "--since", "1h")
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_nothing_at_all_readable_says_so_rather_than_nothing_happened(self):
        # The worst version: every file locked.  "nothing new yet" is a claim
        # about the agent.  The truth is a claim about permissions.
        os.chmod(self.good, 0o000)
        p = self.run_cli("--once", "--since", "1h")
        self.assertIn("could not be read", p.stderr.lower(), p.stderr)


class TestAFileThatOpensButYieldsNoEvents(Case):
    """Not reported — most records in a real log are not events."""

    def setUp(self):
        super().setUp()
        self.write("ccc-quiet.jsonl", "not json\n{ broken\n")

    def test_it_is_not_called_unreadable(self):
        w = self.watcher()
        w.poll()
        self.assertEqual(w.unreadable(), [])

    def test_no_note_is_printed(self):
        p = self.run_cli("--once", "--since", "1h")
        self.assertNotIn("could not be read", p.stderr.lower(), p.stderr)


class TestPermissionsFixedWhileWatching(Case):
    """A tail runs for hours; the note has to be able to stop being true."""

    def setUp(self):
        super().setUp()
        self.locked = self.write("bbb-locked.jsonl", _records("s2"), mode=0o000)
        # The directory is rescanned at most every RESCAN_MIN_S, so back-to-back
        # polls in a test would never look at the tree a second time.  Winding
        # the clock forward is what a real watch does by taking a second.
        self._now = datetime.now(timezone.utc).timestamp()

    def watcher(self):
        return Watcher(self.home,
                       since=datetime.now(timezone.utc) - timedelta(hours=1),
                       clock=lambda: self._now)

    def poll(self, w):
        self._now += 10.0
        return w.poll()

    def test_the_file_stops_being_listed_once_it_can_be_read(self):
        w = self.watcher()
        self.poll(w)
        self.assertEqual(w.unreadable(), [self.locked])
        os.chmod(self.locked, 0o644)
        self.poll(w)
        self.assertEqual(w.unreadable(), [])

    def test_its_events_arrive_after_it_is_unlocked(self):
        w = self.watcher()
        self.poll(w)
        os.chmod(self.locked, 0o644)
        events = self.poll(w)
        self.assertTrue(any(e.get("text") == "echo hi" for e in events),
                        "no events after the file became readable: %r" % events)

    def test_a_file_that_becomes_unreadable_starts_being_listed(self):
        w = self.watcher()
        self.poll(w)
        os.chmod(self.locked, 0o644)
        self.poll(w)
        os.chmod(self.locked, 0o644)
        with open(self.locked, "a") as fh:
            fh.write(_records("s4"))
        os.chmod(self.locked, 0o000)
        # A file with no new bytes in it is never opened, so there is nothing
        # to discover — the appended records are what make poll() try again,
        # and they are also the activity that is now being missed.
        self.poll(w)
        self.assertEqual(w.unreadable(), [self.locked])


class TestAnOrdinaryRunIsUnaffected(Case):

    def test_nothing_is_said_when_every_log_opens(self):
        p = self.run_cli("--once", "--since", "1h")
        self.assertNotIn("could not be read", p.stderr.lower(), p.stderr)

    def test_the_watcher_reports_no_unreadable_files(self):
        w = self.watcher()
        w.poll()
        self.assertEqual(w.unreadable(), [])

    def test_the_events_are_unchanged(self):
        p = self.run_cli("--once", "--since", "1h")
        self.assertIn("echo hi", p.stdout, p.stdout)


if __name__ == "__main__":
    unittest.main()
