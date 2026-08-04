"""`»` means you spoke, so the machine must not print one for itself.

Claude Code writes a `user` record, marked `isMeta: true`, whenever it needs to
put text into the conversation on its own account: the caveat before a slash
command's output, the body of a skill being loaded, a message relayed from
another session, a nudge to continue, the placeholder standing in for a pasted
image.  Nobody typed any of it.

Two rules already keep the agent's own loop out of the turn count — a record
carrying tool results, and a record marked `isSidechain` — and an injected
record trips neither.  So `»` appears in the live feed at a moment when the
person was not there.

That is worse here than in a digest.  `»` is the mark somebody scans for to
find where they left off; pointing it at machine text points them at the wrong
place, and in a feed they are watching precisely because they stepped away, it
reads as the session having been handed an instruction they did not give.

On the developer's own logs, 210 records were injected against 2109 typed.

The record is still part of the sitting — its commands, writes and failures
ran on this machine — so only the claim that you spoke goes.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agentwatch.follow import Watcher  # noqa: E402


class Case(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="agentwatch-meta-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.now = datetime.now(timezone.utc)
        self.n = 0

    def at(self, minutes_ago):
        return self.now - timedelta(minutes=minutes_ago)

    def uid(self, prefix):
        self.n += 1
        return "%s-%04d" % (prefix, self.n)

    def typed(self, text, minutes_ago=30):
        """What a person typing looks like."""
        return {"type": "user", "uuid": self.uid("u"),
                "timestamp": self.at(minutes_ago).isoformat(),
                "cwd": "/home/you/api",
                "message": {"role": "user", "content": text}}

    def injected(self, text, minutes_ago=29):
        rec = self.typed(text, minutes_ago)
        rec["uuid"] = self.uid("m")
        rec["isMeta"] = True
        return rec

    def assistant(self, command, call, minutes_ago=28):
        return {"type": "assistant", "uuid": self.uid("a"),
                "timestamp": self.at(minutes_ago).isoformat(),
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

    def write(self, records, project="-home-you-api", name="aaaa1111"):
        d = os.path.join(self.home, ".claude", "projects", project)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, name + ".jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r) + "\n")
        return path

    def poll(self):
        w = Watcher(home=self.home, since=self.now - timedelta(hours=2))
        return w.poll()

    def kinds(self):
        return [e["kind"] for e in self.poll()]


class TestTheShapesClaudeCodeInjects(Case):
    """Each opening line here was taken from a real log."""

    def turns(self, text):
        self.write([self.typed("run the tests"), self.injected(text)])
        return self.kinds().count("turn")

    def test_a_slash_command_caveat_does_not_print(self):
        self.assertEqual(self.turns(
            "<local-command-caveat>Caveat: The messages below were generated "
            "by the user while running a local command.</local-command-caveat>"
        ), 1)

    def test_a_skill_body_does_not_print(self):
        self.assertEqual(self.turns(
            "Base directory for this skill: /tmp/bundled-skills/dataviz\n\n"
            "# Data Visualization\n\nA chart is read by people."), 1)

    def test_a_message_relayed_from_another_session_does_not_print(self):
        self.assertEqual(self.turns(
            "Another Claude session sent a message while you were working:\n"
            "the build is green"), 1)

    def test_a_nudge_to_continue_does_not_print(self):
        self.assertEqual(self.turns("Continue from where you left off."), 1)

    def test_an_image_placeholder_does_not_print(self):
        self.assertEqual(self.turns(
            "[Image: original 5712x4284, displayed at 2000x1500.]"), 1)

    def test_text_delivered_as_blocks_rather_than_a_string(self):
        rec = self.injected("")
        rec["message"]["content"] = [{"type": "text", "text": "# A Skill"}]
        self.write([self.typed("go"), rec])
        self.assertEqual(self.kinds().count("turn"), 1)


class TestWhatStillPrints(Case):

    def test_a_person_typing_prints(self):
        self.write([self.typed("run the tests", 30),
                    self.typed("now ship it", 25)])
        self.assertEqual(self.kinds().count("turn"), 2)

    def test_the_field_set_false_prints(self):
        rec = self.typed("now ship it", 25)
        rec["isMeta"] = False
        self.write([self.typed("go", 30), rec])
        self.assertEqual(self.kinds().count("turn"), 2)

    def test_a_record_without_the_field_prints(self):
        # Older logs have no isMeta.  Only an explicit true is machine text;
        # treating absence as true would silence every turn recorded before
        # the field existed, which is the opposite mistake and a worse one.
        rec = self.typed("now ship it", 25)
        self.assertNotIn("isMeta", rec)
        self.write([self.typed("go", 30), rec])
        self.assertEqual(self.kinds().count("turn"), 2)

    def test_a_string_true_is_not_an_explicit_true(self):
        rec = self.typed("now ship it", 25)
        rec["isMeta"] = "true"
        self.write([self.typed("go", 30), rec])
        self.assertEqual(self.kinds().count("turn"), 2)


class TestTheWorkAroundItStillStreams(Case):

    def test_the_commands_still_stream(self):
        # The point of the tool.  Silencing the mark must not silence the run.
        self.write([self.injected("Continue from where you left off.", 30),
                    self.assistant("pytest -x", "t1", 29),
                    self.result("t1", 28)])
        events = self.poll()
        self.assertEqual([e["text"] for e in events if e["kind"] == "cmd"],
                         ["pytest -x"])
        self.assertEqual([e["kind"] for e in events], ["cmd"])

    def test_a_failure_after_injected_text_still_streams(self):
        self.write([self.injected("Continue.", 30),
                    self.assistant("pytest -x", "t1", 29),
                    self.result("t1", 28, is_error=True)])
        self.assertIn("error", self.kinds())

    def test_an_error_inside_an_injected_record_still_streams(self):
        # Belt and braces: no real injected record carries a tool result, but
        # if one ever did, a failure that happened is a failure that happened.
        # This is the difference between not printing the mark and not reading
        # the record — the tempting shortcut is to drop the record whole.
        rec = self.result("t1", 28, is_error=True)
        rec["isMeta"] = True
        self.write([self.typed("go", 30),
                    self.assistant("pytest -x", "t1", 29), rec])
        self.assertEqual(self.kinds(), ["turn", "cmd", "error"])

    def test_the_project_is_still_named(self):
        # The injected record is where a resumed session says which directory
        # it is in.  Not printing a mark for it is not the same as not
        # reading it.
        self.write([self.injected("Continue.", 30),
                    self.assistant("pytest -x", "t1", 29),
                    self.result("t1", 28)])
        events = self.poll()
        self.assertTrue(events)
        self.assertTrue(all(e["project"] for e in events), events)


class TestItComposesWithTheRulesAlreadyThere(Case):

    def test_tool_results_sidechains_and_injected_text_all_stay_quiet(self):
        side = self.typed("a prompt for the subagent", 26)
        side["isSidechain"] = True
        self.write([
            self.typed("run the tests", 30),
            self.assistant("pytest -x", "t1", 29),
            self.result("t1", 28),
            side,
            self.injected("Continue from where you left off.", 25),
        ])
        self.assertEqual(self.kinds().count("turn"), 1)


if __name__ == "__main__":
    unittest.main()
