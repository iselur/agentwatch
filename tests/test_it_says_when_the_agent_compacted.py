"""The gap in the feed where the agent was summarising itself.

Compacting is the one thing an agent does that produces no activity at all.
The commands stop, the writes stop, the feed goes quiet for a minute or two,
and then it picks up again — and the reader, who is watching precisely because
they stepped away, has no way to tell that from a session that died.  This was
the largest silence agentwatch had left.

Both formats announce it and they announce different amounts.  Claude Code
writes a `compact_boundary` carrying the size of the context before and after,
so the line can say what it cost.  Codex writes a `context_compacted` whose
entire payload is its own type — no numbers exist to print.  So the promise
being checked here is per source, not per page: asking whether the *output*
ever mentions compacting passes happily while one of the two formats says
nothing, which is the failure this file exists to catch.

The count is `preTokens - postTokens`.  The record also carries
`cumulativeDroppedTokens`, a running total — a plausible field to reach for,
and wrong from the second compaction onwards in a way no reader could catch,
because the only thing wrong with the number is that it is too big.  agentlog
subtracts, so agentwatch subtracts, and one of these tests is two tools reading
one record and being made to agree about it.
"""

from __future__ import annotations

import json
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from agentwatch.cli import DEFAULT_KINDS                    # noqa: E402
from agentwatch.events import KINDS, Tracker, events_from_line  # noqa: E402
from agentwatch.render import (                             # noqa: E402
    ASCII_MARKS, MARKS, _COLORS, format_event, format_json,
)

#: A real record, taken off this machine and trimmed to the fields that are
#: read.  99,593 is 114,570 - 14,977, and is also what its own
#: `cumulativeDroppedTokens` said — it was the session's first compaction, so
#: the two agree.  A second one is added below where they do not.
CLAUDE_COMPACTION = {
    "type": "system",
    "subtype": "compact_boundary",
    "timestamp": "2026-07-21T09:38:50.745Z",
    "compactMetadata": {
        "trigger": "auto",
        "preTokens": 114570,
        "postTokens": 14977,
        "cumulativeDroppedTokens": 99593,
        "durationMs": 84082,
    },
}

CODEX_COMPACTION = {
    "timestamp": "2026-08-03T22:48:26.102Z",
    "type": "event_msg",
    "payload": {"type": "context_compacted"},
}


def claude(record) -> list:
    return events_from_line(json.dumps(record), Tracker("s1", "claude", "/p"))


def codex(record) -> list:
    return events_from_line(json.dumps(record), Tracker("s1", "codex", "/p"))


def both_formats():
    """The compaction event each format produces, by the format's name."""
    return {"claude": claude(CLAUDE_COMPACTION),
            "codex": codex(CODEX_COMPACTION)}


class TestBothFormatsAnnounceIt(unittest.TestCase):
    """Per source, because a page-wide check cannot see one of two go quiet."""

    def test_each_one_produces_exactly_one_compact_event(self):
        for source, events in both_formats().items():
            self.assertEqual(
                [e["kind"] for e in events], ["compact"],
                "{} logs a compaction and agentwatch says nothing".format(source))

    def test_each_one_says_the_word_a_reader_is_looking_for(self):
        for source, events in both_formats().items():
            self.assertIn("compacted", events[0]["text"],
                          "{} does not say what happened".format(source))

    def test_the_stamp_is_kept(self):
        # Without it the line prints `--:--`, and a line with no clock on it in
        # a feed sorted by clock is the one thing that cannot be placed.
        for source, events in both_formats().items():
            self.assertIsNotNone(events[0]["at"],
                                 "{} loses the time".format(source))


class TestWhatItCost(unittest.TestCase):
    """The number, where there is one, and no number where there is not."""

    def test_claude_reports_this_compactions_own_loss(self):
        self.assertEqual(claude(CLAUDE_COMPACTION)[0]["text"],
                         "compacted, 99,593 tokens dropped")

    def test_the_running_total_is_not_what_is_printed(self):
        # The second compaction of a session: it dropped 20,000 of its own, and
        # `cumulativeDroppedTokens` says 119,593 because it is still counting
        # the first.  Reaching for that field reads as correct on every session
        # with one compaction in it and inflates every session with more.
        record = json.loads(json.dumps(CLAUDE_COMPACTION))
        record["compactMetadata"].update(
            {"preTokens": 35000, "postTokens": 15000,
             "cumulativeDroppedTokens": 119593})
        text = claude(record)[0]["text"]
        self.assertIn("20,000", text)
        self.assertNotIn("119,593", text)

    def test_codex_says_the_word_and_invents_no_number(self):
        # Its whole payload is its own type.  A count made up from a record
        # that did not carry one is the one part of the line nobody can check.
        text = codex(CODEX_COMPACTION)[0]["text"]
        self.assertEqual(text, "compacted")

    def test_a_record_missing_its_sizes_still_announces_itself(self):
        # Better a compaction with no number than a silence: the silence is
        # what the mark exists to explain.
        #
        # The shapes below are not all dictionaries, and that is the point.
        # `events_from_line` ends in a blanket `except Exception: return []`,
        # so a metadata that is a string and gets `.get` called on it does not
        # print a worse line — it prints no line, and the reader is back to
        # staring at a gap in the feed with nothing to explain it.  The
        # promise is about a record that cannot say what it cost, whatever
        # shape the log chose to say so in.
        for broken in ({}, {"preTokens": 100},
                       {"preTokens": "lots", "postTokens": 3},
                       {"preTokens": True, "postTokens": False},
                       None, "n/a", 0, [], ["preTokens", "postTokens"]):
            record = json.loads(json.dumps(CLAUDE_COMPACTION))
            record["compactMetadata"] = broken
            events = claude(record)
            self.assertEqual([e["kind"] for e in events], ["compact"],
                             repr(broken))
            self.assertEqual(events[0]["text"], "compacted", repr(broken))

    def test_a_post_larger_than_pre_is_not_a_gain(self):
        record = json.loads(json.dumps(CLAUDE_COMPACTION))
        record["compactMetadata"].update({"preTokens": 10, "postTokens": 90})
        self.assertEqual(claude(record)[0]["text"],
                         "compacted, 0 tokens dropped")

    def test_the_count_is_grouped_the_way_agentlog_groups_it(self):
        # `16516640 tokens dropped` is a number a reader has to count digits on.
        self.assertIn("99,593", claude(CLAUDE_COMPACTION)[0]["text"])


class TestItIsOnByDefault(unittest.TestCase):
    """A mark that has to be asked for cannot explain a silence."""

    def test_compact_is_a_kind(self):
        self.assertIn("compact", KINDS)

    def test_it_shows_without_a_flag(self):
        self.assertIn("compact", DEFAULT_KINDS)


class TestNoKindIsHalfDeclared(unittest.TestCase):
    """A kind lives in four tables, and nothing used to check they agreed.

    `KINDS` validates `--only`; `MARKS` draws it; `ASCII_MARKS` draws it where
    the terminal cannot carry the glyph; `_COLORS` colours it.  Adding a kind
    to three of the four leaves a mark that renders as `?` on somebody's
    terminal and nowhere else — a bug that reproduces only on the machines that
    have it, which is the worst kind to ship.
    """

    def test_every_kind_has_a_mark(self):
        for kind in KINDS:
            self.assertIn(kind, MARKS)

    def test_every_kind_has_an_ascii_mark(self):
        # The fallback is not cosmetic: an unencodable glyph raises mid-write
        # and takes the watcher down with it.
        for kind in KINDS:
            self.assertIn(kind, ASCII_MARKS)

    def test_every_kind_has_a_colour(self):
        for kind in KINDS:
            self.assertIn(kind, _COLORS)

    def test_no_two_kinds_share_a_mark(self):
        # The mark is the only thing scanned at speed, so two kinds drawing the
        # same one is worse than either of them being invisible.
        for table in (MARKS, ASCII_MARKS):
            marks = [table[k] for k in KINDS]
            self.assertEqual(sorted(marks), sorted(set(marks)), marks)

    def test_no_table_has_a_kind_that_does_not_exist(self):
        for table in (MARKS, ASCII_MARKS, _COLORS):
            self.assertEqual(sorted(table), sorted(KINDS))


class TestTheLineItPrints(unittest.TestCase):
    def event(self):
        return claude(CLAUDE_COMPACTION)[0]

    def test_the_mark_is_drawn_rather_than_a_question_mark(self):
        line = format_event(self.event(), dict(MARKS), width=100)
        self.assertIn(MARKS["compact"], line)
        self.assertNotIn("?", line)

    def test_the_ascii_terminal_gets_a_mark_it_can_print(self):
        line = format_event(self.event(), dict(ASCII_MARKS), width=100)
        line.encode("ascii", "strict")  # the text is ASCII on this path too
        self.assertIn(ASCII_MARKS["compact"], line)

    def test_the_json_stream_carries_it(self):
        shown = json.loads(format_json(self.event()))
        self.assertEqual(shown["kind"], "compact")
        self.assertIn("99,593", shown["text"])


if __name__ == "__main__":
    unittest.main()
