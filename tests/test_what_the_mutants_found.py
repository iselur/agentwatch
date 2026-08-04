"""Lines the suite ran but did not actually pin down.

From the mutation sweep over `agentwatch/follow.py`.  Only one survivor was
real: the cap on how much of a session tree gets walked.

`_walk_jsonl` stops when it has collected more than `_MAX_WALK_ENTRIES`, and
the check runs once per directory, after that directory's files are already in
the list.  So the cap is a "stop looking soon" signal, not an exact ceiling —
and the boundary matters, because it decides whether the *next* directory is
looked at at all.  Move it by one and a tree sitting exactly on the limit stops
being followed, silently: agentwatch prints nothing for those sessions and
looks like an agent that has gone quiet.

(The other survivor, `len(parts) >= 5` in the codex session-id split, is
equivalent — `"-".join(parts[-5:])` is the whole string when there are exactly
five parts, so both operators produce the same id.  Recorded, not tested.)
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from agentwatch import follow


class TestTheWalkCapBoundary(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="agentwatch_cap_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        old = follow._MAX_WALK_ENTRIES
        follow._MAX_WALK_ENTRIES = 3
        self.addCleanup(setattr, follow, "_MAX_WALK_ENTRIES", old)

    def _files(self, subdir, n):
        """`n` session logs in a directory, nested so the walk order is fixed.

        Siblings come back in whatever order the filesystem hands them over, and
        a cap that is checked once per directory gives a different answer for
        each order — so the second directory is a *child* of the first.  os.walk
        is top-down, which is the one ordering guarantee it makes.
        """
        d = os.path.join(self.root, subdir)
        os.makedirs(d, exist_ok=True)
        for i in range(n):
            open(os.path.join(d, f"s{i}.jsonl"), "w").close()

    def test_a_tree_sitting_exactly_on_the_cap_keeps_being_walked(self):
        # Three files fill the cap exactly; the fourth is one level down.
        # Stopping here would drop a live session on the floor.
        self._files("a", 3)
        self._files("a/b", 1)
        self.assertEqual(len(follow._walk_jsonl(self.root)), 4)

    def test_past_the_cap_it_stops(self):
        self._files("a", 4)
        self._files("a/b", 1)
        # The cap is per-directory, so the directory that crosses it is still
        # finished before the check runs: four, and then nothing deeper.
        self.assertEqual(len(follow._walk_jsonl(self.root)), 4)

    def test_a_missing_root_is_empty_not_an_error(self):
        self.assertEqual(follow._walk_jsonl(os.path.join(self.root, "nope")), [])


class TestTheStalenessBoundary(unittest.TestCase):
    """A log exactly `stale_s` old is still worth following.

    `--stale 900` reads as "up to fifteen minutes of silence is fine", and a
    session that goes quiet for exactly that long is the one this setting was
    written for.  Excluding it makes the flag mean one second less than it
    says, on the boundary people actually land on because they typed a round
    number.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="agentwatch_stale_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.path = os.path.join(self.root, "s.jsonl")
        open(self.path, "w").close()
        os.utime(self.path, (1000.0, 1000.0))

    def watcher(self, now):
        w = follow.Watcher.__new__(follow.Watcher)
        w.since = None
        w.stale_s = 100.0
        w._clock = lambda: now
        return w

    def test_exactly_stale_is_still_fresh(self):
        self.assertTrue(self.watcher(1100.0)._fresh(self.path))

    def test_one_second_past_is_not(self):
        self.assertFalse(self.watcher(1101.0)._fresh(self.path))

    def test_a_file_that_cannot_be_statted_is_not_fresh(self):
        self.assertFalse(self.watcher(1000.0)._fresh(
            os.path.join(self.root, "gone.jsonl")))


if __name__ == "__main__":
    unittest.main()
