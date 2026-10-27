#!/usr/bin/env python3
"""GitHub mode: a finished task held for its PR, opened and watched (README, "GitHub mode").

Run:  python3 <scripts>/test_github.py
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import paths as dfs_paths  # noqa: E402


class Switch(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self._wr = dfs_paths.work_root
        dfs_paths.work_root = lambda: self.root

    def tearDown(self):
        dfs_paths.work_root = self._wr
        shutil.rmtree(self.root, ignore_errors=True)

    def test_off_until_written_and_off_again_when_removed(self):
        self.assertEqual(dfs_paths.read_github(), "")
        self.assertEqual(dfs_paths.write_github("upstream"), "upstream")
        self.assertEqual(dfs_paths.read_github(), "upstream")
        self.assertEqual(dfs_paths.write_github(None), "")
        self.assertFalse(dfs_paths.github_file().exists())
        self.assertEqual(dfs_paths.read_github(), "")

    def test_an_empty_remote_is_origin(self):
        self.assertEqual(dfs_paths.write_github(""), "origin")
        dfs_paths.github_file().write_text("\n")
        self.assertEqual(dfs_paths.read_github(), "origin")

    def test_a_remote_that_is_not_a_name_is_refused(self):
        for bad in ("-x", "a b", "../x", "--upload-pack=sh"):
            with self.assertRaises(ValueError):
                dfs_paths.write_github(bad)
        self.assertFalse(dfs_paths.github_file().exists())

    def test_the_switch_is_part_of_the_roadmap(self):
        self.assertIn(dfs_paths.github_file(), dfs_paths.roadmap_parts())


if __name__ == "__main__":
    unittest.main()
