"""`--project` takes the same string here as it does at the other command.

`agentlog --project /home/you/relay` listed the project.  `agentwatch --project
/home/you/relay` printed

    nothing has happened in that window

on a project that had been busy all afternoon.  Two commands out of one install,
one flag, one string, two answers -- and the wrong one is silence dressed as a
finding, which sends the reader off to widen `--since`.  They can widen it as
far as they like; nothing on screen says the flag was the problem.

The rule lives in `project.py` now, one copy in each package, so this file is
mostly about the half `agentwatch` could not do: matching a path.  A live tail
knows a project by its *name* long before the log says which directory it is in,
so the path has to be carried far enough to be asked about -- and asked about
again when the log finally says.
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

from agentwatch.follow import Watcher


def cmd_record(command, when):
    return {"type": "assistant", "timestamp": when.isoformat(), "message": {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": "t", "name": "Bash",
                     "input": {"command": command}}]}}


class Scratch(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="agentwatch-project-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.now = datetime.now(timezone.utc)

    def claude_path(self, project="-home-you-relay", name="s1"):
        folder = os.path.join(self.home, ".claude", "projects", project)
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, name + ".jsonl")

    def append(self, path, *records):
        with open(path, "a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")

    def watcher(self, **kwargs):
        kwargs.setdefault("since", self.now - timedelta(minutes=10))
        return Watcher(home=self.home, **kwargs)

    def said(self, **kwargs):
        return [e["text"] for e in self.watcher(**kwargs).poll()]


class TestThePathIsAWayOfNamingIt(Scratch):
    """The half that did not work: asking by the directory it ran in."""

    def setUp(self):
        super().setUp()
        self.append(self.claude_path(project="-home-you-relay"),
                    cmd_record("pytest", self.now))
        self.append(self.claude_path(project="-home-you-web", name="s2"),
                    cmd_record("npm test", self.now))

    def test_the_whole_path_finds_it(self):
        self.assertEqual(self.said(project="/home/you/relay"), ["pytest"])

    def test_the_last_component_still_finds_it(self):
        # The spelling that already worked.  It is asserted beside the one that
        # did not, because widening a match is exactly how you stop it being a
        # match for the thing it used to find.
        self.assertEqual(self.said(project="relay"), ["pytest"])

    def test_a_directory_above_it_finds_it(self):
        # `--project /home/you` is not a project, it is a place projects are
        # kept, and someone who types it means "everything of mine".
        self.assertEqual(sorted(self.said(project="/home/you")),
                         ["npm test", "pytest"])

    def test_a_path_that_is_a_different_project_finds_nothing(self):
        self.assertEqual(self.said(project="/home/you/relay-ui"), [])

    def test_the_trailing_slash_tab_completion_adds_does_not_lose_it(self):
        # `--project ~/relay/` is what a shell types for you when you complete a
        # directory name.  Without the strip it is not a substring of
        # `/home/you/relay`, so the slash alone was the difference between the
        # project and an empty screen -- with nothing on screen to say so.
        self.assertEqual(self.said(project="/home/you/relay/"), ["pytest"])

    def test_case_does_not_matter_in_a_path_either(self):
        self.assertEqual(self.said(project="/HOME/YOU/RELAY"), ["pytest"])

    def test_asking_for_nothing_still_asks_for_everything(self):
        # The default.  Getting this the wrong way round is a watcher that
        # shows nothing at all until you pass a flag.
        self.assertEqual(sorted(self.said()), ["npm test", "pytest"])
        self.assertEqual(sorted(self.said(project="")), ["npm test", "pytest"])


class TestThePathTheLogItselfGives(Scratch):
    """A decoded directory name is a guess; the log's own `cwd` is not.

    A project whose path contains a dash decodes wrongly --
    ``-home-you-r102-bench`` reads back as ``/home/you/r102/bench`` -- so the
    filter has to be able to change its mind when the log says otherwise, in
    both directions, and on the path as well as on the name.
    """

    def late_cwd_log(self, project, cwd, quiet_lines=80):
        path = self.claude_path(project=project)
        self.append(path, *[cmd_record("echo %d" % i, self.now)
                            for i in range(quiet_lines)])
        self.append(path, {"type": "user", "cwd": cwd,
                           "timestamp": self.now.isoformat(),
                           "message": {"role": "user", "content": "hi"}})
        self.append(path, cmd_record("pytest", self.now))
        return path

    def test_a_path_the_log_only_says_later_still_matches(self):
        # The decoded guess is `/home/you/r102/bench`, which does not contain
        # the path asked for.  The log says otherwise eighty lines in.
        self.late_cwd_log("-home-you-r102-bench", "/home/you/r102-bench")
        self.assertEqual(self.said(project="/home/you/r102-bench")[-1:],
                         ["pytest"])

    def test_a_path_the_log_contradicts_stops_matching(self):
        # The other direction, which is the one that leaks: the guess matched,
        # so the log was adopted and read, and then it said it was somewhere
        # else entirely.  Its events must not be shown.
        self.late_cwd_log("-home-you-relay", "/home/you/checkout")
        self.assertEqual(self.said(project="/home/you/relay"), [])

    def test_a_log_that_never_says_is_matched_on_the_guess(self):
        # No cwd anywhere in it.  A guess is a label of last resort, not of no
        # resort, and it is a whole path -- so a path can be matched against it.
        self.append(self.claude_path(project="-home-you-api"),
                    cmd_record("curl", self.now))
        self.assertEqual(self.said(project="/home/you/api"), ["curl"])


if __name__ == "__main__":
    unittest.main()
