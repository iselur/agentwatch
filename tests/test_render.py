"""How one event becomes one line.

The layout is fixed-width on purpose: a live stream is read by glancing at it,
and a glance needs the mark in the same column every time.  So most of these
tests are about columns staying where they are, whatever the content does.
"""

import io
import json
import os
import sys
import unittest
from datetime import datetime, timezone

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agentwatch.render import (
    ASCII_MARKS, MARKS, PROJECT_WIDTH, format_event, format_json, marks_for,
    terminal_width, use_color, write_line,
)

AT = datetime(2026, 8, 4, 9, 41, 7, tzinfo=timezone.utc)


def event(**kwargs):
    base = {"at": AT, "kind": "cmd", "text": "pytest -x", "project": "api",
            "session": "s1", "source": "claude"}
    base.update(kwargs)
    return base


class Stream:
    """A text sink that can claim an encoding and a tty, like a real stream.

    Not a StringIO subclass: StringIO refuses to let ``encoding`` be set, and
    the encoding is precisely what these tests need to vary.
    """

    def __init__(self, encoding="utf-8", tty=False):
        self._buf = io.StringIO()
        self.encoding = encoding
        self._tty = tty

    def write(self, text):
        return self._buf.write(text)

    def flush(self):
        pass

    def isatty(self):
        return self._tty

    def getvalue(self):
        return self._buf.getvalue()


class TestMarks(unittest.TestCase):

    def test_a_utf8_stream_gets_the_real_marks(self):
        self.assertEqual(marks_for(Stream("utf-8")), MARKS)

    def test_an_ascii_stream_falls_back_rather_than_crashing_later(self):
        self.assertEqual(marks_for(Stream("ascii")), ASCII_MARKS)

    def test_a_nonsense_encoding_falls_back(self):
        self.assertEqual(marks_for(Stream("definitely-not-a-codec")), ASCII_MARKS)

    def test_a_stream_with_no_encoding_at_all_falls_back(self):
        self.assertEqual(marks_for(object()), ASCII_MARKS)

    def test_every_kind_has_a_mark_in_both_sets(self):
        self.assertEqual(sorted(MARKS), sorted(ASCII_MARKS))

    def test_the_marks_are_all_one_column_wide(self):
        for table in (MARKS, ASCII_MARKS):
            for mark in table.values():
                self.assertEqual(len(mark), 1, mark)


class TestColor(unittest.TestCase):

    def test_a_pipe_is_not_colourised(self):
        self.assertFalse(use_color(Stream(tty=False)))

    def test_a_terminal_is(self):
        self.assertTrue(use_color(Stream(tty=True)))

    def test_forcing_beats_everything(self):
        self.assertTrue(use_color(Stream(tty=False), True))
        self.assertFalse(use_color(Stream(tty=True), False))

    def test_no_color_is_honoured(self):
        saved = os.environ.get("NO_COLOR")
        os.environ["NO_COLOR"] = "1"
        try:
            self.assertFalse(use_color(Stream(tty=True)))
        finally:
            if saved is None:
                os.environ.pop("NO_COLOR", None)
            else:
                os.environ["NO_COLOR"] = saved

    def test_a_stream_that_cannot_say_is_not_colourised(self):
        self.assertFalse(use_color(object()))

    def test_colour_is_always_closed_again(self):
        line = format_event(event(), MARKS, color=True, width=100)
        self.assertTrue(line.count("\033[0m") >= 2)
        self.assertFalse(line.endswith("\033["))


class TestLayout(unittest.TestCase):

    def test_the_mark_lands_in_the_same_column_whatever_the_project(self):
        columns = set()
        for project in ("a", "api-server", "a-very-long-project-name-indeed", ""):
            line = format_event(event(project=project), MARKS, width=100)
            columns.add(line.index(MARKS["cmd"]))
        self.assertEqual(len(columns), 1)

    def test_a_long_project_is_cut_not_allowed_to_push_the_column(self):
        line = format_event(event(project="x" * 40), MARKS, width=100)
        self.assertIn("…", line[:8 + 2 + PROJECT_WIDTH + 2])

    def test_an_empty_project_shows_a_dash(self):
        self.assertIn("-", format_event(event(project=""), MARKS, width=100))

    def test_the_line_never_exceeds_the_width(self):
        for width in (40, 60, 80, 100, 200):
            line = format_event(event(text="x" * 500), MARKS, width=width)
            self.assertLessEqual(len(line), width, width)

    def test_a_very_narrow_terminal_still_shows_some_text(self):
        line = format_event(event(text="pytest -x"), MARKS, width=40)
        self.assertIn("pytest", line)

    def test_the_columns_still_line_up_on_a_narrow_terminal(self):
        columns = set()
        for project in ("a", "api-server", "x" * 40, ""):
            line = format_event(event(project=project), MARKS, width=40)
            columns.add(line.index(MARKS["cmd"]))
            self.assertLessEqual(len(line), 40)
        self.assertEqual(len(columns), 1)

    def test_newlines_in_a_command_never_break_the_layout(self):
        line = format_event(event(text="python3 - <<'PY'\nimport os\nPY"), MARKS,
                            width=100)
        self.assertEqual(len(line.splitlines()), 1)
        self.assertIn("import os", line)

    def test_a_tab_is_folded_into_a_space(self):
        line = format_event(event(text="a\tb"), MARKS, width=100)
        self.assertIn("a b", line)
        self.assertNotIn("\t", line)

    def test_the_time_is_local_and_eight_columns(self):
        line = format_event(event(), MARKS, width=100)
        self.assertRegex(line[:8], r"^\s?\d{2}:\d{2}:\d{2}$")

    def test_an_event_with_no_time_still_renders(self):
        line = format_event(event(at=None), MARKS, width=100)
        self.assertIn("--:--", line)
        self.assertIn("pytest -x", line)


class TestWhatEachKindSays(unittest.TestCase):

    def test_a_turn_says_you_and_never_the_message(self):
        line = format_event(event(kind="turn", text="my secret prompt"), MARKS,
                            width=100)
        self.assertIn("you", line)
        self.assertNotIn("secret", line)

    def test_a_write_is_shortened_to_the_project_relative_path(self):
        line = format_event(
            event(kind="write", project="api", text="/home/you/api/src/app.py"),
            MARKS, width=100)
        self.assertIn("src/app.py", line)
        self.assertNotIn("/home/you", line)

    def test_a_write_outside_the_project_keeps_enough_to_find_it(self):
        line = format_event(
            event(kind="write", project="api", text="/etc/hosts"), MARKS, width=100)
        self.assertIn("/etc/hosts", line)

    def test_a_write_under_home_is_tilde_shortened(self):
        home = os.path.expanduser("~")
        if home in ("", os.sep):
            self.skipTest("no home directory to shorten against")
        line = format_event(
            event(kind="write", project="", text=os.path.join(home, "notes.md")),
            MARKS, width=100)
        self.assertIn("~/notes.md", line)

    def test_a_nameless_failure_still_says_something(self):
        line = format_event(event(kind="error", text=""), MARKS, width=100)
        self.assertIn("failed", line)

    def test_an_unknown_kind_gets_a_question_mark_not_a_traceback(self):
        line = format_event(event(kind="whatever"), MARKS, width=100)
        self.assertIn("?", line)


class TestJson(unittest.TestCase):

    def test_the_field_set_is_fixed_and_sorted(self):
        row = json.loads(format_json(event()))
        self.assertEqual(sorted(row),
                         ["at", "kind", "project", "session", "source", "text"])

    def test_the_time_is_iso_with_an_offset(self):
        row = json.loads(format_json(event()))
        self.assertTrue(row["at"].startswith("2026-08-04T09:41:07"))
        self.assertIn("+00:00", row["at"])

    def test_a_missing_time_is_null_not_absent(self):
        row = json.loads(format_json(event(at=None)))
        self.assertIsNone(row["at"])

    def test_non_ascii_survives_the_round_trip(self):
        row = json.loads(format_json(event(text="touch café.py")))
        self.assertEqual(row["text"], "touch café.py")

    def test_a_missing_field_becomes_empty_rather_than_raising(self):
        row = json.loads(format_json({"at": None}))
        self.assertEqual(row["kind"], "")
        self.assertEqual(row["text"], "")

    def test_it_is_always_exactly_one_line(self):
        row = format_json(event(text="a\nb\nc"))
        self.assertEqual(len(row.splitlines()), 1)


class TestWriteLine(unittest.TestCase):

    def test_every_line_is_flushed_immediately(self):
        stream = Stream()
        flushed = []
        stream.flush = lambda: flushed.append(True)
        write_line("hello", stream)
        self.assertTrue(flushed)

    def test_a_glyph_the_terminal_cannot_encode_is_replaced_not_fatal(self):
        class Narrow(Stream):
            def __init__(self):
                super().__init__("ascii")
                self.first = True

            def write(self, text):
                if self.first and any(ord(c) > 127 for c in text):
                    self.first = False
                    raise UnicodeEncodeError("ascii", text, 0, 1, "nope")
                return super().write(text)

        stream = Narrow()
        write_line("09:41  api  ✎ x.py", stream)
        self.assertIn("x.py", stream.getvalue())

    def test_a_broken_pipe_is_raised_for_the_caller_to_handle(self):
        class Broken(Stream):
            def write(self, text):
                raise BrokenPipeError()

        with self.assertRaises(BrokenPipeError):
            write_line("x", Broken())


class TestTerminalWidth(unittest.TestCase):

    def test_the_result_is_always_usable(self):
        self.assertGreaterEqual(terminal_width(), 40)
        self.assertLessEqual(terminal_width(), 200)

    def test_a_nonsense_columns_variable_does_not_raise(self):
        saved = os.environ.get("COLUMNS")
        os.environ["COLUMNS"] = "wide please"
        try:
            self.assertGreaterEqual(terminal_width(), 40)
        finally:
            if saved is None:
                os.environ.pop("COLUMNS", None)
            else:
                os.environ["COLUMNS"] = saved


if __name__ == "__main__":
    unittest.main()
