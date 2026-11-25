#!/usr/bin/env python3
"""The status file of a history-format task: its Live tree and Raises are generated from the
history, the digest and the budget read that file alone, and a new node adds no stub (W203.6).

Run:  python3 roadmap-dfs/scripts/test_status.py
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import taskhistory as th    # noqa: E402
import tree as dfs_tree     # noqa: E402

FX = HERE / "fixtures" / "history"


class Base(unittest.TestCase):
    def setUp(self):
        self.cwd = os.getcwd()
        self.root = Path(tempfile.mkdtemp())
        (self.root / ".dfs" / "items").mkdir(parents=True)
        os.chdir(self.root)
        self.addCleanup(os.chdir, self.cwd)

    def put(self, name, text):
        (self.root / ".dfs/items" / name).write_text(text)

    def get(self, name):
        return (self.root / ".dfs/items" / name).read_text()

    def fixture(self):
        for n in ("W9.md", "W9.history.md", "W9.log.md"):
            self.put(n, (FX / n).read_text())
        return dfs_tree.load("W9")

    def confirm(self, nid, *more):
        self.put("W9.history.md", th.append(self.get("W9.history.md"), nid,
                                            ["Status: confirmed", "Determination: Confirmed: done."] + list(more)))


class Generated(Base):
    def test_the_fixture_status_file_is_what_the_generator_writes(self):
        t = self.fixture()
        want = (FX / "W9.md").read_text()
        self.assertEqual(th.status_text(want, t), want)

    def test_hand_kept_sections_are_untouched_and_the_old_tree_and_log_are_dropped(self):
        t = self.fixture()
        old = (FX / "W9.md").read_text()
        text = old.replace("## Decisions in force", "## Tree\n\n### W9.1 · stale\nStatus: open\n\n## Log\n\n"
                           "- 2026-11-19T10:00:00Z · session · work\n\n## Decisions in force")
        new = th.status_text(text, t)
        self.assertEqual(new, old)
        self.assertNotIn("stale", new)

    def test_a_file_with_no_generated_sections_gets_them_after_the_others(self):
        t = self.fixture()
        new = th.status_text("# W9 · Sample board\n\n## Goal\n\nMake it.\n", t)
        self.assertEqual([l for l in new.splitlines() if l.startswith("## ")], ["## Goal", "## Live tree", "## Raises"])

    def test_a_node_that_is_confirmed_leaves_the_live_tree_and_its_children_keep_one_title_line(self):
        t = self.fixture()
        live = th.live_tree(t)
        self.assertEqual(live[0], "- W9.3 · charger, third try (confirmed)")
        self.confirm("W9.4")
        live = th.live_tree(dfs_tree.load("W9"))
        self.assertNotIn("### W9.4 · pick the boost part", live)
        self.assertEqual([l for l in live if l.startswith("###")], ["### W9.5 · antenna on the edge"])
        self.assertFalse(any(l.startswith("- W9.3") for l in live))      # nothing live hangs below it now

    def test_a_node_below_two_confirmed_ancestors_shows_both_as_title_lines_root_first(self):
        self.fixture()
        self.put("W9.history.md", self.get("W9.history.md").rstrip("\n") + "\n\n### W9.7 · deeper\nParent: W9.4\n"
                 "Status: open\nApproach: Go deeper.\n")
        self.confirm("W9.4")
        live = th.live_tree(dfs_tree.load("W9"))
        at = live.index("- W9.3 · charger, third try (confirmed)")
        self.assertEqual(live[at:at + 3], ["- W9.3 · charger, third try (confirmed)", "- W9.4 · pick the boost part (confirmed)",
                                           "### W9.7 · deeper"])

    def test_a_plan_a_confirmed_node_supersedes_and_a_refuted_branch_are_not_live(self):
        self.fixture()
        self.put("W9.history.md", self.get("W9.history.md").rstrip("\n") + "\n\n### W9.7 · other boost\n"
                 "Parent: W9.4\nStatus: open\nApproach: x\n\n### W9.8 · boost, better\nSupersedes: W9.4\nStatus: open\n"
                 "Approach: y\nStatus: confirmed\nDetermination: Confirmed: y.\n")
        ids = th.live_ids(dfs_tree.load("W9"))
        self.assertNotIn("W9.4", ids)          # replaced by a confirmed node
        self.assertNotIn("W9.7", ids)          # hangs under a node that left the live tree
        self.assertNotIn("W9.6", ids)          # refuted
        self.assertIn("W9.5", ids)             # parked stays: a session unparks it

    def test_raises_hold_the_open_raise_and_an_answer_nobody_has_acted_on(self):
        t = self.fixture()
        self.assertEqual(len(th.raise_entries(t)), 1)
        self.put("W9.log.md", self.get("W9.log.md") + "\n- 2026-11-23T09:00:00Z · answer · 2026-11-22T10:05:00Z\n  Edge.\n")
        got = th.raise_entries(dfs_tree.load("W9"))
        self.assertEqual(len(got), 1)
        self.assertIn("· answer ·", got[0])        # the raise is answered; the answer waits to be acted on
        self.assertIn("Edge.", got[0])

    def test_regenerating_twice_changes_nothing(self):
        self.fixture()
        once = th.write_status("W9")
        self.assertEqual(th.write_status("W9"), once)

    def test_the_status_command_writes_the_file(self):
        self.fixture()
        self.confirm("W9.4")
        self.assertEqual(dfs_tree.main(["tree.py", "status", "W9"]), 0)
        self.assertNotIn("### W9.4", self.get("W9.md"))
        self.assertIn("### W9.5", self.get("W9.md"))


class StartAndAdd(Base):
    def test_start_marks_the_status_file_and_never_the_history(self):
        self.fixture()
        history = self.get("W9.history.md")
        self.put("W9.md", self.get("W9.md").replace("Status: started", "Status: open"))
        dfs_tree.start_node("W9", "W9.4")
        self.assertIn("Status: started", self.get("W9.md"))
        self.assertEqual(self.get("W9.history.md"), history)
        self.assertEqual({n["id"]: n["status"] for n in dfs_tree.load("W9")["nodes"]}["W9.4"], "started")
        self.assertEqual(th.write_status("W9"), self.get("W9.md"))           # regeneration keeps it

    def test_start_refuses_a_node_that_is_not_open(self):
        self.fixture()
        with self.assertRaises(ValueError):
            dfs_tree.start_node("W9", "W9.1")

    def test_adding_a_node_appends_to_the_history_and_shows_in_the_live_tree(self):
        self.fixture()
        before = self.get("W9.history.md")
        nid = dfs_tree.add_node("W9", "pick a regulator", "W9.4", "Compare two.")
        self.assertEqual(nid, "W9.7")
        after = self.get("W9.history.md")
        self.assertTrue(after.startswith(before.rstrip("\n")))                 # only grew
        self.assertIn("### W9.7 · pick a regulator\nParent: W9.4\nStatus: open\nApproach: Compare two.", after)
        self.assertIn("### W9.7 · pick a regulator", self.get("W9.md"))
        self.assertNotIn("## Tree", self.get("W9.md"))

    def test_editing_or_deleting_a_node_is_refused_until_it_is_built(self):
        self.fixture()
        with self.assertRaises(ValueError):
            dfs_tree.edit_node("W9", "W9.4", "x")
        with self.assertRaises(ValueError):
            dfs_tree.delete_node("W9", "W9.4")

    def test_compact_and_shelve_are_refused_so_the_log_stays_whole_for_load(self):
        # compact moved the log to the archive, which a history-format task's load never reads
        self.fixture()
        n = len(dfs_tree.load("W9")["log"])
        log = self.get("W9.log.md")
        self.assertGreater(n, 0)
        for cmd in (dfs_tree.compact, dfs_tree.shelve):
            with self.assertRaises(ValueError) as e:
                cmd("W9")
            self.assertIn("history", str(e.exception))
        self.assertEqual(self.get("W9.log.md"), log)
        self.assertFalse((self.root / ".dfs/archive/items/W9.md").exists())
        self.assertEqual(len(dfs_tree.load("W9")["log"]), n)


class Budget(Base):
    def long_task(self, confirmed):
        """A task of `confirmed` settled top-level nodes and one open one. (The longest chain of
        parents in the repo is 16 deep, so a chain is a handful of title lines, not the history.)"""
        hist = ["# W7 · long · history\n"]
        for n in range(1, confirmed + 1):
            hist.append("### W7.%d · step %d\nStatus: open\nApproach: %s\nStatus: confirmed\nEvidence:\n- for: %s\n"
                        "Determination: Confirmed: %s\n" % (n, n, "do it " * 40, "saw it " * 60, "done " * 30))
        hist.append("### W7.%d · the open one\nStatus: open\nApproach: Next.\n" % (confirmed + 1))
        self.put("W7.history.md", "\n".join(hist))
        self.put("W7.log.md", "# W7 · log\n")
        self.put("W7.md", "# W7 · long\n\n## Goal\n\nDo it.\n\n## Decisions in force\n\n- it works (W7.1, 2026-11-20)\n")
        th.write_status("W7")
        return dfs_tree.load("W7")

    def test_the_digest_is_the_status_file_and_does_not_grow_with_the_history(self):
        small = self.long_task(10)
        big = self.long_task(300)
        d_small, d_big = dfs_tree.digest(small), dfs_tree.digest(big)
        self.assertLess(len(d_big), 4 * len(d_small))          # 30 times the history, nowhere near 30 times the digest
        self.assertLess(len(d_big) // 4, 10_000)
        self.assertGreater(len(self.get("W7.history.md")), 20 * len(self.get("W7.md")))
        self.assertIn("it works", d_big)

    def test_confirming_the_open_node_does_not_grow_the_status_file_unless_it_leaves_a_live_child(self):
        self.long_task(50)
        before = len(self.get("W7.md"))
        self.put("W7.history.md", th.append(self.get("W7.history.md"), "W7.51",
                                            ["Status: confirmed", "Determination: Confirmed: done."]))
        th.write_status("W7")
        self.assertLess(len(self.get("W7.md")), before)

    def test_the_digest_of_a_status_file_read_alone_equals_that_of_the_loaded_task(self):
        t = self.long_task(5)
        self.assertEqual(dfs_tree.digest(dfs_tree.parse(self.get("W7.md"), "W7")), dfs_tree.digest(t))


if __name__ == "__main__":
    unittest.main()
