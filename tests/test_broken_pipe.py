"""`agentwatch | head` is a normal thing to do, and it used to be half-handled.

This is a tailer, so piping it somewhere that stops reading is not an edge
case — it is most of how people use it: `| head -20` to glance at the last few
events, `| less` and quit with `q`, `| grep -q Bash` that stops as soon as it
has its answer.  The next write fails with EPIPE, Python raises
`BrokenPipeError`, and unhandled the interpreter prints

    Exception ignored in: <_io.TextIOWrapper name='<stdout>' ...>
    BrokenPipeError: [Errno 32] Broken pipe

over the output and exits 120.

The polling loop already caught it.  `--help` and `--version` did not, because
argparse prints those and exits before the loop is ever built — so the handler
has to be outside argparse, not inside the part that watches files.

141 is 128 + SIGPIPE, the shell's own spelling of "the reader hung up", and the
answer the rest of the family gives.  Ctrl-c stays 0 here: stopping a tailer
with ctrl-c is how you use it, not a failure.

The read end is closed before the command writes a byte, so none of this
depends on how much output there is or on the size of the pipe buffer.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from tests.fixtures import ago as _ago, env as _env  # noqa: E402


def run_with_no_reader(args):
    """Run the CLI with a stdout pipe whose read end is already closed."""
    read_fd, write_fd = os.pipe()
    os.close(read_fd)                       # the reader went away
    proc = subprocess.Popen(
        [sys.executable, "-m", "agentwatch"] + list(args),
        stdout=write_fd, stderr=subprocess.PIPE, cwd=_ROOT, env=_env())
    os.close(write_fd)
    _, err = proc.communicate(timeout=120)
    return proc.returncode, err.decode("utf-8", "replace")


def run_normally(args):
    proc = subprocess.Popen(
        [sys.executable, "-m", "agentwatch"] + list(args),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=_ROOT, env=_env())
    out, err = proc.communicate(timeout=120)
    return (proc.returncode,
            out.decode("utf-8", "replace"),
            err.decode("utf-8", "replace"))


class TestTheReaderHungUp(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="agentwatch_epipe_")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        folder = os.path.join(self.home, ".claude", "projects", "-tmp-proj")
        os.makedirs(folder)
        lines = []
        for i in range(60):
            lines.append({
                "type": "user", "cwd": "/tmp/proj", "timestamp": _ago(600 - i),
                "message": {"role": "user",
                            "content": [{"type": "text", "text": "hi"}]}})
            lines.append({
                "type": "assistant", "timestamp": _ago(600 - i),
                "message": {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "t{}".format(i), "name": "Bash",
                     "input": {"command": "pytest -x -k case{}".format(i)}}]}})
        path = os.path.join(folder, "4ef1361b-07e4-4bc9-bb29-1783b761d677.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            for line in lines:
                fh.write(json.dumps(line) + "\n")

    def commands(self):
        home = ["--home", self.home]
        return [
            home + ["--once", "--since", "1h"],
            home + ["--once", "--since", "1h", "--json"],
            home + ["--once", "--since", "1h", "--no-color"],
            home + ["--once", "--since", "1h", "--reads"],
            ["--version"],
            ["--help"],
        ]

    def test_nothing_is_printed_about_a_broken_pipe(self):
        for args in self.commands():
            with self.subTest(args=args[-2:]):
                _, err = run_with_no_reader(args)
                self.assertNotIn("BrokenPipeError", err, err)
                self.assertNotIn("Exception ignored", err, err)

    def test_it_is_not_a_traceback(self):
        for args in self.commands():
            with self.subTest(args=args[-2:]):
                _, err = run_with_no_reader(args)
                self.assertNotIn("Traceback", err, err)

    def test_the_exit_code_says_the_reader_hung_up(self):
        for args in self.commands():
            with self.subTest(args=args[-2:]):
                code, err = run_with_no_reader(args)
                self.assertEqual(code, 141,
                                 "{} -> {}\n{}".format(args[-2:], code, err))

    def test_help_and_version_are_covered_too(self):
        # These print and exit inside argparse, before the watcher exists.
        for args in (["--version"], ["--help"]):
            with self.subTest(args=args):
                code, err = run_with_no_reader(args)
                self.assertEqual(code, 141, err)
                self.assertEqual(err, "", err)

    def test_a_reader_that_stays_still_gets_the_real_answer(self):
        code, out, err = run_normally(
            ["--home", self.home, "--once", "--since", "1h", "--no-color"])
        self.assertEqual(code, 0, err)
        self.assertIn("pytest", out, out)


if __name__ == "__main__":
    unittest.main()
