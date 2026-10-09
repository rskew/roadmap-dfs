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

    def test_the_list_cell_is_blank_at_zero(self):
        self.assertEqual(dfs_tui.ahead_cell({"ahead": 0}), "")
        self.assertEqual(dfs_tui.ahead_cell({}), "")
        self.assertEqual(dfs_tui.ahead_cell({"ahead": 3}), "ahead 3")
        self.assertEqual(dfs_tui.ahead_cell({"uncommitted": 1}), "edits")
        self.assertEqual(dfs_tui.ahead_cell({"ahead": 3, "uncommitted": 1}), "ahead 3 +edits")

    def test_task_files_with_uncommitted_edits_are_named(self):
        self.git("init", "-q")
        items = self.root / ".dfs" / "items"
        items.mkdir(parents=True)
        (items / "W1.md").write_text("# W1\n")
        (items / "W2.md").write_text("# W2\n")
        self.git("add", ".dfs")
        self.commit("W1.1: both")
        self.assertEqual(dfs_tree.uncommitted(self.root, ttl=0), set())
        (items / "W1.md").write_text("# W1 edited\n")
        (items / "W3.md").write_text("# W3 new\n")
        self.assertEqual(dfs_tree.uncommitted(self.root, ttl=0), {"W1", "W3"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
