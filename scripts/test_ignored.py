#!/usr/bin/env python3
"""A gitignored `.dfs`: detected, seen by the idle check, and not judged against a HEAD
that holds none of it.

Run:  python3 <scripts>/test_ignored.py
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
import state as dfs_state  # noqa: E402

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
        sys.argv = ["check.py"]
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                rc = dfs_check.main()
        finally:
            sys.argv = old
        self.assertEqual(rc, 0, out.getvalue())


class Hidden(unittest.TestCase):
    """`dfs_paths.hidden()`: the roadmap files git will not add, by the rule that hides
    them, except a roadmap kept as planning by a rule naming it."""

    tearDown = Ignored.tearDown

    def setUp(self):
        Ignored.setUp(self)
        (self.root / ".dfs" / "items" / "W1.md").write_text(TASK)
        (self.root / ".dfs" / "artefacts").mkdir()
        (self.root / ".dfs" / "artefacts" / "a.html").write_text("<p>a\n")
        (self.root / ".dfs" / ".gitignore").write_text("runs/\n")
        (self.root / ".dfs" / "runs").mkdir()
        (self.root / ".dfs" / "runs" / "log.json").write_text("{}")

    def ignore(self, rules):
        (self.root / ".gitignore").write_text(rules)

    def rules(self):
        return sorted((h["path"], h["source"], h["line"], h["pattern"])
                      for h in dfs_paths.hidden())

    def test_nothing_when_git_can_add_it_all(self):
        self.ignore("__pycache__\n")
        (self.root / ".dfs" / "__pycache__").mkdir()
        (self.root / ".dfs" / "__pycache__" / "x.pyc").write_text("")
        # runs/ is the roadmap's own rule, and a cache is not the roadmap
        self.assertEqual(self.rules(), [])

    def test_a_blanket_dot_rule_hides_the_directory(self):
        self.ignore(".*\n!/.gitignore\n")
        self.assertEqual(self.rules(), [(".dfs/", ".gitignore", "1", ".*")])

    def test_a_pattern_hides_the_files_it_matches(self):
        self.ignore("*.html\n")
        self.assertEqual(self.rules(), [(".dfs/artefacts/a.html", ".gitignore", "1", "*.html")])

    def test_a_rule_naming_the_roadmap_keeps_it_as_planning(self):
        for rule in (".dfs/\n", "/.dfs\n", ".dfs\n"):
            self.ignore(rule)
            self.assertEqual(self.rules(), [], rule)

    def test_files_forced_past_the_rule_are_half_a_roadmap(self):
        self.ignore(".dfs/\n")
        subprocess.run(["git", "add", "-f", ".dfs/ROADMAP.md"], cwd=self.root, check=True)
        self.assertEqual([p for p, *_ in self.rules()],
                         [".dfs/artefacts/", ".dfs/items/"])

    def test_the_message_names_the_rule(self):
        self.ignore(".*\n")
        said = dfs_paths.hidden_message(dfs_paths.hidden())
        self.assertIn(".gitignore:1  .*   hides .dfs/", said)
        self.assertIn("1 roadmap path,", said)

    def test_outside_git_nothing(self):
        shutil.rmtree(self.root / ".git")
        self.assertEqual(dfs_paths.hidden(), [])


if __name__ == "__main__":
    unittest.main()
