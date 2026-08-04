"""Ctrl-c during `--once` used to hand back a truncated file marked success.

`--once` is the scripting mode — "print what is there and exit", as opposed to
following.  The way it gets used is

    agentwatch --once --json > events.json && process events.json

and the `&&` is the whole point: exit 0 means the file is complete.

It was not.  Every interrupt, at every delay, exited 0:

    delay=0.30  rc=0  lines=13137 / 20000
    delay=0.35  rc=0  lines=5363 / 20000
    delay=0.38  rc=0  lines=14379 / 20000

and interrupting earlier, while the logs were still being parsed, gave `rc=0`
with an *empty* file — which reads as "nothing happened today", a perfectly
ordinary answer for this tool to give.  Nothing downstream could tell.

The cause was one handler doing two jobs.  `main` caught `KeyboardInterrupt`
outside the parsed arguments, where it could not know which mode it was in, and
returned 0 for all of them.  For following that is right and deliberate — ctrl-c
is how you stop a tailer, not a failure — but it was being applied to a mode
that had made a promise about its output.

So the default moves to 130, the family's spelling of "you stopped it", and
following keeps its 0 as the documented exception, in the one place that knows
it is following.

Partial output is still written out, and still worth having.  It is only no
longer allowed to call itself whole.
"""

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


def _ago(seconds):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


class _WithSomeEvents(unittest.TestCase):
    """A home with enough in it that an interrupt can land mid-print."""

    EVENTS = 400

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="agentwatch_interrupt_")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        folder = os.path.join(self.home, ".claude", "projects", "-tmp-proj")
        os.makedirs(folder)
        path = os.path.join(folder, "4ef1361b-07e4-4bc9-bb29-1783b761d677.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            for i in range(self.EVENTS):
                fh.write(json.dumps({
                    "type": "assistant", "timestamp": _ago(3000 - i),
                    "message": {"role": "assistant", "content": [
                        {"type": "tool_use", "id": "t{}".format(i),
                         "name": "Bash",
                         "input": {"command": "pytest -k case{}".format(i)}}]},
                }) + "\n")

    def once(self, *extra, stop_after=None):
        """Run `--once`, optionally interrupted after `stop_after` lines.

        The interrupt is raised from the write itself rather than delivered as a
        real signal, so the test lands in the same place every time instead of
        depending on how fast the machine is.
        """
        written = []
        real = cli.write_line

        def counting(text):
            if stop_after is not None and len(written) >= stop_after:
                raise KeyboardInterrupt
            written.append(text)
            return real(text)

        cli.write_line = counting
        self.addCleanup(setattr, cli, "write_line", real)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = cli.main(["--home", self.home, "--once", "--since", "2h"]
                          + list(extra))
        return rc, written


class TestAnInterruptedOnceIsNotASuccess(_WithSomeEvents):

    def test_interrupted_partway_through(self):
        rc, written = self.once(stop_after=5)
        self.assertLess(len(written), self.EVENTS, "the probe did not truncate")
        self.assertNotEqual(
            rc, 0, "handed back {} of {} events and called it success"
                   .format(len(written), self.EVENTS))

    def test_the_code_is_the_families_one_for_you_stopped_it(self):
        rc, _ = self.once(stop_after=5)
        self.assertEqual(rc, 130)

    def test_interrupted_before_it_printed_anything(self):
        # The worst version: an empty file and exit 0 is indistinguishable from
        # a real "no events in this window".
        rc, written = self.once(stop_after=0)
        self.assertEqual(written, [])
        self.assertNotEqual(rc, 0, "an empty result reported as complete")

    def test_the_json_path_too(self):
        # The mode most likely to be redirected into a file and read back.
        rc, _ = self.once("--json", stop_after=5)
        self.assertNotEqual(rc, 0)

    def test_what_was_printed_is_still_printed(self):
        # Truncated output is still worth having; it is only no longer allowed
        # to call itself whole.
        _, written = self.once(stop_after=5)
        self.assertEqual(len(written), 5)


class TestFollowingKeepsItsZero(_WithSomeEvents):
    """Ctrl-c is how you stop a tailer.  That was always right."""

    def test_ctrl_c_while_following_is_not_an_error(self):
        real = cli.write_line
        self.addCleanup(setattr, cli, "write_line", real)

        def stop(text):
            raise KeyboardInterrupt
        cli.write_line = stop
        rc = cli.main(["--home", self.home, "--since", "2h"])
        self.assertEqual(rc, 0, "ctrl-c on the tailer is not a failure")

    def test_the_documented_exit_codes_still_hold(self):
        # The module docstring is what people read; it has to keep matching.
        self.assertIn("Ctrl-C", cli.__doc__)


class TestUninterruptedRunsAreUnchanged(_WithSomeEvents):
    """The regression guard: none of this may cost an ordinary run."""

    def test_once_still_exits_zero(self):
        rc, written = self.once()
        self.assertEqual(rc, 0)
        self.assertEqual(len(written), self.EVENTS)

    def test_once_with_nothing_to_show_still_exits_zero(self):
        empty = tempfile.mkdtemp(prefix="agentwatch_empty_")
        self.addCleanup(shutil.rmtree, empty, ignore_errors=True)
        self.assertEqual(
            cli.main(["--home", empty, "--once", "--since", "2h"]), 0)

    def test_version_and_help_are_untouched(self):
        for flag in ("--version", "--help"):
            with self.subTest(flag=flag):
                p = subprocess.run(
                    [sys.executable, "-m", "agentwatch", flag],
                    cwd=_ROOT, capture_output=True,
                    env=dict(os.environ, PYTHONPATH=_ROOT))
                self.assertEqual(p.returncode, 0)


class TestARealSignal(_WithSomeEvents):
    """The same thing again with an actual SIGINT, end to end.

    The in-process tests above pin the behaviour deterministically; this one
    checks it survives contact with a real signal and a real pipe.
    """

    EVENTS = 20000

    def _interrupt_at(self, delay):
        out = os.path.join(self.home, "out.txt")
        with open(out, "wb") as fh:
            proc = subprocess.Popen(
                [sys.executable, "-m", "agentwatch", "--home", self.home,
                 "--once", "--since", "2h"],
                stdout=fh, stderr=subprocess.DEVNULL, cwd=_ROOT,
                env=dict(os.environ, PYTHONPATH=_ROOT))
            try:
                proc.wait(timeout=delay)
            except subprocess.TimeoutExpired:
                proc.send_signal(subprocess.signal.SIGINT)
            rc = proc.wait(timeout=120)
        with open(out, "r", encoding="utf-8", errors="replace") as fh:
            return rc, sum(1 for _ in fh)

    def test_a_truncated_run_never_reports_success(self):
        truncated = 0
        for delay in (0.15, 0.25, 0.3, 0.35, 0.4, 0.45):
            rc, lines = self._interrupt_at(delay)
            if lines >= self.EVENTS:
                continue                    # finished before the signal landed
            truncated += 1
            self.assertNotEqual(
                rc, 0, "{} of {} lines, exit 0".format(lines, self.EVENTS))
        if not truncated:
            self.skipTest("machine too fast to interrupt mid-print")


if __name__ == "__main__":
    unittest.main()
