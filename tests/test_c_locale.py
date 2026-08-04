"""What agentwatch does on a machine whose locale says ASCII.

A container with no locale set is the ordinary case, not the exotic one: it is
what CI runs on, what a Dockerfile without `ENV LANG` gives you, and what cron
hands a hook.  Python takes the locale at its word there — stdout encodes as
ASCII — and a watcher is exactly the thing somebody leaves running in a tmux
pane on a box like that.

This tool does not crash on it; ``write_line`` already catches the encode error
and falls back.  That fallback is the problem being tested here.  It replaces
every character the codec could not take with ``?``, so a file named in Japanese
scrolls past as ``????.py`` — and under ``--json``, where the whole point is
that another program reads the path and does something with it, the path that
arrives is not a path.  A watcher that names the wrong file is worse than one
that says nothing.

Everything here runs the real command in a real subprocess with that
environment, because the codec is chosen when the process starts and cannot be
faked from inside one.
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

from tests.fixtures import ago as _ago, env  # noqa: E402


def _ascii_env():
    """The environment of a container nobody gave a locale to."""
    return env(LC_ALL="C", LANG="C", LANGUAGE="C",
               PYTHONCOERCECLOCALE="0",   # or Python quietly upgrades C to C.UTF-8
               PYTHONUTF8="0",            # or UTF-8 mode overrides the locale
               PYTHONIOENCODING=None)     # None removes it -- see fixtures.env


class TestAnAsciiTerminal(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="agentwatch_locale_")
        folder = os.path.join(self.home, ".claude", "projects", "-home-you-設定")
        os.makedirs(folder)
        path = os.path.join(folder, "s.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            for line in (
                {"type": "user", "cwd": "/home/you/設定", "timestamp": _ago(50),
                 "message": {"role": "user",
                             "content": [{"type": "text", "text": "hi"}]}},
                {"type": "assistant", "timestamp": _ago(45), "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "t1", "name": "Edit",
                                 "input": {"file_path": "/home/you/設定/請求書.py"}}]}},
            ):
                handle.write(json.dumps(line) + "\n")

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def run_watch(self, *args):
        result = subprocess.run(
            [sys.executable, "-m", "agentwatch", "--home", self.home,
             "--once", "--since", "10m"] + list(args),
            capture_output=True, text=True, encoding="utf-8", env=_ascii_env(),
            cwd=_ROOT, timeout=60)
        self.assertNotIn("Traceback", result.stderr,
                         "{}: {}".format(args, result.stderr))
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_the_file_it_names_is_the_file_that_changed(self):
        # The one thing a watcher is for.  `????.py` is not a filename anybody
        # can go and look at.
        out = self.run_watch("--no-color")
        self.assertIn("請求書.py", out, out)

    def test_the_project_it_names_is_the_project(self):
        out = self.run_watch("--no-color")
        self.assertIn("設定", out, out)

    def test_a_script_reading_the_json_gets_a_real_path(self):
        # Here the output is not read by a person at all — it is fed to
        # something that will open the file, or grep for it, or post it.
        paths = []
        for line in self.run_watch("--json").splitlines():
            if line.strip():
                paths.append(json.loads(line).get("text") or "")
        self.assertTrue(any("請求書.py" in p for p in paths), paths)

    def test_the_project_filter_still_matches(self):
        # Filtering happens before printing, so this would keep working even
        # with the output mangled — it is here so that it keeps working after
        # the fix, too.
        out = self.run_watch("--no-color", "--project", "設定")
        self.assertIn("請求書.py", out, out)


if __name__ == "__main__":
    unittest.main()
