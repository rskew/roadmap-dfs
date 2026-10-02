#!/usr/bin/env python3
"""A net under the roadmap's implementation order — nothing here lives unchecked.

Two things are worth guarding and neither is the swap itself. The first is that an
order file which has fallen out of step with the items does something SAFE: an id it
never names, and an id it names that no longer exists, are both routine (an item is
opened after the last move, or closed and deleted) and both must leave every other
item where it was. The second is that the screen's selection follows the ITEM rather
than the row — a move that left the cursor on the row would silently select the item
that just swapped with it, which is the row-versus-identity confusion this repo has
paid for in the app itself.

Run:  python3 roadmap-dfs/scripts/test_order.py
"""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import order as dfs_order            # noqa: E402
import paths as dfs_paths            # noqa: E402
import state as dfs_state            # noqa: E402

class InAWorkRoot(unittest.TestCase):
    """A throwaway work root, which is the whole of "which project is this".

    The tool reads `pwd`, so a test names a different project by pointing
    `work_root` somewhere else — not by threading a root argument through every
    call, which was the old shape and was a second way to say the same thing.
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / ".dfs" / "items").mkdir(parents=True)
        self._work_root = dfs_paths.work_root
        dfs_paths.work_root = lambda: self.root
        self.more_setup()

    def more_setup(self):
        pass

    def tearDown(self):
        dfs_paths.work_root = self._work_root


TUI_SPEC = importlib.util.spec_from_file_location(
    "roadmap_tui", HERE / "tui.py")
TUI = importlib.util.module_from_spec(TUI_SPEC)
TUI_SPEC.loader.exec_module(TUI)

ROADMAP = """# Roadmap

## §0 How work is done

Each task is a decision tree.
"""


class OrderFile(unittest.TestCase):
    def test_reads_bullets_bare_ids_and_ignores_prose(self):
        text = ("# Implementation order\n\nW7 is blocked on the pairing token.\n\n"
                "- W7\nW3\n* W10\n")
        self.assertEqual(dfs_order.order_of(text), ["W7", "W3", "W10"])

    def test_a_sentence_naming_an_item_does_not_rank_it(self):
        self.assertEqual(dfs_order.order_of("Do W4 before W9.\n"), [])

    def test_duplicate_id_keeps_its_first_position(self):
        self.assertEqual(dfs_order.order_of("- W5\n- W2\n- W5\n"), ["W5", "W2"])

    def test_unranked_items_follow_the_ranked_ones_by_id(self):
        key = dfs_order.sort_key("- W9\n- W2\n")
        self.assertEqual(sorted(["W1", "W2", "W9", "W10"], key=key),
                         ["W9", "W2", "W1", "W10"])

    def test_an_id_that_no_longer_exists_shifts_nothing(self):
        key = dfs_order.sort_key("- W99\n- W3\n- W1\n")
        self.assertEqual(sorted(["W1", "W2", "W3"], key=key), ["W3", "W1", "W2"])

    def test_no_file_at_all_is_id_order(self):
        key = dfs_order.sort_key("")
        self.assertEqual(sorted(["W10", "W2", "W1"], key=key), ["W1", "W2", "W10"])


class Tree(unittest.TestCase):
    """Indentation is the structure, and a node moves with its subtree.

    Both halves are load-bearing for the walk: the shape decides what a blocked
    branch takes down with it, and a move that dropped a child would silently
    re-parent work onto whatever happened to be above it.
    """

    SHAPE = "- W1\n  - W2\n    - W3\n  - W4\n- W5\n"

    def nodes(self):
        return dfs_order.nodes_of(self.SHAPE)

    def test_indentation_is_the_structure(self):
        self.assertEqual(self.nodes(),
                         [("W1", 0), ("W2", 1), ("W3", 2), ("W4", 1), ("W5", 0)])

    def test_any_consistent_indent_nests_the_way_it_looks(self):
        # Four spaces, a tab, and a ragged hand-edit all say the same tree.
        self.assertEqual(dfs_order.nodes_of("- W1\n    - W2\n\t\t- W3\n"),
                         [("W1", 0), ("W2", 1), ("W3", 2)])

    def test_a_flat_file_is_a_tree_of_roots(self):
        self.assertEqual(dfs_order.nodes_of("- W1\n- W2\n"), [("W1", 0), ("W2", 0)])

    def test_up_and_down_stay_among_siblings_and_carry_the_subtree(self):
        n = self.nodes()
        self.assertEqual(dfs_order.moved(n, "W2", 1),
                         [("W1", 0), ("W4", 1), ("W2", 1), ("W3", 2), ("W5", 0)])
        # W2 went past W4 without leaving W3 behind, and without leaving W1.
        self.assertEqual(dfs_order.moved(n, "W1", 1),
                         [("W5", 0), ("W1", 0), ("W2", 1), ("W3", 2), ("W4", 1)])

    def test_down_is_refused_at_the_last_sibling_not_at_the_last_row(self):
        # W4 is the last child of W1 but nowhere near the last line of the file;
        # a move that walked the flattened list would silently promote it instead.
        self.assertIsNone(dfs_order.moved(self.nodes(), "W4", 1))

    def test_indent_goes_under_the_sibling_above_as_its_last_child(self):
        self.assertEqual(dfs_order.indented(self.nodes(), "W4"),
                         [("W1", 0), ("W2", 1), ("W3", 2), ("W4", 2), ("W5", 0)])

    def test_indent_is_refused_with_no_sibling_above(self):
        # Not "no node above": W2 has W1 above it, and reaching into a branch the
        # author was not looking at is the move this refuses.
        self.assertIsNone(dfs_order.indented(self.nodes(), "W2"))

    def test_outdent_becomes_the_next_sibling_of_the_parent(self):
        self.assertEqual(dfs_order.outdented(self.nodes(), "W3"),
                         [("W1", 0), ("W2", 1), ("W3", 1), ("W4", 1), ("W5", 0)])

    def test_outdent_carries_the_subtree_and_adopts_nobody(self):
        # W2 takes W3 with it and leaves W4 exactly where it was, under W1.
        self.assertEqual(dfs_order.outdented(self.nodes(), "W2"),
                         [("W1", 0), ("W4", 1), ("W2", 0), ("W3", 1), ("W5", 0)])

    def test_outdent_is_refused_at_the_root(self):
        self.assertIsNone(dfs_order.outdented(self.nodes(), "W1"))

    def test_a_move_never_loses_or_duplicates_a_node(self):
        n = self.nodes()
        for after in (dfs_order.moved(n, "W2", 1), dfs_order.indented(n, "W4"),
                      dfs_order.outdented(n, "W3"), dfs_order.outdented(n, "W2")):
            self.assertEqual(sorted(x for x, _ in after),
                             sorted(x for x, _ in n))
            self.assertEqual(after[0][1], 0, "the first node is always a root")


class Breaks(unittest.TestCase):
    """⚠️ A fence across the forest: everything after it waits on everything before.

    The shape checked here is the one nesting cannot express — a claim about every
    branch above at once — so the tests are about what a break is NOT allowed to be:
    inside a subtree, above the first item, or something an ordinary move can lose.
    """

    SHAPE = "- W1\n  - W2\n- W3\n\n---\n\n- W4\n  - W5\n"

    def nodes(self):
        return dfs_order.nodes_of(self.SHAPE)

    def test_a_rule_between_branches_is_a_node_in_its_place(self):
        self.assertEqual(self.nodes(),
                         [("W1", 0), ("W2", 1), ("W3", 0), (dfs_order.BREAK, 0),
                          ("W4", 0), ("W5", 1)])

    def test_the_ids_are_the_ids_whatever_the_fences(self):
        self.assertEqual(dfs_order.order_of(self.SHAPE),
                         ["W1", "W2", "W3", "W4", "W5"])

    def test_a_rule_before_the_first_id_is_prose(self):
        # Front matter, and a horizontal rule under a heading: both are a fence with
        # nothing above them to gate, and one that held the whole roadmap would be
        # unattributable to anything the author did.
        self.assertEqual(dfs_order.nodes_of("---\n# Tree\n---\n\n- W1\n- W2\n"),
                         [("W1", 0), ("W2", 0)])

    def test_two_rules_running_are_one_fence(self):
        self.assertEqual(dfs_order.nodes_of("- W1\n---\n---\n- W2\n"),
                         [("W1", 0), (dfs_order.BREAK, 0), ("W2", 0)])

    def test_a_fence_ends_every_open_branch(self):
        # ⚠️ The line after a rule is a ROOT whatever its indent. That is what makes
        # "a break is never inside a subtree" true by construction, which every move
        # below relies on without checking for it.
        self.assertEqual(dfs_order.nodes_of("- W1\n  - W2\n---\n    - W3\n"),
                         [("W1", 0), ("W2", 1), (dfs_order.BREAK, 0), ("W3", 0)])

    def test_round_trip_through_the_file(self):
        self.assertEqual(dfs_order.nodes_of(dfs_order.render(self.nodes())),
                         self.nodes())

    def test_a_rule_is_written_with_air_around_it(self):
        # `---` directly under a list item is a thematic break to one markdown parser
        # and the underline of a setext heading to another; a file whose fences
        # render as headings is one nobody will trust the shape of.
        self.assertIn("- W3\n\n---\n\n- W4\n", dfs_order.render(self.nodes()))

    def test_up_and_down_step_a_branch_across_a_fence(self):
        # Deliberate, and the second thing an author does with a break: the fence is
        # a sibling at the root, so `[` puts W4 and its child on the near side of it.
        self.assertEqual(dfs_order.moved(self.nodes(), "W4", -1),
                         [("W1", 0), ("W2", 1), ("W3", 0), ("W4", 0), ("W5", 1),
                          (dfs_order.BREAK, 0)])
        self.assertEqual(dfs_order.moved(self.nodes(), "W3", 1),
                         [("W1", 0), ("W2", 1), (dfs_order.BREAK, 0), ("W3", 0),
                          ("W4", 0), ("W5", 1)])

    def test_indent_is_refused_across_a_fence(self):
        # W4 has W3 above it on the page and a fence between them. Nesting under it
        # would carry W4 across the break as a side effect of a key that says nothing
        # about breaks.
        self.assertIsNone(dfs_order.indented(self.nodes(), "W4"))

    def test_outdent_stays_on_its_own_side(self):
        self.assertEqual(dfs_order.outdented(self.nodes(), "W5"),
                         [("W1", 0), ("W2", 1), ("W3", 0), (dfs_order.BREAK, 0),
                          ("W4", 0), ("W5", 0)])
        self.assertEqual(dfs_order.outdented(self.nodes(), "W2"),
                         [("W1", 0), ("W2", 0), ("W3", 0), (dfs_order.BREAK, 0),
                          ("W4", 0), ("W5", 1)])

    def test_only_the_toggle_changes_how_many_fences_there_are(self):
        # A move carries a subtree past a fence; it never drops one, and `b` is the
        # only key that can. The screen writes the WHOLE tree on every move, so a
        # fence lost here is a fence lost from the file.
        for after in (dfs_order.moved(self.nodes(), "W1", 1),
                      dfs_order.moved(self.nodes(), "W4", -1),
                      dfs_order.outdented(self.nodes(), "W5"),
                      dfs_order.indented(self.nodes(), "W3")):
            self.assertEqual(sum(1 for n, _ in after if dfs_order.is_break(n)), 1)
        self.assertEqual(
            sum(1 for n, _ in dfs_order.break_toggled(self.nodes(), "W3")
                if dfs_order.is_break(n)), 2)

    def test_the_toggle_adds_and_takes_away(self):
        added = dfs_order.break_toggled(self.nodes(), "W3")
        self.assertEqual(added[2], (dfs_order.BREAK, 0))
        self.assertTrue(dfs_order.has_break_above(added, "W3"))
        self.assertEqual(dfs_order.break_toggled(added, "W3"), self.nodes())
        self.assertEqual(dfs_order.break_toggled(self.nodes(), "W4"),
                         [("W1", 0), ("W2", 1), ("W3", 0), ("W4", 0), ("W5", 1)])

    def test_the_toggle_refuses_a_nested_item(self):
        # A fence separates whole branches. Cutting a parent from its children with
        # one would be two claims at once, and `{` is where the other one is made.
        self.assertIsNone(dfs_order.break_toggled(self.nodes(), "W2"))
        self.assertIsNone(dfs_order.break_toggled(self.nodes(), "W5"))

    def test_the_toggle_refuses_the_first_item(self):
        # Nothing above it to be finished, so the fence would gate nothing while
        # looking as though it gated everything.
        self.assertIsNone(dfs_order.break_toggled(self.nodes(), "W1"))

    def test_a_fence_with_nothing_after_it_is_not_written_back(self):
        # It gates nothing, the screen draws no rule for it, and — since a break is
        # toggled from the row below — it is one the author has no row to press on.
        self.assertEqual(dfs_order.render([("W1", 0), (dfs_order.BREAK, 0)]), "- W1\n")


class Walking(unittest.TestCase):
    """⚠️ A RAISE blocks the whole SUBTREE, and a walk steps onto the next branch.

    This is the rule the tool is named for, so it is checked here rather than left
    to the screen: without it a blocked parent would stall a walk that has perfectly
    good work sitting one branch over.
    """

    def tree(self, *rows):
        return dfs_state.walk([dict(id=i, depth=d, status=s) for i, d, s in rows])

    def fenced(self, *rows):
        """The same, with each row's SEGMENT — how many fences are above it."""
        return dfs_state.walk([dict(id=i, depth=d, status=s, segment=g)
                               for i, d, s, g in rows])

    def test_a_blocked_item_takes_its_descendants_with_it(self):
        t = self.tree(("A", 0, "open"), ("B", 1, "blocked"), ("C", 2, "open"),
                      ("D", 1, "open"))
        by = {i["id"]: i for i in t}
        self.assertFalse(by["C"]["reachable"])
        self.assertEqual(by["C"]["blocked_by"], "B")
        self.assertTrue(by["D"]["reachable"], "a sibling of the block is untouched")

    def test_blocked_and_blocked_by_are_different_questions(self):
        # "answer this" and "answer something above this" must not read the same.
        t = self.tree(("A", 0, "blocked"), ("B", 1, "open"))
        by = {i["id"]: i for i in t}
        self.assertIsNone(by["A"]["blocked_by"])
        self.assertEqual(by["A"]["status"], "blocked")
        self.assertEqual(by["B"]["status"], "open")
        self.assertEqual(by["B"]["blocked_by"], "A")

    def test_the_block_ends_where_the_branch_does(self):
        t = self.tree(("A", 0, "blocked"), ("B", 1, "open"), ("C", 0, "open"))
        self.assertEqual([i["reachable"] for i in t], [True, False, True])

    def test_next_steps_sideways_onto_the_next_branch(self):
        t = self.tree(("A", 0, "blocked"), ("B", 1, "open"), ("C", 0, "open"))
        self.assertEqual(dfs_state.next_item(t), "C")

    def test_next_is_none_when_every_branch_is_blocked_or_finished(self):
        t = self.tree(("A", 0, "done"), ("B", 0, "blocked"), ("C", 1, "open"))
        self.assertIsNone(dfs_state.next_item(t))

    def test_a_fence_holds_every_branch_after_it(self):
        t = self.fenced(("A", 0, "open", 0), ("B", 0, "open", 1), ("C", 1, "open", 1))
        by = {i["id"]: i for i in t}
        self.assertEqual(by["B"]["waiting_on"], "A")
        self.assertEqual(by["C"]["waiting_on"], "A")
        self.assertFalse(by["B"]["reachable"])
        self.assertEqual(dfs_state.next_item(t), "A")

    def test_the_fence_opens_when_everything_before_it_is_done(self):
        t = self.fenced(("A", 0, "done", 0), ("B", 1, "done", 0), ("C", 0, "open", 1))
        self.assertIsNone({i["id"]: i for i in t}["C"]["waiting_on"])
        self.assertEqual(dfs_state.next_item(t), "C")

    def test_not_done_covers_blocked_as_well_as_open(self):
        # ⚠️ A fence is not satisfied by a branch that merely has nobody asking a
        # question about it. This is the one case where the depth-first sidestep does
        # NOT apply, and it is the whole point of drawing a fence: B is perfectly
        # workable and the author has said it is not to be worked yet.
        t = self.fenced(("A", 0, "blocked", 0), ("B", 0, "open", 1))
        self.assertEqual({i["id"]: i for i in t}["B"]["waiting_on"], "A")
        self.assertIsNone(dfs_state.next_item(t))

    def test_the_gate_is_the_first_unfinished_item_not_the_nearest(self):
        # The first is the one a walk would pick up next, so it is the one worth
        # naming on a held row.
        t = self.fenced(("A", 0, "open", 0), ("B", 0, "open", 0), ("C", 0, "open", 1))
        self.assertEqual({i["id"]: i for i in t}["C"]["waiting_on"], "A")

    def test_a_gate_carries_through_every_later_fence(self):
        t = self.fenced(("A", 0, "open", 0), ("B", 0, "done", 1), ("C", 0, "open", 2))
        self.assertEqual({i["id"]: i for i in t}["C"]["waiting_on"], "A")

    def test_an_item_does_not_gate_its_own_segment(self):
        t = self.fenced(("A", 0, "done", 0), ("B", 0, "open", 1), ("C", 0, "open", 1))
        self.assertTrue(all(i["reachable"] for i in t))
        self.assertEqual(dfs_state.next_item(t), "B")

    def test_three_questions_three_answers(self):
        # "answer this", "answer something above this", "finish something before
        # this" — an item can be in all three at once and they must not read alike.
        t = self.fenced(("A", 0, "open", 0), ("B", 0, "blocked", 1), ("C", 1, "open", 1))
        by = {i["id"]: i for i in t}
        self.assertEqual(by["B"]["status"], "blocked")
        self.assertIsNone(by["B"]["blocked_by"])
        self.assertEqual(by["C"]["blocked_by"], "B")
        self.assertEqual(by["C"]["waiting_on"], "A")

    def test_parent_is_the_nearest_shallower_node_above(self):
        t = self.tree(("A", 0, "open"), ("B", 1, "open"), ("C", 2, "open"),
                      ("D", 1, "open"))
        self.assertEqual([i["parent"] for i in t], [None, "A", "B", "A"])


class Writing(InAWorkRoot):
    def test_first_write_carries_the_default_header(self):
        dfs_order.write_order([("W2", 0), ("W1", 0)])
        text = dfs_order.read_text()
        self.assertIn("# Implementation tree", text)
        self.assertEqual(dfs_order.order_of(text), ["W2", "W1"])

    def test_a_rewrite_keeps_the_prose_the_author_wrote(self):
        dfs_order.path().write_text(
            "# Implementation tree\n\nW2 first: the roadmap cannot wait.\n\n- W1\n- W2\n")
        dfs_order.write_order([("W2", 0), ("W1", 0)])
        text = dfs_order.read_text()
        self.assertIn("W2 first: the roadmap cannot wait.", text)
        self.assertEqual(dfs_order.order_of(text), ["W2", "W1"])

    def test_round_trip(self):
        nodes = [("W5", 0), ("W1", 1), ("W18", 2), ("W3", 0)]
        dfs_order.write_order(nodes)
        self.assertEqual(dfs_order.nodes_of(dfs_order.read_text()), nodes)

    def test_moved_refuses_at_the_ends_and_off_the_tree(self):
        nodes = [("W1", 0), ("W2", 0), ("W3", 0)]
        self.assertIsNone(dfs_order.moved(nodes, "W1", -1))
        self.assertIsNone(dfs_order.moved(nodes, "W3", 1))
        self.assertIsNone(dfs_order.moved(nodes, "W9", 1))
        self.assertEqual(dfs_order.moved(nodes, "W2", -1),
                         [("W2", 0), ("W1", 0), ("W3", 0)])


class ThroughTheDerivation(InAWorkRoot):
    """dfs_state is what the screen and the runner both read, so it is the one
    that has to come back ordered — a screen that sorted for itself would be the
    second definition this module's docstring argues against."""

    def more_setup(self):
        dfs_paths.roadmap().write_text(ROADMAP)
        for n, goal in ((1, "the first thing"), (2, "the second thing"), (3, "the third thing")):
            (dfs_paths.items() / ("W%d.md" % n)).write_text(
                "# W%d · %s\n\n## Goal\n\n%s\n\n## Tree\n\n## Log\n" % (n, goal, goal))

    def ids(self):
        return [i["id"] for i in dfs_state.full()["items"]]

    def test_id_order_without_a_file(self):
        self.assertEqual(self.ids(), ["W1", "W2", "W3"])

    def test_the_order_file_decides(self):
        dfs_order.write_order([("W3", 0), ("W1", 0), ("W2", 0)])
        self.assertEqual(self.ids(), ["W3", "W1", "W2"])

    def test_an_item_opened_since_the_last_move_lands_at_the_bottom(self):
        dfs_order.write_order([("W3", 0), ("W2", 0)])
        self.assertEqual(self.ids(), ["W3", "W2", "W1"])

    def test_the_items_come_back_with_the_segment_they_are_in(self):
        dfs_order.write_order([("W3", 0), (dfs_order.BREAK, 0), ("W2", 0), ("W1", 0)])
        self.assertEqual([(i["id"], i["segment"]) for i in dfs_state.full()["items"]],
                         [("W3", 0), ("W2", 1), ("W1", 1)])

    def test_an_item_nobody_placed_is_where_the_file_says_it_is(self):
        # ⚠️ Behind the fence, because that is where it is written — at the bottom,
        # where new work goes. "Unplaced means ungated" was the alternative and it
        # would flip the moment an unrelated `[` rewrote the file with this same item
        # in this same place: a silent change of meaning from a keypress about
        # something else.
        dfs_order.write_order([("W3", 0), (dfs_order.BREAK, 0), ("W2", 0)])
        by = {i["id"]: i for i in dfs_state.full()["items"]}
        self.assertEqual(by["W1"]["segment"], 1)
        self.assertEqual(by["W1"]["waiting_on"], "W3")

    def test_a_segment_whose_items_are_all_gone_still_leaves_its_gap(self):
        # W9 was closed and its file deleted; the fences around it are still two
        # fences, and the screen has to be able to put both back where they were.
        dfs_order.write_order([("W1", 0), (dfs_order.BREAK, 0), ("W9", 0),
                               (dfs_order.BREAK, 0), ("W2", 0)])
        self.assertEqual([(i["id"], i["segment"]) for i in dfs_state.full()["items"]],
                         [("W1", 0), ("W2", 2), ("W3", 2)])


class ContinuingTheLastRun(InAWorkRoot):
    """⚠️ A walk finishes what it started, so `next_item` is not the order's answer
    alone (`dfs_state.continue_last`).

    What is guarded is the DIFFERENCE between the two: an item earlier in the order
    becoming workable — a raise answered, an item reopened, an item moved up — must
    not take the walk off a tree it is in the middle of. And the other half: the
    continuation is not a licence to stay, so an item that cannot be worked hands the
    walk straight back to the order.
    """

    def more_setup(self):
        dfs_paths.roadmap().write_text(ROADMAP)
        for n in (1, 2, 3):
            self.task(n)

    def task(self, n, tree="", log=""):
        (dfs_paths.items() / ("W%d.md" % n)).write_text(
            "# W%d · the %dth thing\n\n## Goal\n\nthe %dth thing\n\n"
            "## Background\n\n## Tree\n\n%s\n## Log\n\n%s" % (n, n, n, tree, log))

    def session(self, n, ts):
        self.task(n, log="- %s · session · work · dfs_run_x/run-1\n" % ts)

    def nxt(self):
        d = dfs_state.full()
        # The order's own answer, beside the walk's, so a test that passes for the
        # wrong reason (both naming the same item) cannot hide here.
        return d["next_item"], dfs_state.next_item(d["items"])

    def test_the_walk_continues_the_item_the_last_run_was_on(self):
        self.session(2, "2026-09-25T10:00:00Z")
        self.assertEqual(self.nxt(), ("W2", "W1"))

    def test_the_last_run_over_every_task_is_the_one_that_counts(self):
        self.session(3, "2026-09-25T10:00:00Z")
        self.session(2, "2026-09-25T09:00:00Z")
        self.assertEqual(self.nxt(), ("W3", "W1"))

    def test_with_no_run_at_all_the_order_decides(self):
        self.assertEqual(self.nxt(), ("W1", "W1"))

    def test_an_item_that_raised_hands_the_walk_back_to_the_order(self):
        # The depth-first sidestep: a question on the item the walk was on is not a
        # reason to sit on it.
        self.task(2, log="- 2026-09-25T10:00:00Z · session · work · dfs_run_x/run-1\n"
                         "- 2026-09-25T10:30:00Z · raise · W2.1\n  which way?\n")
        self.assertEqual(self.nxt(), ("W1", "W1"))

    def test_a_finished_item_does_not_hold_the_walk(self):
        self.task(2, tree="### W2.1 · the only way\nStatus: confirmed\nApproach: it\n\n",
                  log="- 2026-09-25T10:00:00Z · session · work · dfs_run_x/run-1\n")
        self.assertEqual({i["id"]: i["status"] for i in dfs_state.full()["items"]}["W2"],
                         "done")
        self.assertEqual(self.nxt(), ("W1", "W1"))

    def test_a_fence_outranks_the_continuation(self):
        # The one claim the author makes over the whole forest: nothing past the fence
        # is worth starting until everything before it is done. A walk already past it
        # does not get to stay there.
        dfs_order.write_order([("W1", 0), (dfs_order.BREAK, 0), ("W2", 0), ("W3", 0)])
        self.session(2, "2026-09-25T10:00:00Z")
        self.assertEqual(self.nxt(), ("W1", "W1"))


class BreaksOnTheScreen(InAWorkRoot):
    """⚠️ The screen writes the WHOLE tree on every move, so it has to be holding the
    fences. The derivation hands back items alone, each carrying its segment; if
    `nodes()` did not put the breaks back between them, the first `[` would quietly
    delete every fence in the file."""

    def more_setup(self):
        self.ui = object.__new__(TUI.UI)
        self.ui.msg = ""
        self.ui.sel = 0

        def reload(note=""):
            items, seg = [], 0
            for name, depth in dfs_order.nodes_of(dfs_order.read_text()):
                if dfs_order.is_break(name):
                    seg += 1
                    continue
                items.append({"id": name, "depth": depth, "segment": seg,
                              "status": "open"})
            # Through the real derivation, so the rows carry what the screen reads
            # off them — `parent` after a reparent, `waiting_on` after a fence.
            self.ui.data = {"items": dfs_state.walk(items)}

        self.ui.reload = reload
        dfs_order.write_order([("W1", 0), ("W2", 0), ("W3", 0)])
        reload()

    def test_b_puts_a_fence_above_the_selected_item(self):
        self.ui.sel = 1
        self.ui.toggle_break()
        self.assertEqual(dfs_order.nodes_of(dfs_order.read_text()),
                         [("W1", 0), (dfs_order.BREAK, 0), ("W2", 0), ("W3", 0)])
        self.assertEqual([i["segment"] for i in self.ui.items], [0, 1, 1])
        self.assertEqual(self.ui.current()["id"], "W2", "the cursor stays on the item")
        self.assertEqual(self.ui.current()["waiting_on"], "W1")
        self.assertEqual([r[0] for r in self.ui.list_rows()],
                         ["item", "break", "item", "item"])

    def test_what_a_held_row_says(self):
        # ⚠️ A marker only ever replaces `open`. The fence is the reason this matters:
        # `blocked_by` holds one branch, but a break holds every row after it, so a
        # marker that overwrote `done` would relabel whole segments of finished work
        # as waiting.
        say = TUI.row_state
        self.assertEqual(say(dict(status="open")), "open")
        self.assertEqual(say(dict(status="open", waiting_on="W2")), "»W2")
        self.assertEqual(say(dict(status="open", blocked_by="W7")), "↳W7")
        self.assertEqual(say(dict(status="open", blocked_by="W7", waiting_on="W2")),
                         "↳W7", "the answerable one first")
        self.assertEqual(say(dict(status="done", waiting_on="W2")), "done")
        self.assertEqual(say(dict(status="blocked", waiting_on="W2")), "blocked")

    def test_b_again_takes_it_away(self):
        self.ui.sel = 1
        self.ui.toggle_break()
        self.ui.toggle_break()
        self.assertEqual(dfs_order.order_of(dfs_order.read_text()),
                         ["W1", "W2", "W3"])
        self.assertNotIn("---", dfs_order.read_text().split("W1")[-1])

    def test_an_ordinary_move_keeps_the_fence(self):
        self.ui.sel = 1
        self.ui.toggle_break()
        self.ui.sel = 2                      # W3, on the far side
        self.ui.reorder(-1)
        self.assertEqual(dfs_order.nodes_of(dfs_order.read_text()),
                         [("W1", 0), (dfs_order.BREAK, 0), ("W3", 0), ("W2", 0)])

    def test_b_on_a_nested_item_says_so_and_writes_nothing(self):
        self.ui.sel = 1
        self.ui.reparent(1)                  # W2 under W1
        before = dfs_order.read_text()
        self.ui.toggle_break()
        self.assertEqual(dfs_order.read_text(), before)
        self.assertIn("nested", self.ui.msg)

    def test_b_on_the_first_item_says_so_and_writes_nothing(self):
        before = dfs_order.read_text()
        self.ui.toggle_break()
        self.assertEqual(dfs_order.read_text(), before)
        self.assertIn("gate nothing", self.ui.msg)


class Selection(InAWorkRoot):
    """The screen's half: the cursor stays on the item that moved."""

    def more_setup(self):
        self.ui = object.__new__(TUI.UI)
        self.ui.msg = ""
        self.ui.sel = 1
        self.order = ["W1", "W2", "W3"]
        self.ui.data = {"items": [{"id": i, "depth": 0} for i in self.order]}

        def reload(note=""):
            # What the real reload does: re-derive the list from disk. Stubbed to the
            # order file alone, which is the only input this behaviour turns on.
            nodes = dfs_order.nodes_of(dfs_order.read_text())
            self.ui.data = {"items": [{"id": i, "depth": d} for i, d in nodes]}

        self.ui.reload = reload

    def test_moving_down_keeps_the_cursor_on_the_moved_item(self):
        self.ui.reorder(1)
        self.assertEqual([i["id"] for i in self.ui.items], ["W1", "W3", "W2"])
        self.assertEqual(self.ui.current()["id"], "W2")
        self.assertEqual(self.ui.sel, 2)

    def test_moving_up_keeps_the_cursor_on_the_moved_item(self):
        self.ui.reorder(-1)
        self.assertEqual([i["id"] for i in self.ui.items], ["W2", "W1", "W3"])
        self.assertEqual(self.ui.current()["id"], "W2")
        self.assertEqual(self.ui.sel, 0)

    def test_at_the_top_it_says_so_and_writes_nothing(self):
        self.ui.sel = 0
        self.ui.reorder(-1)
        self.assertFalse(dfs_order.path().exists())
        self.assertIn("first", self.ui.msg)

    def test_at_the_bottom_it_says_so_and_writes_nothing(self):
        self.ui.sel = 2
        self.ui.reorder(1)
        self.assertFalse(dfs_order.path().exists())
        self.assertIn("last", self.ui.msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
