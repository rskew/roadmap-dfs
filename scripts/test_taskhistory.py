#!/usr/bin/env python3
"""The history file's parser and writer (taskhistory.py), on the fixture task W9.

Run:  python3 roadmap-dfs/scripts/test_taskhistory.py
"""
import difflib
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import taskhistory as th  # noqa: E402

FIX = HERE / "fixtures" / "history"
TEXT = (FIX / "W9.history.md").read_text()


class History(unittest.TestCase):
    def test_parse_then_render_is_the_same_bytes(self):
        self.assertEqual(th.render(th.parse(TEXT, "W9")), TEXT)
        odd = TEXT.replace("\n\n### W9.4", "\n\n\n### W9.4").rstrip("\n")    # extra blank, no final newline
        self.assertEqual(th.render(th.parse(odd, "W9")), odd)

    def test_every_node_comes_back_folded(self):
        nodes = {n["id"]: n for n in th.parse(TEXT, "W9")["nodes"]}
        self.assertEqual(list(nodes), ["W9.%d" % i for i in range(1, 7)])
        self.assertEqual({k: v["status"] for k, v in nodes.items()},
                         dict(zip(nodes, ["confirmed"] * 3 + ["open", "parked", "refuted"])))   # last Status wins
        self.assertEqual(nodes["W9.3"]["supersedes"], ["W9.2"])
        self.assertEqual(nodes["W9.3"]["tags"], ["charger", "power"])
        self.assertEqual(nodes["W9.4"]["parent"], "W9.3")

    def test_evidence_is_returned_for_every_node_that_has_it(self):
        nodes = {n["id"]: n for n in th.parse(TEXT, "W9")["nodes"]}
        self.assertEqual(len(nodes["W9.3"]["evidence"]), 2)
        self.assertEqual(nodes["W9.6"]["evidence"], ["against: the cold test jammed it twice in ten runs."])
        self.assertEqual(nodes["W9.4"]["evidence"], [])

    def test_a_later_line_replaces_a_single_valued_field_and_evidence_adds_up(self):
        t = th.append(TEXT, "W9.4", ["Approach: Use the TPS61023.", "Evidence:", "- for: it fits."])
        n = [x for x in th.parse(t, "W9")["nodes"] if x["id"] == "W9.4"][0]
        self.assertEqual(n["fields"]["Approach"], "Use the TPS61023.")
        self.assertEqual(n["evidence"], ["for: it fits."])

    def test_an_append_changes_only_the_end_of_one_section(self):
        t = th.append(TEXT, "W9.4", ["Status: confirmed", "Determination: Confirmed: it fits."])
        d = [l for l in difflib.ndiff(TEXT.splitlines(), t.splitlines()) if l[0] in "+-"]
        self.assertEqual(d, ["+ Status: confirmed", "+ Determination: Confirmed: it fits."])
        i = t.index("Determination: Confirmed: it fits.")
        self.assertTrue(t[i:].split("\n", 2)[1] == "" and t[i:].split("\n", 3)[2].startswith("### W9.5"))
        last = th.append(TEXT, "W9.6", ["Corrects: none."])          # the last section, with no blank after it
        self.assertEqual(last, TEXT + "Corrects: none.\n")

    def test_append_to_a_missing_node_is_refused(self):
        with self.assertRaises(KeyError):
            th.append(TEXT, "W9.99", ["Status: open"])

    def test_a_node_of_another_task_is_refused(self):
        with self.assertRaises(ValueError):
            th.parse(TEXT + "\n### W8.1 · stray\nStatus: open\n", "W9")


if __name__ == "__main__":
    unittest.main()
