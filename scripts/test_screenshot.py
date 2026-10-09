#!/usr/bin/env python3
"""A finished task that touched UI files must show its change: check.py refuses a new or
changed Summary that embeds no screenshot (or names one that is not there), unless it says
`No screenshot:` and why.

Run:  python3 <scripts>/test_screenshot.py
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
import check as dfs_check  # noqa: E402
import paths as dfs_paths  # noqa: E402

TASK = """# W1 · a task

%s## Goal

A goal.

## Tree

### W1.1 · the first step
Status: confirmed
Approach: change the page so the bar is wider and check it in the browser
Determination: done

## Log
"""
SHOT = "![the wider bar](.dfs/artefacts/0a1b2c.png)"


class Screenshot(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self._wr = dfs_paths.work_root
        dfs_paths.work_root = lambda: self.root
        self.git("init", "-q", "-b", "main")
        (self.root / ".dfs" / "items").mkdir(parents=True)
        (self.root / ".dfs" / "artefacts").mkdir()
        (self.root / ".dfs" / "ROADMAP.md").write_text("# the law\n")
        self.task(None)
        self.commit("init")

    def tearDown(self):
        dfs_paths.work_root = self._wr
        shutil.rmtree(self.root, ignore_errors=True)

    def git(self, *args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                       cwd=self.root, check=True, capture_output=True)

    def commit(self, subject):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", subject)

    def task(self, summary):
        (self.root / ".dfs" / "items" / "W1.md").write_text(
            TASK % ("## Summary\n\n%s\n\n" % summary if summary else ""))

    def touch(self, name, subject):
        (self.root / name).write_text("x\n")
        self.commit(subject)

    def check(self, summary):
        """check.py --staged on a Summary being added: (exit code, what it printed)."""
        self.task(summary)
        self.git("add", ".dfs")
        old, sys.argv = sys.argv, ["check.py", "--staged"]
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                rc = dfs_check.main()
        finally:
            sys.argv = old
        return rc, out.getvalue()

    def shot(self):
        (self.root / ".dfs" / "artefacts" / "0a1b2c.png").write_bytes(b"\x89PNG\xff\xfe")

    def test_a_ui_task_with_no_screenshot_is_refused(self):
        self.touch("page.html", "W1.1: the bar is wider")
        rc, out = self.check("The bar is wider.")
        self.assertEqual(rc, 1, out)
        self.assertIn("embeds no screenshot", out)
        self.assertIn("page.html", out)

    def test_a_ui_task_with_a_screenshot_is_accepted(self):
        self.touch("page.html", "W1.1: the bar is wider")
        self.shot()
        rc, out = self.check("The bar is wider. " + SHOT)
        self.assertEqual(rc, 0, out)

    def test_a_screenshot_that_is_not_there_is_refused(self):
        self.touch("page.html", "W1.1: the bar is wider")
        rc, out = self.check("The bar is wider. " + SHOT)
        self.assertEqual(rc, 1, out)
        self.assertIn("0a1b2c.png, which does not exist", out)

    def test_no_screenshot_with_a_reason_is_accepted(self):
        self.touch("page.html", "W1.1: the bar is wider")
        rc, out = self.check("The bar is wider.\n\nNo screenshot: no browser would start.")
        self.assertEqual(rc, 0, out)

    def test_a_task_that_touched_no_ui_file_needs_none(self):
        self.touch("tool.py", "W1.1: the bar is wider")
        rc, out = self.check("The bar is wider.")
        self.assertEqual(rc, 0, out)

    def test_another_tasks_ui_commit_does_not_count(self):
        self.touch("page.html", "W2.1: something else")
        rc, out = self.check("The bar is wider.")
        self.assertEqual(rc, 0, out)

    def test_a_ui_file_inside_dfs_does_not_count(self):
        self.touch(".dfs/artefacts/map.html", "W1.1: a page for the raise")
        rc, out = self.check("The bar is wider.")
        self.assertEqual(rc, 0, out)

    def test_a_summary_already_committed_is_not_judged_again(self):
        self.touch("page.html", "W1.1: the bar is wider")
        self.task("The bar is wider.")
        self.commit("W1.1: summary")      # committed before the rule: history
        rc, out = self.check("The bar is wider.")
        self.assertEqual(rc, 0, out)


if __name__ == "__main__":
    unittest.main()
