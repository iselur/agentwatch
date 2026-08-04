"""A subagent's transcript is part of a session, not a session of its own.

When Claude Code hands work to a subagent it writes that subagent's whole
transcript to its own file, in a `subagents/` directory beside the parent's
log and named after the parent session:

    ~/.claude/projects/<project>/<session-id>.jsonl
    ~/.claude/projects/<project>/<session-id>/subagents/agent-<hex>.jsonl

Every `.jsonl` under the projects tree was being adopted as a session.  On the
developer's machine 393 of 864 Claude log files are subagent transcripts — 45%
of them — so `watching N session logs` was counting nearly twice the sittings
that exist, and every event from a subagent carried a `session` id of
`agent-a0940e681059ff8ec`, which names nothing a person can find.

The same recurring direction as the turn-count bugs before it: a summary
computed over more things than exist, reported as complete.  A reader who
suspects an under-count can go and add the files up; there is nothing to check
an over-count against.

The work itself must keep streaming, and unchanged.  A `pytest -x` a subagent
ran is a command that ran on this machine, during this sitting, and the whole
reason to watch a feed is to see it.  Only the claim that it is a separate
session goes.
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

from agentwatch.follow import Watcher, session_id_for  # noqa: E402

PARENT = "689e648c-e034-43ab-9783-a72191da648f"
CHILD = "agent-a8818327f2efaf469"


class Case(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="agentwatch-subagent-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.now = datetime.now(timezone.utc)
        self.n = 0
        self.project_dir = os.path.join(
            self.home, ".claude", "projects", "-home-you-api")

    def at(self, minutes_ago):
        return self.now - timedelta(minutes=minutes_ago)

    def uid(self, prefix):
        self.n += 1
        return "%s-%04d" % (prefix, self.n)

    def typed(self, text, minutes_ago=30):
        return {"type": "user", "uuid": self.uid("u"),
                "timestamp": self.at(minutes_ago).isoformat(),
                "cwd": "/home/you/api",
                "message": {"role": "user", "content": text}}

    def ran(self, command, call, minutes_ago=28):
        return {"type": "assistant", "uuid": self.uid("a"),
                "timestamp": self.at(minutes_ago).isoformat(),
                "cwd": "/home/you/api",
                "message": {"role": "assistant",
                            "content": [{"type": "tool_use", "id": call,
                                         "name": "Bash",
                                         "input": {"command": command}}]}}

    def result(self, call, minutes_ago=27, is_error=False):
        return {"type": "user", "uuid": self.uid("r"),
                "timestamp": self.at(minutes_ago).isoformat(),
                "message": {"role": "user",
                            "content": [{"type": "tool_result",
                                         "tool_use_id": call,
                                         "is_error": is_error,
                                         "content": "output"}]}}

    def _write(self, path, records):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r) + "\n")
        return path

    def parent_log(self, records, session=PARENT):
        return self._write(
            os.path.join(self.project_dir, session + ".jsonl"), records)

    def subagent_log(self, records, session=PARENT, name=CHILD):
        return self._write(
            os.path.join(self.project_dir, session, "subagents",
                         name + ".jsonl"), records)

    def workflow_log(self, records, session=PARENT, name=CHILD,
                     run="wf_5e1bb28a-07b"):
        return self._write(
            os.path.join(self.project_dir, session, "subagents", "workflows",
                         run, name + ".jsonl"), records)

    def watcher(self):
        return Watcher(home=self.home, since=self.now - timedelta(hours=2))


class TestTheCountOfSessions(Case):

    def test_a_subagent_transcript_is_not_a_second_session(self):
        self.parent_log([self.typed("delegate it"),
                         self.ran("make", "t1"), self.result("t1")])
        self.subagent_log([self.ran("pytest -x", "s1", 26),
                           self.result("s1", 25)])
        w = self.watcher()
        w.poll()
        self.assertEqual(w.watched(), 1)

    def test_many_subagents_are_still_one_session(self):
        # The shape that made this visible: one sitting, 200 subagents.
        self.parent_log([self.typed("fan out"),
                         self.ran("make", "t1"), self.result("t1")])
        for i in range(20):
            self.subagent_log([self.ran("pytest -x", "s%d" % i, 26)],
                              name="agent-a%016x" % i)
        w = self.watcher()
        w.poll()
        self.assertEqual(w.watched(), 1)

    def test_two_real_sessions_are_still_two(self):
        # The guard against the lazy fix of counting one log per directory.
        self.parent_log([self.typed("first"), self.ran("make", "t1")])
        self.parent_log([self.typed("second"), self.ran("make", "t2")],
                        session="aaaa1111-0000-0000-0000-000000000000")
        w = self.watcher()
        w.poll()
        self.assertEqual(w.watched(), 2)


class TestTheWorkStillStreams(Case):
    """The point of the tool.  Not counting it twice must not silence it."""

    def texts(self, kind="cmd"):
        w = self.watcher()
        return [e["text"] for e in w.poll() if e["kind"] == kind]

    def test_a_command_a_subagent_ran_still_streams(self):
        self.parent_log([self.typed("delegate it"), self.ran("make", "t1"),
                         self.result("t1")])
        self.subagent_log([self.ran("pytest -x", "s1", 26),
                           self.result("s1", 25)])
        self.assertEqual(sorted(self.texts()), ["make", "pytest -x"])

    def test_a_failure_a_subagent_hit_still_streams(self):
        self.parent_log([self.typed("delegate it")])
        self.subagent_log([self.ran("pytest -x", "s1", 26),
                           self.result("s1", 25, is_error=True)])
        self.assertEqual(self.texts("error"), ["pytest -x"])

    def test_a_file_a_subagent_wrote_still_streams(self):
        rec = self.ran("", "s1", 26)
        rec["message"]["content"] = [{"type": "tool_use", "id": "s1",
                                      "name": "Edit",
                                      "input": {"file_path":
                                                "/home/you/api/config.py"}}]
        self.parent_log([self.typed("delegate it")])
        self.subagent_log([rec, self.result("s1", 25)])
        self.assertEqual(self.texts("write"), ["/home/you/api/config.py"])

    def test_the_project_is_still_named(self):
        self.parent_log([self.typed("delegate it")])
        self.subagent_log([self.ran("pytest -x", "s1", 26)])
        events = self.watcher().poll()
        self.assertTrue(events)
        self.assertTrue(all(e["project"] for e in events), events)


class TestWhichSessionItBelongsTo(Case):

    def sessions_of(self, kind="cmd"):
        w = self.watcher()
        return {e["text"]: e["session"] for e in w.poll()
                if e["kind"] == kind}

    def test_a_subagents_events_carry_the_parent_session_id(self):
        # Not `agent-a8818327f2efaf469`, which names nothing a person can look
        # up.  A reader joining `--json` output by session must find the
        # subagent's work under the sitting that spawned it.
        self.parent_log([self.typed("delegate it"), self.ran("make", "t1")])
        self.subagent_log([self.ran("pytest -x", "s1", 26)])
        self.assertEqual(self.sessions_of(),
                         {"make": PARENT, "pytest -x": PARENT})

    def test_the_id_comes_from_the_directory_not_the_file(self):
        self.assertEqual(
            session_id_for(os.path.join(self.project_dir, PARENT, "subagents",
                                        CHILD + ".jsonl"), "claude"),
            PARENT)

    def test_a_session_log_still_names_itself(self):
        self.assertEqual(
            session_id_for(os.path.join(self.project_dir, PARENT + ".jsonl"),
                           "claude"),
            PARENT)

    def test_a_subagent_whose_parent_log_is_missing_still_streams(self):
        # A session log can be deleted, or the parent can be outside the
        # window.  The subagent's work happened either way, and the directory
        # still says which sitting it belonged to.
        self.subagent_log([self.ran("pytest -x", "s1", 26)])
        w = self.watcher()
        events = w.poll()
        self.assertEqual([e["text"] for e in events if e["kind"] == "cmd"],
                         ["pytest -x"])
        self.assertEqual({e["session"] for e in events}, {PARENT})
        self.assertEqual(w.watched(), 1)


class TestASubagentOfAWorkflow(Case):
    """The same transcript, one directory deeper.

    A workflow's agents get a run directory of their own inside `subagents/`:

        <project>/<session-id>/subagents/workflows/<run-id>/agent-<hex>.jsonl

    The first fix looked only at the directory immediately holding the file, so
    189 of the developer's 582 subagent transcripts — every one a workflow ran —
    went on being adopted as sessions of their own.  Found by diffing the
    session ids against agentlog's over the whole corpus after that fix shipped:
    190 sessions agentwatch saw that agentlog did not, and every one of them was
    named `agent-<hex>`.  What names the sitting is the directory above
    `subagents`, however many directories the transcript itself sits under.
    """

    def test_a_workflow_subagent_is_not_a_second_session(self):
        self.parent_log([self.typed("run the workflow"),
                         self.ran("make", "t1")])
        self.workflow_log([self.ran("pytest -x", "s1", 26)])
        w = self.watcher()
        w.poll()
        self.assertEqual(w.watched(), 1)

    def test_its_events_carry_the_parent_session_id(self):
        self.parent_log([self.typed("run the workflow")])
        self.workflow_log([self.ran("pytest -x", "s1", 26)])
        events = self.watcher().poll()
        self.assertEqual([e["text"] for e in events if e["kind"] == "cmd"],
                         ["pytest -x"])
        self.assertEqual({e["session"] for e in events}, {PARENT})

    def test_the_id_survives_any_depth_below_subagents(self):
        # The run directory is one level today.  Nothing promises it stays one.
        deep = os.path.join(self.project_dir, PARENT, "subagents", "workflows",
                            "wf_5e1bb28a-07b", "round-2", CHILD + ".jsonl")
        self.assertEqual(session_id_for(deep, "claude"), PARENT)

    def test_the_project_is_the_parents_project(self):
        # Not the run id, and not the word `subagents`.  Whatever label the
        # sitting carries, its subagents carry the same one.
        self.parent_log([self.typed("run the workflow")])
        self.workflow_log([self.ran("pytest -x", "s1", 26)])
        events = self.watcher().poll()
        self.assertTrue(events)
        self.assertEqual(len({e["project"] for e in events}), 1, events)
        self.assertNotIn("wf_5e1bb28a-07b",
                         {e["project"] for e in events})

    def test_two_runs_of_one_session_are_still_one_session(self):
        self.parent_log([self.typed("run both")])
        self.workflow_log([self.ran("pytest -x", "s1", 26)],
                          run="wf_aaaaaaaa-000", name="agent-a1")
        self.workflow_log([self.ran("pytest -q", "s2", 26)],
                          run="wf_bbbbbbbb-111", name="agent-a2")
        w = self.watcher()
        texts = [e["text"] for e in w.poll() if e["kind"] == "cmd"]
        self.assertEqual(sorted(texts), ["pytest -q", "pytest -x"])
        self.assertEqual(w.watched(), 1)

    def test_a_session_directory_named_subagents_is_not_walked_past(self):
        # The guard against walking up until something looks like a session id:
        # the search stops at the first `subagents` above the file, and the
        # sitting is whatever directory holds it.
        path = os.path.join(self.project_dir, "subagents", "subagents",
                            CHILD + ".jsonl")
        self.assertEqual(session_id_for(path, "claude"), "subagents")


class TestCodexIsUnaffected(Case):
    """Codex writes no subagent transcripts; its layout must not shift."""

    def test_a_codex_rollout_still_names_itself(self):
        path = ("/home/you/.codex/sessions/2026/08/04/"
                "rollout-2026-08-04T09-00-00-019f7a74-f70f-71f2-87c9-"
                "2e11fb4c1ddb.jsonl")
        self.assertEqual(session_id_for(path, "codex"),
                         "019f7a74-f70f-71f2-87c9-2e11fb4c1ddb")

    def test_a_codex_log_in_a_subagents_directory_is_read_as_codex(self):
        # Belt and braces: the rule is Claude's layout, not the word.
        path = ("/home/you/.codex/sessions/subagents/"
                "rollout-2026-08-04T09-00-00-019f7a74-f70f-71f2-87c9-"
                "2e11fb4c1ddb.jsonl")
        self.assertEqual(session_id_for(path, "codex"),
                         "019f7a74-f70f-71f2-87c9-2e11fb4c1ddb")


if __name__ == "__main__":
    unittest.main()
