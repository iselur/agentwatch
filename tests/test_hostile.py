"""Input written by something that does not care whether we cope.

A session log is a file on disk that some other program writes, in a format it
changes without telling us, containing text an agent chose.  None of that is
trusted here.  The rule these tests hold to is simple: agentwatch may show
nothing, but it may never crash, never hang, and never print something that
takes over the terminal it is printing to.
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

from agentwatch.events import KINDS, Tracker, events_from_line
from agentwatch.follow import Watcher
from agentwatch.render import (
    _COLORS, _DIM, _RESET, MARKS, format_event, format_json,
)

NOW = datetime.now(timezone.utc)


def parse(source, record):
    return events_from_line(json.dumps(record), Tracker("s", source))


class TestRecordsThatAreNotWhatTheySay(unittest.TestCase):
    """Every field replaced by the wrong type, one at a time and all at once."""

    WRONG = (None, 0, 1.5, True, [], {}, "", "  ", [None], {"a": [1]},
             {"nested": {"deep": [{"deeper": 1}]}})

    def test_a_line_that_is_not_json_yields_nothing(self):
        for raw in ("", "   ", "not json", "{", "}", "[]", "null", "3", '"a"',
                    "\x00\x01", "{'single': 'quotes'}", "{\"a\": }"):
            self.assertEqual(events_from_line(raw, Tracker("s", "claude")), [], raw)

    def test_a_json_array_at_the_top_level_yields_nothing(self):
        self.assertEqual(events_from_line("[1,2,3]", Tracker("s", "codex")), [])

    def test_every_claude_field_can_be_the_wrong_type(self):
        for wrong in self.WRONG:
            for record in (
                {"type": wrong, "message": {"content": [{"type": "tool_use"}]}},
                {"type": "assistant", "message": wrong},
                {"type": "assistant", "message": {"content": wrong}},
                {"type": "assistant", "message": {"content": [wrong]}},
                {"type": "assistant", "message": {"content": [
                    {"type": "tool_use", "name": wrong, "input": wrong}]}},
                {"type": "assistant", "timestamp": wrong, "message": {"content": [
                    {"type": "tool_use", "name": "Bash", "input": {"command": wrong}}]}},
            ):
                events = parse("claude", record)
                self.assertIsInstance(events, list)
                for event in events:
                    self.assertIn(event["kind"], KINDS)
                    self.assertIsInstance(event["text"], str)

    def test_every_codex_field_can_be_the_wrong_type(self):
        for wrong in self.WRONG:
            for record in (
                {"type": wrong, "payload": {"type": "custom_tool_call"}},
                {"type": "response_item", "payload": wrong},
                {"type": "response_item", "payload": {"type": wrong}},
                {"type": "response_item", "payload": {
                    "type": "custom_tool_call", "name": "exec", "input": wrong}},
                {"type": "response_item", "payload": {
                    "type": "custom_tool_call_output", "output": wrong}},
                {"type": "response_item", "payload": {
                    "type": "function_call", "name": "exec_command",
                    "arguments": wrong}},
                {"type": "event_msg", "payload": {
                    "type": "patch_apply_end", "success": wrong, "changes": wrong}},
                {"type": "session_meta", "payload": {"cwd": wrong}},
            ):
                events = parse("codex", record)
                self.assertIsInstance(events, list)
                for event in events:
                    self.assertIn(event["kind"], KINDS)
                    self.assertIsInstance(event["text"], str)

    def test_arguments_that_are_json_encoded_nonsense(self):
        for raw in ('{"cmd":', "not json at all", "[1,2,3]", '"a string"', "null"):
            events = parse("codex", {
                "type": "response_item",
                "payload": {"type": "function_call", "name": "exec_command",
                            "call_id": "c", "arguments": raw}})
            self.assertIsInstance(events, list)

    def test_deeply_nested_content_does_not_recurse_forever(self):
        deep = {"type": "tool_use", "name": "Bash", "input": {"command": "ok"}}
        for _ in range(400):
            deep = {"wrapped": deep}
        events = parse("claude", {"type": "assistant",
                                  "message": {"content": [deep]}})
        self.assertIsInstance(events, list)


class TestTextThatFightsBack(unittest.TestCase):
    """What an agent puts in a command string is not trusted output."""

    NASTY = (
        "\033[2J\033[H",                     # clear the screen
        "\033]0;pwned\007",                  # retitle the window
        "\033[31mred forever",               # leave the terminal coloured
        "a\rb",                              # carriage return over the line
        "a\x08\x08b",                        # backspace over what was printed
        "\x07\x07\x07",                      # bell
        "a‮b",                          # right-to-left override
        "\x00\x01\x02\x1f",                  # raw control bytes
        "line break",                   # unicode line separator
    )

    def test_control_sequences_never_reach_the_terminal(self):
        for nasty in self.NASTY:
            line = format_event(
                {"at": NOW, "kind": "cmd", "text": nasty, "project": "p"},
                MARKS, color=False, width=100)
            for char in line:
                self.assertFalse(
                    ord(char) < 32 or ord(char) == 127,
                    "control character {!r} survived from {!r}".format(char, nasty))
            self.assertNotIn("‮", line)
            self.assertNotIn(" ", line)

    def test_a_project_name_cannot_inject_either(self):
        line = format_event(
            {"at": NOW, "kind": "cmd", "text": "ok", "project": "\033[2Jp"},
            MARKS, color=False, width=100)
        self.assertNotIn("\033", line)

    def test_colour_output_contains_only_the_codes_we_chose(self):
        # A blanket "no escapes" check cannot be made here — the colouring is
        # escapes.  The property that matters is that every one of them is one
        # we emitted, so nothing from the log can start a sequence of its own.
        ours = set(_COLORS.values()) | {_DIM, _RESET}
        for nasty in self.NASTY:
            line = format_event(
                {"at": NOW, "kind": "cmd", "text": nasty, "project": nasty},
                MARKS, color=True, width=100)
            for piece in line.split("\033")[1:]:
                self.assertTrue(
                    any(("\033" + piece).startswith(code) for code in ours),
                    "unrecognised escape {!r} from {!r}".format(piece, nasty))

    def test_json_output_escapes_control_characters(self):
        for nasty in self.NASTY:
            row = format_json({"at": NOW, "kind": "cmd", "text": nasty,
                               "project": "p", "session": "s", "source": "claude"})
            self.assertEqual(len(row.splitlines()), 1, repr(nasty))
            json.loads(row)      # still valid JSON

    def test_a_very_long_command_is_cut_to_the_width(self):
        line = format_event(
            {"at": NOW, "kind": "cmd", "text": "x" * 1000000, "project": "p"},
            MARKS, width=100)
        self.assertLessEqual(len(line), 100)


class TestPathsThatAreNotPaths(unittest.TestCase):

    def test_traversal_in_a_patched_path_is_resolved_not_repeated(self):
        # Normalising is right, hiding is not: if an agent wrote through a
        # ``..`` the person watching needs to see where it landed, which is the
        # resolved path and not the ``..`` it was written as.
        tracker = Tracker("s", "codex", "/home/you/api")
        events = events_from_line(json.dumps({
            "type": "response_item",
            "payload": {"type": "function_call", "name": "apply_patch",
                        "call_id": "c",
                        "arguments": json.dumps(
                            {"input": "*** Update File: ../../etc/passwd"})}}),
            tracker)
        self.assertEqual([e["text"] for e in events], ["/home/etc/passwd"])

    def test_an_absolute_path_is_not_joined_to_the_project(self):
        tracker = Tracker("s", "codex", "/home/you/api")
        events = events_from_line(json.dumps({
            "type": "response_item",
            "payload": {"type": "function_call", "name": "apply_patch",
                        "call_id": "c",
                        "arguments": json.dumps(
                            {"input": "*** Add File: /tmp/x.py"})}}), tracker)
        self.assertEqual([e["text"] for e in events], ["/tmp/x.py"])

    def test_a_path_of_only_punctuation_does_not_become_an_event(self):
        for path in ("", "   ", '"', "'", "\\", ";"):
            events = events_from_line(json.dumps({
                "type": "response_item",
                "payload": {"type": "function_call", "name": "apply_patch",
                            "call_id": "c",
                            "arguments": json.dumps(
                                {"input": "*** Add File: " + path})}}),
                Tracker("s", "codex"))
            self.assertEqual(events, [], repr(path))


class TestMemoryStaysBounded(unittest.TestCase):
    """A watcher left running for a week must not grow without limit."""

    def test_the_call_label_table_stops_growing(self):
        tracker = Tracker("s", "codex")
        for i in range(20000):
            tracker.remember("call_{}".format(i), "cmd {}".format(i))
        self.assertLessEqual(len(tracker._labels), tracker._max_labels + 1)

    def test_the_pending_envelope_table_stops_growing(self):
        tracker = Tracker("s", "codex")
        at = NOW
        for i in range(20000):
            tracker.envelope_sent("/f/{}".format(i), at + timedelta(seconds=i))
        self.assertLessEqual(len(tracker._pending), tracker._max_labels + 1)

    def test_one_envelope_covers_one_confirmation_and_no_more(self):
        # The suppression is paired, not timed: an envelope is spent by the
        # result that confirms it, and the next report of that file is a
        # second edit however soon it lands.
        tracker = Tracker("s", "codex")
        tracker.envelope_sent("/f/a.py", NOW)
        self.assertTrue(
            tracker.confirms_envelope("/f/a.py", NOW + timedelta(seconds=1)))
        self.assertFalse(
            tracker.confirms_envelope("/f/a.py", NOW + timedelta(seconds=2)))

    def test_an_envelope_nobody_confirmed_expires(self):
        # It must not sit waiting to swallow the next real edit to that file.
        tracker = Tracker("s", "codex")
        tracker.envelope_sent("/f/a.py", NOW)
        self.assertFalse(
            tracker.confirms_envelope("/f/a.py", NOW + timedelta(minutes=5)))

    def test_a_write_with_no_time_is_never_swallowed(self):
        tracker = Tracker("s", "codex")
        tracker.envelope_sent("/f/a.py", None)
        self.assertFalse(tracker.confirms_envelope("/f/a.py", None))
        self.assertFalse(tracker.confirms_envelope("/f/a.py", NOW))


class TestFilesThatMisbehave(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="agentwatch-hostile-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.folder = os.path.join(self.home, ".claude", "projects", "-home-you-api")
        os.makedirs(self.folder)

    def write(self, data, name="s1.jsonl"):
        path = os.path.join(self.folder, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def watcher(self):
        return Watcher(home=self.home, since=NOW - timedelta(minutes=10))

    def test_a_log_with_no_newline_at_all_is_held_not_mangled(self):
        self.write(b'{"type": "assistant"')
        self.assertEqual(self.watcher().poll(), [])

    def test_a_log_of_only_newlines_yields_nothing(self):
        self.write(b"\n" * 10000)
        self.assertEqual(self.watcher().poll(), [])

    def test_a_binary_log_yields_nothing_and_does_not_raise(self):
        self.write(bytes(range(256)) * 500)
        self.assertEqual(self.watcher().poll(), [])

    def test_a_null_byte_inside_a_valid_record_does_not_stop_the_rest(self):
        good = json.dumps({
            "type": "assistant", "timestamp": NOW.isoformat(),
            "message": {"content": [{"type": "tool_use", "id": "t", "name": "Bash",
                                     "input": {"command": "after"}}]}}).encode()
        self.write(b'{"type": "assis\x00tant"}\n' + good + b"\n")
        self.assertEqual([e["text"] for e in self.watcher().poll()], ["after"])

    def test_a_timestamp_from_the_future_is_still_shown(self):
        record = {"type": "assistant",
                  "timestamp": (NOW + timedelta(days=365)).isoformat(),
                  "message": {"content": [{"type": "tool_use", "id": "t",
                                           "name": "Bash",
                                           "input": {"command": "tomorrow"}}]}}
        self.write((json.dumps(record) + "\n").encode())
        self.assertEqual([e["text"] for e in self.watcher().poll()], ["tomorrow"])

    def test_many_logs_at_once_do_not_break_anything(self):
        for i in range(200):
            record = {"type": "assistant", "timestamp": NOW.isoformat(),
                      "message": {"content": [{"type": "tool_use", "id": "t",
                                               "name": "Bash",
                                               "input": {"command": "c{}".format(i)}}]}}
            self.write((json.dumps(record) + "\n").encode(), "s{}.jsonl".format(i))
        events = self.watcher().poll()
        self.assertEqual(len(events), 200)


if __name__ == "__main__":
    unittest.main()
