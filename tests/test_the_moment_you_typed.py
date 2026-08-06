"""`agentlog since 10m` and `agentwatch --since 10m` are the same question.

They were not the same answer.  One tool knew minutes and the other did not, so
the spelling printed as the first example in `agentwatch --help` was a usage
error in `agentlog` — from the same install, on the same logs, five seconds
apart.  Nobody chose that; it is what two parsers do when each is edited by
somebody looking at one of them.

So the spellings are one module now, and this file is what it promises.  The
promise is small enough to say in a sentence: a length of time back from now,
or a date, and if it is neither then a message naming the forms that work.

The two things worth being careful about are both here.  A bare date is
midnight *on that date* — resolved against the rules in force then, not
midnight plus today's offset, which is an hour out for half the year in a zone
that observes daylight saving and does not look like an error when it happens.
And every example the module prints has to be an example it accepts, checked by
parsing the sentence back rather than by reading it, because a help string that
promises a spelling the parser drops is worse than never offering it: the
person who typed it has been told by the program that it works.

The moment is an argument throughout.  A test that cannot name `now` asks a
different question every time it runs, and a different one again after
midnight.
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agentwatch.when import (  # noqa: E402
    HOW_TO_SPELL_IT,
    is_a_length,
    parse_moment,
)

NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)


class TestALengthOfTimeBackFromNow(unittest.TestCase):

    def test_the_four_units(self):
        for raw, delta in (("10m", timedelta(minutes=10)),
                           ("2h", timedelta(hours=2)),
                           ("3d", timedelta(days=3)),
                           ("1w", timedelta(weeks=1))):
            with self.subTest(raw):
                self.assertEqual(parse_moment(raw, NOW), NOW - delta)

    def test_minutes_because_that_is_the_one_that_was_missing(self):
        # Named on its own, because this is the bug: `agentlog since 10m` said
        # it could not read the argument while `agentwatch --since 10m` was the
        # first line of its README.
        self.assertEqual(parse_moment("10m", NOW), NOW - timedelta(minutes=10))

    def test_case_does_not_matter(self):
        self.assertEqual(parse_moment("2H", NOW), parse_moment("2h", NOW))
        self.assertEqual(parse_moment("30M", NOW), parse_moment("30m", NOW))

    def test_a_space_before_the_unit_is_still_a_length(self):
        self.assertEqual(parse_moment("3 d", NOW), parse_moment("3d", NOW))

    def test_surrounding_space_is_ignored(self):
        self.assertEqual(parse_moment("  2h  ", NOW), parse_moment("2h", NOW))

    def test_zero_is_refused_because_it_is_a_window_from_now_until_now(self):
        for raw in ("0m", "0h", "0d", "0w"):
            with self.subTest(raw):
                with self.assertRaises(ValueError):
                    parse_moment(raw, NOW)

    def test_a_negative_length_is_the_future_and_is_refused(self):
        for raw in ("-3d", "-1h"):
            with self.subTest(raw):
                with self.assertRaises(ValueError):
                    parse_moment(raw, NOW)

    def test_a_number_larger_than_time_is_a_message_not_a_traceback(self):
        with self.assertRaises(ValueError) as caught:
            parse_moment("999999999999999w", NOW)
        self.assertIn("further back than time goes", str(caught.exception))


class TestADate(unittest.TestCase):

    def test_a_bare_date_is_midnight_local(self):
        parsed = parse_moment("2026-08-03", NOW)
        self.assertEqual((parsed.year, parsed.month, parsed.day), (2026, 8, 3))
        self.assertEqual((parsed.hour, parsed.minute), (0, 0))
        self.assertIsNotNone(parsed.tzinfo)

    def test_a_date_is_midnight_on_that_date_not_midnight_plus_todays_offset(self):
        # The whole of the daylight-saving correction, stated as one equality:
        # whatever the platform says midnight on that date was, that is the
        # answer.  test_day_boundaries_across_dst.py pins the zone and asks it
        # in both halves of the year.
        for iso in ("2026-01-15", "2026-07-15"):
            with self.subTest(iso):
                y, m, d = (int(part) for part in iso.split("-"))
                self.assertEqual(parse_moment(iso, NOW),
                                 datetime(y, m, d).astimezone())

    def test_a_time_of_day_is_kept(self):
        # `agentlog` used to refuse this outright while `agentwatch` took it,
        # so the same string meant "from half past two" at one command and "not
        # a date" at the other.
        parsed = parse_moment("2026-08-03T14:30", NOW)
        self.assertEqual((parsed.hour, parsed.minute), (14, 30))

    def test_a_trailing_z_is_utc(self):
        self.assertEqual(parse_moment("2026-08-03T14:30:00Z", NOW),
                         datetime(2026, 8, 3, 14, 30, tzinfo=timezone.utc))

    def test_a_written_out_offset_is_kept_as_written(self):
        self.assertEqual(parse_moment("2026-08-03T14:30:00+02:00", NOW),
                         datetime(2026, 8, 3, 12, 30, tzinfo=timezone.utc))

    def test_the_two_spellings_of_utc_are_one_moment(self):
        # `fromisoformat` learned to read a trailing `Z` in 3.11, and this
        # package supports 3.9.  On this interpreter the rewrite in the module
        # is a no-op and deleting it changes nothing; on the floor it is the
        # difference between a moment and an error.  So the assertion is the
        # equivalence, which is true on every interpreter, rather than the
        # rewrite, which can only be observed on the old one.
        self.assertEqual(parse_moment("2026-08-03T14:30:00Z", NOW),
                         parse_moment("2026-08-03T14:30:00+00:00", NOW))

    def test_a_date_that_is_not_a_date(self):
        for raw in ("2026-13-01", "2026-02-30", "tuesday", "garbage", "3x",
                    "10", "m10", "10y", "--since"):
            with self.subTest(raw):
                with self.assertRaises(ValueError):
                    parse_moment(raw, NOW)


class TestNothingAtAll(unittest.TestCase):

    def test_empty_says_what_to_type(self):
        for raw in ("", "   ", None):
            with self.subTest(repr(raw)):
                with self.assertRaises(ValueError) as caught:
                    parse_moment(raw, NOW)
                self.assertIn(HOW_TO_SPELL_IT, str(caught.exception))

    def test_giving_nothing_is_not_the_same_as_giving_something_unreadable(self):
        # Deleting the check for nothing at all does not stop the error: an
        # empty string reaches the date parser and fails there too, carrying
        # the same spellings.  It changes what the person is told.  `'' is not
        # a time` quotes back a value nobody typed and reads as though the
        # empty string had been an attempt at a date — when what happened is
        # that the argument was left off, or a shell variable was empty.  So
        # the two are checked as two messages, not one raised exception.
        for raw in ("", "   ", None):
            with self.subTest(repr(raw)):
                with self.assertRaises(ValueError) as caught:
                    parse_moment(raw, NOW)
                message = str(caught.exception)
                self.assertIn("empty", message)
                self.assertNotIn(repr(raw), message)


class TestTheHelpCannotPromiseWhatTheParserRefuses(unittest.TestCase):
    """The drift that started it, made structural.

    Three copies of the same fact had gone their own ways: the parser knew
    d/h/w, the help offered `3d, 12h, 2w`, and the other tool's help offered
    `10m`.  Any of the three could be edited without the other two, and two of
    them were.
    """

    def examples(self):
        """The spellings the sentence actually offers, read back out of it."""
        head = HOW_TO_SPELL_IT.replace(" or a date like", ",")
        return [word.strip() for word in head.split(",") if word.strip()]

    def test_the_sentence_names_at_least_one_of_each_unit(self):
        self.assertGreaterEqual(len(self.examples()), 5, HOW_TO_SPELL_IT)

    def test_every_example_it_offers_parses(self):
        for example in self.examples():
            with self.subTest(example):
                self.assertIsInstance(parse_moment(example, NOW), datetime)

    def test_every_unit_the_parser_takes_is_in_the_sentence(self):
        # The other direction: a unit that works and is never mentioned is a
        # feature nobody finds.
        for unit in ("m", "h", "d", "w"):
            with self.subTest(unit):
                parse_moment("2" + unit, NOW)          # it works, and
                self.assertRegex(HOW_TO_SPELL_IT,      # it is offered
                                 r"\d" + unit + r"\b")

    def test_a_unit_the_parser_does_not_take_is_not_offered(self):
        # Seconds and years are the two somebody reaches for next.  Not `M` for
        # months: the match is case-insensitive, so `2M` is two minutes, and a
        # unit table that wanted months would have to say so in some other
        # letter.
        for unit in ("s", "y", "mo"):
            with self.subTest(unit):
                with self.assertRaises(ValueError):
                    parse_moment("2" + unit, NOW)


class TestWhichCommandTakesIt(unittest.TestCase):
    """`agentlog on 12h` is the wrong command, not a typo."""

    def test_a_length_is_a_length(self):
        for raw in ("10m", "12h", "3d", "2w", " 2 W "):
            with self.subTest(raw):
                self.assertTrue(is_a_length(raw, NOW))

    def test_a_date_is_not_a_length(self):
        for raw in ("2026-08-03", "2026-08-03T14:30"):
            with self.subTest(raw):
                self.assertFalse(is_a_length(raw, NOW))

    def test_nonsense_is_not_a_length(self):
        for raw in ("", None, "tuesday", "3x", "-3d"):
            with self.subTest(repr(raw)):
                self.assertFalse(is_a_length(raw, NOW))

    def test_a_length_since_would_refuse_anyway_is_not_worth_pointing_at(self):
        # Sending somebody to a second command that will also reject them is
        # worse than saying nothing.
        for raw in ("0d", "999999999999999w"):
            with self.subTest(raw):
                self.assertFalse(is_a_length(raw, NOW))
                with self.assertRaises(ValueError):
                    parse_moment(raw, NOW)


class TestTheMomentIsAnArgument(unittest.TestCase):

    def test_now_decides_what_a_length_means(self):
        later = NOW + timedelta(days=100)
        self.assertEqual(parse_moment("1d", later) - parse_moment("1d", NOW),
                         timedelta(days=100))

    def test_without_one_it_reads_the_clock(self):
        self.assertLess(parse_moment("1m"), datetime.now(timezone.utc))
        self.assertGreater(parse_moment("1m"),
                           datetime.now(timezone.utc) - timedelta(minutes=2))


if __name__ == "__main__":
    unittest.main()
