"""`--json` is a machine contract, and the README states it in three places.

    One object per line, sorted keys, stable field set: `at`, `kind`,
    `project`, `session`, `source`, `text`. Notes ("watching 3 session logs")
    go to stderr, so stdout stays machine-clean.

The example line above that sentence is already compared with the code
(test_the_readme_shows_what_it_prints), so a renamed key fails there.  What
that leaves is everything the sentence claims *around* the keys, none of
which anything ran:

  * **one object per line** — the thing that makes `while read l` work at all
  * **sorted keys** — a golden file or a `diff` of two runs depends on it, and
    it is invisible to any test that compares parsed objects
  * **the field list in the prose** — a third copy of the field set, next to
    the example and the code, and the one most likely to be left behind
  * **stdout stays machine-clean** — the claim that has to hold on the bad
    day, not the good one.  A run with nothing to report is trivially clean;
    a run that has something to say about an unreadable log is the case worth
    checking, because that note is exactly what would land in the stream.

So this runs the real command against a real home and reads its stdout the
way a script would.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

README = os.path.join(_ROOT, "README.md")

# "stable field set: `at`, `kind`, `project`, `session`, `source`, `text`."
# The list is long enough to wrap, so the separator is sometimes a newline.
_CLAIM = re.compile(r"stable field set: ((?:`[a-z_]+`[,\s]*)+)")
_FIELD = re.compile(r"`([a-z_]+)`")


def readme() -> str:
    with open(README, encoding="utf-8") as handle:
        return handle.read()


def claimed_fields(text):
    """The field set the README's prose promises."""
    found = _CLAIM.search(text)
    return _FIELD.findall(found.group(1)) if found else []


def _records(sid, when=None, command="pytest -x"):
    stamp = (when or datetime.now(timezone.utc)).isoformat()
    return "\n".join([
        json.dumps({"type": "user", "timestamp": stamp, "sessionId": sid,
                    "cwd": "/tmp/api-server",
                    "message": {"role": "user", "content": "run the tests"}}),
        json.dumps({"type": "assistant", "timestamp": stamp, "sessionId": sid,
                    "message": {"role": "assistant", "id": "m-" + sid,
                                "content": [{"type": "tool_use",
                                             "id": "t-" + sid, "name": "Bash",
                                             "input": {"command": command}}]}}),
    ]) + "\n"


class Case(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="aw-jsonstream-")
        self.addCleanup(self._cleanup)
        self.proj = os.path.join(self.home, ".claude", "projects", "api-server")
        os.makedirs(self.proj)
        self.write("aaa.jsonl", _records("s1"))

    def _cleanup(self):
        for dirpath, _dirs, names in os.walk(self.home):
            for name in names:
                try:
                    os.chmod(os.path.join(dirpath, name), 0o644)
                except OSError:
                    pass
        shutil.rmtree(self.home, ignore_errors=True)

    def write(self, name, text, mode=0o644):
        path = os.path.join(self.proj, name)
        with open(path, "w") as handle:
            handle.write(text)
        os.chmod(path, mode)
        return path

    def run_json(self):
        # `--since` has no default: without one there is nothing to replay and
        # every assertion below would pass against an empty stream.  The README
        # spells the same shape one line above the sentence being checked.
        return subprocess.run(
            [sys.executable, "-m", "agentwatch", "--since", "1h", "--once",
             "--json", "--home", self.home],
            capture_output=True, text=True,
            env=dict(os.environ, PYTHONPATH=_ROOT))


class TestTheJSONStreamIsWhatItClaims(Case):

    def test_there_is_something_to_read(self):
        # Every assertion below is vacuously true on an empty stream, and an
        # empty stream is also what a broken fixture produces.
        proc = self.run_json()
        self.assertTrue(proc.stdout.strip(),
                        "the fixture produced no events at all:\n"
                        + proc.stderr)

    def test_it_is_one_object_per_line(self):
        proc = self.run_json()
        for number, line in enumerate(proc.stdout.splitlines(), 1):
            try:
                parsed = json.loads(line)
            except ValueError as e:
                self.fail("line {} of --json is not an object on its own "
                          "line: {} -- {!r}".format(number, e, line))
            self.assertIsInstance(parsed, dict,
                                  "line {} of --json is not an object"
                                  .format(number))

    def test_the_keys_are_sorted(self):
        # Invisible to any test that compares parsed objects, and the reason
        # two runs of the same window diff cleanly.
        proc = self.run_json()
        for line in proc.stdout.splitlines():
            keys = list(json.loads(line).keys())
            self.assertEqual(keys, sorted(keys),
                             "--json emitted keys out of order: " + line)

    def test_the_field_list_in_the_prose_is_the_field_set_emitted(self):
        fields = claimed_fields(readme())
        self.assertGreaterEqual(len(fields), 4,
                                "the README no longer lists the JSON fields")
        proc = self.run_json()
        for line in proc.stdout.splitlines():
            self.assertEqual(sorted(json.loads(line)), sorted(fields),
                             "--json emits a different field set from the one "
                             "README.md promises: " + line)

    def test_a_line_separator_in_the_text_does_not_split_the_object(self):
        # U+2028 and U+2029 are newlines to a reader but not control characters
        # to json.dumps, so they pass through unescaped and turn one object into
        # two lines.  render._one_line exists to escape them and nothing ran it.
        # The text comes from whatever the agent was told to run, so this is
        # reachable by typing it — no hostile input needed.
        # json.dumps escapes it on the way into the fixture, so the file
        # stays one line and the text is a real U+2028 when it is read
        # back.  The escape is spelled out rather than typed literally:
        # a raw U+2028 in this file would be invisible to anyone reading
        # it, which is the whole problem being tested.
        self.write("ccc.jsonl", _records(
            "s3", command="echo one\u2028two\u2029three"))
        proc = self.run_json()
        lines = proc.stdout.splitlines()
        texts = []
        for number, line in enumerate(lines, 1):
            try:
                texts.append(json.loads(line).get("text", ""))
            except ValueError as e:
                self.fail("a U+2028 in the text split object {} across lines: "
                          "{} -- {!r}".format(number, e, line))
        self.assertTrue(
            any("one" in text and "three" in text for text in texts),
            "the text either went missing or was cut at the separator: "
            + repr(texts))

    def test_a_note_about_an_unreadable_log_does_not_land_in_the_stream(self):
        # The good day is trivially clean.  This is the bad day: agentwatch
        # has something to say, and a script is reading stdout.
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("running as root — chmod does not deny us anything")
        self.write("bbb.jsonl", _records("s2"), mode=0)
        proc = self.run_json()
        self.assertIn("could not", proc.stderr.lower(),
                      "agentwatch said nothing about a log it could not read:\n"
                      + proc.stderr)
        for line in proc.stdout.splitlines():
            json.loads(line)  # fails loudly if the note landed here
        self.assertTrue(proc.stdout.strip(),
                        "the readable log's events went missing too")


if __name__ == "__main__":
    unittest.main()
