"""The edit that is announced only after it lands — and the ones that never are.

Codex sends a patch in two records: the call carrying the envelope
(``*** Begin Patch`` / ``*** Update File: ...``), and a ``patch_apply_end``
that says whether it applied.  This watcher reports the write from the *end*
record, never from the envelope, and that is a decision rather than an
oversight: a scrolling feed has no retraction.  A `✎ config.py` printed the
instant the envelope goes by, for a patch that comes back rejected a moment
later, cannot be taken back — the line has already scrolled past the person
reading it.  Announcing an edit that did not happen is worse than announcing
it a fraction of a second late.

It has a price, and the price is measured.  On the 1189 Codex session files
this was checked against, 44 of them sent a patch envelope and no
``patch_apply_end`` ever followed — 56 calls in all, against 713 end records
elsewhere.  Those builds hand the envelope to a `custom_tool_call` and report
nothing afterwards.  agentwatch shows nothing for them, and there is no honest
way to fix it in a stream: the end record is the next record 711 times out of
763, so any trigger that flushed on the *next* record would fire early for 35
calls that were still in flight and be wrong 47 times over.  Guessing would
buy 56 edits at the cost of announcing edits that failed.

agentlog, which parses whole files and can look at the end before deciding,
does read those envelopes — per session, and only for sessions that sent no
end record at all (`1d13f77` there).  It can be sure; a stream cannot.

These tests exist so the silence stays deliberate.  If someone later makes the
envelope emit a write, these fail and say why.
"""

from __future__ import annotations

import json
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from agentwatch.events import Tracker, events_from_line  # noqa: E402

PATCH = ("*** Begin Patch\n"
         "*** Update File: /home/you/api/config.py\n"
         "@@\n-old\n+new\n"
         "*** End Patch\n")


def envelope(patch=PATCH, call_id="c1", ts="2026-08-04T09:00:05.000Z"):
    """The current shape: a ``custom_tool_call`` carrying the envelope."""
    return json.dumps({"timestamp": ts, "type": "response_item",
                       "payload": {"type": "custom_tool_call", "name": "exec",
                                   "call_id": call_id, "input": patch}})


def applied(paths=("/home/you/api/config.py",), success=True,
            ts="2026-08-04T09:00:05.400Z"):
    """``patch_apply_end`` — the record that knows whether it landed."""
    return json.dumps({"timestamp": ts, "type": "event_msg",
                       "payload": {"type": "patch_apply_end",
                                   "changes": {p: {"update": {}} for p in paths},
                                   "success": success}})


class Case(unittest.TestCase):
    def setUp(self):
        self.tr = Tracker("019fb76f", "codex", "/home/you/api")

    def events(self, *lines):
        out = []
        for line in lines:
            out.extend(events_from_line(line, self.tr))
        return out

    def kinds(self, *lines):
        return [(e["kind"], e["text"]) for e in self.events(*lines)]


class TestTheEnvelopeAloneSaysNothing(Case):
    """The whole point: a patch that was sent is not a patch that landed."""

    def test_an_envelope_on_its_own_produces_no_write(self):
        self.assertEqual(self.events(envelope()), [])

    def test_an_envelope_on_its_own_produces_no_event_of_any_kind(self):
        # Not a write, not a cmd, not an error.  Silence until it lands.
        self.assertEqual(self.kinds(envelope()), [])

    def test_a_multi_file_envelope_is_equally_silent(self):
        patch = ("*** Begin Patch\n"
                 "*** Update File: /home/you/api/a.py\n@@\n-x\n+y\n"
                 "*** Add File: /home/you/api/b.py\n+z\n"
                 "*** Delete File: /home/you/api/c.py\n"
                 "*** End Patch\n")
        self.assertEqual(self.events(envelope(patch)), [])

    def test_a_rejected_patch_never_becomes_a_write(self):
        # The case the silence is for.  If the envelope spoke, this stream
        # would have said `✎ config.py` and then, too late, `✗`.
        self.assertEqual(self.kinds(envelope(), applied(success=False)),
                         [("error", "patch did not apply: config.py")])


class TestTheEndRecordIsWhatSpeaks(Case):

    def test_an_applied_patch_is_one_write(self):
        self.assertEqual(self.kinds(envelope(), applied()),
                         [("write", "/home/you/api/config.py")])

    def test_the_end_record_alone_is_enough(self):
        # Sessions exist where the envelope was built somewhere unreadable.
        # The end record still names the files, absolutely.
        self.assertEqual(self.kinds(applied()),
                         [("write", "/home/you/api/config.py")])

    def test_every_file_in_the_patch_is_named(self):
        end = applied(paths=("/home/you/api/a.py", "/home/you/api/b.py"))
        self.assertEqual(sorted(t for _k, t in self.kinds(envelope(), end)),
                         ["/home/you/api/a.py", "/home/you/api/b.py"])

    def test_a_file_is_not_written_twice_for_one_patch(self):
        # The envelope and the end record name the same file.  Only one line.
        writes = [e for e in self.events(envelope(), applied())
                  if e["kind"] == "write"]
        self.assertEqual(len(writes), 1)


class TestTheGapIsRealAndPinned(Case):
    """44 files on the corpus this was measured against look like this."""

    def test_an_envelope_with_no_end_record_is_never_reported(self):
        # This is the known cost, written down rather than discovered later.
        # A build that sends the envelope and reports nothing afterwards gets
        # nothing in the stream.  agentlog covers this case; a stream cannot,
        # because at this point in the file the outcome has not been written
        # yet and there is nothing to wait for that is guaranteed to arrive.
        self.assertEqual(self.events(envelope()), [])

    def test_a_later_unrelated_record_does_not_flush_the_envelope(self):
        # The tempting fix: flush the pending envelope when anything else
        # arrives.  On the corpus that would fire early for 35 calls whose end
        # record had not landed yet.  Nothing here holds pending state at all.
        call = json.dumps({
            "timestamp": "2026-08-04T09:00:09.000Z", "type": "response_item",
            "payload": {"type": "custom_tool_call", "name": "exec",
                        "call_id": "c2",
                        "input": 'await tools.exec_command({cmd:"pytest -x"});'}})
        self.assertEqual(self.kinds(envelope(), call),
                         [("cmd", "pytest -x")])

    def test_the_end_of_the_session_does_not_flush_it_either(self):
        # There is no end-of-stream hook that could, and adding one would
        # announce edits that failed in exactly the sessions least able to
        # say so.
        self.assertEqual(self.events(envelope()), [])


class TestItDoesNotSwallowTheOrdinaryCase(Case):

    def test_a_command_in_the_same_snippet_is_still_reported(self):
        # An envelope in the input must not make the rest of the call silent.
        both = json.dumps({
            "timestamp": "2026-08-04T09:00:05.000Z", "type": "response_item",
            "payload": {"type": "custom_tool_call", "name": "exec",
                        "call_id": "c1",
                        "input": 'await tools.exec_command({cmd:"git status"});'
                                 + PATCH}})
        self.assertEqual(self.kinds(both), [("cmd", "git status")])

    def test_two_patches_to_one_file_a_minute_apart_are_two_writes(self):
        # The echo suppressor is paired, not timed: each envelope is spent by
        # the one result that confirms it, so this is a second edit rather
        # than the same one seen twice.  See test_repeated_edits.py, where
        # the same holds eight seconds apart.
        first = applied(ts="2026-08-04T09:00:05.400Z")
        second = applied(ts="2026-08-04T09:01:05.400Z")
        writes = [e for e in self.events(envelope(), first, envelope(), second)
                  if e["kind"] == "write"]
        self.assertEqual(len(writes), 2)


if __name__ == "__main__":
    unittest.main()
