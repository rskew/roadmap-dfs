#!/usr/bin/env python3
"""replay.py: a task's starting point is rebuilt from git, and a replay is told apart from history."""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
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


can_exec = replay.hooks_can_run   # the real probe, kept before any test patches replay's


class StartingPointTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        if not can_exec(self.tmp.name):   # /tmp is often noexec: the other tests do not need hooks
            patch = mock.patch.object(replay, "hooks_can_run", return_value=True)
            patch.start()
            self.addCleanup(patch.stop)
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
        self.assertEqual(git(out, "config", "user.email"), "t@t")   # the checkout's identity
        self.assertEqual(json.loads((out / ".git" / "replay.json").read_text())["from"], self.start)
        with self.assertRaises(SystemExit):   # a replay starts from nothing
            replay.prepare(self.repo, "W5", dest)

    def test_a_dest_where_hooks_cannot_run_is_refused_before_cloning(self):
        dest = Path(self.tmp.name) / "dfs-replay-W5-noexec"
        with mock.patch.object(replay, "hooks_can_run", return_value=False):
            with self.assertRaises(SystemExit) as e:
                replay.prepare(self.repo, "W5", dest)
        self.assertIn("noexec", str(e.exception))
        self.assertFalse(dest.exists())

    def test_the_clone_holds_nothing_of_the_originals_later_work(self):
        git(self.repo, "tag", "later")   # a tag on a commit after the starting point
        out, _ = replay.prepare(self.repo, "W5", Path(self.tmp.name) / "dfs-replay-W5-c",
                                tool=Path(__file__).resolve().parent.parent)
        self.assertEqual(git(out, "remote"), "")
        self.assertEqual(git(out, "tag", "--list"), "")
        self.assertEqual(git(out, "branch", "--all", "--format=%(refname)"), "refs/heads/main")
        later = git(self.repo, "rev-parse", "HEAD")
        self.assertNotEqual(subprocess.run(["git", "cat-file", "-e", later], cwd=out).returncode, 0)

    def test_the_guard_refuses_a_commit_in_a_prepared_clone(self):
        if not can_exec(self.tmp.name):
            self.skipTest("this temp dir is noexec, so git would skip the hook; set TMPDIR to one that is not")
        out, _ = replay.prepare(self.repo, "W5", Path(self.tmp.name) / "dfs-replay-W5-d",
                                tool=Path(__file__).resolve().parent.parent)
        item = out / ".dfs" / "items" / "W5.md"
        # a Status that is not open | parked | confirmed | refuted is a shape fault the guard refuses
        item.write_text(item.read_text() + "\n## Tree\n\n### W5.1 · Do it\nStatus: done\nApproach: Do the thing in code.py.\n")
        git(out, "add", ".dfs")
        r = subprocess.run(["git", "commit", "-qm", "did it"], cwd=out, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)

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


class TranscriptsOfOneReplayTest(unittest.TestCase):
    def test_replay_1_does_not_take_in_replay_10_but_does_take_its_worktrees(self):
        with tempfile.TemporaryDirectory() as cfg, mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": cfg}):
            for name in ("-tmp-dfs-replay-W5-1", "-tmp-dfs-replay-W5-1--dfs-worktrees-W5",
                         "-tmp-dfs-replay-W5-10", "-tmp-dfs-replay-W5-10--dfs-worktrees-W5",
                         "-tmp-dfs-replay-W5-1x"):
                d = Path(cfg) / "projects" / name
                d.mkdir(parents=True)
                (d / "s.jsonl").write_text("{}\n")
            found = {Path(f).parent.name for f in history._glob(replay.transcripts("/tmp/dfs-replay-W5-1"))}
            self.assertEqual(found, {"-tmp-dfs-replay-W5-1", "-tmp-dfs-replay-W5-1--dfs-worktrees-W5"})


class OriginalIsTheRepoOwnTest(unittest.TestCase):
    def test_a_task_of_the_same_name_in_another_project_is_not_the_original(self):
        lines = [
            {"type": "user", "timestamp": "2026-10-01T00:00:00Z", "message": {"content": "You are the implementation reviewer for task W5. x"}},
            {"type": "assistant", "timestamp": "2026-10-01T00:01:00Z", "message": {"id": "m", "model": "m", "usage": {"input_tokens": 1, "output_tokens": 1}, "content": [
                {"type": "tool_use", "id": "t", "name": "Bash", "input": {"command": "tree.py log W5 review issues"}}]}},
        ]
        with tempfile.TemporaryDirectory() as cfg, mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": cfg}):
            for name in ("-tmp-mine", "-tmp-other"):
                d = Path(cfg) / "projects" / name
                d.mkdir(parents=True)
                (d / "s.jsonl").write_text("".join(json.dumps(r) + "\n" for r in lines))
            # transcripts() is patched wide, as it was before it was narrowed, to show that
            # original() itself refuses the other project's task.
            wide = [os.path.join(cfg, "projects", "*", "*.jsonl")]
            with mock.patch.object(replay, "transcripts", return_value=wide):
                self.assertIsNotNone(replay.original("/tmp/mine", "W5"))
                self.assertIsNone(replay.original("/tmp/nobody", "W5"))


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
