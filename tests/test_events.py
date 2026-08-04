"""What one line of a session log turns into.

The log formats are somebody else's, and they change without telling us — so
every record shape tested here was copied from a real log on disk, reduced to
its smallest recognisable form.
"""

import json
import os
import sys
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agentwatch.events import KINDS, Tracker, events_from_line, parse_time


def claude(**record):
    return Tracker("sess", "claude"), json.dumps(record)


def _events(source, record, tracker=None):
    tracker = tracker or Tracker("sess", source)
    return events_from_line(json.dumps(record), tracker), tracker


class TestParseTime(unittest.TestCase):

    def test_z_suffix_is_utc(self):
        at = parse_time("2026-08-04T09:41:07.123Z")
        self.assertIsNotNone(at)
        self.assertEqual(at.utcoffset().total_seconds(), 0)

    def test_offset_is_kept(self):
        self.assertEqual(
            parse_time("2026-08-04T09:41:07+02:00").utcoffset().total_seconds(), 7200)

    def test_nonsense_is_none_not_an_exception(self):
        for raw in ("", "yesterday", None, 17, "2026-13-45T99:99:99Z"):
            self.assertIsNone(parse_time(raw))


class TestClaude(unittest.TestCase):

    def test_a_person_typing_is_a_turn(self):
        events, _ = _events("claude", {
            "type": "user", "timestamp": "2026-08-04T09:41:00Z",
            "cwd": "/home/you/api",
            "message": {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        })
        self.assertEqual([e["kind"] for e in events], ["turn"])

    def test_a_turn_never_carries_the_message_text(self):
        events, _ = _events("claude", {
            "type": "user", "timestamp": "2026-08-04T09:41:00Z",
            "message": {"role": "user",
                        "content": [{"type": "text", "text": "my secret plan"}]},
        })
        self.assertEqual(events[0]["text"], "")
        self.assertNotIn("secret", json.dumps(events, default=str))

    def test_a_tool_result_coming_back_is_not_a_turn(self):
        # The agent feeding itself is not a person typing; counting it as one
        # makes the turn mark meaningless.
        events, _ = _events("claude", {
            "type": "user", "timestamp": "2026-08-04T09:41:00Z",
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
        })
        self.assertEqual(events, [])

    def test_the_cwd_becomes_the_project(self):
        _events_, tracker = _events("claude", {
            "type": "user", "timestamp": "2026-08-04T09:41:00Z",
            "cwd": "/home/you/api-server",
            "message": {"role": "user", "content": []},
        })
        self.assertEqual(tracker.project, "/home/you/api-server")

    def test_bash_is_a_command(self):
        events, _ = _events("claude", {
            "type": "assistant", "timestamp": "2026-08-04T09:41:07Z",
            "message": {"content": [
                {"type": "tool_use", "id": "t1", "name": "Bash",
                 "input": {"command": "pytest -x"}}]},
        })
        self.assertEqual([(e["kind"], e["text"]) for e in events], [("cmd", "pytest -x")])

    def test_every_write_tool_is_a_write(self):
        for name in ("Write", "Edit", "MultiEdit"):
            events, _ = _events("claude", {
                "type": "assistant", "timestamp": "2026-08-04T09:41:07Z",
                "message": {"content": [
                    {"type": "tool_use", "id": "t1", "name": name,
                     "input": {"file_path": "/p/src/app.py"}}]},
            })
            self.assertEqual([e["kind"] for e in events], ["write"], name)

    def test_read_is_a_read(self):
        events, _ = _events("claude", {
            "type": "assistant", "timestamp": "2026-08-04T09:41:07Z",
            "message": {"content": [
                {"type": "tool_use", "id": "t1", "name": "Read",
                 "input": {"file_path": "/p/src/app.py"}}]},
        })
        self.assertEqual([e["kind"] for e in events], ["read"])

    def test_a_failed_call_is_named_by_what_it_was(self):
        tracker = Tracker("sess", "claude")
        _events("claude", {
            "type": "assistant", "timestamp": "2026-08-04T09:41:07Z",
            "message": {"content": [
                {"type": "tool_use", "id": "t1", "name": "Bash",
                 "input": {"command": "pytest -x"}}]},
        }, tracker)
        events, _ = _events("claude", {
            "type": "user", "timestamp": "2026-08-04T09:41:19Z",
            "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t1", "is_error": True,
                 "content": "1 failed"}]},
        }, tracker)
        self.assertEqual([(e["kind"], e["text"]) for e in events],
                         [("error", "pytest -x")])

    def test_a_failure_for_a_call_we_never_saw_is_still_reported(self):
        # Following a live log starts mid-stream; the call that failed may have
        # been issued before we joined.
        events, _ = _events("claude", {
            "type": "user", "timestamp": "2026-08-04T09:41:19Z",
            "message": {"content": [
                {"type": "tool_result", "tool_use_id": "gone", "is_error": True}]},
        })
        self.assertEqual([e["kind"] for e in events], ["error"])

    def test_an_unknown_tool_produces_nothing_but_is_remembered(self):
        tracker = Tracker("sess", "claude")
        events, _ = _events("claude", {
            "type": "assistant", "timestamp": "2026-08-04T09:41:07Z",
            "message": {"content": [
                {"type": "tool_use", "id": "t9", "name": "WebFetch",
                 "input": {"url": "https://example.com"}}]},
        }, tracker)
        self.assertEqual(events, [])
        self.assertEqual(tracker.recall("t9"), "WebFetch")


class TestCodex(unittest.TestCase):
    """Codex's own shapes, both the current one and the older one."""

    def test_session_meta_gives_the_project(self):
        _e, tracker = _events("codex", {
            "timestamp": "2026-08-04T01:35:09Z", "type": "session_meta",
            "payload": {"cwd": "/home/you/relay"},
        })
        self.assertEqual(tracker.project, "/home/you/relay")

    def test_a_user_message_is_a_turn_without_its_text(self):
        events, _ = _events("codex", {
            "timestamp": "2026-08-04T01:35:10Z", "type": "event_msg",
            "payload": {"type": "user_message", "message": "my secret plan"},
        })
        self.assertEqual([e["kind"] for e in events], ["turn"])
        self.assertNotIn("secret", json.dumps(events, default=str))

    def test_the_current_exec_shape_is_a_command(self):
        # Codex 0.146 runs shell through a JS snippet in a custom_tool_call;
        # there is no structured command field anywhere in the record.
        events, _ = _events("codex", {
            "timestamp": "2026-08-04T01:35:20Z", "type": "response_item",
            "payload": {
                "type": "custom_tool_call", "name": "exec",
                "call_id": "call_1",
                "input": 'const r = await tools.exec_command({"cmd":"pytest -x",'
                         '"workdir":"/home/you/api","yield_time_ms":10000});'
                         ' text(r.output);\n',
            },
        })
        self.assertEqual([(e["kind"], e["text"]) for e in events],
                         [("cmd", "pytest -x")])

    def test_unquoted_keys_in_the_snippet_still_parse(self):
        events, _ = _events("codex", {
            "timestamp": "2026-08-04T01:35:20Z", "type": "response_item",
            "payload": {"type": "custom_tool_call", "name": "exec", "call_id": "c",
                        "input": 'tools.exec_command({cmd:"ls -la",workdir:"/tmp"});'},
        })
        self.assertEqual([(e["kind"], e["text"]) for e in events], [("cmd", "ls -la")])

    def test_several_commands_in_one_snippet_are_several_events(self):
        events, _ = _events("codex", {
            "timestamp": "2026-08-04T01:35:20Z", "type": "response_item",
            "payload": {"type": "custom_tool_call", "name": "exec", "call_id": "c",
                        "input": 'await Promise.all([tools.exec_command({"cmd":"one"}),'
                                 'tools.exec_command({"cmd":"two"})]);'},
        })
        self.assertEqual([e["text"] for e in events], ["one", "two"])

    def test_an_escaped_quote_in_a_command_survives(self):
        events, _ = _events("codex", {
            "timestamp": "2026-08-04T01:35:20Z", "type": "response_item",
            "payload": {"type": "custom_tool_call", "name": "exec", "call_id": "c",
                        "input": 'tools.exec_command({"cmd":"echo \\"hi there\\""});'},
        })
        self.assertEqual([e["text"] for e in events], ['echo "hi there"'])

    def test_a_patch_inside_the_snippet_waits_for_its_result(self):
        # The snippet only proves an envelope was sent.  Across 1189 real logs a
        # patch_apply_end follows essentially every one of them, carries absolute
        # paths, and says whether it worked — so the write is reported from there.
        events, _ = _events("codex", {
            "timestamp": "2026-08-04T01:35:20Z", "type": "response_item",
            "payload": {
                "type": "custom_tool_call", "name": "exec", "call_id": "c",
                "input": 'const patch = "*** Begin Patch\\n'
                         '*** Update File: /home/you/api/src/app.py\\n'
                         '*** End Patch";\ntext(await tools.apply_patch(patch));\n',
            },
        })
        self.assertEqual(events, [])

    def test_a_relative_patched_path_is_resolved_against_the_project(self):
        # The older shape has no patch result to wait for, so its own paths are
        # read — and a relative one is meaningless without the project it is under.
        tracker = Tracker("sess", "codex", "/home/you/api")
        events, _ = _events("codex", {
            "timestamp": "2026-08-04T01:35:20Z", "type": "response_item",
            "payload": {"type": "function_call", "name": "apply_patch", "call_id": "c",
                        "arguments": '{"input": "*** Add File: src/new.py"}'},
        }, tracker)
        self.assertEqual([e["text"] for e in events], ["/home/you/api/src/new.py"])

    def test_a_failed_script_is_an_error_named_by_its_command(self):
        tracker = Tracker("sess", "codex")
        _events("codex", {
            "timestamp": "2026-08-04T01:35:20Z", "type": "response_item",
            "payload": {"type": "custom_tool_call", "name": "exec",
                        "call_id": "call_1",
                        "input": 'tools.exec_command({"cmd":"pytest -x"});'},
        }, tracker)
        events, _ = _events("codex", {
            "timestamp": "2026-08-04T01:35:25Z", "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output", "call_id": "call_1",
                "output": [{"type": "input_text",
                            "text": "Script failed\nWall time 0.1 seconds\nOutput:\n"}],
            },
        }, tracker)
        self.assertEqual([(e["kind"], e["text"]) for e in events],
                         [("error", "pytest -x")])

    def test_a_script_that_completed_is_not_an_error(self):
        events, _ = _events("codex", {
            "timestamp": "2026-08-04T01:35:25Z", "type": "response_item",
            "payload": {"type": "custom_tool_call_output", "call_id": "call_1",
                        "output": [{"type": "input_text",
                                    "text": "Script completed\nWall time 0.1 s"}]},
        })
        self.assertEqual(events, [])

    def test_patch_apply_end_reports_every_file_it_changed(self):
        events, _ = _events("codex", {
            "timestamp": "2026-08-04T01:35:30Z", "type": "event_msg",
            "payload": {
                "type": "patch_apply_end", "success": True,
                "changes": {"/home/you/api/a.py": {"type": "update"},
                            "/home/you/api/b.py": {"type": "add"}},
            },
        })
        self.assertEqual(sorted((e["kind"], e["text"]) for e in events),
                         [("write", "/home/you/api/a.py"),
                          ("write", "/home/you/api/b.py")])

    def test_a_patch_that_did_not_apply_is_an_error_not_a_write(self):
        events, _ = _events("codex", {
            "timestamp": "2026-08-04T01:35:30Z", "type": "event_msg",
            "payload": {"type": "patch_apply_end", "success": False,
                        "changes": {"/home/you/api/a.py": {"type": "update"}}},
        })
        self.assertEqual([e["kind"] for e in events], ["error"])

    def test_a_failed_patch_is_reported_once_not_twice(self):
        # Copied from a real session: one failed patch produces three records —
        # the call that sent it, the patch result, and the script result.  Two of
        # those say it failed, and only one line should say so.
        tracker = Tracker("sess", "codex")
        for record in (
            {"timestamp": "2026-07-13T10:43:08.725Z", "type": "response_item",
             "payload": {
                 "type": "custom_tool_call", "name": "exec", "call_id": "call_X",
                 "input": 'const patch = "*** Begin Patch\\n*** Add File: hello.txt'
                          '\\n+hello\\n*** End Patch";\ntext(await tools.apply_patch(patch));\n',
             }},
            {"timestamp": "2026-07-13T10:43:08.819Z", "type": "event_msg",
             "payload": {"type": "patch_apply_end", "call_id": "exec-b00e",
                         "success": False, "stderr": "Failed to write file /w/hello.txt\n",
                         "changes": {"/w/hello.txt": {"type": "add"}}}},
            {"timestamp": "2026-07-13T10:43:08.820Z", "type": "response_item",
             "payload": {"type": "custom_tool_call_output", "call_id": "call_X",
                         "output": [{"type": "input_text",
                                     "text": "Script failed\nWall time 0.1 seconds\n"}]}},
        ):
            _events("codex", record, tracker)[0]
        kinds = []
        tracker = Tracker("sess", "codex")
        for record in (
            {"timestamp": "2026-07-13T10:43:08.725Z", "type": "response_item",
             "payload": {
                 "type": "custom_tool_call", "name": "exec", "call_id": "call_X",
                 "input": 'const patch = "*** Begin Patch\\n*** Add File: hello.txt'
                          '\\n+hello\\n*** End Patch";\ntext(await tools.apply_patch(patch));\n',
             }},
            {"timestamp": "2026-07-13T10:43:08.819Z", "type": "event_msg",
             "payload": {"type": "patch_apply_end", "call_id": "exec-b00e",
                         "success": False,
                         "changes": {"/w/hello.txt": {"type": "add"}}}},
            {"timestamp": "2026-07-13T10:43:08.820Z", "type": "response_item",
             "payload": {"type": "custom_tool_call_output", "call_id": "call_X",
                         "output": [{"type": "input_text",
                                     "text": "Script failed\nWall time 0.1 seconds\n"}]}},
        ):
            kinds += [e["kind"] for e in _events("codex", record, tracker)[0]]
        self.assertEqual(kinds.count("error"), 1)

    def test_a_patch_that_failed_is_never_called_a_write(self):
        # The call that sends an envelope is not evidence the file changed; the
        # result that follows it is.  Reporting the attempt as a write means the
        # stream claims an edit that never happened.
        tracker = Tracker("sess", "codex")
        events, _ = _events("codex", {
            "timestamp": "2026-07-13T10:43:08.725Z", "type": "response_item",
            "payload": {
                "type": "custom_tool_call", "name": "exec", "call_id": "call_X",
                "input": 'const patch = "*** Begin Patch\\n*** Add File: hello.txt'
                         '\\n+hello\\n*** End Patch";\ntext(await tools.apply_patch(patch));\n',
            }}, tracker)
        self.assertEqual([e["kind"] for e in events], [])
        events, _ = _events("codex", {
            "timestamp": "2026-07-13T10:43:08.819Z", "type": "event_msg",
            "payload": {"type": "patch_apply_end", "call_id": "exec-b00e",
                        "success": True,
                        "changes": {"/w/hello.txt": {"type": "add"}}}}, tracker)
        self.assertEqual([(e["kind"], e["text"]) for e in events],
                         [("write", "/w/hello.txt")])

    def test_a_failed_command_is_still_named_even_with_no_patch_result(self):
        tracker = Tracker("sess", "codex")
        _events("codex", {
            "timestamp": "2026-07-13T10:43:12Z", "type": "response_item",
            "payload": {"type": "custom_tool_call", "name": "exec", "call_id": "c9",
                        "input": 'await tools.exec_command({cmd:"npm test"});'}}, tracker)
        events, _ = _events("codex", {
            "timestamp": "2026-07-13T10:43:13Z", "type": "response_item",
            "payload": {"type": "custom_tool_call_output", "call_id": "c9",
                        "output": [{"type": "input_text", "text": "Script failed\n"}]}},
            tracker)
        self.assertEqual([(e["kind"], e["text"]) for e in events],
                         [("error", "npm test")])

    def test_a_snippet_teaches_the_watcher_where_it_is_working(self):
        # Codex does not always announce a cwd, but every exec snippet carries a
        # workdir — without reading it, a relative patch path stays relative and
        # two names for one file end up in the stream.
        tracker = Tracker("sess", "codex")
        _events("codex", {
            "timestamp": "2026-07-13T10:43:12Z", "type": "response_item",
            "payload": {"type": "custom_tool_call", "name": "exec", "call_id": "c1",
                        "input": 'await tools.exec_command({cmd:"pwd","workdir":"/home/you/api"});'}},
            tracker)
        self.assertEqual(tracker.project, "/home/you/api")

    def test_an_announced_cwd_still_wins_over_a_snippet(self):
        tracker = Tracker("sess", "codex")
        _events("codex", {
            "timestamp": "2026-07-13T10:43:00Z", "type": "session_meta",
            "payload": {"cwd": "/home/you/relay"}}, tracker)
        _events("codex", {
            "timestamp": "2026-07-13T10:43:12Z", "type": "response_item",
            "payload": {"type": "custom_tool_call", "name": "exec", "call_id": "c1",
                        "input": 'await tools.exec_command({cmd:"pwd","workdir":"/tmp/x"});'}},
            tracker)
        self.assertEqual(tracker.project, "/home/you/relay")

    def test_the_older_function_call_shape_still_works(self):
        # Older Codex builds are still on disk and people still read them back.
        events, _ = _events("codex", {
            "timestamp": "2026-08-04T01:35:20Z", "type": "response_item",
            "payload": {"type": "function_call", "name": "exec_command",
                        "call_id": "c", "arguments": '{"command": "pytest -x"}'},
        })
        self.assertEqual([(e["kind"], e["text"]) for e in events], [("cmd", "pytest -x")])

    def test_the_older_failure_shape_still_works(self):
        events, _ = _events("codex", {
            "timestamp": "2026-08-04T01:35:25Z", "type": "response_item",
            "payload": {"type": "function_call_output", "call_id": "c",
                        "output": {"metadata": {"exit_code": 1}}},
        })
        self.assertEqual([e["kind"] for e in events], ["error"])


class TestNothingUnexpectedGetsThrough(unittest.TestCase):

    def test_every_event_kind_is_a_known_kind(self):
        records = [
            ("claude", {"type": "user", "timestamp": "2026-08-04T09:00:00Z",
                        "message": {"content": []}}),
            ("codex", {"type": "event_msg", "timestamp": "2026-08-04T09:00:00Z",
                       "payload": {"type": "user_message"}}),
        ]
        for source, record in records:
            events, _ = _events(source, record)
            for event in events:
                self.assertIn(event["kind"], KINDS)

    def test_every_event_has_the_full_field_set(self):
        events, _ = _events("claude", {
            "type": "assistant", "timestamp": "2026-08-04T09:41:07Z",
            "message": {"content": [
                {"type": "tool_use", "id": "t1", "name": "Bash",
                 "input": {"command": "ls"}}]},
        })
        for event in events:
            self.assertEqual(set(event),
                             {"at", "kind", "text", "session", "source", "project"})


class TestTrackerDoesNotGrowForever(unittest.TestCase):

    def test_old_labels_are_dropped(self):
        tracker = Tracker("sess", "claude")
        for i in range(tracker._max_labels + 50):
            tracker.remember("id%d" % i, "cmd %d" % i)
        self.assertLessEqual(len(tracker._labels), tracker._max_labels)
        self.assertEqual(tracker.recall("id0"), "")
        self.assertNotEqual(tracker.recall("id%d" % (tracker._max_labels + 49)), "")

    def test_a_repeated_id_does_not_grow_the_queue(self):
        tracker = Tracker("sess", "claude")
        for _ in range(100):
            tracker.remember("same", "ls")
        self.assertEqual(len(tracker._order), 1)


if __name__ == "__main__":
    unittest.main()
