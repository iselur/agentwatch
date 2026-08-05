"""The command line: what the flags mean, and what a person sees when they are wrong.

Most of these run ``main()`` in-process against a scratch home directory, so
nothing here can see or touch a real session log.
"""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agentwatch.cli import build_parser, main, parse_kinds, parse_since


@contextmanager
def captured():
    """stdout and stderr as text, restored afterwards."""
    out, err = io.StringIO(), io.StringIO()
    saved = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        yield out, err
    finally:
        sys.stdout, sys.stderr = saved


def ago(seconds):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


class Home(unittest.TestCase):
    """A scratch home with whatever logs a test wants in it."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="agentwatch-cli-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)

    def claude_log(self, project, lines, name="4ef1361b-07e4-4bc9-bb29-1783b761d677"):
        folder = os.path.join(self.home, ".claude", "projects", project)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name + ".jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            for line in lines:
                handle.write(json.dumps(line) + "\n")
        return path

    def run_cli(self, *argv):
        with captured() as (out, err):
            code = main(["--home", self.home] + list(argv))
        return code, out.getvalue(), err.getvalue()


class TestSince(unittest.TestCase):

    def test_the_offsets_people_actually_type(self):
        now = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
        for raw, delta in (("10m", timedelta(minutes=10)), ("2h", timedelta(hours=2)),
                           ("3d", timedelta(days=3)), ("1w", timedelta(weeks=1))):
            self.assertEqual(parse_since(raw, now), now - delta, raw)

    def test_case_does_not_matter(self):
        now = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(parse_since("2H", now), parse_since("2h", now))

    def test_a_bare_date_is_local_midnight(self):
        parsed = parse_since("2026-08-03")
        self.assertEqual((parsed.year, parsed.month, parsed.day), (2026, 8, 3))
        self.assertIsNotNone(parsed.tzinfo)

    def test_zero_and_negative_are_refused(self):
        for raw in ("0m", "0h", "0d"):
            with self.assertRaises(ValueError):
                parse_since(raw)

    def test_nonsense_says_what_to_type_instead(self):
        for raw in ("", "   ", "yesterday", "10", "m10", "10y", "--since"):
            with self.assertRaises(ValueError) as caught:
                parse_since(raw)
            self.assertRegex(str(caught.exception), r"10m|2h|3d")

    def test_a_number_too_large_to_subtract_is_a_message_not_a_traceback(self):
        with self.assertRaises(ValueError):
            parse_since("999999999999999w")


class TestOnly(unittest.TestCase):

    def test_a_list_keeps_its_order_and_drops_repeats(self):
        self.assertEqual(parse_kinds("error,cmd,error"), ("error", "cmd"))

    def test_spacing_and_case_are_forgiven(self):
        self.assertEqual(parse_kinds(" CMD , Write "), ("cmd", "write"))

    def test_an_unknown_kind_lists_the_known_ones(self):
        with self.assertRaises(ValueError) as caught:
            parse_kinds("cmd,bogus")
        message = str(caught.exception)
        self.assertIn("bogus", message)
        self.assertIn("cmd", message)

    def test_empty_is_refused(self):
        for raw in ("", " ", ",,,"):
            with self.assertRaises(ValueError):
                parse_kinds(raw)


class TestRunning(Home):

    def setUp(self):
        super().setUp()
        self.claude_log("-home-you-api-server", [
            {"type": "user", "cwd": "/home/you/api-server", "timestamp": ago(50),
             "message": {"role": "user", "content": [{"type": "text", "text": "hi"}]}},
            {"type": "assistant", "timestamp": ago(45), "message": {
                "role": "assistant", "content": [{"type": "tool_use", "id": "t1",
                 "name": "Bash", "input": {"command": "pytest -x"}}]}},
            {"type": "user", "timestamp": ago(40), "message": {
                "role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                 "is_error": True, "content": "boom"}]}},
            {"type": "assistant", "timestamp": ago(35), "message": {
                "role": "assistant", "content": [{"type": "tool_use", "id": "t2",
                 "name": "Edit", "input": {"file_path": "/home/you/api-server/a.py"}}]}},
        ])

    def test_once_prints_and_exits_zero(self):
        code, out, _ = self.run_cli("--once", "--since", "10m", "--no-color")
        self.assertEqual(code, 0)
        self.assertIn("pytest -x", out)
        self.assertIn("a.py", out)

    def test_only_error_shows_the_failure_and_nothing_else(self):
        _code, out, _ = self.run_cli("--once", "--since", "10m", "--only", "error")
        self.assertEqual(len(out.strip().splitlines()), 1)
        self.assertIn("pytest -x", out)

    def test_json_is_one_object_per_line_with_a_stable_field_set(self):
        _code, out, _ = self.run_cli("--once", "--since", "10m", "--json")
        rows = [json.loads(line) for line in out.strip().splitlines()]
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(sorted(row), ["at", "kind", "project", "session",
                                           "source", "text"])

    def test_json_keeps_stdout_clean_when_there_is_nothing(self):
        _code, out, err = self.run_cli("--once", "--since", "10m", "--json",
                                       "--project", "nothing-matches-this")
        self.assertEqual(out, "")
        self.assertEqual(err, "")

    def test_an_empty_result_explains_itself_on_stderr(self):
        _code, out, err = self.run_cli("--once", "--since", "10m",
                                       "--project", "nothing-matches-this")
        self.assertEqual(out, "")
        self.assertIn("nothing-matches-this", err)

    def test_project_filters_by_substring(self):
        _code, out, _ = self.run_cli("--once", "--since", "10m", "--project", "api")
        self.assertIn("pytest -x", out)

    def test_a_window_that_excludes_everything_says_so(self):
        # Derived, not written down.  This was the literal 2026-08-04T23:59:59,
        # which excluded everything until 2026-08-05 and then excluded nothing
        # -- a test that passes for a while and starts failing on a date, with
        # nothing in the diff to point at.
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).astimezone().strftime(
            "%Y-%m-%dT%H:%M:%S")
        _code, out, err = self.run_cli("--once", "--since", tomorrow,
                                       "--no-color")
        self.assertEqual(out, "")
        self.assertIn("window", err)

    def test_reads_are_off_until_asked_for(self):
        self.claude_log("-home-you-api-server", [
            {"type": "assistant", "timestamp": ago(20), "message": {
                "role": "assistant", "content": [{"type": "tool_use", "id": "r1",
                 "name": "Read", "input": {"file_path": "/home/you/api-server/b.py"}}]}},
        ], name="99999999-0000-0000-0000-000000000000")
        _code, without, _ = self.run_cli("--once", "--since", "10m")
        _code, with_reads, _ = self.run_cli("--once", "--since", "10m", "--reads")
        self.assertNotIn("b.py", without)
        self.assertIn("b.py", with_reads)

    def test_codex_only_ignores_claude_logs(self):
        _code, out, err = self.run_cli("--once", "--since", "10m", "--codex")
        self.assertEqual(out, "")
        self.assertIn("no", err)

    def test_version_prints_and_exits_zero(self):
        with captured() as (out, _err):
            with self.assertRaises(SystemExit) as caught:
                main(["--version"])
        self.assertEqual(caught.exception.code, 0)
        self.assertIn("agentwatch", out.getvalue())


class TestUsageErrors(Home):
    """Every one of these is exit 2 — never 1, and never a traceback."""

    def _exit_code(self, argv):
        with captured():
            with self.assertRaises(SystemExit) as caught:
                main(argv)
        return caught.exception.code

    def test_a_bad_since_is_exit_two(self):
        self.assertEqual(self._exit_code(["--home", self.home, "--since", "soon"]), 2)

    def test_a_bad_only_is_exit_two(self):
        self.assertEqual(self._exit_code(["--home", self.home, "--only", "bogus"]), 2)

    def test_a_home_that_is_not_there_is_exit_two(self):
        missing = os.path.join(self.home, "nope", "nope")
        self.assertEqual(self._exit_code(["--home", missing]), 2)

    def test_a_zero_interval_is_exit_two(self):
        self.assertEqual(
            self._exit_code(["--home", self.home, "--interval", "0"]), 2)

    def test_a_negative_stale_is_exit_two(self):
        self.assertEqual(
            self._exit_code(["--home", self.home, "--stale", "-5"]), 2)

    def test_an_unknown_flag_is_exit_two(self):
        self.assertEqual(self._exit_code(["--home", self.home, "--wat"]), 2)


class TestEnvironment(Home):

    def test_agentwatch_home_is_honoured_when_no_flag_is_given(self):
        saved = os.environ.get("AGENTWATCH_HOME")
        os.environ["AGENTWATCH_HOME"] = self.home
        try:
            self.claude_log("-home-you-x", [
                {"type": "assistant", "timestamp": ago(10), "message": {
                    "role": "assistant", "content": [{"type": "tool_use", "id": "t",
                     "name": "Bash", "input": {"command": "echo hi"}}]}},
            ])
            with captured() as (out, _err):
                main(["--once", "--since", "10m", "--no-color"])
            self.assertIn("echo hi", out.getvalue())
        finally:
            if saved is None:
                os.environ.pop("AGENTWATCH_HOME", None)
            else:
                os.environ["AGENTWATCH_HOME"] = saved


class TestHelp(unittest.TestCase):

    def test_help_mentions_that_it_reads_no_message_text(self):
        text = build_parser().format_help()
        self.assertIn("message", text)

    def test_every_flag_has_help_text(self):
        for action in build_parser()._actions:
            self.assertTrue(action.help, action.dest)


if __name__ == "__main__":
    unittest.main()
