#!/usr/bin/env python3
"""The item tree: the lower half of the TUI's item pane, where a tree is read and
answered. Tab (or `r`) gives it the keys; Tab or esc hands them back to the item list.

`--review` printed a tree as one line per node with its whole determination on it,
and a correction meant typing a node id copied out of that wall. The pane lays the
tree out a line per node, opens the node under the cursor, and makes the review's
three writes (answer, correct, accept) where the node is. What is checked here is
the part that can mislead: which rows show, what a fold hides, what an opened node
says, and that each write lands in the task's Log exactly as the author typed it.

Run:  python3 roadmap-dfs/scripts/test_review_tree.py
"""
import importlib.util
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

TUI_SPEC = importlib.util.spec_from_file_location("dfs_tui", HERE / "tui.py")
TUI = importlib.util.module_from_spec(TUI_SPEC)
TUI_SPEC.loader.exec_module(TUI)
import tree as dfs_tree   # noqa: E402

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
        # A chain (W1.1 → W1.2 → W1.3) stays in one column: only a fork steps in.
        self.assertEqual([r["depth"] for r in rows], [0, 0, 0, 0, 0, 0, 0])

    def test_a_lone_child_is_marked_as_a_chain_step_and_a_fork_or_a_root_is_not(self):
        chain = {r["key"]: r["chain"] for r in TUI.tree_entries(tree()) if r["kind"] == "node"}
        # W1.2 and W1.3 sit at their parent's column, so they say whose child they are;
        # W1.1 and W1.4 have no parent.
        self.assertEqual(chain, {"W1.1": False, "W1.2": True, "W1.3": True, "W1.4": False})
        t = dfs_tree.parse(TREE.replace(
            "### W1.4 · A refuted sibling\nStatus: refuted",
            "### W1.4 · A refuted sibling\nParent: W1.2\nStatus: refuted"), "W1")
        forked = {r["key"]: r["chain"] for r in TUI.tree_entries(t) if r["kind"] == "node"}
        self.assertEqual(forked, {"W1.1": False, "W1.2": True, "W1.3": False, "W1.4": False},
                         "two children step in, which already says they are children")

    def test_a_fork_steps_its_children_in_and_a_chain_does_not(self):
        t = dfs_tree.parse(TREE.replace(
            "### W1.4 · A refuted sibling\nStatus: refuted",
            "### W1.4 · A refuted sibling\nParent: W1.2\nStatus: refuted"), "W1")
        rows = {r["key"]: r["depth"] for r in TUI.tree_entries(t) if r["kind"] == "node"}
        # W1.2 has two children (W1.3, W1.4): both step in. W1.1 → W1.2 does not.
        self.assertEqual(rows, {"W1.1": 0, "W1.2": 0, "W1.3": 1, "W1.4": 1})

    def test_a_finished_task_shows_its_summary_above_the_goal(self):
        done = TREE.replace("Status: open", "Status: confirmed").replace(
            "\n## Goal", "\n## Summary\n\nIt works now: the other thing no longer happens.\n\n## Goal", 1
        ).replace("- 2026-09-27T01:05:00Z · raise · W1.3\n  Which way should W1.3 go?\n", "").replace(
            "- 2026-09-27T01:06:00Z · raise\n  Ten sessions without you.\n", "")
        t = dfs_tree.parse(done, "W1")
        self.assertEqual(dfs_tree.status_of(t)[0], "done")
        rows = TUI.tree_entries(t)
        self.assertEqual([r["key"] for r in rows[:3]],
                         ["section:W1:Summary", "section:W1:Goal", "section:W1:Background"])
        self.assertIn("no longer happens", rows[0]["text"])
        self.assertTrue(rows[0]["open_default"])

    def test_a_finished_task_without_one_says_so_and_an_open_one_shows_none(self):
        done = dfs_tree.parse(TREE.replace("Status: open", "Status: confirmed").replace(
            "- 2026-09-27T01:05:00Z · raise · W1.3\n  Which way should W1.3 go?\n", "").replace(
            "- 2026-09-27T01:06:00Z · raise\n  Ten sessions without you.\n", ""), "W1")
        self.assertEqual(TUI.tree_entries(done)[0]["text"], TUI.SUMMARY_MISSING)
        opened = TREE.replace("\n## Goal", "\n## Summary\n\nAll done.\n\n## Goal", 1)
        self.assertNotIn("section:W1:Summary",
                         [r["key"] for r in TUI.tree_entries(dfs_tree.parse(opened, "W1"))])

    def test_only_a_node_with_a_hypothesis_rests_on_an_assumption(self):
        t = dfs_tree.parse(TREE.replace(
            "Status: confirmed\nApproach: go deeper.",
            "Status: confirmed\nApproach: go deeper.\nHypothesis: the author means v2 only."
        ).replace("Status: refuted\nApproach: try the other way.",
                  "Status: refuted\nApproach: try the other way.\nHypothesis: moot."), "W1")
        self.assertEqual(TUI.assumed_nodes(t), ["W1.2"])
        self.assertEqual(TUI.assumed_nodes(tree()), [], "a plain node states none")

    def test_a_finished_task_shows_its_summary_above_the_goal(self):
        done = TREE.replace("Status: open", "Status: confirmed").replace(
            "\n## Goal", "\n## Summary\n\nIt works now: the other thing no longer happens.\n\n## Goal", 1
        ).replace("- 2026-09-27T01:05:00Z · raise · W1.3\n  Which way should W1.3 go?\n", "").replace(
            "- 2026-09-27T01:06:00Z · raise\n  Ten sessions without you.\n", "")
        t = dfs_tree.parse(done, "W1")
        self.assertEqual(dfs_tree.status_of(t)[0], "done")
        rows = TUI.tree_entries(t)
        self.assertEqual([r["key"] for r in rows[:3]],
                         ["section:W1:Summary", "section:W1:Goal", "section:W1:Background"])
        self.assertIn("no longer happens", rows[0]["text"])
        self.assertTrue(rows[0]["open_default"])

    def test_a_finished_task_without_one_says_so_and_an_open_one_shows_none(self):
        done = dfs_tree.parse(TREE.replace("Status: open", "Status: confirmed").replace(
            "- 2026-09-27T01:05:00Z · raise · W1.3\n  Which way should W1.3 go?\n", "").replace(
            "- 2026-09-27T01:06:00Z · raise\n  Ten sessions without you.\n", ""), "W1")
        self.assertEqual(TUI.tree_entries(done)[0]["text"], TUI.SUMMARY_MISSING)
        opened = TREE.replace("\n## Goal", "\n## Summary\n\nAll done.\n\n## Goal", 1)
        self.assertNotIn("section:W1:Summary",
                         [r["key"] for r in TUI.tree_entries(dfs_tree.parse(opened, "W1"))])

    def test_only_a_node_with_a_hypothesis_rests_on_an_assumption(self):
        t = dfs_tree.parse(TREE.replace(
            "Status: confirmed\nApproach: go deeper.",
            "Status: confirmed\nApproach: go deeper.\nHypothesis: the author means v2 only."
        ).replace("Status: refuted\nApproach: try the other way.",
                  "Status: refuted\nApproach: try the other way.\nHypothesis: moot."), "W1")
        self.assertEqual(TUI.assumed_nodes(t), ["W1.2"])
        self.assertEqual(TUI.assumed_nodes(tree()), [], "a plain node states none")

    def test_the_goal_starts_open_and_the_background_folded(self):
        rows = {r["key"]: r for r in TUI.tree_entries(tree())}
        self.assertTrue(rows["section:W1:Goal"]["open_default"])
        self.assertFalse(rows["section:W1:Background"]["open_default"])

    def test_the_reviewer_notes_are_listed_folded(self):
        noted = TREE.replace("## Log\n", "## Log\n\n- 2026-09-27T02:00:00Z · review · ok\n"
                             "  A minor wording to read.\n", 1)
        rows = {r["key"]: r for r in TUI.tree_entries(dfs_tree.parse(noted, "W1"))}
        self.assertFalse(rows["section:W1:Notes"]["open_default"])

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

    def test_flagged_keeps_only_the_nodes_that_want_the_author(self):
        t = dfs_tree.parse(TREE.replace(
            "### W1.2 · Under the first\nParent: W1.1\nStatus: confirmed\n",
            "### W1.2 · Under the first\nParent: W1.1\nStatus: open\n"
            "Hypothesis: it is so. Wrong if it is not.\n"), "W1")
        rows = TUI.tree_entries(t, {"W1.1"}, flagged=True)
        nodes = [r for r in rows if r["kind"] == "node"]
        # W1.2 (assumption) and W1.3 (open raise) stay; W1.1 and W1.4 go, and the
        # fold on the dropped W1.1 does not hide what is under it.
        self.assertEqual([r["key"] for r in nodes], ["W1.2", "W1.3"])
        self.assertEqual([r["kids"] for r in nodes], [0, 0])
        self.assertEqual([r["key"] for r in rows if r["kind"] != "node"],
                         ["section:W1:Goal", "section:W1:Background",
                          "raise:2026-09-27T01:06:00Z"])

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
        ui.tree_folded, ui.tree_open, ui.tree_flagged = set(), set(), False
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
        ui.choose = lambda label, keys: ui.prompted[:1] if ui.prompted[:1] in keys else ""
        ui.live = lambda mode="work": []
        self.ui = ui

    def to(self, key):
        keys = [r["key"] for r in self.ui.tree_rows()]
        self.ui.tree_sel = keys.index(key)

    def flagged_tree(self):
        t = dfs_tree.parse(TREE.replace(
            "Status: confirmed\nApproach: go deeper.",
            "Status: confirmed\nApproach: go deeper.\nHypothesis: it holds. Wrong if it leaks.\n"
            "Ask: warn or fail?\nDetermination: Confirmed: it holds. Next, more."), "W1")
        self.ui.tree_of = lambda task: (t, ARCHIVE, {})
        return t

    def test_n_walks_the_raises_and_assumptions_and_wraps(self):
        ui = self.ui
        self.flagged_tree()
        ui.open_tree()
        seen = []
        for _ in range(4):
            ui.act(ord("n"))
            seen.append(ui.tree_row()["key"])
        # the task's raise row, then W1.2 (⚑ and ask), then W1.3 (its raise), then round.
        self.assertEqual(seen, ["raise:2026-09-27T01:06:00Z", "W1.2", "W1.3",
                                "raise:2026-09-27T01:06:00Z"])
        ui.act(ord("N"))
        self.assertEqual(ui.tree_row()["key"], "W1.3")

    def test_g_and_G_move_the_cursor_not_the_text(self):
        ui = self.ui
        ui.open_tree()
        ui.act(ord("G"))
        self.assertEqual(ui.tree_sel, len(ui.tree_rows()) - 1)
        ui.act(ord("g"))
        self.assertEqual(ui.tree_sel, 0)

    def test_the_card_says_what_the_node_says_and_what_the_author_said(self):
        t = self.flagged_tree()
        row = next(r for r in TUI.tree_entries(t) if r["key"] == "W1.2")
        card = TUI.node_card(t, row)
        roles = [r for r, _ in card]
        self.assertEqual(roles[:2], ["head", "meta"])
        self.assertIn(("red", "warn or fail?"), card)
        self.assertIn(("amber", "it holds."), card)
        self.assertIn(("amber", "it leaks."), card)
        self.assertLess(card.index(("label", "Ask — a question for you")),
                        card.index(("label", "Approach")))

    def test_a_skim_line_says_the_verdict_without_the_verdict_word(self):
        self.flagged_tree()
        self.ui.open_tree()
        text = "\n".join(l for l, *_ in self.ui.lines_tree(100))
        self.assertIn("it holds.", text)
        self.assertNotIn("Next, more", text)
        self.assertIn("? ask", text)

    def test_adding_under_a_refuted_node_asks_first(self):
        ui = self.ui
        ui.open_tree()
        self.to("W1.4")
        added = []
        saved = TUI.dfs_tree.add_node
        TUI.dfs_tree.add_node = lambda *a: added.append(a) or "W1.5"
        self.addCleanup(setattr, TUI.dfs_tree, "add_node", saved)
        answers = iter(["", "a title"])
        ui.prompt = lambda label, default="": next(answers)
        ui.tree_add("W1")
        self.assertEqual(added, [], "an empty answer to the warning adds nothing")
        self.assertEqual(ui.msg, "no node added")

    def test_adding_under_the_cursor_node_puts_the_new_node_under_it(self):
        ui = self.ui
        ui.open_tree()
        self.to("W1.3")
        added = []
        saved = TUI.dfs_tree.add_node
        TUI.dfs_tree.add_node = lambda *a: added.append(a) or "W1.5"
        self.addCleanup(setattr, TUI.dfs_tree, "add_node", saved)
        answers = iter(["a title", ""])
        ui.prompt = lambda label, default="": next(answers)
        ui.tree_add("W1")
        self.assertEqual(added, [("W1", "a title", "W1.3", "")])

    def test_a_chain_step_is_drawn_with_the_mark_and_a_root_without(self):
        self.ui.open_tree()
        lines = [l for l, *_ in self.ui.lines_tree(100)]
        self.assertTrue(any("↳W1.3" in l for l in lines), lines)
        self.assertFalse(any("↳W1.1" in l or "↳W1.4" in l for l in lines))

    def test_H_lists_every_assumption_and_enter_goes_to_it_in_its_tree(self):
        ui = self.ui
        t = self.flagged_tree()
        ui.data["items"][0]["assumptions"] = dfs_tree.assumed(t)
        ui.act(ord("H"))
        self.assertEqual(ui.pane, "assumptions")
        self.assertEqual([nd["id"] for _, nd in ui.asm_rows()], ["W1.2"])
        text = "\n".join(l for l, *_ in ui.lines_assumptions(100))
        self.assertIn("wrong if it leaks.", text)
        self.assertIn("ask: warn or fail?", text)
        ui.act(10)
        self.assertTrue(ui.tree_focused())
        self.assertEqual(ui.tree_row()["key"], "W1.2")
        ui.act(ord("H"))
        ui.act(27)
        self.assertEqual(ui.pane, "item")

    def test_a_raise_says_how_long_it_has_waited_not_its_timestamp(self):
        now = 1_800_000_000
        import calendar
        ts = lambda secs: __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ",
                                                      __import__("time").gmtime(now - secs))
        self.assertEqual(TUI.age(ts(3 * 86400 + 5), now), "3d ago")
        self.assertEqual(TUI.age(ts(7200), now), "2h ago")
        self.assertEqual(TUI.age(ts(30), now), "just now")
        self.ui.open_tree()
        text = "\n".join(l for l, *_ in self.ui.lines_tree(100))
        self.assertIn("raise on the task ·", text)
        self.assertNotIn("2026-09-27T01:06:00Z", text)

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
        self.assertIn("cut the slice, and here is the whole", text)
        self.assertIn("confirmed. It holds.", text)
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

    def test_p_shows_only_the_nodes_that_want_the_author(self):
        ui = self.ui
        ui.open_tree()
        self.to("W1.3")
        ui.act(ord("p"))
        text = "\n".join(l for l, *_ in ui.lines_tree(100))
        self.assertIn("W1.3 ", text)
        self.assertNotIn("The first slice", text, "a done node with nothing to ask is hidden")
        self.assertIn("p shows every node", text)
        self.assertEqual(ui.tree_row()["key"], "W1.3", "the cursor stays on its row")
        ui.act(ord("p"))
        text = "\n".join(l for l, *_ in ui.lines_tree(100))
        self.assertIn("The first slice", text)
        self.assertFalse(ui.tree_flagged)

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
        ui.open_chat = lambda scope: ran.append(scope)
        ui.act(ord("c"))
        self.assertEqual(self.logged, [])
        self.assertEqual(len(ran), 1)
        self.assertNotEqual(ran[0], "project")      # the item's chat, not the project's

    def test_c_is_chat_while_the_tree_has_the_keys_too(self):
        # ⚠️ It was the correction there: one key, two writes, chosen by focus.
        ui = self.ui
        ran = []
        ui.open_chat = lambda scope: ran.append(scope)
        ui.open_tree()
        self.to("W1.4")
        ui.act(ord("c"))
        self.assertEqual(self.logged, [])
        self.assertEqual(len(ran), 1)
        self.assertNotEqual(ran[0], "project")      # the item's chat, not the project's

    def test_capital_c_is_a_chat_about_the_project_not_only_a_new_task(self):
        ui = self.ui
        ran = []
        ui.open_chat = lambda scope: ran.append(scope)
        ui.act(ord("C"))
        self.assertEqual(ran, ["project"])

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
