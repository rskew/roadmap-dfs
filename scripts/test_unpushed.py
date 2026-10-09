#!/usr/bin/env python3
"""Which node commits origin/main lacks, and the places that say so.

Run:  python3 roadmap-dfs/scripts/test_unpushed.py
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tree as dfs_tree    # noqa: E402
import tui as dfs_tui      # noqa: E402


class Unpushed(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def git(self, *args):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                              cwd=self.root, capture_output=True, text=True, check=True).stdout

    def commit(self, subject):
        self.git("commit", "-q", "--allow-empty", "-m", subject)
        return self.git("rev-parse", "HEAD").strip()

    def test_no_origin_main_means_nothing_to_compare(self):
        self.git("init", "-q")
        self.commit("W1.1: first")
        self.assertIsNone(dfs_tree.unpushed(self.root, ttl=0))

    def test_only_commits_past_origin_main_count_by_task(self):
        self.git("init", "-q")
        self.commit("W1.1: pushed")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        one = self.commit("W1.2: not pushed")
        two = self.commit("W2.1@fx: not pushed either")
        self.commit("W1.3: also not pushed")
        shas, tasks = dfs_tree.unpushed(self.root, ttl=0)
        self.assertEqual(len(shas), 3)
        self.assertTrue({one, two} <= shas)
        self.assertEqual(tasks, {"W1": 2, "W2": 1})

    def test_a_pushed_head_has_nothing(self):
        self.git("init", "-q")
        self.commit("W1.1: pushed")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.assertEqual(dfs_tree.unpushed(self.root, ttl=0), (set(), {}))

    def test_master_is_found_when_origin_has_no_main(self):
        self.git("init", "-q")
        self.commit("W1.1: pushed")
        self.git("update-ref", "refs/remotes/origin/master", "HEAD")
        self.commit("W1.2: not pushed")
        self.assertEqual(dfs_tree.default_ref(self.root), "origin/master")
        self.assertEqual(dfs_tree.unpushed(self.root, ttl=0), (set(self.git("rev-list", "-1", "HEAD").split()), {"W1": 1}))

    def test_origin_head_beats_the_guesses(self):
        self.git("init", "-q")
        self.commit("W1.1: pushed")
        for b in ("main", "trunk"):
            self.git("update-ref", "refs/remotes/origin/" + b, "HEAD")
        self.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
        self.assertEqual(dfs_tree.default_ref(self.root), "origin/trunk")

    def test_init_default_branch_names_it(self):
        self.git("init", "-q")
        self.commit("W1.1: pushed")
        self.git("update-ref", "refs/remotes/origin/develop", "HEAD")
        self.assertIsNone(dfs_tree.default_ref(self.root))
        self.git("config", "init.defaultBranch", "develop")
        self.assertEqual(dfs_tree.default_ref(self.root), "origin/develop")

    def test_the_list_cell_is_blank_at_zero(self):
        self.assertEqual(dfs_tui.ahead_cell({"ahead": 0}), "")
        self.assertEqual(dfs_tui.ahead_cell({}), "")
        self.assertEqual(dfs_tui.ahead_cell({"ahead": 3}), "ahead 3")
        self.assertEqual(dfs_tui.ahead_cell({"uncommitted": 1}), "edits")
        self.assertEqual(dfs_tui.ahead_cell({"ahead": 3, "uncommitted": 1}), "ahead 3 +edits")
        self.assertEqual(dfs_tui.ahead_cell({"uncommitted": "log"}), "log")
        self.assertEqual(dfs_tui.ahead_cell({"ahead": 3, "uncommitted": "log"}), "ahead 3 +log")

    def test_task_files_with_uncommitted_edits_are_named(self):
        self.git("init", "-q")
        items = self.root / ".dfs" / "items"
        items.mkdir(parents=True)
        (items / "W1.md").write_text("# W1\n")
        (items / "W2.md").write_text("# W2\n")
        self.git("add", ".dfs")
        self.commit("W1.1: both")
        self.assertEqual(dfs_tree.uncommitted(self.root, ttl=0), {})
        (items / "W1.md").write_text("# W1 edited\n")
        (items / "W3.md").write_text("# W3 new\n")
        self.assertEqual(dfs_tree.uncommitted(self.root, ttl=0), {"W1": "edits", "W3": "edits"})

    def test_the_runners_own_log_lines_do_not_tag_a_task(self):
        """A chain writes session and end lines to every task it runs, and only a node
        commit of that file takes them, so counting them tags every task it touched."""
        self.git("init", "-q")
        items = self.root / ".dfs" / "items"
        items.mkdir(parents=True)
        def base(t, goal="g"):
            return "# %s · x\n\n## Goal\n\n%s\n\n## Tree\n\n## Log\n\n" % (t, goal)
        for t in ("W1", "W2", "W3", "W4"):
            (items / (t + ".md")).write_text(base(t))
        self.git("add", ".dfs")
        self.commit("W1.1: all")
        run = "- 2026-10-09T09:00:00Z · session · work · r/run-1\n- 2026-10-09T09:01:00Z · end · work · ok · peak 1\n"
        (items / "W1.md").write_text(base("W1") + run)
        (items / "W2.md").write_text(base("W2") + run + "- 2026-10-09T09:02:00Z · review · ok\n  a note\n")
        (items / "W3.md").write_text(base("W3", "changed goal") + run)
        (items / "W4.md").write_text(base("W4") + "- 2026-10-09T09:03:00Z · raise · W4.1\n  which one?\n")
        self.assertEqual(dfs_tree.uncommitted(self.root, ttl=0),
                         {"W2": "log", "W3": "edits", "W4": "log"})

if __name__ == "__main__":
    unittest.main(verbosity=2)
