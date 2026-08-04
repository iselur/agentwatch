"""Editing one file twice in half a minute is two edits, not one seen twice.

Codex used to name a patched file twice for a single edit: once in the call
that sent the envelope, and again in the ``patch_apply_end`` that confirmed it.
Both are worth parsing — the call is the earliest sight of it, the end record
is the only sight of it when the envelope was built somewhere unreadable — so
the second report of the same path was dropped as an echo.

The rule used to drop it was thirty seconds on the clock: the same path again,
within the window, is the echo.  Nothing in that rule asks whether an echo was
possible.  An agent that fixes a file, runs the tests, and fixes it again eight
seconds later has made two edits, and the second one silently did not exist.

On the developer's 1189 Codex session files, 133 of 742 successfully patched
paths were being dropped this way — better than one in six — across 87
sessions.  The gaps were spread evenly from five seconds to thirty, which is
what ordinary consecutive work looks like; a real echo lands in well under a
second.  Current Codex builds report nothing at all from the envelope, so in
those sessions there was no echo to suppress and every drop was a real edit.

The fix is to pair rather than to time: an echo is only possible when an
envelope for that path has already been reported and not yet confirmed, so one
envelope buys exactly one suppression.  Two edits in a row bring two envelopes
and two end records, and both are shown however close together they land.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import timedelta

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from agentwatch.events import Tracker, events_from_line, parse_time  # noqa: E402

FILE = "/home/you/api/config.py"


def _ts(second, milli=0):
    return "2026-08-04T09:00:%02d.%03dZ" % (second, milli)


def applied(second, milli=0, paths=(FILE,), success=True):
    """``patch_apply_end`` — the record that says an edit landed."""
    return json.dumps({"timestamp": _ts(second, milli), "type": "event_msg",
                       "payload": {"type": "patch_apply_end",
                                   "changes": {p: {"type": "update"}
                                               for p in paths},
                                   "success": success}})


def old_envelope(second, milli=0, path=FILE, call_id="c1"):
    """The older ``function_call`` shape, which does name the file up front."""
    patch = ("*** Begin Patch\n*** Update File: %s\n@@\n-old\n+new\n"
             "*** End Patch\n" % path)
    return json.dumps({"timestamp": _ts(second, milli), "type": "response_item",
                       "payload": {"type": "function_call", "name": "apply_patch",
                                   "call_id": call_id,
                                   "arguments": json.dumps({"patch": patch})}})


class Case(unittest.TestCase):

    def setUp(self):
        self.tr = Tracker("019fb76f", "codex", "/home/you/api")

    def events(self, *lines):
        out = []
        for line in lines:
            out.extend(events_from_line(line, self.tr))
        return out

    def writes(self, *lines):
        return [e["text"] for e in self.events(*lines) if e["kind"] == "write"]


class TestASecondEditIsASecondWrite(Case):
    """No envelope was ever sent here, so no report can be an echo."""

    def test_two_edits_eight_seconds_apart(self):
        # The shape taken straight from a real session: two patch_apply_end
        # records, different diffs, eight seconds between them.
        self.assertEqual(self.writes(applied(37), applied(45)), [FILE, FILE])

    def test_two_edits_one_second_apart(self):
        self.assertEqual(self.writes(applied(37), applied(38)), [FILE, FILE])

    def test_two_edits_in_the_same_second(self):
        # Fast is not the same as duplicated.  An agent that sends two patches
        # back to back can land both inside one second.
        self.assertEqual(self.writes(applied(37, 100), applied(37, 900)),
                         [FILE, FILE])

    def test_a_run_of_edits_to_one_file(self):
        lines = [applied(30 + n) for n in range(5)]
        self.assertEqual(self.writes(*lines), [FILE] * 5)

    def test_two_files_edited_together_twice(self):
        other = "/home/you/api/util.py"
        first = applied(37, paths=(FILE, other))
        second = applied(44, paths=(FILE, other))
        self.assertEqual(sorted(self.writes(first, second)),
                         sorted([FILE, other, FILE, other]))


class TestTheEchoIsStillSuppressed(Case):
    """The case the rule was written for still behaves."""

    def test_an_envelope_and_its_confirmation_are_one_write(self):
        # The old shape names the file when the patch is sent and again when
        # it lands.  One edit, one line in the feed.
        self.assertEqual(self.writes(old_envelope(37), applied(37, 400)),
                         [FILE])

    def test_two_envelopes_each_confirmed_are_two_writes(self):
        # And this is the case the clock got wrong even in the old shape: the
        # second envelope was inside the first one's window, so both the
        # envelope and its confirmation were swallowed.
        self.assertEqual(self.writes(old_envelope(37), applied(37, 400),
                                     old_envelope(45, call_id="c2"),
                                     applied(45, 400)),
                         [FILE, FILE])

    def test_one_envelope_buys_exactly_one_suppression(self):
        # Two end records behind a single envelope: the first is the echo, the
        # second is a genuine edit that was reported without an envelope.
        self.assertEqual(self.writes(old_envelope(37), applied(37, 400),
                                     applied(44)),
                         [FILE, FILE])

    def test_an_envelope_that_is_never_confirmed_is_still_one_write(self):
        # The old shape reports from the call, so the edit is not lost when no
        # end record follows.  Only the current shape stays silent.
        self.assertEqual(self.writes(old_envelope(37)), [FILE])

    def test_an_envelope_for_one_file_does_not_cover_another(self):
        other = "/home/you/api/util.py"
        self.assertEqual(self.writes(old_envelope(37, path=other),
                                     applied(37, 400)),
                         [other, FILE])

    def test_a_confirmation_long_after_its_envelope_is_not_an_echo(self):
        # Past the window, the pairing is stale and the end record speaks for
        # itself; a file named twice ten minutes apart is two edits by any
        # reading.
        late = json.dumps({"timestamp": "2026-08-04T09:10:00.000Z",
                           "type": "event_msg",
                           "payload": {"type": "patch_apply_end",
                                       "changes": {FILE: {"type": "update"}},
                                       "success": True}})
        self.assertEqual(self.writes(old_envelope(37), late), [FILE, FILE])


class TestWhatMustNotChange(Case):

    def test_a_failed_patch_is_still_an_error_and_not_a_write(self):
        kinds = [(e["kind"], e["text"])
                 for e in self.events(applied(37, success=False))]
        self.assertEqual(kinds, [("error", "patch did not apply: config.py")])

    def test_a_failure_after_a_success_still_reports(self):
        events = self.events(applied(37), applied(44, success=False))
        self.assertEqual([e["kind"] for e in events], ["write", "error"])

    def test_a_write_with_no_time_is_never_swallowed(self):
        # Undated records are the ones nothing can be reasoned about; the
        # honest failure is to show them.
        undated = json.dumps({"type": "event_msg",
                              "payload": {"type": "patch_apply_end",
                                          "changes": {FILE: {"type": "update"}},
                                          "success": True}})
        self.assertEqual(self.writes(undated, undated), [FILE, FILE])

    def test_the_pending_table_stops_growing(self):
        # A watcher left running for a week must not grow without limit.
        at = parse_time(_ts(0))
        for n in range(20000):
            self.tr.envelope_sent("/f/%d" % n, at + timedelta(seconds=n))
        self.assertLessEqual(len(self.tr._pending), self.tr._max_labels + 1)

    def test_a_stale_envelope_does_not_swallow_a_later_edit(self):
        # An envelope whose end record never came leaves an entry behind.  It
        # must expire, or the next edit to that file disappears instead.
        at = parse_time(_ts(0))
        self.tr.envelope_sent(FILE, at)
        self.assertFalse(self.tr.confirms_envelope(FILE, at + timedelta(hours=1)))


if __name__ == "__main__":
    unittest.main()
