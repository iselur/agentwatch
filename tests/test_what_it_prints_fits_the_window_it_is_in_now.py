"""A window resized during a follow was never noticed.

`agentwatch` with no arguments follows until you stop it, which is usually
hours.  The width every line was fitted to was read once, before the loop
started, and never again — so widening the terminal left the output in a narrow
column down the left-hand side with the text still cut off at the old edge, and
narrowing it left every line wrapping onto a second one, which is exactly what
the fixed layout exists to prevent.  It stayed that way until the command was
restarted.

Nothing in the renderer was wrong.  The width was the caller's to hold, because
printing a line took seven names called in the right order with three derived
values carried between them, and one of those values was a fact about the
terminal — the kind of fact that goes stale while you are holding it.

So the sequence lives in `printer` now, the command hands it events, and the
width is read for the line it is about to fit.  These are the promises that
made moving it worth doing.
"""

import io
import os
import sys
import unittest
from datetime import datetime, timedelta

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from agentwatch import cli, printer                        # noqa: E402
from agentwatch.printer import Printer                     # noqa: E402


def _at(days_back=0, hour=9):
    """An aware stamp that many days back, at a fixed hour of that day."""
    day = (datetime.now().astimezone() - timedelta(days=days_back)).date()
    return datetime(day.year, day.month, day.day, hour, 22, 58).astimezone()


def an_event(text="pytest -k something_with_a_fairly_long_name_on_the_end",
             kind="cmd", at=None, project="proj"):
    return {"at": at if at is not None else _at(0),
            "kind": kind, "text": text, "project": project, "session": "s",
            "source": "claude"}


class _Reporting:
    """A window whose width changes between one line and the next."""

    def __init__(self, test, *widths):
        self.widths = list(widths)
        self.asked = 0
        real = printer.terminal_width
        test.addCleanup(setattr, printer, "terminal_width", real)
        printer.terminal_width = self

    def __call__(self):
        self.asked += 1
        return self.widths[min(self.asked - 1, len(self.widths) - 1)]


def lines(buffer):
    return [line for line in buffer.getvalue().split("\n") if line]


class TestTheWidthIsTheOneTheWindowHasNow(unittest.TestCase):

    def test_a_window_widened_mid_run_gets_wider_lines(self):
        # The bug, stated as the thing somebody would do: start a follow, drag
        # the window out, keep watching.  Before, the second line was fitted to
        # the width the first one was.
        _Reporting(self, 60, 120)
        out = io.StringIO()
        p = Printer(out, color=False)
        p.write(an_event())
        p.write(an_event())
        first, second = lines(out)
        self.assertGreater(len(second), len(first),
                           "the window was widened and the line did not follow")

    def test_a_window_narrowed_mid_run_stops_overflowing_it(self):
        # The worse direction: a line longer than the window wraps, and a
        # wrapped line puts the marks in a different column on every other row,
        # which is the whole reason the layout is fixed-width.
        _Reporting(self, 120, 60)
        out = io.StringIO()
        p = Printer(out, color=False)
        p.write(an_event())
        p.write(an_event())
        for line in lines(out):
            self.assertLessEqual(len(line), 120)
        self.assertLessEqual(len(lines(out)[1]), 60,
                             "the line kept the width the window used to have")

    def test_it_asks_once_for_every_line_it_prints(self):
        # Said directly, so that caching it back is caught as itself rather
        # than through whichever line happened to change length.
        window = _Reporting(self, 80)
        out = io.StringIO()
        p = Printer(out, color=False)
        for _ in range(3):
            p.write(an_event())
        self.assertEqual(window.asked, 3)

    def test_the_dated_rule_spans_the_window_it_is_drawn_in(self):
        # The rule is a run of dashes to the edge; drawn at the old width it is
        # visibly short or wraps, and it is the one line on screen whose length
        # is exactly the promise.
        # One width per line printed, and the rule rides along with the event
        # that caused it -- both are drawn to the width asked for that event.
        _Reporting(self, 70, 110)
        out = io.StringIO()
        p = Printer(out, color=False)
        p.write(an_event(at=_at(3)))
        p.write(an_event(at=_at(2)))
        rules = [line for line in lines(out) if line.startswith("──")]
        self.assertEqual([len(r) for r in rules], [70, 110], rules)


class TestTheDayIsTheReadersAndTheStateIsThePrinters(unittest.TestCase):
    """Crossing midnight is a thing that happens mid-run, so it is held here."""

    def test_a_run_that_crosses_a_day_dates_both_of_them(self):
        out = io.StringIO()
        p = Printer(out, color=False)
        p.write(an_event(at=_at(4)))
        p.write(an_event(at=_at(2)))
        self.assertEqual(len(lines(out)), 4, lines(out))

    def test_two_events_on_the_same_day_are_dated_once(self):
        out = io.StringIO()
        p = Printer(out, color=False)
        p.write(an_event(at=_at(4, hour=9)))
        p.write(an_event(at=_at(4, hour=17)))
        self.assertEqual(len(lines(out)), 3, lines(out))

    def test_todays_events_are_not_dated_at_all(self):
        # Writing "today" across the top of a live watch is noise about the one
        # date nobody has to be told.
        out = io.StringIO()
        p = Printer(out, color=False)
        p.write(an_event(at=_at(0)))
        self.assertEqual(len(lines(out)), 1, lines(out))

    def test_two_printers_do_not_share_a_day(self):
        # The state belongs to the run that is printing, not to the module.  A
        # second command in the same process starts its own page.
        first, second = io.StringIO(), io.StringIO()
        Printer(first, color=False).write(an_event(at=_at(3)))
        Printer(second, color=False).write(an_event(at=_at(3)))
        self.assertEqual(len(lines(first)), len(lines(second)), 2)
        self.assertEqual(len(lines(second)), 2, lines(second))


class TestTheMachineStreamIsNotATerminal(unittest.TestCase):

    def test_json_gets_one_line_for_one_event_across_any_number_of_days(self):
        out = io.StringIO()
        p = Printer(out, as_json=True)
        p.write(an_event(at=_at(4)))
        p.write(an_event(at=_at(2)))
        self.assertEqual(len(lines(out)), 2, "a display rule leaked into --json")

    def test_json_never_asks_how_wide_the_window_is(self):
        # Nothing downstream of a pipe has a width, and asking would make the
        # machine output depend on the terminal that happened to launch it.
        window = _Reporting(self, 80)
        out = io.StringIO()
        p = Printer(out, as_json=True)
        p.write(an_event())
        self.assertEqual(window.asked, 0)


class TestItReadsTheStreamItWasGiven(unittest.TestCase):
    """Marks and colour are facts about this stream, not about ``sys.stdout``."""

    class _AsciiOnly(io.StringIO):
        encoding = "ascii"

        def isatty(self):
            return False

    def test_a_stream_that_cannot_carry_the_marks_gets_the_ascii_ones(self):
        out = self._AsciiOnly()
        Printer(out, color=False).write(an_event(kind="write", text="a.py"))
        self.assertIn(" w ", out.getvalue())
        self.assertNotIn("✎", out.getvalue())

    def test_colour_stays_off_on_a_stream_nobody_is_watching(self):
        out = io.StringIO()
        Printer(out).write(an_event())
        self.assertNotIn("\033[", out.getvalue())

    def test_colour_can_be_insisted_on_anyway(self):
        # `use_color(stream, True)` is how a pager or a CI log that does render
        # escapes asks for them; the printer has to pass the answer through
        # rather than decide for itself.
        out = io.StringIO()
        Printer(out, color=True).write(an_event())
        self.assertIn("\033[", out.getvalue())


class TestTheCommandNoLongerDrawsAnything(unittest.TestCase):
    """The point of the move, stated as what `cli` is no longer able to do."""

    def test_the_command_does_not_know_how_a_line_is_drawn(self):
        for name in ("day_rule", "format_event", "format_json", "marks_for",
                     "terminal_width", "use_color", "write_line"):
            self.assertFalse(
                hasattr(cli, name),
                "cli still reaches past the printer for {!r}".format(name))

    def test_the_printer_is_the_whole_of_what_it_needs(self):
        self.assertIs(cli.Printer, Printer)


if __name__ == "__main__":
    unittest.main()
