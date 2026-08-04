"""A timestamp with no timezone crashed the watcher and moved the clock.

`parse_time` says it returns "an aware datetime", and for every real Claude
Code record it does — they end in `Z`.  It was not enforced, though, and a
record whose stamp arrives without an offset came back naive.  Two things then
went wrong, and only one of them was visible:

    $ agentwatch --since 2h
    TypeError: can't compare offset-naive and offset-aware datetimes

`--since` is aware, the event was not, and the comparison in `Watcher.poll`
raised.  The traceback escaped `main`, so the tool exited 1 — a code its own
README promises it never returns, because "nothing it can see is a failure of
yours".  A malformed line in someone else's log file is exactly that.

The quiet half is worse.  A naive datetime handed to `.timestamp()` or
`.astimezone()` is resolved as *local* time, so the same log line read in Tokyo
landed nine hours from where agentlog put it — agentlog assumes UTC, which is
the offset the format is written in.  Two tools in one family disagreeing by
nine hours about the same line, neither of them complaining.

So `parse_time` now enforces what it documents, and reads a missing offset the
way agentlog does.  Assuming UTC can still be wrong, but it is wrong by the
same amount everywhere, and it never depends on where the reader is sitting.
"""

from __future__ import annotations

import contextlib
import io
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

from agentwatch import cli
from agentwatch.events import parse_time


class TestParseTimeKeepsItsWord(unittest.TestCase):
    """The docstring says aware.  Now it is true for every input."""

    def test_a_stamp_with_no_offset_is_still_aware(self):
        at = parse_time("2026-08-04T09:00:00")
        self.assertIsNotNone(at)
        self.assertIsNotNone(at.tzinfo, "naive datetime escaped parse_time")
        self.assertIsNotNone(at.utcoffset())

    def test_a_missing_offset_is_read_as_utc(self):
        # The same reading agentlog takes: UTC is the offset the format is
        # written in, and unlike "local" it is the same answer everywhere.
        self.assertEqual(parse_time("2026-08-04T09:00:00"),
                         datetime(2026, 8, 4, 9, 0, tzinfo=timezone.utc))

    def test_an_offset_that_is_there_is_left_alone(self):
        self.assertEqual(parse_time("2026-08-04T09:00:00+05:30"),
                         datetime(2026, 8, 4, 9, 0,
                                  tzinfo=timezone(timedelta(hours=5, minutes=30))))

    def test_z_still_means_utc(self):
        self.assertEqual(parse_time("2026-08-04T09:00:00Z"),
                         datetime(2026, 8, 4, 9, 0, tzinfo=timezone.utc))

    def test_the_reading_does_not_depend_on_where_you_are_sitting(self):
        # The bug in one line: this used to give three different instants.
        got = []
        for zone in ("UTC", "Asia/Tokyo", "America/Los_Angeles"):
            out = subprocess.run(
                [sys.executable, "-c",
                 "import sys; sys.path.insert(0, %r)\n"
                 "from agentwatch.events import parse_time\n"
                 "print(parse_time('2026-08-04T09:00:00').timestamp())" % _ROOT],
                cwd=_ROOT, capture_output=True, text=True,
                env=dict(os.environ, TZ=zone, PYTHONPATH=_ROOT))
            self.assertEqual(out.returncode, 0, out.stderr)
            got.append(out.stdout.strip())
        self.assertEqual(len(set(got)), 1,
                         "the same log line reads as a different instant per "
                         "timezone: {}".format(got))

    def test_rubbish_is_still_none(self):
        for raw in ("", "not a time", "2026-13-45", None, 17):
            self.assertIsNone(parse_time(raw), repr(raw))


class _WithANaiveLog(unittest.TestCase):
    """A session log whose stamps arrived without an offset."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="agentwatch_naive_")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        folder = os.path.join(self.home, ".claude", "projects", "-tmp-proj")
        os.makedirs(folder)
        self.log = os.path.join(
            folder, "4ef1361b-07e4-4bc9-bb29-1783b761d677.jsonl")

    def write(self, stamps):
        with open(self.log, "w", encoding="utf-8") as fh:
            for i, stamp in enumerate(stamps):
                fh.write(json.dumps({
                    "type": "assistant",
                    "timestamp": stamp,
                    "message": {"role": "assistant", "content": [
                        {"type": "tool_use", "id": "t{}".format(i),
                         "name": "Bash",
                         "input": {"command": "echo {}".format(i)}}]},
                }) + "\n")

    def run_once(self, *extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cli.main(["--home", self.home, "--once"] + list(extra))
        return rc, out.getvalue()

    def naive_now(self, minutes_ago=1):
        return (datetime.now(timezone.utc)
                - timedelta(minutes=minutes_ago)).replace(
                    tzinfo=None, microsecond=0).isoformat()


class TestTheWatcherSurvivesIt(_WithANaiveLog):

    def test_since_does_not_raise(self):
        self.write([self.naive_now()])
        rc, out = self.run_once("--since", "2h")
        self.assertEqual(rc, 0, "a log line without an offset took the tool down")
        self.assertIn("echo 0", out)

    def test_it_never_exits_1(self):
        # The README promises there is no exit 1: agentwatch reports what an
        # agent did, it does not judge it, and a malformed line in someone
        # else's file is not a verdict about the person reading it.
        self.write([self.naive_now()])
        rc, _ = self.run_once("--since", "2h")
        self.assertNotEqual(rc, 1)

    def test_no_traceback_reaches_the_terminal(self):
        self.write([self.naive_now()])
        p = subprocess.run(
            [sys.executable, "-m", "agentwatch",
             "--home", self.home, "--once", "--since", "2h"],
            cwd=_ROOT, capture_output=True, text=True,
            env=dict(os.environ, PYTHONPATH=_ROOT))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("Traceback", p.stderr)

    def test_a_mix_of_naive_and_aware_sorts_into_one_order(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        self.write([
            (now - timedelta(minutes=3)).replace(tzinfo=None).isoformat(),
            (now - timedelta(minutes=2)).isoformat(),
            (now - timedelta(minutes=1)).replace(tzinfo=None).isoformat(),
        ])
        rc, out = self.run_once("--since", "2h")
        self.assertEqual(rc, 0)
        lines = [l for l in out.splitlines() if "echo" in l]
        self.assertEqual([l.split("echo ")[1] for l in lines], ["0", "1", "2"],
                         "events came back out of order:\n" + out)

    def test_since_actually_filters_a_naive_stamp(self):
        # Not merely "does not crash": the old one, patched only to stop
        # raising, would have kept everything.
        now = datetime.now(timezone.utc).replace(microsecond=0)
        self.write([
            (now - timedelta(days=3)).replace(tzinfo=None).isoformat(),
            (now - timedelta(minutes=1)).replace(tzinfo=None).isoformat(),
        ])
        rc, out = self.run_once("--since", "2h")
        self.assertEqual(rc, 0)
        self.assertNotIn("echo 0", out, "an event from three days ago survived "
                                        "--since 2h")
        self.assertIn("echo 1", out)

    def test_json_carries_an_offset(self):
        # `at` is documented as ISO 8601 and consumed by other programs; a
        # naive one there pushes the same ambiguity downstream.
        self.write([self.naive_now()])
        rc, out = self.run_once("--since", "2h", "--json")
        self.assertEqual(rc, 0)
        rows = [json.loads(l) for l in out.splitlines() if l.strip()]
        self.assertTrue(rows, out)
        for row in rows:
            self.assertIsNotNone(
                datetime.fromisoformat(row["at"]).tzinfo,
                "naive timestamp in --json output: {}".format(row["at"]))


class TestNothingElseMoves(_WithANaiveLog):
    """An ordinary log with proper stamps behaves exactly as it did."""

    def test_a_normal_log_is_unaffected(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        self.write([(now - timedelta(minutes=n)).isoformat()
                    for n in (3, 2, 1)])
        rc, out = self.run_once("--since", "2h")
        self.assertEqual(rc, 0)
        self.assertEqual(len([l for l in out.splitlines() if "echo" in l]), 3)

    def test_an_undated_record_still_sorts_first(self):
        self.write([self.naive_now()])
        with open(self.log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "type": "assistant",
                "message": {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "tX", "name": "Bash",
                     "input": {"command": "echo undated"}}]},
            }) + "\n")
        rc, out = self.run_once("--since", "2h")
        self.assertEqual(rc, 0)
        lines = [l for l in out.splitlines() if "echo" in l]
        self.assertIn("undated", lines[0],
                      "an undated record no longer sorts first:\n" + out)


if __name__ == "__main__":
    unittest.main()
