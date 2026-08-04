"""Following files that are being written underneath us.

Everything a log can do while you are reading it — grow, rotate, get truncated,
vanish, hand back half a line — happens here on purpose.  A watcher that dies
when a log rotates is worse than no watcher.
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

from agentwatch.follow import (
    Watcher, decode_claude_project, discover, session_id_for,
)


def cmd_record(command, when):
    return {"type": "assistant", "timestamp": when.isoformat(), "message": {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": "t", "name": "Bash",
                     "input": {"command": command}}]}}


class Scratch(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="agentwatch-follow-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.now = datetime.now(timezone.utc)

    def claude_path(self, project="-home-you-api", name="s1"):
        folder = os.path.join(self.home, ".claude", "projects", project)
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, name + ".jsonl")

    def codex_path(self, name="rollout-2026-08-04T09-40-00-1a2b3c4d-5e6f-7081-92a3-b4c5d6e7f809"):
        folder = os.path.join(self.home, ".codex", "sessions", "2026", "08", "04")
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, name + ".jsonl")

    def append(self, path, *records):
        with open(path, "a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")

    def watcher(self, **kwargs):
        kwargs.setdefault("since", self.now - timedelta(minutes=10))
        return Watcher(home=self.home, **kwargs)


class TestDiscovery(Scratch):

    def test_both_trees_are_found(self):
        self.append(self.claude_path(), cmd_record("a", self.now))
        self.append(self.codex_path(), {"type": "event_msg",
                                        "payload": {"type": "user_message"}})
        found = dict((os.path.basename(p), s) for p, s in discover(self.home))
        self.assertEqual(sorted(found.values()), ["claude", "codex"])

    def test_only_the_source_asked_for(self):
        self.append(self.claude_path(), cmd_record("a", self.now))
        self.append(self.codex_path(), {"type": "event_msg", "payload": {}})
        self.assertEqual([s for _p, s in discover(self.home, ("codex",))], ["codex"])

    def test_files_that_are_not_jsonl_are_ignored(self):
        folder = os.path.join(self.home, ".claude", "projects", "-home-you-api")
        os.makedirs(folder)
        for name in ("notes.txt", "s1.json", "s1.jsonl.bak", ".hidden"):
            open(os.path.join(folder, name), "w").close()
        self.assertEqual(discover(self.home), [])

    def test_a_missing_home_is_empty_not_an_error(self):
        self.assertEqual(discover(os.path.join(self.home, "nowhere")), [])

    def test_a_symlinked_directory_is_not_followed_twice(self):
        # A log reachable by two paths would print every event twice.
        real = self.claude_path()
        self.append(real, cmd_record("a", self.now))
        link = os.path.join(self.home, ".claude", "projects", "-home-you-link")
        try:
            os.symlink(os.path.dirname(real), link)
        except (OSError, NotImplementedError):
            self.skipTest("no symlinks here")
        self.assertEqual(len(discover(self.home)), 1)


class TestNaming(Scratch):

    def test_a_claude_session_id_is_its_filename(self):
        self.assertEqual(session_id_for("/x/4ef1361b-07e4.jsonl", "claude"),
                         "4ef1361b-07e4")

    def test_a_codex_session_id_is_the_uuid_not_the_date(self):
        name = "/x/rollout-2026-08-04T09-40-00-1a2b3c4d-5e6f-7081-92a3-b4c5d6e7f809.jsonl"
        self.assertEqual(session_id_for(name, "codex"),
                         "1a2b3c4d-5e6f-7081-92a3-b4c5d6e7f809")

    def test_a_codex_name_with_too_few_parts_is_left_alone(self):
        self.assertEqual(session_id_for("/x/rollout.jsonl", "codex"), "rollout")

    def test_the_encoded_project_directory_decodes(self):
        self.assertEqual(
            decode_claude_project("/h/.claude/projects/-home-you-api-server/s.jsonl"),
            "/home/you/api/server")   # ambiguous by construction; a cwd wins

    def test_a_directory_that_is_not_encoded_is_kept_as_it_is(self):
        self.assertEqual(
            decode_claude_project("/h/.claude/projects/plain/s.jsonl"), "plain")


class TestReading(Scratch):

    def test_new_lines_come_back_and_old_ones_do_not_repeat(self):
        path = self.claude_path()
        self.append(path, cmd_record("first", self.now))
        watcher = self.watcher()
        self.assertEqual([e["text"] for e in watcher.poll()], ["first"])
        self.assertEqual(watcher.poll(), [])
        self.append(path, cmd_record("second", self.now))
        self.assertEqual([e["text"] for e in watcher.poll()], ["second"])

    def test_a_half_written_line_waits_for_its_newline(self):
        path = self.claude_path()
        self.append(path, cmd_record("whole", self.now))
        watcher = self.watcher()
        watcher.poll()
        partial = json.dumps(cmd_record("torn in half", self.now))
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(partial[:len(partial) // 2])
        self.assertEqual(watcher.poll(), [])          # nothing mangled
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(partial[len(partial) // 2:] + "\n")
        self.assertEqual([e["text"] for e in watcher.poll()], ["torn in half"])

    def test_a_truncated_log_is_read_from_the_start_again(self):
        path = self.claude_path()
        self.append(path, cmd_record("before", self.now))
        watcher = self.watcher()
        watcher.poll()
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(cmd_record("after", self.now)) + "\n")
        self.assertEqual([e["text"] for e in watcher.poll()], ["after"])

    def test_a_rotated_log_is_picked_up_by_inode_not_by_size(self):
        path = self.claude_path()
        self.append(path, cmd_record("original which is quite a long command", self.now))
        watcher = self.watcher()
        watcher.poll()
        os.remove(path)
        self.append(path, cmd_record("replacement", self.now))
        self.assertIn("replacement", [e["text"] for e in watcher.poll()])

    def test_a_log_that_vanishes_is_dropped_quietly(self):
        path = self.claude_path()
        self.append(path, cmd_record("gone soon", self.now))
        watcher = self.watcher()
        watcher.poll()
        os.remove(path)
        self.assertEqual(watcher.poll(), [])
        self.assertEqual(watcher.poll(), [])

    def test_events_from_several_logs_come_back_in_time_order(self):
        first = self.claude_path(name="s1")
        second = self.claude_path(project="-home-you-web", name="s2")
        self.append(first, cmd_record("late", self.now - timedelta(seconds=5)))
        self.append(second, cmd_record("early", self.now - timedelta(seconds=60)))
        self.assertEqual([e["text"] for e in self.watcher().poll()],
                         ["early", "late"])

    def test_a_log_appearing_mid_run_is_read_whole(self):
        watcher = self.watcher()
        watcher.poll()
        self.append(self.claude_path(name="new"), cmd_record("appeared", self.now))
        watcher._last_scan = 0.0        # force the rescan the interval would do
        self.assertEqual([e["text"] for e in watcher.poll()], ["appeared"])


class TestFilters(Scratch):

    def test_a_log_older_than_since_is_not_adopted(self):
        path = self.claude_path()
        self.append(path, cmd_record("ancient", self.now - timedelta(days=3)))
        os.utime(path, (0, (self.now - timedelta(days=3)).timestamp()))
        watcher = self.watcher(since=self.now - timedelta(minutes=10))
        self.assertEqual(watcher.poll(), [])
        self.assertEqual(watcher.watched(), 0)

    def test_events_older_than_since_are_dropped_even_from_a_fresh_log(self):
        path = self.claude_path()
        self.append(path,
                    cmd_record("old", self.now - timedelta(hours=5)),
                    cmd_record("new", self.now))
        self.assertEqual([e["text"] for e in self.watcher().poll()], ["new"])

    def test_stale_logs_are_skipped_when_there_is_no_since(self):
        path = self.claude_path()
        self.append(path, cmd_record("stale", self.now))
        os.utime(path, (0, (self.now - timedelta(hours=2)).timestamp()))
        watcher = Watcher(home=self.home, stale_s=60)
        self.assertEqual(watcher.poll(), [])

    def test_project_matches_on_a_substring_case_insensitively(self):
        self.append(self.claude_path(project="-home-you-APIserver"),
                    cmd_record("yes", self.now))
        self.append(self.claude_path(project="-home-you-web", name="s2"),
                    cmd_record("no", self.now))
        watcher = self.watcher(project="api")
        self.assertEqual([e["text"] for e in watcher.poll()], ["yes"])

    def test_a_filtered_log_is_counted_as_found_but_not_watched(self):
        self.append(self.claude_path(project="-home-you-web"),
                    cmd_record("no", self.now))
        watcher = self.watcher(project="api")
        watcher.poll()
        self.assertEqual((watcher.found(), watcher.watched()), (1, 0))

    def test_live_mode_joins_an_existing_log_at_its_end(self):
        # Without --since, history is not replayed: you asked what is happening
        # now, not what happened before you looked.
        path = self.claude_path()
        self.append(path, cmd_record("history", self.now))
        watcher = Watcher(home=self.home)
        self.assertEqual(watcher.poll(), [])
        self.append(path, cmd_record("live", self.now))
        self.assertEqual([e["text"] for e in watcher.poll()], ["live"])


class TestNothingCrashesIt(Scratch):

    def test_a_log_of_pure_rubbish_yields_nothing_and_does_not_raise(self):
        path = self.claude_path()
        with open(path, "wb") as handle:
            handle.write(b"not json\n{\n\x00\x01\x02\n[]\n" + b"\xff" * 200 + b"\n")
        self.assertEqual(self.watcher().poll(), [])

    def test_invalid_utf8_is_replaced_not_raised(self):
        path = self.claude_path()
        record = json.dumps(cmd_record("café", self.now)).encode("utf-8")
        with open(path, "wb") as handle:
            handle.write(b"\xff\xfe garbage\n" + record + b"\n")
        self.assertEqual([e["text"] for e in self.watcher().poll()], ["café"])

    def test_a_directory_named_like_a_log_is_skipped(self):
        folder = os.path.join(self.home, ".claude", "projects", "-home-you-api")
        os.makedirs(os.path.join(folder, "weird.jsonl"))
        self.assertEqual(self.watcher().poll(), [])

    def test_an_unreadable_log_is_skipped_rather_than_fatal(self):
        path = self.claude_path()
        self.append(path, cmd_record("secret", self.now))
        os.chmod(path, 0)
        self.addCleanup(os.chmod, path, 0o644)
        if os.access(path, os.R_OK):
            self.skipTest("running as root; permissions do not apply")
        self.assertEqual(self.watcher().poll(), [])

    def test_an_enormous_single_line_does_not_hang(self):
        path = self.claude_path()
        self.append(path, cmd_record("x" * 500000, self.now))
        events = self.watcher().poll()
        self.assertEqual(len(events), 1)


if __name__ == "__main__":
    unittest.main()
