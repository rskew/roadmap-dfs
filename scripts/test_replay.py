#!/usr/bin/env python3
"""replay.py: a task's starting point is rebuilt from git, and a replay is told apart from history."""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import history  # noqa: E402
import replay  # noqa: E402

TASK_FILE = """# W5 · Make the thing

## Summary

Added by the finishing session.

## Goal

Make the thing.

## Background

The author's note.

## Tree

### W5.1 · Do it
Status: confirmed

## Log

- 2026-10-01T00:00:00Z · session · work · x
"""


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd,
                          check=True, capture_output=True, text=True).stdout.strip()


class StartingPointTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "code.py").write_text("v1\n")
        (self.repo / ".dfs" / "items").mkdir(parents=True)
        (self.repo / ".dfs" / "ROADMAP.md").write_text("law\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "before the task")
        self.start = git(self.repo, "rev-parse", "HEAD")
        (self.repo / "code.py").write_text("v2\n")
        (self.repo / ".dfs" / "items" / "W5.md").write_text(TASK_FILE)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "W5.1: do it")
        (self.repo / "code.py").write_text("v3\n")
        git(self.repo, "commit", "-qam", "W5.2: more")

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_author_part_keeps_goal_and_background_only(self):
        text = replay.author_part(TASK_FILE)
        self.assertIn("## Goal\n\nMake the thing.", text)
        self.assertIn("## Background", text)
        for gone in ("Summary", "## Tree", "## Log", "W5.1"):
            self.assertNotIn(gone, text)

    def test_the_starting_point_is_the_commit_before_the_first_node(self):
        sha, text = replay.starting_point(self.repo, "W5")
        self.assertEqual(sha, self.start)
        self.assertTrue(text.startswith("# W5 · Make the thing"))
        self.assertNotIn("Tree", text)

    def test_a_task_with_no_node_commit_is_refused(self):
        with self.assertRaises(SystemExit):
            replay.starting_point(self.repo, "W9")

    def test_prepare_clones_the_old_code_with_the_goal_alone_and_a_clean_tree(self):
        dest = Path(self.tmp.name) / "dfs-replay-W5-a"
        out, tool = replay.prepare(self.repo, "W5", dest, tool=Path(__file__).resolve().parent.parent)
        self.assertEqual((out / "code.py").read_text(), "v1\n")
        self.assertNotIn("## Tree", (out / ".dfs" / "items" / "W5.md").read_text())
        self.assertEqual(git(out, "status", "--porcelain"), "")
        self.assertTrue((out / ".git" / "hooks" / "pre-commit").exists())
        self.assertEqual(json.loads((out / ".git" / "replay.json").read_text())["from"], self.start)
        with self.assertRaises(SystemExit):   # a replay starts from nothing
            replay.prepare(self.repo, "W5", dest)

    def test_a_dest_not_named_as_a_replay_is_refused(self):
        with self.assertRaises(SystemExit):
            replay.prepare(self.repo, "W5", Path(self.tmp.name) / "scratch")

    def test_law_replaces_the_starting_commits_law(self):
        law = Path(self.tmp.name) / "law.md"
        law.write_text("a changed law\n")
        out, _ = replay.prepare(self.repo, "W5", Path(self.tmp.name) / "dfs-replay-W5-b",
                                tool=Path(__file__).resolve().parent.parent, law=law)
        self.assertEqual((out / ".dfs" / "ROADMAP.md").read_text(), "a changed law\n")


class ScoreTest(unittest.TestCase):
    def test_a_replay_bounds_one_bash_call(self):
        b = replay.bash_bound(15)
        self.assertEqual(b, {"BASH_DEFAULT_TIMEOUT_MS": "900000", "BASH_MAX_TIMEOUT_MS": "1800000"})
        self.assertEqual(replay.bash_bound(0.5)["BASH_DEFAULT_TIMEOUT_MS"], "30000")

    def test_why_a_chain_stopped_is_its_last_stop_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".git").mkdir()
            self.assertEqual(replay.stopped_on(tmp), "")
            (Path(tmp) / ".git" / "replay.out").write_text(
                "dfs_run: BLOCKED a raise\n...\ndfs_run: DONE after run 6 — every live node\n")
            self.assertEqual(replay.stopped_on(tmp), "DONE")
            (Path(tmp) / ".git" / "replay.out").write_text("dfs_run: it fell over\n")
            self.assertEqual(replay.stopped_on(tmp), "other")

    def test_the_comparison_names_both_columns(self):
        t = {"sessions": {"work": 2, "review": 1}, "usd": 1.5, "nodes": 3, "first_review": "issues",
             "review_issues": 1, "review_ok": 1, "critic_issues": 0, "raises": 0, "refused": 0,
             "after_issues": 0.5}
        buf = io.StringIO()
        with redirect_stdout(buf):
            replay.compare("W5", t, dict(t, stopped="DONE", finished=True))
        out = buf.getvalue()
        self.assertIn("original", out)
        self.assertIn("first review", out)
        self.assertIn("DONE", out)


class HistoryKeepsReplaysApartTest(unittest.TestCase):
    def test_default_mining_skips_replay_transcripts_and_notes_the_first_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("-w-proj", "-tmp-dfs-replay-W5-a"):
                d = Path(tmp) / name
                d.mkdir()
                lines = [
                    {"type": "user", "timestamp": "2026-10-01T00:00:00Z", "message": {"content": "You are the implementation reviewer for task W5. x"}},
                    {"type": "assistant", "timestamp": "2026-10-01T00:01:00Z", "message": {"id": "m", "model": "m", "usage": {"input_tokens": 1, "output_tokens": 1}, "content": [
                        {"type": "tool_use", "id": "t", "name": "Bash", "input": {"command": "tree.py log W5 review issues"}}]}},
                ]
                (d / "s.jsonl").write_text("".join(json.dumps(r) + "\n" for r in lines))
            _, tasks, _, _ = history.load(os.path.join(tmp, "*", "*.jsonl"), {})
            self.assertEqual([p for p, _ in tasks], ["-w-proj"])
            self.assertEqual(tasks[("-w-proj", "W5")]["first_review"], "issues")
            _, tasks, _, _ = history.load(os.path.join(tmp, "*", "*.jsonl"), {}, replays=True)
            self.assertEqual(len(tasks), 2)


if __name__ == "__main__":
    unittest.main()
