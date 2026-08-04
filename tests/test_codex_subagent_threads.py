"""Codex spawns subagents too, and each one gets a rollout file of its own.

The file is named after the thread that was spawned, and the `session_meta`
record on its first line names the sitting that spawned it:

    {"type": "session_meta", "payload": {
        "session_id": "<the sitting>",
        "id": "<this thread, and the name of the file>",
        "thread_source": "subagent",
        "source": {"subagent": {"thread_spawn": {"parent_thread_id": ...}}}}}

Taking the id from the filename made every one of those a session of its own.
On the developer's machine 42 of 1189 Codex rollouts are subagent threads, and
each was reported as a separate sitting under a uuid that appears nowhere the
person could look it up — the same defect the Claude side had, in the other
vendor's layout, and found the same way: by diffing the session ids the two
tools produce over the whole corpus and asking about the ones only one of them
had.

The work still streams, and still belongs to the sitting that asked for it.
Only the claim that it is a separate sitting goes.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agentwatch.follow import Watcher  # noqa: E402

PARENT = "019fc384-9672-7120-893b-a8856b5f08e2"
CHILD = "019fc384-fddd-72b0-9ef2-cec8742f6acc"


class Case(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="agentwatch-codex-sub-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.now = datetime.now(timezone.utc)
        self.dir = os.path.join(self.home, ".codex", "sessions",
                                "2026", "08", "02")

    def at(self, minutes_ago):
        return self.now - timedelta(minutes=minutes_ago)

    def meta(self, session_id, own_id, minutes_ago=30, subagent=True,
             cwd="/home/you/api"):
        payload = {"session_id": session_id, "id": own_id,
                   "timestamp": self.at(minutes_ago).isoformat(),
                   "cwd": cwd, "originator": "codex_exec"}
        if subagent:
            payload["thread_source"] = "subagent"
            payload["forked_from_id"] = session_id
            payload["source"] = {"subagent": {"thread_spawn": {
                "parent_thread_id": session_id, "depth": 1}}}
        else:
            payload["thread_source"] = "user"
            payload["source"] = "exec"
        return {"timestamp": self.at(minutes_ago).isoformat(),
                "type": "session_meta", "payload": payload}

    def typed(self, text="go", minutes_ago=29):
        return {"timestamp": self.at(minutes_ago).isoformat(),
                "type": "event_msg",
                "payload": {"type": "user_message", "message": text}}

    def ran(self, command, minutes_ago=28, call="c1"):
        return {"timestamp": self.at(minutes_ago).isoformat(),
                "type": "response_item",
                "payload": {"type": "function_call", "name": "exec_command",
                            "call_id": call,
                            "arguments": json.dumps({"command": command})}}

    def rollout(self, own_id, records, minutes_ago=30):
        stamp = self.at(minutes_ago).strftime("%Y-%m-%dT%H-%M-%S")
        path = os.path.join(self.dir, "rollout-%s-%s.jsonl" % (stamp, own_id))
        os.makedirs(self.dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r) + "\n")
        return path

    def watcher(self):
        return Watcher(home=self.home, since=self.now - timedelta(hours=2))


class TestTheCountOfSessions(Case):

    def test_a_subagent_thread_is_not_a_second_session(self):
        self.rollout(PARENT, [self.meta(PARENT, PARENT, subagent=False),
                              self.typed(), self.ran("make")])
        self.rollout(CHILD, [self.meta(PARENT, CHILD),
                             self.ran("pytest -x", 27, "c2")])
        w = self.watcher()
        w.poll()
        self.assertEqual(w.watched(), 1)

    def test_two_real_sittings_are_still_two(self):
        other = "019fc999-0000-7000-8000-000000000000"
        self.rollout(PARENT, [self.meta(PARENT, PARENT, subagent=False),
                              self.typed()])
        self.rollout(other, [self.meta(other, other, subagent=False),
                             self.typed()])
        w = self.watcher()
        w.poll()
        self.assertEqual(w.watched(), 2)

    def test_a_rollout_with_no_meta_still_names_itself(self):
        # An old build, a truncated file, a rollout joined partway through:
        # the filename is all there is, and it is better than nothing.
        self.rollout(CHILD, [self.typed(), self.ran("make")])
        w = self.watcher()
        events = w.poll()
        self.assertEqual({e["session"] for e in events}, {CHILD})


class TestTheWorkStillStreams(Case):

    def test_the_subagents_command_still_streams(self):
        self.rollout(PARENT, [self.meta(PARENT, PARENT, subagent=False),
                              self.typed(), self.ran("make")])
        self.rollout(CHILD, [self.meta(PARENT, CHILD),
                             self.ran("pytest -x", 27, "c2")])
        texts = [e["text"] for e in self.watcher().poll() if e["kind"] == "cmd"]
        self.assertEqual(sorted(texts), ["make", "pytest -x"])

    def test_its_events_carry_the_parent_session_id(self):
        self.rollout(CHILD, [self.meta(PARENT, CHILD),
                             self.ran("pytest -x", 27, "c2")])
        events = self.watcher().poll()
        self.assertTrue(events)
        self.assertEqual({e["session"] for e in events}, {PARENT})

    def test_the_cwd_in_the_meta_is_still_read(self):
        # The same record carries the project, and reading the id out of it
        # must not cost that.
        self.rollout(CHILD, [self.meta(PARENT, CHILD, cwd="/home/you/api"),
                             self.ran("pytest -x", 27, "c2")])
        events = self.watcher().poll()
        self.assertTrue(all(e["project"] for e in events), events)


class TestATopLevelSessionIsUntouched(Case):

    def test_its_own_meta_names_itself(self):
        self.rollout(PARENT, [self.meta(PARENT, PARENT, subagent=False),
                              self.typed(), self.ran("make")])
        events = self.watcher().poll()
        self.assertEqual({e["session"] for e in events}, {PARENT})

    def test_a_meta_with_no_session_id_changes_nothing(self):
        rec = self.meta(PARENT, CHILD)
        rec["payload"].pop("session_id")
        self.rollout(CHILD, [rec, self.ran("pytest -x", 27, "c2")])
        events = self.watcher().poll()
        self.assertEqual({e["session"] for e in events}, {CHILD})

    def test_only_the_opening_record_may_name_the_sitting(self):
        # Deliberate, and worth pinning because nothing on disk can tell the
        # two rules apart: across 1280 real `turn_context` records not one
        # carries a `session_id`.  `session_meta` is the first line of the
        # file, so renaming from it costs nothing; `turn_context` arrives
        # again every turn, and a rename partway through would orphan every
        # event already shown under the old name.
        rec = {"timestamp": self.at(28).isoformat(), "type": "turn_context",
               "payload": {"cwd": "/home/you/api", "session_id": PARENT}}
        self.rollout(CHILD, [self.typed(), rec, self.ran("pytest -x", 27, "c2")])
        events = self.watcher().poll()
        self.assertEqual({e["session"] for e in events}, {CHILD})

    def test_a_session_id_that_is_not_a_string_changes_nothing(self):
        rec = self.meta(PARENT, CHILD)
        rec["payload"]["session_id"] = {"id": PARENT}
        self.rollout(CHILD, [rec, self.ran("pytest -x", 27, "c2")])
        events = self.watcher().poll()
        self.assertEqual({e["session"] for e in events}, {CHILD})


if __name__ == "__main__":
    unittest.main()
