#!/usr/bin/env python3
"""The item tree: the lower half of the TUI's item pane, where a tree is read and
answered. Tab (or `r`) gives it the keys; Tab or esc hands them back to the item list.

`--review` printed a tree as one line per node with its whole determination on it,
and a correction meant typing a node id copied out of that wall. The pane lays the
tree out a line per node, opens the node under the cursor, and makes the review's
three writes (answer, correct, accept) where the node is. What is checked here is
the part that can mislead: which rows show, what a fold hides, what an opened node
says, and that each write lands in the task's Log exactly as the author typed it.

Run:  python3 roadmap-dfs/scripts/test_dfs_review_tree.py
"""
import importlib.util
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

TUI_SPEC = importlib.util.spec_from_file_location("dfs_tui", HERE / "dfs_tui.py")
TUI = importlib.util.module_from_spec(TUI_SPEC)
TUI_SPEC.loader.exec_module(TUI)
import dfs_tree   # noqa: E402

TREE = """# W1 · A task

## Goal

Make the thing, so that the
other thing stops happening.

- one list item
- another

## Background

Why this came up, at length.

## Tree

### W1.1 · The first slice
Status: confirmed
Approach: cut the slice. Verbatim in the archive.
Evidence:
- for: it ran.
Determination: confirmed. It holds.

### W1.2 · Under the first
Parent: W1.1
Status: confirmed
Approach: go deeper.
Determination: confirmed.

### W1.3 · Deeper still
Parent: W1.2
Status: open
Approach: the open question.

### W1.4 · A refuted sibling
Status: refuted
Approach: try the other way.
Determination: refuted.

## Log

- 2026-09-27T01:00:00Z · session · work · run-1
- 2026-09-27T01:05:00Z · raise · W1.3
  Which way should W1.3 go?
- 2026-09-27T01:06:00Z · raise
  Ten sessions without you.
"""

ARCHIVE = """# W1 · archive

**W1.1 Plan**: an older wording.

**W1.1 Plan**: cut the slice, and here is the whole of what that meant.
"""


def tree():
    return dfs_tree.parse(TREE, "W1")


class Rows(unittest.TestCase):
    """`tree_entries(t, folded)`: what the pane lists, in order."""

    def test_the_whole_tree_shows_by_default(self):
        rows = TUI.tree_entries(tree())
        self.assertEqual([r["key"] for r in rows],
                         ["section:W1:Goal", "section:W1:Background",
                          "raise:2026-09-27T01:06:00Z", "W1.1", "W1.2", "W1.3", "W1.4"])
        self.assertEqual([r["depth"] for r in rows], [0, 0, 0, 0, 1, 2, 0])

    def test_the_goal_starts_open_and_the_background_folded(self):
        rows = {r["key"]: r for r in TUI.tree_entries(tree())}
        self.assertTrue(rows["section:W1:Goal"]["open_default"])
        self.assertFalse(rows["section:W1:Background"]["open_default"])

    def test_a_missing_section_has_no_row(self):
        t = dfs_tree.parse(TREE.replace("## Background\n\nWhy this came up, at length.\n\n",
                                        ""), "W1")
        self.assertNotIn("section:W1:Background",
                         [r["key"] for r in TUI.tree_entries(t)])

    def test_prose_rejoins_hard_wrapped_lines_and_keeps_lists(self):
        self.assertEqual(TUI.prose_blocks("Make the thing, so that the\nother thing.\n\n"
                                          "- one\n- two\n  continued"),
                         ["Make the thing, so that the other thing.", "",
                          "- one", "- two continued"])

    def test_a_raise_on_a_node_rides_on_its_row(self):
        rows = {r["key"]: r for r in TUI.tree_entries(tree())}
        self.assertEqual([r["ts"] for r in rows["W1.3"]["raises"]],
                         ["2026-09-27T01:05:00Z"])
        self.assertEqual(rows["W1.1"]["raises"], [])

    def test_a_fold_hides_the_descendants_and_keeps_the_row(self):
        rows = TUI.tree_entries(tree(), {"W1.1"})
        self.assertEqual([r["key"] for r in rows if r["kind"] == "node"],
                         ["W1.1", "W1.4"])
        self.assertTrue({r["key"]: r for r in rows}["W1.1"]["folded"])

    def test_a_fold_never_hides_a_raise(self):
        # ⚠️ The first real tree this ran on had its only open raise four levels
        # under a folded node, and the fold took it off the screen.
        rows = {r["key"]: r for r in TUI.tree_entries(tree(), {"W1.1"})}
        self.assertEqual(rows["W1.1"]["raises_below"], 1)
        rows = {r["key"]: r for r in TUI.tree_entries(tree())}
        self.assertEqual(rows["W1.1"]["raises_below"], 0, "nothing is hidden when open")

    def test_folding_a_leaf_is_not_a_fold(self):
        rows = {r["key"]: r for r in TUI.tree_entries(tree(), {"W1.3"})}
        self.assertFalse(rows["W1.3"]["folded"])


class Detail(unittest.TestCase):
    """`node_detail`: an opened node, as a reviewer reads it."""

    def test_a_plan_moved_to_the_archive_reads_as_its_whole_self(self):
        nd = dfs_tree.by_id(tree())["W1.1"]
        got = dict(TUI.node_detail(nd, TUI.archived_fields(ARCHIVE, "W1.1")))
        self.assertEqual(got["Approach (archive)"],
                         "cut the slice, and here is the whole of what that meant.",
                         "the latest archived wording, not the stub")
        self.assertNotIn("Approach", got)

    def test_a_plan_load_put_back_is_labelled_as_the_archives(self):
        nd = dfs_tree.by_id(tree())["W1.1"]
        nd = dict(nd, fields=dict(nd["fields"], Approach="the whole of it"),
                  from_archive={"Approach"})
        got = dict(TUI.node_detail(nd))
        self.assertEqual(got["Approach (archive)"], "the whole of it")
        self.assertNotIn("Approach", got)

    def test_a_plan_still_in_the_task_file_is_not_replaced(self):
        nd = dfs_tree.by_id(tree())["W1.2"]
        got = dict(TUI.node_detail(nd, {"Approach": "something else"}))
        self.assertEqual(got["Approach"], "go deeper.")

    def test_the_order_is_plan_evidence_determination_commits(self):
        nd = dfs_tree.by_id(tree())["W1.1"]
        labels = [l for l, _ in TUI.node_detail(
            nd, {}, [("abcdef1234", "W1.1: the slice", "2026-09-27T01:00:00Z")])]
        self.assertEqual(labels, ["Approach", "Evidence", "Determination", "Commit"])

    def test_the_editor_frame_is_dropped_and_the_words_kept(self):
        self.assertEqual(TUI.strip_comments("yes, go left\n\n# the raise\n#  said"),
                         "yes, go left")


class Pane(unittest.TestCase):
    """The pane's keys, with the tree and the Log stubbed."""

    def setUp(self):
        # Colours need a real terminal; the attribute is all these tests ignore.
        real = TUI.curses.color_pair
        TUI.curses.color_pair = lambda n: 0
        self.addCleanup(setattr, TUI.curses, "color_pair", real)
        real_log = dfs_tree.append_log
        self.logged = []
        dfs_tree.append_log = lambda task, kind, args=(), body="": (
            self.logged.append((task, kind, list(args), body)))
        self.addCleanup(setattr, dfs_tree, "append_log", real_log)

        ui = object.__new__(TUI.UI)
        ui.msg, ui.pane, ui.scroll, ui.follow, ui.body_h = "", "item", 0, False, 20
        ui.focus = "list"
        ui.sel, ui.agent, ui._sel_row = 0, "claude", None
        ui.tree_sel, ui.tree_item = 0, None
        ui.tree_folded, ui.tree_open = set(), set()
        ui.walk_on = False
        ui.data = {"items": [dict(id="W1", goal="Make the thing.", status="blocked",
                                  why="open raise")]}
        t = tree()
        ui.tree_of = lambda task: (t, ARCHIVE, {})
        ui.reload = lambda note="": None
        ui.composed = []
        ui.typed = ""
        ui.compose = lambda frame: (ui.composed.append(frame) or ui.typed)
        ui.prompted = ""
        ui.prompt = lambda label, default="": ui.prompted or default
        ui.live = lambda mode="work": []
        self.ui = ui

    def to(self, key):
        keys = [r["key"] for r in self.ui.tree_rows()]
        self.ui.tree_sel = keys.index(key)

    def test_the_goal_shows_above_the_tree_and_folds(self):
        ui = self.ui
        ui.open_tree()
        text = "\n".join(l for l, *_ in ui.lines_tree(100))
        self.assertIn("Make the thing, so that the other thing stops happening.", text)
        self.assertLess(text.index("Goal"), text.index("W1.1"))
        self.assertNotIn("Why this came up", text, "Background starts folded")
        self.to("section:W1:Goal")
        ui.act(ord(" "))
        text = "\n".join(l for l, *_ in ui.lines_tree(100))
        self.assertNotIn("stops happening", text)
        self.assertIn("▸ Goal", text)
        self.to("section:W1:Background")
        ui.act(10)
        text = "\n".join(l for l, *_ in ui.lines_tree(100))
        self.assertIn("Why this came up, at length.", text)

    def test_answer_and_correct_on_a_section_row_do_nothing(self):
        ui = self.ui
        ui.open_tree()
        self.to("section:W1:Goal")
        ui.act(ord("a"))
        ui.act(ord("f"))
        self.assertEqual(self.logged, [])

    def test_r_opens_the_tree_and_enter_opens_a_node(self):
        ui = self.ui
        ui.act(ord("r"))
        self.assertTrue(ui.tree_focused())
        ui.lines_tree(100)
        self.to("W1.1")
        ui.act(10)
        text = "\n".join(l for l, *_ in ui.lines_tree(100))
        self.assertIn("Approach (archive): cut the slice, and here is the whole", text)
        self.assertIn("Determination: confirmed. It holds.", text)
        ui.act(10)
        text = "\n".join(l for l, *_ in ui.lines_tree(100))
        self.assertNotIn("Determination: confirmed. It holds.", text, "enter closes it")

    def test_space_folds_in_the_tree_and_still_pages_elsewhere(self):
        ui = self.ui
        ui.open_tree()
        self.to("W1.1")
        ui.act(ord(" "))
        self.assertEqual(ui.tree_folded, {"W1.1"})
        text = "\n".join(l for l, *_ in ui.lines_tree(100))
        self.assertNotIn("W1.3 ○", text, "the fold hides what is under it")
        self.assertIn("W1.1 ✓ The first slice  ● 1 raise below", text)
        ui.act(9)
        ui.scroll = 0
        ui.act(ord(" "))
        self.assertEqual(ui.scroll, 19, "space is a page when the list has the keys")
        self.assertEqual(ui.tree_folded, {"W1.1"})

    def test_a_answers_the_raise_on_the_node_under_the_cursor(self):
        ui = self.ui
        ui.open_tree()
        self.to("W1.3")
        ui.typed = "go left"
        ui.act(ord("a"))
        self.assertEqual(self.logged,
                         [("W1", "answer", ["2026-09-27T01:05:00Z"], "go left")])
        self.assertIn("Which way should W1.3 go?", ui.composed[0])

    def test_a_answers_a_raise_on_the_task_from_its_own_row(self):
        ui = self.ui
        ui.open_tree()
        self.to("raise:2026-09-27T01:06:00Z")
        ui.typed = "carry on"
        ui.act(ord("a"))
        self.assertEqual(self.logged,
                         [("W1", "answer", ["2026-09-27T01:06:00Z"], "carry on")])

    def test_an_empty_answer_writes_nothing(self):
        ui = self.ui
        ui.open_tree()
        self.to("W1.3")
        ui.act(ord("a"))
        self.assertEqual(self.logged, [])
        self.assertIn("stays open", ui.msg)

    def test_a_on_a_node_with_no_raise_says_so(self):
        ui = self.ui
        ui.open_tree()
        self.to("W1.2")
        ui.act(ord("a"))
        self.assertEqual(self.logged, [])
        self.assertIn("no open raise here", ui.msg)

    def test_f_corrects_the_node_under_the_cursor(self):
        ui = self.ui
        ui.open_tree()
        self.to("W1.4")
        ui.prompted, ui.typed = "c", "it was right; build on it"
        ui.act(ord("f"))
        self.assertEqual(self.logged, [("W1", "correct", ["W1.4", "confirmed"],
                                        "it was right; build on it")])
        self.assertIn("### W1.4 · A refuted sibling", ui.composed[0])

    def test_a_cancelled_verdict_corrects_nothing(self):
        ui = self.ui
        ui.open_tree()
        self.to("W1.4")
        ui.typed = "never asked for"
        ui.act(ord("f"))
        self.assertEqual(self.logged, [])
        self.assertEqual(ui.composed, [], "the editor is not opened without a verdict")

    def test_esc_hands_the_keys_back_to_the_list(self):
        ui = self.ui
        ui.open_tree()
        ui.act(27)
        self.assertEqual((ui.pane, ui.focus), ("item", "list"))

    def test_tab_swaps_the_keys_between_the_list_and_the_tree(self):
        ui = self.ui
        ui.act(9)
        self.assertTrue(ui.tree_focused())
        ui.act(ord("j"))
        self.assertEqual(ui.tree_sel, 1, "j moves the tree's cursor")
        self.assertEqual(ui.sel, 0, "and not the item")
        ui.act(9)
        self.assertFalse(ui.tree_focused())
        self.assertEqual(ui.tree_sel, 1, "the tree keeps its place")

    def test_c_is_chat_while_the_list_has_the_keys(self):
        ui = self.ui
        ran = []
        ui.shell = lambda argv, pause=True: ran.append(argv)
        ui.act(ord("c"))
        self.assertEqual(self.logged, [])
        self.assertIn("--chat", ran[0])

    def test_c_is_chat_while_the_tree_has_the_keys_too(self):
        # ⚠️ It was the correction there: one key, two writes, chosen by focus.
        ui = self.ui
        ran = []
        ui.shell = lambda argv, pause=True: ran.append(argv)
        ui.open_tree()
        self.to("W1.4")
        ui.act(ord("c"))
        self.assertEqual(self.logged, [])
        self.assertIn("--chat", ran[0])

    def test_f_outside_the_tree_corrects_nothing_and_says_where_to_go(self):
        ui = self.ui
        ran = []
        ui.shell = lambda argv, pause=True: ran.append(argv)
        ui.act(ord("f"))
        self.assertEqual((self.logged, ran), ([], []))
        self.assertIn("tab to the tree", ui.msg)

    def test_the_tree_cursor_is_drawn_only_while_it_has_the_keys(self):
        ui = self.ui
        lines = [l for l, *_ in ui.lines_tree(100)]
        self.assertFalse([l for l in lines if l.startswith(">")])
        ui.act(9)
        lines = [l for l, *_ in ui.lines_tree(100)]
        self.assertEqual(len([l for l in lines if l.startswith(">")]), 1)


if __name__ == "__main__":
    unittest.main()
