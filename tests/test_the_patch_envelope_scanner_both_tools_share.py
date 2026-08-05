"""The apply_patch scanner, pinned to the cases agentwatch pins it to.

`patched_files` used to exist twice — once here and once in
`agentlog/parser.py` — because the two tools read the same Codex log for
different purposes and the family does not import across packages.  Two copies
of one scanner was a fact of the layout; two copies that disagreed was a bug,
and it was one.

It now lives in `transcript.py`, which is still copied into both packages but
copied whole and pinned byte-identical, so the two cannot say different things
about one log line.  These cases stay where they are: what that file pins is
that the two scanners are the same scanner, and what this file pins is that the
scanner is right.  Sameness is not correctness, and the copy that made this
worth writing was consistent for a year.

The two drifted over one case.  Current Codex builds send the shell a snippet
of JavaScript, so a patch envelope arrives as the *contents of a string
literal* on one physical line:

    const patch = "*** Update File: src/app.py\\n+hello\\n*** End Patch";

The original scanner split the text on newlines and asked whether a line
*started* with a marker.  Against that input it returned nothing at all — the
file was edited, the digest said no files were touched, and nothing failed.
agentwatch was taught to match the marker wherever it sits; agentlog was not,
and went on under-reporting.  One family, one log, two answers, no test.

So this file is the shared contract, and the same cases are asserted in
agentlog's copy.  A change to the scanner that is not made to both breaks one of
these two files.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agentwatch.transcript import patched_files  # noqa: E402

# The envelope as a JavaScript string literal: one line, newlines still
# escaped, the marker nowhere near the start of it.  This is the case that was
# silently returning nothing.
EMBEDDED_IN_JS = (
    'const patch = "*** Update File: src/app.py\\n+hello\\n*** End Patch";')


class TestThePatchEnvelopeScannerBothToolsShare(unittest.TestCase):

    def test_an_envelope_inside_a_javascript_string_is_still_found(self):
        # The regression.  Before the fix this was [].
        self.assertEqual(patched_files(EMBEDDED_IN_JS), ["src/app.py"])

    def test_a_plain_envelope_on_its_own_lines(self):
        patch = ("*** Begin Patch\n*** Update File: src/app.py\n"
                 "@@\n-a\n+b\n*** End Patch")
        self.assertEqual(patched_files(patch), ["src/app.py"])

    def test_add_and_delete_are_writes_too(self):
        patch = ("*** Begin Patch\n*** Add File: a.py\n"
                 "*** Delete File: b.py\n*** End Patch")
        self.assertEqual(patched_files(patch), ["a.py", "b.py"])

    def test_a_trailing_quote_from_the_surrounding_literal_is_not_the_path(self):
        self.assertEqual(patched_files("*** Update File: app.py'"), ["app.py"])

    def test_prose_about_the_marker_is_not_a_patch(self):
        # No colon, so it is somebody writing about the format rather than
        # using it.  A scanner that matches this reports edits to files that
        # were never touched.
        self.assertEqual(patched_files("+ # see *** Update File docs"), [])

    def test_a_marker_with_no_path_names_nothing(self):
        self.assertEqual(patched_files("*** Update File:   "), [])

    def test_a_command_that_is_not_a_patch(self):
        self.assertEqual(patched_files("git status"), [])

    def test_nothing_at_all(self):
        self.assertEqual(patched_files(""), [])


if __name__ == "__main__":
    unittest.main()
