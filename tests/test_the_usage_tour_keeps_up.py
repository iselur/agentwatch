"""Not "is the flag mentioned" — "is the flag explained".

`test_the_readme_documents_the_flags` already checks both directions between
the parser and the README, and it counts a flag as documented when any of its
spellings appears anywhere in the file.  That is the right bar for "can I find
this at all" and too low for "does the README tell me what it does": a flag
dropped into one copy-pasteable example and explained nowhere passes it, and
somebody reading the file learns the flag exists and not what it is for.

So the bar here is that every flag is *explained*, in one of the two ways this
README explains things:

  * a line in the usage tour, whose `#` comment says what the flag does
  * a paragraph of prose, for the two that are not part of the tour

`--stale` and `--home` are the two, and they are named with the reason.  Naming
them is the point — the alternative is subtracting whatever happens to be
missing, which is the same as not checking.  The docstring of the older file
records how this decayed the last time: agentwatch grew `--interval` and
`--no-color` and the README mentioned neither.  A flag added to the parser and
not to the tour is a decision, and this makes someone make it on purpose.
"""

from __future__ import annotations

import os
import re
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from agentwatch.cli import build_parser  # noqa: E402

README = os.path.join(_ROOT, "README.md")

_FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]*)")
_FENCE = re.compile(r"```[a-z]*\n.*?```", re.S)

# Flags the usage tour leaves out on purpose, each with the section that
# explains it instead.  The section matters: `--home` is also named in the exit
# codes table, and a whole-file search would go on passing after the paragraph
# that says what the flag *does* was deleted.  That is not hypothetical — it is
# what the first draft of this file did, and a neuter caught it.
_NOT_IN_THE_TOUR = {
    "--home": "## What it reads",
    "--stale": "## What it reads",
}

# argparse supplies it and no README needs to say so.
_FREE = ("--help",)


def readme():
    with open(README, encoding="utf-8") as handle:
        return handle.read()


def section(text, heading):
    """One `##` section, from its heading to the next one at any level."""
    start = text.find(heading)
    if start < 0:
        return ""
    end = re.search(r"\n#{1,3} ", text[start + len(heading):])
    return text[start:start + len(heading) + end.start()] if end else text[start:]


def usage_tour(text):
    """The fenced block under `## Usage` — one flag per line with a comment.

    The fence has to open its own line.  A block commented out with `<!--`
    still contains every line it always did and renders as nothing at all, so
    finding "```" anywhere would keep reading a tour no reader can see.
    """
    body = section(text, "## Usage")
    found = re.search(r"^```[a-z]*\n(.*?)^```", body, re.S | re.M)
    return found.group(1) if found else ""


def explained_in_the_tour(text):
    """{flag: what its line says it does}, for lines that say anything."""
    explained = {}
    for line in usage_tour(text).splitlines():
        command, _, comment = line.partition("#")
        if not comment.strip():
            continue
        for flag in _FLAG.findall(command):
            explained[flag] = comment.strip()
    return explained


def prose(text):
    """The README with every fenced block taken out.

    A flag inside a fence is a flag being used, which is not the same as a
    flag being explained — the whole distinction this file exists for.
    """
    return _FENCE.sub("\n", text)


def names(text, flag):
    """Is the flag written here as itself, and not as part of a longer one?"""
    return re.search(r"(?<![\w-])" + re.escape(flag) + r"(?![\w-])",
                     text) is not None


def parser_flags():
    """Every long flag the parser accepts."""
    return sorted({flag for action in build_parser()._actions
                   for flag in action.option_strings
                   if flag.startswith("--") and flag not in _FREE})


class TestTheUsageTourKeepsUp(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.text = readme()
        cls.flags = parser_flags()
        cls.tour = explained_in_the_tour(cls.text)

    def test_the_parser_still_has_flags(self):
        # Every assertion below runs over this list; an empty one passes them all.
        self.assertGreaterEqual(len(self.flags), 8,
                                "the parser walk found nothing to check")

    def test_the_tour_still_explains_things(self):
        # And an empty tour makes "explained in the tour" trivially false for
        # everything, which the next test would then blame on the prose.
        self.assertGreaterEqual(
            len(self.tour), 5,
            "no commented flag lines found under ## Usage — either the block "
            "moved or its comments went away, and both make this file lie")

    def test_every_flag_is_explained_somewhere(self):
        # Reported together: adding three flags in one change is one edit to
        # the README, and hearing about them one run at a time is three.
        unexplained = []
        for flag in self.flags:
            if flag in self.tour:
                continue
            where = _NOT_IN_THE_TOUR.get(flag)
            if where and names(prose(section(self.text, where)), flag):
                continue
            unexplained.append(flag)
        self.assertFalse(
            unexplained,
            "agentwatch accepts {} and README.md never says what {} does — "
            "being able to find a flag is not the same as being told what it "
            "is for".format(", ".join(unexplained),
                            "they" if len(unexplained) > 1 else "it"))

    def test_the_flags_left_out_of_the_tour_are_the_ones_named(self):
        # The tour is a tour, not the whole reference, and that is fine while
        # somebody decides what stays out of it.  A flag that falls out by
        # nobody noticing is the failure this catches: it was already how
        # --interval and --no-color went missing from this same README.
        missing = {flag for flag in self.flags if flag not in self.tour}
        self.assertEqual(
            missing, set(_NOT_IN_THE_TOUR),
            "the usage tour under ## Usage no longer covers exactly the flags "
            "it is meant to; if a flag belongs out of it, put it in "
            "_NOT_IN_THE_TOUR with the reason")

    def test_the_ones_left_out_really_are_explained_in_prose(self):
        # The exclusion above says "explained in that section".  This is that
        # section — not the whole file, which would keep passing on a passing
        # mention somewhere else entirely.
        for flag, where in sorted(_NOT_IN_THE_TOUR.items()):
            body = prose(section(self.text, where))
            self.assertTrue(
                body.strip(),
                "{} says {} explains {}, and there is no such section"
                .format(README, where, flag))
            self.assertTrue(
                names(body, flag),
                "{} is kept out of the usage tour because `{}` explains it, "
                "and that section does not mention it".format(flag, where))

    def test_the_comments_say_something(self):
        # A line commented `# flag` explains nothing and would satisfy every
        # check above.  The shortest real one in the file is four words.
        for flag, comment in sorted(self.tour.items()):
            self.assertGreater(
                len(comment.split()), 1,
                "the usage tour's line for {} is commented `{}`, which tells a "
                "reader nothing the flag name did not".format(flag, comment))


if __name__ == "__main__":
    unittest.main()
