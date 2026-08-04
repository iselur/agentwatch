"""A six-day-old event printed as this morning.

Every line carries a clock and nothing else, which is right for a tailer —
everything on screen happened moments ago and the date would be the same word
repeated down the page.  But `--once --since 1w` is the same renderer over a
week of history, and there it printed:

    09:22:58  proj          $ echo six_days_ago
    09:22:58  proj          $ echo three_days_ago
    09:22:58  proj          $ echo yesterday
    09:12:58  proj          $ echo ten_min_ago

Four events spanning six days.  Three of the lines are byte-identical.  Read
back, that is four things that happened in the same minute this morning, and
there is nothing on the line to say otherwise.

The same gap shows up without any `--since` at all: a session that runs past
midnight prints 23:59 and then 00:01 with nothing between them.

So the day is printed when the day changes, and only then.  A tailer watching
live never crosses one and never sees a rule, which is what the bare clock was
protecting.  Cross a day and it says so — including the first line, if the run
did not start today.

The timestamp was never wrong; `--json` carried the full ISO stamp the whole
time.  It was the human output that could not tell you.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from agentwatch import cli
from agentwatch.render import day_rule, marks_for


def _at(**kw):
    return {"at": datetime.now(timezone.utc) - timedelta(**kw),
            "kind": "cmd", "text": "echo hi", "project": "proj",
            "session": "s", "source": "claude"}


class TestTheRuleItself(unittest.TestCase):

    def rule(self, event, previous):
        return day_rule(event, previous, width=80, color=False)

    def test_today_alone_gets_no_rule(self):
        # The live case.  Nothing changed for the tailer.
        line, _ = self.rule(_at(minutes=10), None)
        self.assertIsNone(line, "a rule appeared on an ordinary live run")

    def test_a_run_that_did_not_start_today_says_so(self):
        line, _ = self.rule(_at(days=3), None)
        self.assertIsNotNone(line)

    def test_the_day_changing_prints_it(self):
        _, day = self.rule(_at(days=3), None)
        line, _ = self.rule(_at(days=2), day)
        self.assertIsNotNone(line)

    def test_the_same_day_twice_prints_it_once(self):
        first, day = self.rule(_at(days=3), None)
        second, _ = self.rule(_at(days=3, minutes=5), day)
        self.assertIsNotNone(first)
        self.assertIsNone(second, "the rule repeated inside one day")

    def test_it_names_the_day(self):
        when = (datetime.now(timezone.utc) - timedelta(days=3)).astimezone()
        line, _ = self.rule(_at(days=3), None)
        self.assertIn(when.strftime("%d").lstrip("0"), line)
        self.assertIn(when.strftime("%b"), line)
        self.assertIn(when.strftime("%a"), line)

    def test_it_fits_the_terminal(self):
        for width in (20, 40, 80, 200):
            with self.subTest(width=width):
                line, _ = day_rule(_at(days=3), None, width=width, color=False)
                self.assertLessEqual(len(line), width)

    def test_an_event_with_no_timestamp_changes_nothing(self):
        event = _at(days=3)
        event["at"] = None
        line, day = self.rule(event, "held")
        self.assertIsNone(line)
        self.assertEqual(day, "held", "a stampless event moved the day on")

    def test_a_stampless_first_event_does_not_start_a_day(self):
        event = _at(days=3)
        event["at"] = None
        line, day = self.rule(event, None)
        self.assertIsNone(line)
        self.assertIsNone(day)


class _Rendered(unittest.TestCase):
    """Run the real CLI over a real home and keep the printed lines."""

    def home_with(self, offsets):
        home = tempfile.mkdtemp(prefix="agentwatch_days_")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        folder = os.path.join(home, ".claude", "projects", "-tmp-proj")
        os.makedirs(folder)
        now = datetime.now(timezone.utc)
        path = os.path.join(folder, "4ef1361b-07e4-4bc9-bb29-1783b761d677.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            for i, delta in enumerate(offsets):
                fh.write(json.dumps({
                    "type": "assistant", "timestamp": (now - delta).isoformat(),
                    "message": {"role": "assistant", "content": [
                        {"type": "tool_use", "id": "t{}".format(i),
                         "name": "Bash",
                         "input": {"command": "echo case{}".format(i)}}]},
                }) + "\n")
        return home

    def run_once(self, home, *extra):
        printed = []
        real = cli.write_line
        cli.write_line = printed.append
        self.addCleanup(setattr, cli, "write_line", real)
        rc = cli.main(["--home", home, "--once", "--since", "2w",
                       "--no-color"] + list(extra))
        return rc, printed


class TestAWeekOfHistoryIsReadable(_Rendered):

    SPREAD = [timedelta(days=6), timedelta(days=3),
              timedelta(days=1), timedelta(minutes=10)]

    def test_no_two_events_from_different_days_look_the_same(self):
        _, printed = self.run_once(self.home_with(self.SPREAD))
        events = [l for l in printed if "echo case" in l]
        stamps = [l.split()[0] for l in events]
        self.assertEqual(len(events), 4)
        # Before the rule, three of these four were byte-identical.
        self.assertGreater(len(printed), len(events),
                           "a week of events printed with nothing to date them")

    def test_one_rule_for_each_day_present(self):
        _, printed = self.run_once(self.home_with(self.SPREAD))
        rules = [l for l in printed if "echo case" not in l and l.strip()]
        self.assertEqual(len(rules), 4, printed)

    def test_the_rule_comes_before_its_events(self):
        _, printed = self.run_once(self.home_with(self.SPREAD))
        self.assertNotIn("echo case", printed[0],
                         "the first day's events printed before its date")

    def test_todays_events_still_need_no_rule(self):
        home = self.home_with([timedelta(minutes=30), timedelta(minutes=10)])
        _, printed = self.run_once(home)
        self.assertEqual(len(printed), 2, printed)

    def test_json_is_untouched(self):
        _, printed = self.run_once(self.home_with(self.SPREAD), "--json")
        self.assertEqual(len(printed), 4, "a display rule leaked into --json")
        for line in printed:
            json.loads(line)

    def test_the_event_lines_themselves_are_unchanged(self):
        # The rule adds lines; it does not alter the ones that were there.
        home = self.home_with([timedelta(minutes=10)])
        _, printed = self.run_once(home)
        self.assertEqual(len(printed), 1)
        self.assertIn("echo case0", printed[0])
        self.assertRegex(printed[0], r"^\d\d:\d\d:\d\d  ")


class TestMidnight(_Rendered):
    """A session running past midnight, with no --since involved."""

    def test_the_new_day_is_announced(self):
        marks = marks_for(sys.stdout)
        yesterday = datetime.now(timezone.utc).astimezone() - timedelta(days=1)
        late = yesterday.replace(hour=23, minute=59)
        early = late + timedelta(minutes=2)
        line, day = day_rule({"at": late}, None, 80, False)
        second, _ = day_rule({"at": early}, day, 80, False)
        self.assertIsNotNone(second, "midnight passed without a word")


if __name__ == "__main__":
    unittest.main()
