"""A notebook edit is an edit, and a watcher that misses it shows nothing.

Claude Code writes files with four tools and this watcher knew three.  The
fourth, `NotebookEdit`, does not put its path under `file_path` the way the
others do — it uses `notebook_path` — so knowing the name would not have been
enough either.

Here the cost is worse than a wrong number.  agentwatch exists so that a quiet
screen means an idle agent; that is the promise the whole tool is built on,
and it is why an unreadable log gets named out loud rather than counted.  An
agent working through a notebook produced exactly that quiet screen — every
edit invisible, nothing to say it was working, and no way for the person
watching to tell that from a stall.

No `NotebookEdit` call appears in the 864 session logs on this machine, so
this is found by reading the tool surface rather than by measuring, and every
fixture below is written.  That is the whole population of notebook users
here: nobody.  For somebody whose work *is* a notebook it is the whole feed.

The subagent case is covered too — a subagent editing a notebook is still work
that happened in this session, and [[test_subagent_turns]] draws that line.
"""

from __future__ import annotations

import json
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from agentwatch.events import Tracker, events_from_line  # noqa: E402

TS = "2026-08-04T09:00:00.000Z"
NB = "/home/you/api/notebooks/explore.ipynb"


def called(name, inp, tool_use_id="t1", ts=TS, sidechain=False):
    return json.dumps({"type": "assistant", "isSidechain": sidechain,
                       "timestamp": ts, "cwd": "/home/you/api",
                       "message": {"role": "assistant", "id": "m1",
                                   "content": [{"type": "tool_use",
                                                "id": tool_use_id,
                                                "name": name,
                                                "input": inp}]}})


def edited_notebook(path=NB, tool_use_id="t1", ts=TS, mode="replace",
                    sidechain=False):
    return called("NotebookEdit",
                  {"notebook_path": path, "cell_id": "c1",
                   "edit_mode": mode, "new_source": "import pandas as pd"},
                  tool_use_id, ts, sidechain)


def failed(tool_use_id="t1", ts=TS):
    return json.dumps({"type": "user", "isSidechain": False, "timestamp": ts,
                       "cwd": "/home/you/api",
                       "message": {"role": "user",
                                   "content": [{"type": "tool_result",
                                                "tool_use_id": tool_use_id,
                                                "is_error": True,
                                                "content": "cell not found"}]}})


class Case(unittest.TestCase):
    def setUp(self):
        self.tr = Tracker("689e648c", "claude", "/home/you/api")

    def events(self, *lines):
        out = []
        for line in lines:
            out.extend(events_from_line(line, self.tr))
        return out

    def kinds(self, *lines):
        return [(e["kind"], e["text"]) for e in self.events(*lines)]


class TestANotebookEditStreamsAsAWrite(Case):

    def test_it_is_a_write(self):
        self.assertEqual(self.kinds(edited_notebook()), [("write", NB)])

    def test_the_path_comes_off_notebook_path(self):
        # The point: reading file_path finds nothing on this record, so the
        # name alone would not have fixed it.
        self.assertEqual(self.kinds(edited_notebook("/home/you/api/a.ipynb")),
                         [("write", "/home/you/api/a.ipynb")])

    def test_every_edit_mode_is_a_write(self):
        # insert and delete change the file as much as replace does.
        for mode in ("replace", "insert", "delete"):
            self.setUp()
            self.assertEqual(self.kinds(edited_notebook(mode=mode)),
                             [("write", NB)], mode)

    def test_two_edits_are_two_lines(self):
        # A feed shows work as it happens; the second edit is news too.
        self.assertEqual(
            self.kinds(edited_notebook(tool_use_id="t1"),
                       edited_notebook(tool_use_id="t2",
                                       ts="2026-08-04T09:00:05.000Z")),
            [("write", NB), ("write", NB)])

    def test_a_subagent_editing_a_notebook_still_shows(self):
        self.assertEqual(self.kinds(edited_notebook(sidechain=True)),
                         [("write", NB)])


class TestItSitsBesideTheOtherWriteTools(Case):

    def test_a_notebook_and_a_python_file_both_stream(self):
        self.assertEqual(
            self.kinds(called("Write", {"file_path": "/home/you/api/x.py",
                                        "content": "x = 1"}, "t1"),
                       edited_notebook(tool_use_id="t2",
                                       ts="2026-08-04T09:00:02.000Z")),
            [("write", "/home/you/api/x.py"), ("write", NB)])

    def test_file_path_still_wins_for_the_ordinary_tools(self):
        # A notebook_path on a Write is somebody else's record shape.
        self.assertEqual(
            self.kinds(called("Write", {"notebook_path": NB}, "t1")), [])

    def test_reading_a_notebook_is_still_a_read(self):
        # Read takes .ipynb, and takes it under file_path.
        self.assertEqual(self.kinds(called("Read", {"file_path": NB}, "t1")),
                         [("read", NB)])


class TestItIsNamedWhenItFails(Case):

    def test_a_failed_notebook_edit_names_the_notebook(self):
        # Without the fix the label is the bare tool name, so the ✗ line says
        # `NotebookEdit` and not which notebook — and a feed cannot be asked.
        self.assertEqual(self.kinds(edited_notebook(), failed()),
                         [("write", NB), ("error", "edit explore.ipynb")])


class TestTheInputIsReadDefensively(Case):

    def test_a_notebook_edit_with_no_path_writes_nothing(self):
        self.assertEqual(
            self.kinds(called("NotebookEdit", {"new_source": "x"}, "t1")), [])

    def test_a_non_string_path_writes_nothing(self):
        self.assertEqual(
            self.kinds(called("NotebookEdit", {"notebook_path": ["a"]}, "t1")),
            [])

    def test_a_pathless_notebook_edit_still_names_itself_on_failure(self):
        # It falls through to the bare-name label, which is the most that can
        # honestly be said about it.
        self.assertEqual(
            self.kinds(called("NotebookEdit", {"new_source": "x"}, "t1"),
                       failed()),
            [("error", "NotebookEdit")])

    def test_an_unknown_tool_is_not_a_write(self):
        # The field is chosen per tool, so a tool nobody knows about does not
        # become a write by carrying a notebook_path.
        self.assertEqual(
            self.kinds(called("SomeFutureTool", {"notebook_path": NB}, "t1")),
            [])

    def test_a_half_written_line_yields_nothing(self):
        self.assertEqual(self.events(edited_notebook()[:40]), [])


if __name__ == "__main__":
    unittest.main()
