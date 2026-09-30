#!/usr/bin/env python3
"""A gitignored `.dfs`: detected, seen by the idle check, and not judged against a HEAD
that holds none of it.

Run:  python3 <scripts>/test_dfs_ignored.py
"""
import contextlib
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dfs_check  # noqa: E402
import dfs_paths  # noqa: E402
import dfs_state  # noqa: E402

TASK = """# W1 · a task

## Goal

A goal.

## Tree

### W1.1 · the first step
Status: confirmed
Approach: do it
Determination: done

## Log
"""


class Ignored(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self._wr = dfs_paths.work_root
        dfs_paths.work_root = lambda: self.root
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.root, check=True)
        (self.root / "app.txt").write_text("app\n")
        (self.root / ".dfs" / "items").mkdir(parents=True)
        (self.root / ".dfs" / "ROADMAP.md").write_text("# the law\n")

    def tearDown(self):
        dfs_paths.work_root = self._wr
        shutil.rmtree(self.root, ignore_errors=True)

    def commit(self, ignore):
        (self.root / ".gitignore").write_text(".dfs/\n" if ignore else "")
        subprocess.run("git add -A && git -c user.name=t -c user.email=t@t commit -qm init",
                       shell=True, cwd=self.root, check=True)

    def test_detected_only_when_ignored(self):
        self.commit(ignore=False)
        self.assertFalse(dfs_paths.ignored())
        self.commit(ignore=True)
        self.assertTrue(dfs_paths.ignored())

    def test_a_task_file_write_is_not_idle(self):
        self.commit(ignore=True)
        before = dfs_state.fingerprint()
        (self.root / ".dfs" / "items" / "W1.md").write_text(TASK)
        self.assertNotEqual(before, dfs_state.fingerprint())

    def test_runs_and_outside_dfs_still_left_out(self):
        self.commit(ignore=True)
        before, outside = dfs_state.fingerprint(), dfs_state.fingerprint(outside_dfs=True)
        (self.root / ".dfs" / "runs").mkdir()
        (self.root / ".dfs" / "runs" / "log.json").write_text("{}")
        self.assertEqual(before, dfs_state.fingerprint())
        (self.root / ".dfs" / "items" / "W1.md").write_text(TASK)
        self.assertEqual(outside, dfs_state.fingerprint(outside_dfs=True))

    def test_check_passes_with_no_head_copy(self):
        self.commit(ignore=True)
        (self.root / ".dfs" / "items" / "W1.md").write_text(TASK)
        old = sys.argv
        sys.argv = ["dfs_check.py"]
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                rc = dfs_check.main()
        finally:
            sys.argv = old
        self.assertEqual(rc, 0, out.getvalue())


if __name__ == "__main__":
    unittest.main()
