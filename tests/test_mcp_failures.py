"""An MCP call that failed, in a stream that showed nothing.

Codex reports every MCP tool call in ``mcp_tool_call_end``, whose ``result`` is
either ``{"Ok": ...}`` or ``{"Err": "..."}``.  Nothing in this parser read that
payload type, so a failed MCP call produced no event at all — an agent sitting
there retrying a server that was not running looked exactly like an agent
thinking.  That is the one thing this tool says it must not do.

On the 1189 Codex session files this was found with, six calls failed across
four sessions, every one of them `resources/read failed: unknown MCP server` —
a server named in the config and not up.  Small in number, and the sort of
failure a person watching the stream is watching *for*.

A successful MCP call stays silent.  It is not a command, and the stream's `cmd`
line means a shell command; putting MCP calls there would trade a missing
failure for a wrong command count.  Only the failure is news.

The same gap was found and fixed in agentlog (`tests/test_mcp_failures.py`
there).  Both tools read the same logs, and the sweep that found it compared
every record and payload type on disk against the ones each parser names.
"""

from __future__ import annotations

import json
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from agentwatch.events import Tracker, events_from_line  # noqa: E402


def mcp(result, server="workspace", tool="read_mcp_resource",
        ts="2026-08-04T09:00:05.000Z", call_id="call_1"):
    """One MCP call as Codex writes it, in the shape found on real logs."""
    return json.dumps({
        "timestamp": ts, "type": "event_msg",
        "payload": {"type": "mcp_tool_call_end", "call_id": call_id,
                    "invocation": {"server": server, "tool": tool,
                                   "arguments": {"uri": "file:///x.py"}},
                    "duration": {"secs": 0, "nanos": 16140},
                    "result": result}})


ERR = {"Err": "resources/read failed: unknown MCP server 'workspace'"}
OK = {"Ok": {"content": [{"type": "text", "text": "..."}]}}


class Case(unittest.TestCase):
    def setUp(self):
        self.tr = Tracker("019fb76f", "codex", "/home/you/api")

    def events(self, *lines):
        out = []
        for line in lines:
            out.extend(events_from_line(line, self.tr))
        return out


class TestAFailedMcpCallIsShown(Case):

    def test_it_produces_an_error_event(self):
        events = self.events(mcp(ERR))
        self.assertEqual([e["kind"] for e in events], ["error"])

    def test_it_names_the_server_and_the_tool(self):
        # The tool name alone does not say which server was down, and that is
        # the part a person can act on.
        events = self.events(mcp(ERR))
        self.assertEqual(events[0]["text"], "mcp workspace/read_mcp_resource")

    def test_it_carries_the_session_and_project_like_any_other_event(self):
        events = self.events(mcp(ERR))
        self.assertEqual(events[0]["session"], "019fb76f")
        self.assertEqual(events[0]["source"], "codex")
        self.assertEqual(events[0]["project"], "/home/you/api")

    def test_two_failures_are_two_events(self):
        events = self.events(mcp(ERR, call_id="a"),
                             mcp(ERR, server="filesystem", call_id="b"))
        self.assertEqual([e["text"] for e in events],
                         ["mcp workspace/read_mcp_resource",
                          "mcp filesystem/read_mcp_resource"])

    def test_it_is_timestamped_from_the_record(self):
        events = self.events(mcp(ERR))
        self.assertEqual(events[0]["at"].strftime("%H:%M:%S"), "09:00:05")


class TestASuccessfulCallStaysSilent(Case):
    """The half that stops this becoming noise."""

    def test_an_ok_result_produces_nothing(self):
        self.assertEqual(self.events(mcp(OK)), [])

    def test_an_ok_call_is_not_a_command(self):
        self.assertEqual([e for e in self.events(mcp(OK)) if e["kind"] == "cmd"], [])

    def test_a_failed_call_is_not_a_command_either(self):
        self.assertEqual(
            [e for e in self.events(mcp(ERR)) if e["kind"] == "cmd"], [])

    def test_a_missing_result_is_not_treated_as_failure(self):
        # A record from a build that does not send it, or one truncated on
        # disk.  Silence is not failure.
        rec = json.loads(mcp(OK))
        del rec["payload"]["result"]
        self.assertEqual(self.events(json.dumps(rec)), [])

    def test_a_result_that_is_not_a_mapping_is_not_a_failure(self):
        self.assertEqual(self.events(mcp("done")), [])

    def test_an_ok_call_does_not_count_as_a_write(self):
        self.assertEqual(
            [e for e in self.events(mcp(OK)) if e["kind"] == "write"], [])


class TestTheRecordIsReadDefensively(Case):
    """It comes off a file being written to right now."""

    def test_a_missing_invocation_still_reports_the_failure(self):
        rec = json.loads(mcp(ERR))
        del rec["payload"]["invocation"]
        events = self.events(json.dumps(rec))
        self.assertEqual([e["text"] for e in events], ["mcp call failed"])

    def test_a_tool_with_no_server_is_named_by_its_tool(self):
        rec = json.loads(mcp(ERR))
        del rec["payload"]["invocation"]["server"]
        events = self.events(json.dumps(rec))
        self.assertEqual([e["text"] for e in events], ["mcp read_mcp_resource"])

    def test_an_invocation_that_is_not_a_mapping_does_not_raise(self):
        rec = json.loads(mcp(ERR))
        rec["payload"]["invocation"] = "workspace.read"
        events = self.events(json.dumps(rec))
        self.assertEqual([e["kind"] for e in events], ["error"])

    def test_a_record_with_no_timestamp_is_still_reported(self):
        rec = json.loads(mcp(ERR))
        del rec["timestamp"]
        events = self.events(json.dumps(rec))
        self.assertEqual([e["kind"] for e in events], ["error"])

    def test_a_half_written_line_yields_nothing(self):
        self.assertEqual(self.events(mcp(ERR)[:40]), [])


class TestItDoesNotDisturbTheRest(Case):

    def test_a_failing_command_is_still_its_own_error(self):
        call = json.dumps({
            "timestamp": "2026-08-04T09:00:06.000Z", "type": "response_item",
            "payload": {"type": "custom_tool_call", "name": "exec",
                        "call_id": "c1",
                        "input": 'await tools.exec_command({cmd:"pytest -x"});'}})
        out = json.dumps({
            "timestamp": "2026-08-04T09:00:07.000Z", "type": "response_item",
            "payload": {"type": "custom_tool_call_output", "call_id": "c1",
                        "output": "Script failed with exit code 1"}})
        events = self.events(mcp(ERR), call, out)
        self.assertEqual([(e["kind"], e["text"]) for e in events],
                         [("error", "mcp workspace/read_mcp_resource"),
                          ("cmd", "pytest -x"),
                          ("error", "pytest -x")])


if __name__ == "__main__":
    unittest.main()
