"""A record shown once, however many files it is written into.

`claude --resume` does not continue the old file.  It opens a new session with
a new id and copies the earlier transcript into it verbatim — same uuids, same
timestamps — and only then starts appending new work.  A watcher adopts that
new file with the rule "one that appeared since we started is read whole,
because all of it is new to the user", and every command, write and error of
the earlier sitting scrolls past a second time as though it were happening now.

The same records also end up in two files when a project directory is copied or
moved: the log exists under both names, neither is a symlink, and the walk
finds both.

Both are the same mistake, and have the same answer: a record uuid names an
event, and an event is shown once.  Codex records carry no uuid and its
duplicate files are parallel workers doing separate work, so nothing there is
deduplicated.
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

from agentwatch.follow import Watcher, _mtime_then_name  # noqa: E402


def bash(uuid, when, command, call="t1"):
    return {"type": "assistant", "uuid": uuid, "timestamp": when.isoformat(),
            "message": {"role": "assistant",
                        "content": [{"type": "tool_use", "id": call,
                                     "name": "Bash",
                                     "input": {"command": command}}]}}


def user(uuid, when, text="do the thing", cwd="/home/you/api"):
    return {"type": "user", "uuid": uuid, "timestamp": when.isoformat(),
            "cwd": cwd, "message": {"role": "user", "content": text}}


class Scratch(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="agentwatch-replay-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.now = datetime.now(timezone.utc)

    def claude(self, name, project="-home-you-api"):
        folder = os.path.join(self.home, ".claude", "projects", project)
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, name + ".jsonl")

    def write(self, path, records):
        with open(path, "w", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r) + "\n")

    def append(self, path, records):
        with open(path, "a", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r) + "\n")

    def watcher(self):
        return Watcher(home=self.home, since=self.now - timedelta(hours=2))

    def commands(self, events):
        return [e["text"] for e in events if e["kind"] == "cmd"]

    def sitting(self):
        t = self.now - timedelta(minutes=30)
        return [
            user("u-1", t),
            bash("a-1", t + timedelta(seconds=1), "pytest -x", "t1"),
            bash("a-2", t + timedelta(seconds=2), "ruff check", "t2"),
        ]


class TestAResumeAppearingWhileWatching(Scratch):

    def test_the_earlier_work_does_not_scroll_past_again(self):
        first = self.claude("aaaa1111")
        self.write(first, self.sitting())
        w = self.watcher()
        seen = self.commands(w.poll())
        self.assertEqual(seen, ["pytest -x", "ruff check"])

        # The resume: a new file, the old transcript copied in, then one new
        # command.  Only the new command is news.
        second = self.claude("bbbb2222")
        self.write(second, self.sitting() + [
            bash("b-1", self.now - timedelta(minutes=1), "mypy .", "t3")])
        w._last_scan -= 10.0          # force the rescan that finds the new file
        self.assertEqual(self.commands(w.poll()), ["mypy ."])

    def test_a_resume_that_did_nothing_yet_shows_nothing(self):
        first = self.claude("aaaa1111")
        self.write(first, self.sitting())
        w = self.watcher()
        w.poll()
        self.write(self.claude("bbbb2222"), self.sitting())
        w._last_scan -= 10.0
        self.assertEqual(w.poll(), [])

    def test_work_appended_to_the_resume_afterwards_still_shows(self):
        # Suppressing the replay must not leave the new file muted.
        first = self.claude("aaaa1111")
        self.write(first, self.sitting())
        w = self.watcher()
        w.poll()
        second = self.claude("bbbb2222")
        self.write(second, self.sitting())
        w._last_scan -= 10.0
        w.poll()
        self.append(second, [bash("b-9", self.now, "make release", "t9")])
        self.assertEqual(self.commands(w.poll()), ["make release"])


class TestTheSameLogUnderTwoProjects(Scratch):

    def test_each_command_is_shown_once(self):
        records = self.sitting()
        self.write(self.claude("aaaa1111", "-home-you-api"), records)
        self.write(self.claude("aaaa1111", "-home-you-api-copy"), records)
        self.assertEqual(self.commands(self.watcher().poll()),
                         ["pytest -x", "ruff check"])

    def test_the_project_the_copy_is_under_is_still_named(self):
        # The duplicate contributes no events, but a record it replayed is
        # still the record that says which directory this log belongs to.
        records = self.sitting()
        self.write(self.claude("aaaa1111", "-home-you-api"), records)
        self.write(self.claude("aaaa1111", "-home-you-api-copy"), records)
        events = self.watcher().poll()
        self.assertTrue(all(e["project"] for e in events), events)

    def test_the_original_is_the_one_that_shows_them(self):
        # Not whichever the directory listing happened to name first: the copy
        # is newer and "zzz" sorts last by name, so only the mtime rule puts
        # the events under the directory the work was actually done in.  The
        # records carry no cwd, so the directory the file was found in is the
        # only thing left to name the project by — which is the question here.
        t = self.now - timedelta(minutes=30)
        records = [user("u-1", t, cwd=""), bash("a-1", t, "pytest -x")]
        original = self.claude("aaaa1111", "-home-you-zzz-original")
        copy = self.claude("aaaa1111", "-home-you-api-copy")
        self.write(original, records)
        self.write(copy, records)
        # Both recent, or the watcher skips them as stale; the original is
        # merely the older of the two.
        stamp = self.now.timestamp()
        os.utime(original, (stamp - 60, stamp - 60))
        os.utime(copy, (stamp, stamp))
        events = self.watcher().poll()
        self.assertEqual({e["project"] for e in events}, {"original"})


class TestTheOrderFilesAreAdopted(Scratch):

    def test_a_file_that_vanished_sorts_last(self):
        # A file listed a moment ago and gone by the time it is stat-ed has no
        # mtime to order it by.  It sorts last rather than first, so that a
        # file whose age is actually known is never displaced by one whose is
        # not -- the ordering is what decides which file owns a shared record.
        real = self.claude("aaaa1111")
        self.write(real, self.sitting())
        gone = os.path.join(os.path.dirname(real), "vanished.jsonl")
        order = sorted([(gone, "p"), (real, "p")], key=_mtime_then_name)
        self.assertEqual([path for path, _ in order], [real, gone])


class TestWhatIsNotDeduplicated(Scratch):

    def test_two_records_with_different_uuids_both_show(self):
        # The same command run twice is two events, and hiding one would be
        # the opposite mistake.
        t = self.now - timedelta(minutes=5)
        self.write(self.claude("aaaa1111"), [
            user("u-1", t),
            bash("a-1", t, "pytest -x", "t1"),
            bash("a-2", t, "pytest -x", "t2"),
        ])
        self.assertEqual(self.commands(self.watcher().poll()),
                         ["pytest -x", "pytest -x"])

    def test_records_without_a_uuid_all_show(self):
        t = self.now - timedelta(minutes=5)
        a = bash("x", t, "make build", "t1")
        b = bash("y", t, "make test", "t2")
        del a["uuid"]
        del b["uuid"]
        self.write(self.claude("aaaa1111"), [user("u-1", t), a, b])
        self.assertEqual(self.commands(self.watcher().poll()),
                         ["make build", "make test"])

    def test_codex_parallel_workers_both_show(self):
        # Codex writes no uuids, and its several files for one session are
        # different workers' real work.
        folder = os.path.join(self.home, ".codex", "sessions", "2026", "08", "04")
        os.makedirs(folder)
        sid = "1a2b3c4d-5e6f-7081-92a3-b4c5d6e7f809"
        for n, command in (("00", "pytest -x"), ("01", "ruff check")):
            path = os.path.join(
                folder, "rollout-2026-08-04T09-40-{}-{}.jsonl".format(n, sid))
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "type": "response_item",
                    "timestamp": (self.now - timedelta(minutes=5)).isoformat(),
                    "payload": {"type": "custom_tool_call", "call_id": n,
                                "input": json.dumps({"command": command})},
                }) + "\n")
        self.assertEqual(sorted(self.commands(self.watcher().poll())),
                         ["pytest -x", "ruff check"])


if __name__ == "__main__":
    unittest.main()
