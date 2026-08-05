"""Colour decisions can only be checked where colour would actually happen.

Every other test in this suite reads the output through a pipe or a buffer, and
on one of those `use_color` says no before any flag is consulted — so
`--no-color` is unobservable there, and so is a rule drawn without the dim it is
supposed to carry.  A test that cannot see the difference is not a guard; it is
a line that runs.

So these run the real command with its stdout attached to a pseudo-terminal,
which is the only arrangement in which the question is live.  Two promises are
being pinned: that `--no-color` is still obeyed on the one kind of stream where
obeying it costs something, and that the dated rule is furniture and dims with
the rest of it.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import timedelta

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from tests.fixtures import a_now_that_keeps                 # noqa: E402

try:
    import pty
except ImportError:                                          # pragma: no cover
    pty = None

ESCAPE = "\033["
DIM = "\033[90m"


@unittest.skipIf(pty is None, "no pseudo-terminals on this platform")
class _OnATerminal(unittest.TestCase):
    """Run `agentwatch --once` with a real terminal on the other end."""

    # Two days apart, so the run prints a dated rule it would never print live.
    OFFSETS = (timedelta(days=3), timedelta(days=1))

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="agentwatch_tty_")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        folder = os.path.join(self.home, ".claude", "projects", "-tmp-proj")
        os.makedirs(folder)
        now = a_now_that_keeps(0)
        path = os.path.join(folder, "4ef1361b-07e4-4bc9-bb29-1783b761d677.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            for i, delta in enumerate(self.OFFSETS):
                fh.write(json.dumps({
                    "type": "assistant", "timestamp": (now - delta).isoformat(),
                    "message": {"role": "assistant", "content": [
                        {"type": "tool_use", "id": "t{}".format(i),
                         "name": "Bash",
                         "input": {"command": "echo case{}".format(i)}}]},
                }) + "\n")

    def on_a_terminal(self, *extra):
        """Everything the command wrote, with a tty where stdout usually is."""
        master, slave = pty.openpty()
        env = dict(os.environ)
        env["PYTHONPATH"] = _ROOT + os.pathsep + env.get("PYTHONPATH", "")
        env.pop("NO_COLOR", None)          # the one thing that would answer first
        env["COLUMNS"] = "100"
        proc = subprocess.Popen(
            [sys.executable, "-m", "agentwatch", "--home", self.home,
             "--once", "--since", "2w"] + list(extra),
            stdout=slave, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
            cwd=_ROOT, env=env)
        os.close(slave)
        chunks = []
        try:
            while True:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break                  # the last slave closed; the run is over
                if not data:
                    break
                chunks.append(data)
        finally:
            os.close(master)
            proc.wait(timeout=60)
        return b"".join(chunks).decode("utf-8", "replace")


class TestNoColorIsObeyedWhereItCosts(_OnATerminal):

    def test_a_terminal_is_given_colour_to_begin_with(self):
        # The premise of the test below: without this, `--no-color` removing
        # nothing would look like a pass.
        self.assertIn(ESCAPE, self.on_a_terminal(),
                      "a real terminal got no colour, so the flag proves nothing")

    def test_no_color_takes_it_away_again(self):
        self.assertNotIn(ESCAPE, self.on_a_terminal("--no-color"))


class TestTheDatedRuleIsFurniture(_OnATerminal):
    """The rule organises the page; it is not one of the things on it."""

    def rules(self, text):
        return [line for line in text.replace("\r\n", "\n").split("\n")
                if "──" in line]

    def test_the_rule_is_dimmed_like_the_rest_of_the_furniture(self):
        # Drawn at full brightness it is the loudest thing on screen, which is
        # backwards: it is a separator between the lines somebody came to read.
        rules = self.rules(self.on_a_terminal())
        self.assertEqual(len(rules), 2, rules)
        for rule in rules:
            self.assertIn(DIM, rule, "the day rule was drawn undimmed")

    def test_no_color_leaves_the_rule_plain_too(self):
        rules = self.rules(self.on_a_terminal("--no-color"))
        self.assertEqual(len(rules), 2, rules)
        for rule in rules:
            self.assertNotIn(ESCAPE, rule)


if __name__ == "__main__":
    unittest.main()
