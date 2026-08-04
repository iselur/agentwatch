"""The lines the README shows, produced by the code that prints them.

The README quotes real output — the event lines, the dated rule, and the
`--json` object:

    09:41:02  api-server    » you
    09:41:07  api-server    $ pytest tests/test_auth.py -x
    ── Wed 29 Jul ──────────────────────────────

Every one of those is generated: the clock column, the fixed-width project
column that exists so the marks stay in one place, the mark itself, and the
rule that dates a day change.  None of it was checked.  The layout is the
promise this tool makes — "narrow enough to read while it scrolls" — so a
README showing a layout the tool stopped producing is describing a different
program.

Each quoted line is parsed back into the event that would produce it and handed
to the renderer.  Only the timestamp, the project and the text are taken from
the README; the mark, the column widths and the spacing come from the code,
because those are the parts being checked.  Feeding the whole line back and
comparing it to itself is a test that passes for every layout there is.

The clock column is local time, so each event is built from the README's hour
as a naive local stamp and then given this machine's own offset.  Building them
in UTC instead would pass in London and fail everywhere else.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from agentwatch import render

README = os.path.join(_ROOT, "README.md")

# "09:41:07  api-server    $ pytest tests/test_auth.py -x"
_EVENT = re.compile(r"^((\d\d):(\d\d):(\d\d)  (\S+) +(\S) (.*))$", re.M)
_RULE = re.compile(r"^(── (.+?) ─+)$", re.M)

_KIND_OF = {mark: kind for kind, mark in render.MARKS.items()}


def readme() -> str:
    with open(README, encoding="utf-8") as handle:
        return handle.read()


def at_local(hour: int, minute: int, second: int, day=None) -> dt.datetime:
    """A stamp that reads back as this wall-clock time wherever the tests run."""
    day = day or dt.date(2026, 7, 29)
    naive = dt.datetime.combine(day, dt.time(hour, minute, second))
    return naive.astimezone()


class TestTheREADMEShowsWhatItPrints(unittest.TestCase):
    def setUp(self):
        self.text = readme()
        self.marks = dict(render.MARKS)
        self.events = _EVENT.findall(self.text)

    def test_the_readme_still_quotes_event_lines(self):
        # Without this the loop below iterates over nothing and passes, which is
        # what deleting the examples would look like.
        self.assertGreaterEqual(len(self.events), 5, "no event lines in README")

    def test_every_quoted_event_line_is_one_the_renderer_produces(self):
        for whole, hour, minute, second, project, mark, text in self.events:
            self.assertIn(mark, _KIND_OF, "README uses a mark the code has no kind for")
            kind = _KIND_OF[mark]
            # `turn` prints "you" whatever the text was, and `write` shortens the
            # path against the project, so those two are given the text the
            # renderer would have been given rather than the text it printed.
            event = {"at": at_local(int(hour), int(minute), int(second)),
                     "kind": kind, "project": project,
                     "text": "anything" if kind == "turn" else text}
            # Compared against the README's own line, not against a format
            # string built from the same constants the renderer uses — widening
            # the project column moves both sides of that and proves nothing.
            self.assertEqual(render.format_event(event, self.marks), whole,
                             "README.md shows a layout agentwatch no longer prints")

    def test_the_dated_rule_is_the_one_the_code_draws(self):
        rules = _RULE.findall(self.text)
        self.assertTrue(rules, "no day rule in README")
        for whole, shown in rules:
            # A rule with no year on it is claiming this year — the code adds
            # one for any other.  So the day is read back at this year, and a
            # README that has aged past its own example fails here, which is
            # the only place anyone would find out.
            day = dt.datetime.strptime(shown.strip(), "%a %d %b").date()
            day = day.replace(year=dt.date.today().year)
            self.assertEqual(day.strftime("%a"), shown.split()[0],
                             f"README.md dates a rule {shown!r}, which is not a "
                             f"day in {day.year}; the example needs re-dating")
            event = {"at": at_local(9, 22, 58, day), "kind": "cmd",
                     "project": "proj", "text": "pytest -x"}
            # Drawn at the width the README itself shows, so the fill and the
            # padding are checked and not just the label.
            line, _next = render.day_rule(event, dt.date(1999, 1, 1),
                                          width=len(whole))
            self.assertEqual(line, whole,
                             "README.md shows a day rule agentwatch no longer draws")

    def test_the_json_example_has_the_keys_the_code_emits(self):
        quoted = [ln for ln in self.text.splitlines()
                  if ln.startswith('{"at": ')]
        self.assertTrue(quoted, "no --json example in README")
        for line in quoted:
            shown = json.loads(line)
            event = {"at": dt.datetime.now(dt.timezone.utc), "kind": "cmd",
                     "text": "x", "project": "p", "session": "s",
                     "source": "claude"}
            produced = json.loads(render.format_json(event))
            self.assertEqual(sorted(shown), sorted(produced),
                             "README.md shows JSON keys agentwatch no longer emits")


if __name__ == "__main__":
    unittest.main()
