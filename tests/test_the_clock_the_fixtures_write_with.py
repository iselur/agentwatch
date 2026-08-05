"""Fixtures that write "fifty minutes ago" and mean "earlier today".

Those are the same thing for twenty-three hours and ten minutes a day.  For the
other fifty minutes the records land in yesterday, the renderer correctly draws
a day rule between them, and every test that counts printed lines is one over.

Two ways in, and this file guards both:

  * a stamp measured back from the real clock, which crosses midnight (see
    `fixtures.a_now_that_keeps`); and
  * a date written down as a literal, which crosses it once and stays crossed.
    `--since 2026-08-04T23:59:59` was a window that excluded everything, right
    up until 2026-08-05, when it began excluding nothing.  A test that changes
    its mind on a date is worse than one that never worked: the diff that day
    is somebody else's.

The clock cannot be pinned -- some of these fixtures are read by subprocess
runs of the real command, which reads the real one -- so the fixture's clock
moves forward off midnight instead.  That helper is the whole fix, so it is
what gets tested, with a `now` handed in.
"""

from __future__ import annotations

import ast
import os
import re
import sys
import unittest
from datetime import datetime, timedelta

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from tests.fixtures import a_now_that_keeps, midnight_today

# The files whose fixtures write day-anchored stamps and then count what the
# renderer printed, so a day rule they did not expect is a failure.
THE_DAY_ANCHORED_ONES = (
    "test_day_boundaries.py",
    "test_interrupt.py",
)

# A window edge is the one place a date literal is read against the real clock.
THE_WINDOW_FLAGS = ("--since", "--until")
_A_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _at(hour, minute):
    """A local `now` at that time on an ordinary day."""
    return datetime(2026, 8, 5, hour, minute).astimezone()


class TestMidnightToday(unittest.TestCase):

    def test_it_is_the_start_of_the_day_that_now_is_in(self):
        self.assertEqual(midnight_today(_at(14, 30)), _at(0, 0))

    def test_midnight_is_its_own_midnight(self):
        self.assertEqual(midnight_today(_at(0, 0)), _at(0, 0))

    def test_it_keeps_the_offset_it_was_given(self):
        # Local midnight, not UTC midnight: a day rule marks a local day.
        self.assertEqual(midnight_today(_at(3, 0)).utcoffset(),
                         _at(3, 0).utcoffset())


class TestANowThatKeeps(unittest.TestCase):

    def test_the_ordinary_hour_gets_the_real_clock_back(self):
        now = _at(14, 30)
        self.assertEqual(a_now_that_keeps(50, now), now)

    def test_the_first_minutes_of_a_day_are_moved_forward(self):
        self.assertEqual(a_now_that_keeps(50, _at(0, 3)), _at(0, 50))

    def test_what_it_hands_back_always_has_the_history_behind_it(self):
        # The property the callers actually depend on, at every minute of the
        # window where it matters and a few outside it.
        for minute in range(0, 90):
            for reach in (1, 10, 30, 50, 60):
                now = _at(0, 0) + timedelta(minutes=minute)
                moved = a_now_that_keeps(reach, now)
                self.assertGreaterEqual(
                    moved - timedelta(minutes=reach), midnight_today(now),
                    "{}m of history at 00:{:02d} still lands in yesterday"
                    .format(reach, minute))

    def test_it_never_moves_the_clock_backwards(self):
        for minute in (0, 1, 49, 50, 51, 600):
            now = _at(0, 0) + timedelta(minutes=minute)
            self.assertGreaterEqual(a_now_that_keeps(50, now), now)

    def test_asking_for_nothing_changes_nothing(self):
        self.assertEqual(a_now_that_keeps(0, _at(0, 0)), _at(0, 0))

    def test_the_fifty_minute_reach_this_suite_actually_uses(self):
        # test_interrupt writes its oldest event 3000 seconds back.
        from tests.test_interrupt import _WithSomeEvents
        self.assertGreaterEqual(
            a_now_that_keeps(_WithSomeEvents.OLDEST_MINUTES, _at(0, 0))
            - timedelta(minutes=_WithSomeEvents.OLDEST_MINUTES),
            midnight_today(_at(0, 0)))


class TestTheFixturesStillGoThroughIt(unittest.TestCase):
    """A cheap guard: the helper is easy to stop calling and hard to miss."""

    def test_each_one_imports_the_helper(self):
        for name in THE_DAY_ANCHORED_ONES:
            with self.subTest(name):
                tree = ast.parse(open(os.path.join(_ROOT, "tests", name),
                                      encoding="utf-8").read())
                imported = {alias.name
                            for node in ast.walk(tree)
                            if isinstance(node, ast.ImportFrom)
                            and (node.module or "").endswith("fixtures")
                            for alias in node.names}
                self.assertIn("a_now_that_keeps", imported,
                              "{} writes day-anchored stamps off the raw clock"
                              .format(name))


class TestNoWindowEdgeIsWrittenDown(unittest.TestCase):
    """The time bomb, caught by shape rather than by waiting for the date.

    A pinned date is fine everywhere else in here -- a fixture that writes
    09:00 and asserts 09:00 agrees with itself forever.  It is a window edge
    that is read against the real clock, and only there does a literal have a
    shelf life.
    """

    def _window_literals(self, path):
        tree = ast.parse(open(path, encoding="utf-8").read())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            args = [a.value if isinstance(a, ast.Constant) else None
                    for a in node.args]
            for flag, following in zip(args, args[1:]):
                if flag in THE_WINDOW_FLAGS and isinstance(following, str) \
                        and _A_DATE.match(following):
                    yield node.lineno, flag, following

    def test_no_test_pins_a_window_edge_to_a_date(self):
        found = []
        for name in sorted(os.listdir(os.path.join(_ROOT, "tests"))):
            if not name.endswith(".py"):
                continue
            for lineno, flag, value in self._window_literals(
                    os.path.join(_ROOT, "tests", name)):
                found.append("tests/{}:{} {} {}".format(name, lineno, flag, value))
        self.assertEqual(found, [],
                         "a window edge with an expiry date:\n  "
                         + "\n  ".join(found))


if __name__ == "__main__":
    unittest.main()
