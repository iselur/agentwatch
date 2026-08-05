"""The cases a mutant campaign against `transcript.py` found nothing pinning.

`transcript.py` is the reading of the two session formats, copied into
agentwatch and agentlog and pinned byte-identical.  Twenty-six mutants were applied to
it and both tools' suites run against each; these are the ones that survived,
which is to say the behaviour both tools relied on and neither checked.

The same file sits in agentlog, for the same reason `shell.py`'s tests do:
either package's suite, run on its own, has to be able to say its copy is
right.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agentwatch.transcript import (  # noqa: E402
    is_work_call,
    parse_time,
    script_commands,
    script_workdir,
)


class TestAStamp(unittest.TestCase):

    def test_the_two_ways_of_writing_utc_are_one_moment(self):
        # Every stamp in both formats ends in `Z`, and `parse_time` rewrites it
        # to `+00:00` before parsing.  That rewrite does nothing on Python 3.11
        # and later, which learned to read `Z` itself -- and is the whole of the
        # function on 3.9 and 3.10, which raise ValueError on it and would hand
        # back None for every record in the file.  The family supports 3.9.
        #
        # So this assertion cannot fail on a modern interpreter, and is here for
        # the older ones the package promises.  Nothing else in either suite
        # says why the rewrite is there.
        self.assertEqual(parse_time("2026-01-01T09:30:00Z"),
                         parse_time("2026-01-01T09:30:00+00:00"))


class TestWhatCountsAsWork(unittest.TestCase):

    def test_a_call_that_is_not_one_of_the_two_is_not_work(self):
        # Codex writes function calls for things that are not work, and a
        # reader that took every one of them would report a session doing far
        # more than it did.
        for name in ("shell", "update_plan", "web_search", "", None, 0):
            with self.subTest(name):
                self.assertFalse(is_work_call(name))

    def test_the_two_that_are(self):
        self.assertTrue(is_work_call("exec_command"))
        self.assertTrue(is_work_call("apply_patch"))


class TestTheCommandsInASnippet(unittest.TestCase):

    def test_an_empty_command_is_not_a_command(self):
        # A blank line in the stream, and worse in agentwatch, where the
        # command a failure is named after is the first one remembered: an
        # empty string remembered here means a failure reported as "".
        self.assertEqual(script_commands('exec({cmd: "", workdir: "/x"})'), [])

    def test_a_command_beside_an_empty_one_still_arrives(self):
        self.assertEqual(
            script_commands('exec({cmd: ""}); exec({cmd: "ls -l"})'),
            ["ls -l"])


class TestTheWorkdirInASnippet(unittest.TestCase):

    def test_the_escapes_are_undone(self):
        # The snippet is JavaScript source, so a backslash in the path arrives
        # doubled.  Left as it stands it is a directory name that does not
        # exist, printed in the project column of every line of the session.
        self.assertEqual(
            script_workdir(r'exec({cmd: "dir", workdir: "C:\\Users\\me"})'),
            r"C:\Users\me")

    def test_a_snippet_that_did_not_say(self):
        self.assertEqual(script_workdir('exec({cmd: "ls"})'), "")


if __name__ == "__main__":
    unittest.main()
