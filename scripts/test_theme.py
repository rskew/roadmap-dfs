#!/usr/bin/env python3
"""The project's colour: `.dfs/theme`, else one made from the project's name.

Run:  python3 <scripts>/test_theme.py
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import paths as dfs_paths  # noqa: E402


class Theme(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self._wr = dfs_paths.work_root
        dfs_paths.work_root = lambda: self.root
        dfs_paths.write_title("Gateway")

    def tearDown(self):
        dfs_paths.work_root = self._wr
        shutil.rmtree(self.root, ignore_errors=True)

    def test_a_name_without_a_choice_gets_the_same_colour_every_time(self):
        a = dfs_paths.project_colour()
        self.assertRegex(a, r"^#[0-9a-f]{6}$")
        self.assertEqual(a, dfs_paths.project_colour())
        self.assertFalse(dfs_paths.theme_file().exists())

    def test_different_names_give_different_colours_and_a_rename_moves_it(self):
        a = dfs_paths.project_colour()
        dfs_paths.write_title("Billing")
        self.assertNotEqual(a, dfs_paths.project_colour())

    def test_a_chosen_colour_is_kept_through_a_rename_and_clearing_it_returns_to_the_name(self):
        self.assertEqual(dfs_paths.write_theme("#AA3355"), "#aa3355")
        dfs_paths.write_title("Billing")
        self.assertEqual(dfs_paths.project_colour(), "#aa3355")
        dfs_paths.write_theme("")
        self.assertEqual(dfs_paths.project_colour(), dfs_paths.colour_from_name("Billing"))

    def test_what_is_not_a_colour_is_refused_and_a_bad_file_is_ignored(self):
        for bad in ("red", "#fff", "#12345g", "aa3355"):
            with self.assertRaises(ValueError):
                dfs_paths.write_theme(bad)
        self.assertFalse(dfs_paths.theme_file().exists())
        dfs_paths.theme_file().write_text("blue\n")
        self.assertEqual(dfs_paths.project_colour(), dfs_paths.colour_from_name("Gateway"))

    def test_a_chosen_background_is_kept_apart_from_the_project_colour(self):
        self.assertEqual(dfs_paths.read_background(), "")
        line = dfs_paths.project_colour()
        self.assertEqual(dfs_paths.write_background("#FFF2CC"), "#fff2cc")
        self.assertEqual(dfs_paths.read_background(), "#fff2cc")
        self.assertEqual(dfs_paths.project_colour(), line)
        self.assertFalse(dfs_paths.theme_file().exists())
        dfs_paths.write_theme("#aa3355")
        self.assertEqual(dfs_paths.read_background(), "#fff2cc")
        self.assertEqual(dfs_paths.write_background(""), "")
        self.assertFalse(dfs_paths.background_file().exists())
        self.assertEqual(dfs_paths.project_colour(), "#aa3355")

    def test_what_is_not_a_background_is_refused_and_a_bad_file_is_ignored(self):
        for bad in ("red", "#fff", "#12345g", "fff2cc"):
            with self.assertRaises(ValueError):
                dfs_paths.write_background(bad)
        self.assertFalse(dfs_paths.background_file().exists())
        dfs_paths.background_file().write_text("blue\n")
        self.assertEqual(dfs_paths.read_background(), "")

    def test_the_text_on_any_background_reads_and_the_mode_follows_it(self):
        self.assertEqual(dfs_paths.background_palette(""), {})
        n = 0
        for r in range(0, 256, 15):
            for g in range(0, 256, 15):
                for b in range(0, 256, 15):
                    bg = "#%02x%02x%02x" % (r, g, b)
                    p = dfs_paths.background_palette(bg)
                    self.assertGreaterEqual(dfs_paths.contrast(bg, p["ink"]), 4.5, bg)
                    self.assertGreaterEqual(dfs_paths.contrast(bg, p["muted"]), 4.5, bg)
                    n += 1
        self.assertEqual(dfs_paths.background_palette("#fff2cc")["mode"], "light")
        self.assertEqual(dfs_paths.background_palette("#10243a")["mode"], "dark")
        self.assertGreater(n, 4000)

    def test_the_made_colours_hold_white_text(self):
        def lum(c):
            ch = [int(c[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            ch = [x / 12.92 if x <= .03928 else ((x + .055) / 1.055) ** 2.4 for x in ch]
            return .2126 * ch[0] + .7152 * ch[1] + .0722 * ch[2]
        for n in ["project-%d" % i for i in range(500)]:
            self.assertGreaterEqual(1.05 / (lum(dfs_paths.colour_from_name(n)) + .05), 4.5, n)


if __name__ == "__main__":
    unittest.main()
