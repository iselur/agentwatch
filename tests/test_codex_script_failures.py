"""A script that failed is a failure whether or not it named a command.

Codex runs everything through a JavaScript snippet, and a snippet that only
sends a patch has no command in it to be named after.  agentwatch reported a
failing snippet only when it could recall a command for the call, on the
grounds that a patch failure had already been reported, in more detail, by the
`patch_apply_end` record that follows the call.

Measured against every rollout on the developer's machine, that premise is
false almost every time it is used.  67 failing snippets had no command to name
— and not one of them shared a call id with a failing `patch_apply_end`.  The
patches had failed *inside the script*, so no end record was ever emitted and
nothing said anything: session 019f7ac5-88d3-7b93-b790-99adb23103d9 has 23
script results, 7 patch results all successful, and 6 failed patch attempts
that agentwatch reported as `0 errors`.

The silence itself is still worth keeping — a patch that failed to apply
should not be announced twice.  What changes is how it is decided: by which
script the patch belongs to, rather than by whether a command happened to be
remembered.

Finding that script takes two steps, because the end record usually does not
carry the script's id.  Of 713 real `patch_apply_end` records 646 are named
`exec-<uuid>`, which appears nowhere else in the file, and 67 share the
`call_<...>` namespace the scripts use.  So the id is used when it matches, and
otherwise the patch belongs to the script that was running when it failed —
the end record arrives between a script's call and its result every time both
ids matched (53 of 53), and all 5 real patch failures on the machine fall
inside a script that had not yet reported back.

And a failure that can be reported should be named.  The envelope in the
snippet names the files it was trying to change, which is a line a person can
act on where a bare "something failed" is not.
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

SESSION = "019f7ac5-88d3-7b93-b790-99adb23103d9"


class Case(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="agentwatch-script-fail-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.now = datetime.now(timezone.utc)
        self.dir = os.path.join(self.home, ".codex", "sessions",
                                "2026", "08", "02")
        self.records = []

    def at(self, minutes_ago):
        return self.now - timedelta(minutes=minutes_ago)

    def add(self, rec):
        self.records.append(rec)
        return rec

    def typed(self, minutes_ago=30):
        return self.add({"timestamp": self.at(minutes_ago).isoformat(),
                         "type": "event_msg",
                         "payload": {"type": "user_message", "message": "go"}})

    def patch_script(self, call="c1", path="/home/you/api/server.py",
                     minutes_ago=29, extra=""):
        """The snippet Codex sends when it is only applying a patch."""
        snippet = ('const patch = "*** Begin Patch\\n*** Update File: %s\\n'
                   '@@\\n-old\\n+new\\n*** End Patch";%s' % (path, extra))
        return self.add({"timestamp": self.at(minutes_ago).isoformat(),
                         "type": "response_item",
                         "payload": {"type": "custom_tool_call", "name": "exec",
                                     "call_id": call, "input": snippet}})

    def command_script(self, cmd="pytest -x", call="c1", minutes_ago=29):
        snippet = 'await exec({cmd: "%s", workdir: "/home/you/api"});' % cmd
        return self.add({"timestamp": self.at(minutes_ago).isoformat(),
                         "type": "response_item",
                         "payload": {"type": "custom_tool_call", "name": "exec",
                                     "call_id": call, "input": snippet}})

    def bare_script(self, call="c1", minutes_ago=29):
        """A snippet with neither a command nor a patch in it."""
        return self.add({"timestamp": self.at(minutes_ago).isoformat(),
                         "type": "response_item",
                         "payload": {"type": "custom_tool_call", "name": "exec",
                                     "call_id": call,
                                     "input": "const x = require('fs');"}})

    def result(self, call="c1", ok=False, minutes_ago=28, error="oh no"):
        head = "Script completed" if ok else "Script failed"
        return self.add({"timestamp": self.at(minutes_ago).isoformat(),
                         "type": "response_item",
                         "payload": {"type": "custom_tool_call_output",
                                     "call_id": call,
                                     "output": [
                                         {"type": "input_text",
                                          "text": head + "\nWall time 0.1 seconds\nOutput:\n"},
                                         {"type": "input_text",
                                          "text": "Script error:\n" + error}]}})

    def patch_end(self, call="c1", ok=True, path="/home/you/api/server.py",
                  minutes_ago=28.5):
        return self.add({"timestamp": self.at(minutes_ago).isoformat(),
                         "type": "event_msg",
                         "payload": {"type": "patch_apply_end",
                                     "call_id": call, "success": ok,
                                     "stdout": "", "stderr": "",
                                     "changes": {path: {"update": {}}}}})

    def poll(self, minutes_ago=31):
        stamp = self.at(minutes_ago).strftime("%Y-%m-%dT%H-%M-%S")
        path = os.path.join(self.dir, "rollout-%s-%s.jsonl" % (stamp, SESSION))
        os.makedirs(self.dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            for r in self.records:
                fh.write(json.dumps(r) + "\n")
        watcher = Watcher(home=self.home, since=self.now - timedelta(hours=2))
        return watcher.poll()

    def errors(self, events):
        return [e["text"] for e in events if e["kind"] == "error"]


class TestAFailureWithNoCommandToName(Case):

    def test_a_failed_patch_script_is_reported(self):
        # The whole class in one line: six of these in one real session, all
        # of them shown as no errors at all.
        self.typed()
        self.patch_script()
        self.result()
        self.assertEqual(len(self.errors(self.poll())), 1)

    def test_it_is_named_after_the_file_it_tried_to_change(self):
        # Not a bare "something failed" — the envelope says what was being
        # changed, and that is the part a person can act on.
        self.typed()
        self.patch_script(path="/home/you/api/server.py")
        self.result()
        self.assertIn("server.py", self.errors(self.poll())[0])

    def test_a_script_with_nothing_in_it_is_still_reported(self):
        # 5 of the developer's failures are these: a snippet that died of a
        # SyntaxError or a sandbox denial before it ran anything at all.  There
        # is nothing to name it after, and it still failed.
        self.typed()
        self.bare_script()
        self.result(error="SyntaxError: Invalid or unexpected token")
        errors = self.errors(self.poll())
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].strip(), "an error line with no text at all")

    def test_at_most_three_files_are_named(self):
        self.typed()
        snippet = 'const patch = "*** Begin Patch' + "".join(
            "\\n*** Update File: /home/you/api/f%d.py" % i for i in range(6))
        self.add({"timestamp": self.at(29).isoformat(),
                  "type": "response_item",
                  "payload": {"type": "custom_tool_call", "name": "exec",
                              "call_id": "c1", "input": snippet}})
        self.result()
        text = self.errors(self.poll())[0]
        self.assertLessEqual(sum(text.count("f%d.py" % i) for i in range(6)), 3)


class TestTheSilenceThatIsKept(Case):

    def test_a_patch_that_failed_to_apply_is_not_reported_twice(self):
        # patch_apply_end says which files and why; the script's own result
        # says only that something failed.  One line, the better one.
        self.typed()
        self.patch_script()
        self.patch_end(ok=False)
        self.result()
        self.assertEqual(len(self.errors(self.poll())), 1)

    def test_the_line_kept_is_the_one_with_the_detail(self):
        self.typed()
        self.patch_script()
        self.patch_end(ok=False)
        self.result()
        self.assertIn("did not apply", self.errors(self.poll())[0])

    def test_a_patch_that_applied_does_not_silence_a_later_failure(self):
        # The patch landed and the script went on to fail anyway.  Nothing has
        # reported that, so it is reported here.
        self.typed()
        self.patch_script()
        self.patch_end(ok=True)
        self.result()
        self.assertEqual(len(self.errors(self.poll())), 1)

    def test_the_pairing_is_by_call_not_by_session(self):
        # The old rule was really "was a command remembered", which made the
        # silence fall on whichever call happened to be unnamed.  A failed
        # patch in one call says nothing about a different call.
        self.typed()
        self.patch_script(call="c1", path="/home/you/api/a.py")
        self.patch_end(call="c1", ok=False, path="/home/you/api/a.py")
        self.result(call="c1")
        self.patch_script(call="c2", path="/home/you/api/b.py", minutes_ago=27)
        self.result(call="c2", minutes_ago=26)
        errors = self.errors(self.poll())
        self.assertEqual(len(errors), 2, errors)
        self.assertTrue(any("b.py" in e for e in errors), errors)

    def test_a_patch_result_under_its_own_id_still_silences_its_script(self):
        # The common shape, and the one the call id alone cannot resolve: 646
        # of 713 real patch results are named `exec-<uuid>`, which matches no
        # script anywhere in the file.  It belongs to the script it interrupted.
        self.typed()
        self.patch_script(call="call_X")
        self.patch_end(call="exec-595e4329-e089-4822-9e8c-816aa9f13203",
                       ok=False)
        self.result(call="call_X")
        errors = self.errors(self.poll())
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("did not apply", errors[0])

    def test_it_silences_only_the_script_it_interrupted(self):
        # The next script's failure is its own.
        self.typed()
        self.patch_script(call="call_X", path="/home/you/api/a.py")
        self.patch_end(call="exec-595e4329", ok=False, path="/home/you/api/a.py")
        self.result(call="call_X")
        self.patch_script(call="call_Y", path="/home/you/api/b.py",
                          minutes_ago=27)
        self.result(call="call_Y", minutes_ago=26)
        errors = self.errors(self.poll())
        self.assertEqual(len(errors), 2, errors)
        self.assertTrue(any("b.py" in e for e in errors), errors)

    def test_a_patch_failure_with_no_script_running_silences_nothing(self):
        # Nothing to attribute it to: the script it belonged to has already
        # reported back, so the next one is not answering for it.
        self.typed()
        self.patch_script(call="call_X")
        self.result(call="call_X", ok=True)
        self.patch_end(call="exec-595e4329", ok=False, minutes_ago=27.5)
        self.patch_script(call="call_Y", minutes_ago=27)
        self.result(call="call_Y", minutes_ago=26)
        errors = self.errors(self.poll())
        self.assertEqual(len(errors), 2, errors)

    def test_the_envelope_still_reports_no_write(self):
        # Reading a label out of the envelope must not turn into announcing an
        # edit that may never have landed — the deliberate silence in
        # tests/test_patch_envelope_silence.py stands.
        self.typed()
        self.patch_script()
        self.result(ok=True)
        writes = [e for e in self.poll() if e["kind"] == "write"]
        self.assertEqual(writes, [])


class TestWhatDidNotChange(Case):

    def test_a_named_command_is_still_named(self):
        self.typed()
        self.command_script(cmd="pytest -x")
        self.result()
        self.assertEqual(self.errors(self.poll()), ["pytest -x"])

    def test_a_script_that_worked_reports_nothing(self):
        self.typed()
        self.patch_script()
        self.result(ok=True)
        self.assertEqual(self.errors(self.poll()), [])

    def test_a_result_for_a_call_never_seen_is_still_reported(self):
        # A rollout joined partway through: the call is above the point the
        # watcher started reading, so nothing about it can be recalled.  The
        # failure is still real.
        self.typed()
        self.result(call="unknown")
        self.assertEqual(len(self.errors(self.poll())), 1)


if __name__ == "__main__":
    unittest.main()
