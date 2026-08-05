"""The exit codes the README tabulates, from real runs of the real command.

The README publishes a four-row table — 0, 2, 130, 141 — and then makes a
claim that is not in it:

    There is deliberately no exit `1`. agentwatch reports what an agent is
    doing; it does not judge it. Nothing it can see is a failure of yours.

That is a promise about a number that never appears, and a promise like that
decays quietly.  Somebody adds a perfectly reasonable `return 1` for a bad
config file one day, and a pipeline that reads agentwatch's code as
"something is wrong with my agent" starts being right for the wrong reason.
Nothing checked it, and nothing checked the four rows either — the table was
prose next to code.

`130` and `141` need signals to produce and already have their own tests
(test_interrupt, test_broken_pipe).  What was missing is everything else:
that the ordinary run is 0, that a `--home` pointing nowhere is 2 and not a
traceback, that an unknown flag is 2, that the table and the source agree,
and that 1 is absent from both.

Exit 2 is argparse's, not agentwatch's — `parser.error()` raises SystemExit(2)
from inside the standard library, so it is not a constant this source
returns.  It is listed in `_FROM_ARGPARSE` rather than being quietly allowed,
and the runs below prove it actually comes out.
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from tests.fixtures import jsonl, user  # noqa: E402

README = os.path.join(_ROOT, "README.md")
CLI_SOURCE = os.path.join(_ROOT, "agentwatch", "cli.py")
SHELL_SOURCE = os.path.join(_ROOT, "agentwatch", "shell.py")

# "| `130`| Ctrl-C during `--once` — the output is cut short |"
_ROW = re.compile(r"^\|\s*`(\d{1,3})`\s*\|(.+)\|\s*$")

# Codes agentwatch never returns itself, with where they come from instead.
_FROM_ARGPARSE = {2: "parser.error() raises SystemExit(2) from argparse"}


def readme() -> str:
    with open(README, encoding="utf-8") as handle:
        return handle.read()


def documented_codes(text):
    """The codes in the README's exit-code table."""
    start = text.find("## Exit codes")
    if start < 0:
        return set()
    end = text.find("\n---", start)
    return {int(found.group(1))
            for found in (_ROW.match(line)
                          for line in text[start:end].splitlines())
            if found}


def shell_codes():
    """The codes `shell.py` chooses on its own -- the two it gives names to.

    Everything else that module returns is a number this command picked and
    handed back out, and those are counted in cli.py where they were picked.
    These two are picked nowhere else.  They are also the two the README
    documents that no longer appear in cli.py at all, so a reader that stops
    at cli.py sees them vanish and calls that agreement.
    """
    with open(SHELL_SOURCE, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    named = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, int)
                and not isinstance(node.value.value, bool)):
            named[node.targets[0].id] = node.value.value
    returned = {node.value.id for node in ast.walk(tree)
                if isinstance(node, ast.Return)
                and isinstance(node.value, ast.Name)}
    return {named[name] for name in returned & set(named)}


def source_codes():
    """Every constant exit code cli.py produces on its own."""
    with open(CLI_SOURCE, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    codes = set()
    for node in ast.walk(tree):
        value = None
        if isinstance(node, ast.Return):
            value = node.value
        elif (isinstance(node, ast.Call)
              and getattr(node.func, "attr", None) == "exit"):
            value = node.args[0] if node.args else None
        if (isinstance(value, ast.Constant)
                and isinstance(value.value, int)
                and not isinstance(value.value, bool)):
            codes.add(value.value)
    return codes | shell_codes()


def _records():
    """A log with something in it, so a run has a reason to reach an exit."""
    return jsonl([user()])


class TestTheExitCodesTheREADMEPromises(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="aw-exitcode-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        proj = os.path.join(self.home, ".claude", "projects", "p")
        os.makedirs(proj)
        with open(os.path.join(proj, "a.jsonl"), "w") as handle:
            handle.write(_records())

    def run_cli(self, *argv):
        return subprocess.run(
            [sys.executable, "-m", "agentwatch", *argv],
            capture_output=True, text=True,
            env=dict(os.environ, PYTHONPATH=_ROOT))

    def test_the_readme_still_tabulates_exit_codes(self):
        # Without this, deleting the table passes the comparison below by
        # making both sides of it empty.
        self.assertGreaterEqual(len(documented_codes(readme())), 3,
                                "no exit-code table left in README.md")

    def test_the_table_is_the_codes_the_code_produces(self):
        documented = documented_codes(readme())
        self.assertEqual(
            sorted(documented - set(_FROM_ARGPARSE)), sorted(source_codes()),
            "README.md's exit-code table and the codes agentwatch/cli.py "
            "returns disagree")

    def test_the_table_still_lists_the_ones_argparse_produces(self):
        # Subtracting them above means the table could drop the row entirely
        # and the comparison would not notice.  A code that comes out of the
        # command has to be in the table whoever raised it — the person
        # reading the table is looking at exit statuses, not at whose
        # library produced them.
        documented = documented_codes(readme())
        for code, why in _FROM_ARGPARSE.items():
            self.assertIn(code, documented,
                          "README.md's table no longer lists exit {} ({})"
                          .format(code, why))

    def test_there_is_still_deliberately_no_exit_one(self):
        # The README says so in as many words, and the reason it gives is
        # about what this tool is for: it reports, it does not judge.
        self.assertIn("no exit `1`", readme(),
                      "the README no longer claims there is no exit 1 — if "
                      "that changed on purpose, this test changes with it")
        self.assertNotIn(1, source_codes(),
                         "agentwatch/cli.py can now exit 1, which the README "
                         "says it deliberately never does")
        self.assertNotIn(1, documented_codes(readme()))

    def test_an_ordinary_run_is_zero(self):
        proc = self.run_cli("--once", "--home", self.home)
        self.assertEqual(proc.returncode, 0,
                         "an ordinary --once run did not exit 0:\n"
                         + proc.stdout + proc.stderr)

    def test_a_home_that_is_not_there_is_two(self):
        missing = os.path.join(self.home, "no", "such", "dir")
        proc = self.run_cli("--once", "--home", missing)
        self.assertEqual(proc.returncode, 2,
                         "--home pointing nowhere did not exit 2:\n"
                         + proc.stdout + proc.stderr)
        self.assertNotIn("Traceback", proc.stderr, proc.stderr)

    def test_an_unknown_flag_is_two(self):
        proc = self.run_cli("--once", "--home", self.home, "--not-a-flag")
        self.assertEqual(proc.returncode, 2,
                         "an unknown flag did not exit 2:\n"
                         + proc.stdout + proc.stderr)

    def test_an_empty_home_is_still_zero(self):
        # A quiet day is the ordinary case, not an error — this is the run a
        # cron job does most of the time.
        empty = tempfile.mkdtemp(prefix="aw-quiet-")
        self.addCleanup(shutil.rmtree, empty, ignore_errors=True)
        proc = self.run_cli("--once", "--home", empty)
        self.assertEqual(proc.returncode, 0,
                         "an empty home did not exit 0:\n"
                         + proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
