"""`»` says you said something.  A subagent prompt is not you saying something.

Claude Code writes a `type: "user"` record for three different things: a person
typing, a tool result fed back into the loop, and a prompt the agent wrote for
a subagent — the last marked `isSidechain: true`.  This watcher already
excluded the tool results, deliberately and with a comment; it did not exclude
the subagent prompts.

On 896 real session logs that is 2992 `»` marks against 2314 times a person
typed: 678 of them announced a turn that nobody took.  Small next to the same
bug in agentlog's digest (16.6x there, because the digest also counted tool
results), and worse in one way — `»` is the mark that means *you*, and in a
live feed it is the one a person uses to find where they left off.  A `»` for
a prompt the agent wrote for itself points at the wrong place.

The subagent's work is still shown.  Its commands ran, its files were written,
its failures failed — all on this machine, in this session, and all of it is
what the watcher is for.  Only the claim that you spoke is dropped.

A record with no `isSidechain` field at all is a turn: older logs predate the
field, and dropping real turns is the opposite mistake.  Only an explicit
`true` is a subagent.
"""

from __future__ import annotations

import json
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from agentwatch.events import Tracker, events_from_line  # noqa: E402

TS = "2026-08-04T09:00:00.000Z"


def spoke(text="fix the tests", ts=TS, sidechain=False):
    return json.dumps({"type": "user", "isSidechain": sidechain,
                       "timestamp": ts, "cwd": "/home/you/api",
                       "message": {"role": "user", "content": text}})


def subagent_prompt(text="search the repo", ts=TS):
    return spoke(text, ts, sidechain=True)


def ran(cmd="pytest -x", tool_use_id="t1", ts=TS, sidechain=False):
    return json.dumps({"type": "assistant", "isSidechain": sidechain,
                       "timestamp": ts, "cwd": "/home/you/api",
                       "message": {"role": "assistant", "id": "m1",
                                   "content": [{"type": "tool_use",
                                                "id": tool_use_id,
                                                "name": "Bash",
                                                "input": {"command": cmd}}]}})


def result(tool_use_id="t1", is_error=False, ts=TS, sidechain=False):
    block = {"type": "tool_result", "tool_use_id": tool_use_id,
             "content": "ok"}
    if is_error:
        block["is_error"] = True
    return json.dumps({"type": "user", "isSidechain": sidechain,
                       "timestamp": ts, "cwd": "/home/you/api",
                       "message": {"role": "user", "content": [block]}})


class Case(unittest.TestCase):
    def setUp(self):
        self.tr = Tracker("689e648c", "claude", "/home/you/api")

    def events(self, *lines):
        out = []
        for line in lines:
            out.extend(events_from_line(line, self.tr))
        return out

    def kinds(self, *lines):
        return [(e["kind"], e["text"]) for e in self.events(*lines)]


class TestAPersonTypingIsATurn(Case):

    def test_one_message_is_one_turn(self):
        self.assertEqual([e["kind"] for e in self.events(spoke())], ["turn"])

    def test_two_messages_are_two_turns(self):
        events = self.events(spoke("a"), spoke("b", "2026-08-04T09:05:00.000Z"))
        self.assertEqual([e["kind"] for e in events], ["turn", "turn"])

    def test_the_turn_carries_the_session_like_any_other_event(self):
        e = self.events(spoke())[0]
        self.assertEqual(e["session"], "689e648c")
        self.assertEqual(e["project"], "/home/you/api")


class TestASubagentPromptIsNot(Case):

    def test_a_sidechain_record_is_not_a_turn(self):
        self.assertEqual(self.events(subagent_prompt()), [])

    def test_it_produces_no_event_of_any_kind(self):
        self.assertEqual(self.kinds(subagent_prompt()), [])

    def test_a_run_of_subagent_prompts_is_silent(self):
        self.assertEqual(
            self.events(subagent_prompt("a"),
                        subagent_prompt("b", "2026-08-04T09:00:01.000Z"),
                        subagent_prompt("c", "2026-08-04T09:00:02.000Z")), [])

    def test_your_own_turn_beside_them_still_shows(self):
        events = self.events(subagent_prompt(), spoke("mine",
                                                      "2026-08-04T09:00:01.000Z"),
                             subagent_prompt("more", "2026-08-04T09:00:02.000Z"))
        self.assertEqual([e["kind"] for e in events], ["turn"])


class TestTheSubagentsWorkStillShows(Case):
    """Dropping the prompt must not drop the work it started."""

    def test_a_command_a_subagent_ran_is_still_a_command(self):
        self.assertEqual(
            self.kinds(subagent_prompt(), ran("ruff check .", sidechain=True)),
            [("cmd", "ruff check .")])

    def test_a_failure_a_subagent_hit_is_still_a_failure(self):
        events = self.kinds(subagent_prompt(),
                            ran("ruff check .", sidechain=True),
                            result(is_error=True, sidechain=True))
        self.assertEqual(events, [("cmd", "ruff check ."),
                                  ("error", "ruff check .")])

    def test_a_file_a_subagent_wrote_is_still_a_write(self):
        write = json.dumps({
            "type": "assistant", "isSidechain": True, "timestamp": TS,
            "cwd": "/home/you/api",
            "message": {"role": "assistant", "id": "m2",
                        "content": [{"type": "tool_use", "id": "t5",
                                     "name": "Write",
                                     "input": {"file_path": "/home/you/api/x.py"}}]}})
        self.assertEqual(self.kinds(subagent_prompt(), write),
                         [("write", "/home/you/api/x.py")])


class TestTheToolResultHalfIsUnchanged(Case):
    """It was already right; the new check must not disturb it."""

    def test_a_tool_result_is_not_a_turn(self):
        self.assertEqual(self.kinds(ran(), result()), [("cmd", "pytest -x")])

    def test_a_failed_tool_result_is_still_an_error(self):
        self.assertEqual(self.kinds(ran(), result(is_error=True)),
                         [("cmd", "pytest -x"), ("error", "pytest -x")])


class TestTheFlagIsReadDefensively(Case):

    def test_a_record_with_no_sidechain_field_is_a_turn(self):
        rec = json.loads(spoke())
        del rec["isSidechain"]
        self.assertEqual([e["kind"] for e in self.events(json.dumps(rec))],
                         ["turn"])

    def test_only_an_explicit_true_is_a_subagent(self):
        # A truthy string is not a subagent flag.  Losing a real turn is the
        # error that would be noticed, and the one worth avoiding here.
        rec = json.loads(spoke())
        rec["isSidechain"] = "yes"
        self.assertEqual([e["kind"] for e in self.events(json.dumps(rec))],
                         ["turn"])

    def test_a_sidechain_record_still_gives_up_its_project(self):
        # The cwd is read from user records, including ones that are no longer
        # turns.  A watcher that lost the project column would be worse off.
        tr = Tracker("689e648c", "claude", "")
        events_from_line(subagent_prompt(), tr)
        self.assertEqual(tr.project, "/home/you/api")

    def test_a_half_written_line_yields_nothing(self):
        self.assertEqual(self.events(subagent_prompt()[:30]), [])


if __name__ == "__main__":
    unittest.main()
