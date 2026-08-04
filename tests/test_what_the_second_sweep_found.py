"""The second mutation sweep over `agentwatch/follow.py` — the boundaries it found.

Five of the seven survivors were comparisons off by their edge: `>=` where `>`
would do just as well for every input the suite happened to use.  A watcher is
made of those edges.  It decides what is recent enough to follow, what is new
enough to print, and how often to look for files that were not there a moment
ago, and each of those decisions is one comparison against a number.

  * **`--since` includes what happened at that instant.**  Both halves of it:
    the log whose last write lands exactly on the boundary is followed, and the
    event stamped exactly on the boundary is shown.  `--since 09:00` that skips
    the 09:00:00 record is a filter that quietly disagrees with the number it
    was given, and there is nothing on screen to notice it by.

  * **Rescanning is throttled, and does happen.**  The directory is re-read at
    most every `RESCAN_MIN_S`, because `poll` runs in a loop and a full walk of
    every session directory on each pass is the difference between a watcher
    you can leave running and one you notice.  Both sides matter: the flag that
    ends the first scan has to be cleared, or every poll walks the tree again;
    and the interval has to actually elapse, or a session started while you
    were watching never appears.

  * **A session being followed counts even before it has said anything.**  The
    count comes from the tracked files whose offset is not negative, and `-1`
    is the marker for a log deliberately excluded by `--project`.  Zero is not
    that marker — it is every log opened from the beginning, which is every log
    at all under `--since`.  Requiring a positive offset made `watching N
    sessions` count only the ones that had already produced output.

Two survivors are equivalent mutants and are left alive, with the reason at the
bottom of the file.
"""

from __future__ import annotations

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

from agentwatch.follow import RESCAN_MIN_S, Watcher


def cmd_record(command, when):
    return {"type": "assistant", "timestamp": when.isoformat(), "message": {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": "t", "name": "Bash",
                     "input": {"command": command}}]}}


def said_where(cwd, when):
    """The record a session names its working directory in."""
    return {"type": "user", "timestamp": when.isoformat(), "cwd": cwd,
            "message": {"role": "user", "content": []}}


class Scratch(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="agentwatch-sweep2-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.now = datetime.now(timezone.utc)
        self.ticks = [self.now.timestamp()]

    def clock(self):
        """A clock that only moves when a test moves it."""
        return self.ticks[-1]

    def tick(self, seconds):
        self.ticks.append(self.ticks[-1] + seconds)

    def log(self, name="s1", project="-home-you-api"):
        folder = os.path.join(self.home, ".claude", "projects", project)
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, name + ".jsonl")

    def append(self, path, *records):
        with open(path, "a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")

    def watcher(self, **kwargs):
        kwargs.setdefault("since", self.now - timedelta(minutes=10))
        kwargs.setdefault("clock", self.clock)
        return Watcher(home=self.home, **kwargs)


class TestTheEdgeOfSince(Scratch):

    def test_a_log_last_written_exactly_at_since_is_followed(self):
        boundary = self.now - timedelta(minutes=5)
        path = self.log()
        self.append(path, cmd_record("pytest -x", boundary))
        os.utime(path, (boundary.timestamp(), boundary.timestamp()))

        watcher = self.watcher(since=boundary)
        events = watcher.poll()
        self.assertEqual([e["text"] for e in events], ["pytest -x"],
                         "a log whose last write is the boundary was treated as stale")

    def test_an_event_stamped_exactly_at_since_is_shown(self):
        boundary = self.now - timedelta(minutes=5)
        path = self.log()
        self.append(path,
                    cmd_record("before", boundary - timedelta(seconds=1)),
                    cmd_record("on the boundary", boundary),
                    cmd_record("after", boundary + timedelta(seconds=1)))

        watcher = self.watcher(since=boundary)
        shown = [e["text"] for e in watcher.poll()]
        self.assertEqual(shown, ["on the boundary", "after"],
                         "--since dropped the record stamped at the time asked for")


class TestHowOftenItLooksForNewLogs(Scratch):

    def test_it_does_not_walk_the_tree_again_on_every_poll(self):
        first = self.log("s1")
        self.append(first, cmd_record("first", self.now))
        watcher = self.watcher()
        self.assertEqual([e["text"] for e in watcher.poll()], ["first"])

        # A log that appears immediately after a scan is not found by the very
        # next poll — that is the throttle doing its job.
        second = self.log("s2")
        self.append(second, cmd_record("second", self.now))
        self.tick(RESCAN_MIN_S / 2)
        self.assertEqual([e["text"] for e in watcher.poll()], [],
                         "the directory was walked again straight away")

    def test_it_does_walk_it_again_once_the_interval_is_up(self):
        first = self.log("s1")
        self.append(first, cmd_record("first", self.now))
        watcher = self.watcher()
        watcher.poll()

        second = self.log("s2")
        self.append(second, cmd_record("second", self.now))
        self.tick(RESCAN_MIN_S)
        self.assertEqual([e["text"] for e in watcher.poll()], ["second"],
                         "a session started while watching never turned up")


class TestWhatCountsAsWatched(Scratch):

    def test_a_followed_log_counts_before_it_has_said_anything(self):
        path = self.log()
        self.append(path, cmd_record("hello", self.now))
        watcher = self.watcher()
        # Adopted and read from the beginning, so its offset is 0 until the
        # first poll moves it.  It is being watched either way.
        watcher._scan()
        self.assertEqual(watcher.watched(), 1,
                         "a session was not counted until it produced output")

    def test_a_log_ruled_out_by_project_does_not_count(self):
        # Ruled out on the log's own word.  A log whose project was only
        # *guessed* from the directory name is followed anyway and decided
        # again in poll, so it would be counted here — correctly.
        self.append(self.log("s1", "-home-you-api"),
                    said_where("/home/you/api", self.now),
                    cmd_record("mine", self.now))
        self.append(self.log("s2", "-home-you-other"),
                    said_where("/home/you/other", self.now),
                    cmd_record("theirs", self.now))
        watcher = self.watcher(project="api")
        watcher.poll()
        self.assertEqual(watcher.watched(), 1,
                         "a log excluded by --project was counted as watched")


# Equivalent mutants, left alive on purpose:
#
#   `len(parts) >= 5` in session_id_for.  A codex rollout filename ends in a
#   uuid, which is five dash-separated parts, and the branch rejoins the last
#   five of them.  At exactly five, that rejoin reproduces the string it was
#   given, so `>` and `>=` return the same session id for every name there is.
#
#   `cut < 0` in the reader, where `cut` is the index of the last newline in
#   the buffer.  Zero means the buffer begins with a newline and holds no
#   other, so the only line the branch would emit is an empty one — and empty
#   lines are skipped four lines further down.  Flipped to `<= 0`, that newline
#   stays in the buffer and is skipped on the next pass instead.


if __name__ == "__main__":
    unittest.main()
