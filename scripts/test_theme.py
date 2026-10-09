#!/usr/bin/env python3
"""The project's colour: `.dfs/theme`, else one made from the project's name.

Run:  python3 <scripts>/test_theme.py
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import paths as dfs_paths  # noqa: E402


def kind_png(tail: bytes) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + tail


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

    def test_a_background_per_mode_is_kept_apart_from_the_project_colour(self):
        none = dict(light="", dark="")
        self.assertEqual(dfs_paths.read_background(), none)
        line = dfs_paths.project_colour()
        self.assertEqual(dfs_paths.write_background(light="#FFF2CC"), dict(light="#fff2cc", dark=""))
        self.assertEqual(dfs_paths.write_background(dark="#10243a"), dict(light="#fff2cc", dark="#10243a"))
        self.assertEqual(dfs_paths.read_background(), dict(light="#fff2cc", dark="#10243a"))
        self.assertEqual(dfs_paths.background_file().read_text(), "light #fff2cc\ndark #10243a\n")
        self.assertEqual(dfs_paths.project_colour(), line)
        self.assertFalse(dfs_paths.theme_file().exists())
        dfs_paths.write_theme("#aa3355")
        self.assertEqual(dfs_paths.read_background(), dict(light="#fff2cc", dark="#10243a"))
        self.assertEqual(dfs_paths.write_background(light=""), dict(light="", dark="#10243a"))
        self.assertEqual(dfs_paths.write_background(dark=""), none)
        self.assertFalse(dfs_paths.background_file().exists())
        self.assertEqual(dfs_paths.project_colour(), "#aa3355")

    def test_what_is_not_a_background_is_refused_and_a_bad_file_is_ignored(self):
        for bad in ("red", "#fff", "#12345g", "fff2cc"):
            with self.assertRaises(ValueError):
                dfs_paths.write_background(light=bad)
        self.assertFalse(dfs_paths.background_file().exists())
        dfs_paths.background_file().write_text("blue\nlight red\ndark #10243a\nsepia #ffffff\n")
        self.assertEqual(dfs_paths.read_background(), dict(light="", dark="#10243a"))
        dfs_paths.background_file().write_text("#fff2cc\n")             # a bare colour is both modes
        self.assertEqual(dfs_paths.read_background(), dict(light="#fff2cc", dark="#fff2cc"))

    def test_the_text_on_any_background_reads(self):
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
        self.assertGreater(n, 4000)
        dfs_paths.write_background(dark="#10243a")
        pal = dfs_paths.background_palettes()
        self.assertEqual((pal["light"], pal["dark"]["paper"]), ({}, "#10243a"))

    def test_a_background_image_round_trips_and_leaves_the_colours_alone(self):
        kinds = {"image/png": b"\x89PNG\r\n\x1a\n" + b"x" * 20, "image/jpeg": b"\xff\xd8\xff\xe0" + b"x" * 20,
                 "image/webp": b"RIFF\0\0\0\0WEBPVP8 ", "image/gif": b"GIF89a" + b"x" * 20}
        self.assertIsNone(dfs_paths.read_background_image())
        self.assertEqual(dfs_paths.background_image_version(), "")
        dfs_paths.write_background(light="#fff2cc")
        dfs_paths.write_theme("#aa3355")
        for kind, data in kinds.items():
            ver = dfs_paths.write_background_image(data)
            self.assertTrue(ver)
            self.assertEqual(dfs_paths.read_background_image(), (data, kind))
            self.assertEqual(dfs_paths.background_image_version(), ver)
        self.assertEqual(dfs_paths.read_background(), dict(light="#fff2cc", dark=""))
        self.assertEqual(dfs_paths.read_theme(), "#aa3355")
        self.assertEqual(dfs_paths.write_background_image(b""), "")
        self.assertFalse(dfs_paths.background_image_file().exists())
        self.assertIsNone(dfs_paths.read_background_image())
        self.assertEqual(dfs_paths.write_background_image(None), "")        # clearing nothing is fine

    def test_each_mode_shows_its_own_picture_else_the_one_for_both(self):
        a, b, c = kind_png(b"a"), kind_png(b"b"), kind_png(b"c")
        both = dfs_paths.background_image_file()
        dfs_paths.write_background_image(a, "dark")
        self.assertEqual((dfs_paths.read_background_image("dark"), dfs_paths.read_background_image("light")), ((a, "image/png"), None))
        self.assertEqual(dfs_paths.background_image_versions()["light"], "")
        both.write_bytes(b)                                     # a picture put there by hand serves the mode with none
        self.assertEqual((dfs_paths.read_background_image("dark")[0], dfs_paths.read_background_image("light")[0]), (a, b))
        dfs_paths.write_background_image(c, "dark")             # changing one mode hands the shared picture to the other
        self.assertFalse(both.exists())
        self.assertEqual([dfs_paths.read_background_image(m)[0] for m in dfs_paths.MODES], [b, c])
        both.write_bytes(a)
        self.assertEqual(dfs_paths.write_background_image(b"", "light"), "")     # clearing a mode keeps the other's picture
        self.assertEqual((dfs_paths.read_background_image("light"), dfs_paths.read_background_image("dark")[0]), (None, c))
        dfs_paths.write_background_image(b)                     # no mode sets both, replacing the per-mode ones
        self.assertEqual([dfs_paths.read_background_image(m)[0] for m in dfs_paths.MODES], [b, b])
        self.assertFalse(dfs_paths.background_image_file("dark").exists())
        dfs_paths.write_background_image(None)
        self.assertEqual(dfs_paths.background_image_versions(), dict(light="", dark=""))
        with self.assertRaises(ValueError):
            dfs_paths.write_background_image(a, "dusk")

    def test_a_new_picture_is_a_new_version(self):
        a = dfs_paths.write_background_image(kind_png(b"a"))
        os.utime(dfs_paths.background_image_file(), ns=(1, 1))
        self.assertNotEqual(a, dfs_paths.background_image_version())

    def test_what_is_not_a_picture_is_refused_and_a_stray_file_is_ignored(self):
        for bad in (b"just text", b"<svg xmlns='http://www.w3.org/2000/svg'/>", b"RIFF\0\0\0\0WAVEfmt ",
                    kind_png(b"x") + b"\0" * dfs_paths.IMAGE_MAX):
            with self.assertRaises(ValueError):
                dfs_paths.write_background_image(bad)
        self.assertFalse(dfs_paths.background_image_file().exists())
        dfs_paths.background_image_file().write_text("not a picture\n")
        self.assertIsNone(dfs_paths.read_background_image())
        self.assertEqual(dfs_paths.background_image_version(), "")
        dfs_paths.background_image_file().write_bytes(kind_png(b"x") + b"\0" * dfs_paths.IMAGE_MAX)
        self.assertIsNone(dfs_paths.read_background_image())

    def test_the_made_colours_hold_white_text(self):
        def lum(c):
            ch = [int(c[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            ch = [x / 12.92 if x <= .03928 else ((x + .055) / 1.055) ** 2.4 for x in ch]
            return .2126 * ch[0] + .7152 * ch[1] + .0722 * ch[2]
        for n in ["project-%d" % i for i in range(500)]:
            self.assertGreaterEqual(1.05 / (lum(dfs_paths.colour_from_name(n)) + .05), 4.5, n)


if __name__ == "__main__":
    unittest.main()
