#!/usr/bin/env python3
"""What the briefing, the critic and the reviewer pull in from a task's history file (W203.9):
the node's section, its parents, what it supersedes and its tag neighbours, within an allowance,
on the fixture task W9 and on W9 grown with long nodes.

Run:  python3 roadmap-dfs/scripts/test_briefhistory.py
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import paths as dfs_paths       # noqa: E402
import state as dfs_state       # noqa: E402
import taskhistory as th        # noqa: E402
import tree as dfs_tree         # noqa: E402

FX = HERE / "fixtures" / "history"
G = "git -c user.name=t -c user.email=t@t "


def node(nid, title, *lines):
    return "\n### %s · %s\n%s\n" % (nid, title, "\n".join(lines))


class Pull(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / ".dfs" / "items").mkdir(parents=True)
        (self.root / ".dfs" / "ROADMAP.md").write_text("# Roadmap\n")
        self._wr = dfs_paths.work_root
        dfs_paths.work_root = lambda: self.root
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(setattr, dfs_paths, "work_root", self._wr)
        subprocess.run("git init -q -b master", shell=True, cwd=self.root, check=True)
        for n in ("W9.md", "W9.history.md", "W9.log.md"):
            (self.root / ".dfs/items" / n).write_text((FX / n).read_text())
        subprocess.run("git add -A && " + G + "commit -qm base", shell=True, cwd=self.root, check=True,
                       capture_output=True)

    def grow(self, text):
        p = self.root / ".dfs/items/W9.history.md"
        p.write_text(p.read_text().rstrip("\n") + "\n" + text)

    def replaces_w92(self):
        """W9.4 takes over from W9.2 (the fixture's W9.3 already did): so there is something it replaced."""
        p = self.root / ".dfs/items/W9.history.md"
        p.write_text(th.append(p.read_text(), "W9.4", ["Supersedes: W9.2"]))

    def tree(self):
        return dfs_tree.load("W9")

    # the sections are present
    def test_the_briefing_gives_the_node_its_parents_and_what_it_replaced(self):
        self.replaces_w92()
        b = dfs_state.brief("W9")
        self.assertIn("### The history of the node in hand", b)
        pulled = b.split("### The history of the node in hand")[1]
        self.assertIn("### W9.4 · pick the boost part", pulled)             # the node in hand, whole
        self.assertIn("Its parents, nearest first:", pulled)
        self.assertIn("### W9.3 · charger, third try", pulled)
        self.assertIn("What it supersedes, nearest first:", pulled)         # W9.3 replaced W9.2, which replaced W9.1
        self.assertLess(pulled.index("### W9.2 ·"), pulled.index("### W9.1 ·"))
        self.assertIn("for: the part is in stock at two shops.", pulled)     # the evidence comes with it
        self.assertNotIn("W9.6", pulled)                                    # no shared tag, no link
        self.assertNotIn("Recent nodes", pulled)                            # W9.1 to W9.3 are already given

    def test_a_node_that_shares_a_tag_comes_newest_first(self):
        self.grow(node("W9.7", "second boost", "Parent: W9.3", "Tags: boost", "Status: confirmed",
                       "Determination: Confirmed: part A."))
        self.grow(node("W9.8", "third boost", "Parent: W9.3", "Tags: boost", "Status: confirmed",
                       "Determination: Confirmed: part B."))
        pulled = dfs_state.brief("W9").split("### The history of the node in hand")[1]
        self.assertIn("Recent nodes that share a tag with it, newest first:", pulled)
        self.assertLess(pulled.index("### W9.8 ·"), pulled.index("### W9.7 ·"))

    def test_the_briefing_of_an_old_format_task_has_no_history_block(self):
        for n in ("W9.md", "W9.history.md"):
            (self.root / ".dfs/items" / n).unlink()
        (self.root / ".dfs/items/W9.md").write_text(
            "# W9 · old\n\n## Goal\n\nx\n\n## Tree\n\n### W9.1 · a\nStatus: open\nApproach: do it.\n")
        b = dfs_state.brief("W9")
        self.assertIn("W9.1 · a", b)
        self.assertNotIn("The history of", b)

    # the allowance
    def heavy(self):
        """Every charger node and five new boost nodes get a long Approach (about 1,000 tokens each)."""
        big = "word " * 800
        p = self.root / ".dfs/items/W9.history.md"
        s = p.read_text()
        for old in ("Use the BQ24072 charger.", "Move to the BQ25895 for a higher current.",
                    "Move to the BQ25619 because the BQ25895 is out of stock."):
            s = s.replace("Approach: " + old, "Approach: %s %s" % (old, big))
        p.write_text(s)
        for i in range(7, 12):
            self.grow(node("W9.%d" % i, "boost try %d" % i, "Tags: boost", "Status: confirmed",
                           "Approach: " + big, "Determination: Confirmed: try %d held." % i))

    def test_the_pull_stays_within_the_allowance_and_drops_in_order(self):
        self.replaces_w92()
        self.heavy()
        t = self.tree()
        tok = lambda s: len(s) // 4
        own = th.pull(t, "W9.4", allowance=0)
        extra = lambda allowance: tok(th.pull(t, "W9.4", allowance=allowance)) - tok(own)
        full = th.pull(t, "W9.4", allowance=100_000)
        self.assertEqual([i for i in range(7, 12) if "### W9.%d ·" % i in full], [7, 8, 9, 10, 11])
        self.assertIn("Approach: Compare two boost parts", own)             # the node's own section never drops
        self.assertNotIn("Recent nodes", own)                               # the tag neighbours are the first to go
        self.assertIn("### W9.3 · charger, third try\nStatus now: confirmed\nDetermination", own)   # cut, not dropped
        self.assertIn("### W9.1 · charger, first try\nStatus now: superseded\nDetermination", own)
        self.assertNotIn("word word", own)
        # neighbours go first, oldest first, while the parent and the replaced nodes stay whole
        mid = th.pull(t, "W9.4", allowance=extra(100_000) - 900)
        self.assertNotIn("### W9.7 ·", mid)
        self.assertIn("### W9.11 ·", mid)
        self.assertIn("Approach: Use the BQ24072", mid)
        # with no neighbours left, the replaced nodes are cut, furthest first, before the parent is
        three = extra(100_000) - 5 * 1000                                   # the parent and the two replaced, whole
        some = th.pull(t, "W9.4", allowance=three - 500)
        self.assertNotIn("Recent nodes", some)
        self.assertNotIn("Approach: Use the BQ24072", some)                 # W9.1, the furthest, is cut
        self.assertIn("Approach: Move to the BQ25619", some)                # W9.3, the parent, is whole
        # and the parent last
        none = th.pull(t, "W9.4", allowance=300)
        self.assertNotIn("Approach: Move to the BQ25619", none)
        for allowance in (500, 1500, 2500, 3500):
            self.assertLessEqual(extra(allowance), allowance)

    def test_the_default_allowance_is_four_thousand_tokens(self):
        self.assertEqual(th.PULL_ALLOWANCE, 4_000)
        self.replaces_w92()
        self.heavy()
        t = self.tree()
        own = len(th.pull(t, "W9.4", allowance=0)) // 4
        got = len(th.pull(t, "W9.4")) // 4
        self.assertLessEqual(got - own, 4_000)
        self.assertGreater(got - own, 3_000)                                # it did use the room it was given

    # a task moved from the old format has parents and no tags yet
    def test_a_migrated_task_gives_the_node_in_hand_its_parents_within_the_allowance(self):
        big = "word " * 800
        old = "# W8 · old task\n\n## Goal\n\nx\n\n## Tree\n\n"
        for i in range(1, 7):
            old += "### W8.%d · step %d\n%sStatus: %s\nApproach: %s%s\n%s\n" % (
                i, i, "Parent: W8.%d\n" % (i - 1) if i > 1 else "", "confirmed" if i < 6 else "open",
                "step %d. " % i, big,
                "Evidence:\n- for: it ran.\nDetermination: Confirmed: step %d." % i if i < 6 else "")
        (self.root / ".dfs/items/W8.md").write_text(old + "\n## Log\n\n")
        subprocess.run("git add -A && " + G + "commit -qm old", shell=True, cwd=self.root, check=True,
                       capture_output=True)
        th.migrate_task("W8", write=True)
        th.write_status("W8")
        t = dfs_tree.load("W8")
        self.assertTrue(t["new_format"])
        b = dfs_state.brief("W8")
        pulled = b.split("### The history of the node in hand")[1]
        self.assertIn("### W8.6 · step 6", pulled)
        self.assertIn("Its parents, nearest first:", pulled)
        self.assertIn("for: it ran.", pulled)
        # five parents of about 1,000 tokens: over 4,000, so the furthest are cut to title and Determination
        extra = (len(th.pull(t, "W8.6")) - len(th.pull(t, "W8.6", allowance=0))) // 4
        self.assertLessEqual(extra, th.PULL_ALLOWANCE)
        self.assertIn("Approach: step 5. word", pulled)                     # the nearest parent is whole
        self.assertNotIn("Approach: step 1. word", pulled)                  # the furthest is cut
        self.assertIn("### W8.1 · step 1\nStatus now: confirmed\nDetermination: Confirmed: step 1.", pulled)

    # the critic and the reviewer
    def test_the_critic_gets_the_history_of_each_node_it_judges_once(self):
        b = dfs_state.critic_brief("W9")
        self.assertIn("`.dfs/items/W9.md`, `.dfs/items/W9.history.md`", b)
        pulled = b.split("### The history of the nodes to judge")[1]
        self.assertIn("### W9.3 · charger, third try", pulled)
        self.assertIn("### W9.6 · latching valve", pulled)
        for nid in ("W9.1", "W9.2", "W9.3"):                                # each given once, then pointed to
            self.assertEqual(pulled.count("### %s ·" % nid), 1, nid)
        self.assertIn("(W9.3: given above)", pulled)

    def test_the_reviewer_gets_each_decision_with_the_history_behind_it(self):
        b = dfs_state.review_brief("W9")
        pulled = b.split("### The decisions in force, and the history behind each")[1]
        self.assertIn("- charger is the BQ25619 (W9.3, 2026-11-20)", pulled)
        self.assertIn("Determination: Confirmed: the BQ25619 charges at 1 A.", pulled)
        self.assertIn("### W9.1 · charger, first try", pulled)               # the chain it replaced
        self.assertIn("- four layers (answer 2026-11-21T09:14:02Z)", pulled)
        self.assertNotIn("### W9.4", pulled)                                # no parents or neighbours


    # W203.14: a moved task's sessions are not told to run what refuses or to write where `load` does not read
    def test_the_briefing_of_a_moved_task_gives_no_compact_or_shelve_advice(self):
        # a log older than the author's last word, and a status file far over the budget
        log = self.root / ".dfs/items/W9.log.md"
        log.write_text(log.read_text() + "\n- 2026-11-01T00:00:00Z · note\n  " + "word " * 5000 + "\n")
        st = self.root / ".dfs/items/W9.md"
        st.write_text(st.read_text() + "\n## Not taken\n\n" + "- word " * 20000 + "\n")
        b = dfs_state.brief("W9")
        self.assertNotIn("tree.py compact", b)
        self.assertNotIn("tree.py shelve", b)
        self.assertIn("over the", b)                                        # it still says the digest is over budget
        self.assertIn("shorten the hand-kept Decisions in force", b)
        self.assertIn("Its Live tree is the state of the work", b)

    def test_the_critic_and_the_reviewer_add_a_node_with_tree_add_on_a_moved_task(self):
        for b in (dfs_state.critic_brief("W9"), dfs_state.review_brief("W9")):
            self.assertIn("tree.py add W9 <parent node id", b)
            self.assertIn("Never write a `### <id> · …` section into `.dfs/items/W9.md`", b)
            self.assertIn("you put it in the task's history file (`.dfs/items/W9.history.md`)", b)
            self.assertNotIn("you put it in `.dfs/items/W9.md`", b)
            self.assertNotIn("### W9.7 · Fix:", b)                          # no hand-written section shape

    def test_the_briefings_of_an_old_format_task_are_as_they_were(self):
        for n in ("W9.md", "W9.history.md"):
            (self.root / ".dfs/items" / n).unlink()
        (self.root / ".dfs/items/W9.md").write_text(
            "# W9 · old\n\n## Goal\n\nx\n\n## Tree\n\n### W9.1 · a\nStatus: open\nApproach: do it.\n")
        self.assertIn("Its Tree is the state of the work", dfs_state.brief("W9"))
        for b in (dfs_state.critic_brief("W9"), dfs_state.review_brief("W9")):
            self.assertIn("you put it in `.dfs/items/W9.md`", b)
            self.assertIn("### W9.2 · Fix: <the issue, named as what to fix>", b)
            self.assertNotIn("tree.py add", b)


    # W203.16: one total allowance, however many nodes and decisions there are
    def many(self, count=40):
        """`count` confirmed nodes with long sections, each cited by its own decision line."""
        big = "word " * 400
        lines = []
        for i in range(10, 10 + count):
            self.grow(node("W9.%d" % i, "step %d" % i, "Parent: W9.3", "Status: confirmed",
                           "Approach: " + big, "Determination: Confirmed: step %d held. %s" % (i, "more " * 30)))
            lines.append("- step %d held (W9.%d, 2026-11-2%d)" % (i, i, i % 10))
        st = self.root / ".dfs/items/W9.md"
        text = st.read_text()
        st.write_text(text.replace("## Decisions in force\n\n", "## Decisions in force\n\n" + "\n".join(lines) + "\n", 1))
        return lines

    def test_the_reviewers_history_stays_within_the_allowance_and_keeps_every_decision(self):
        lines = self.many(40)
        b = dfs_state.review_brief("W9")
        pulled = b.split("### The decisions in force, and the history behind each")[1]
        for line in lines:
            self.assertIn(line, pulled)
        self.assertEqual(th.HISTORY_ALLOWANCE, 6_000)
        self.assertLessEqual(len(pulled) // 4, th.HISTORY_ALLOWANCE + 500)       # the lines, the answer and the note
        old = pulled.split("### W9.10 · step 10")[1].split("###")[0]
        self.assertNotIn("Approach:", old)                                      # the oldest is cut to title and Determination
        self.assertIn("Determination: Confirmed: step 10 held.", old)
        new = pulled.split("### W9.49 · step 49")[1].split("\n- ")[0]
        self.assertIn("Approach: " + "word " * 5, new)                          # the newest keeps its text

    def test_the_critics_history_stays_within_the_allowance_and_cuts_the_oldest_first(self):
        self.many(150)
        b = dfs_state.critic_brief("W9")
        pulled = b.split("### The history of the nodes to judge")[1]
        self.assertLessEqual(len(pulled) // 4, th.HISTORY_ALLOWANCE + 500)
        self.assertIn("### W9.159 · step 159", pulled)
        old = pulled.split("### W9.10 · step 10\n")[1].split("###")[0]
        self.assertIn("(cut to fit: `python3", old)                             # cut to a pointer, not left out
        self.assertLess(pulled.index("(cut to fit"), pulled.index("### W9.159"))
        self.assertNotIn("(cut to fit", pulled.split("### W9.159 · step 159")[1])

    # W203.23: the cut is sized on the joined text, where a shared neighbour is given once
    def test_nodes_that_share_neighbours_are_not_cut_while_the_joined_text_fits(self):
        for i in range(10, 24):
            self.grow(node("W9.%d" % i, "shared %d" % i, "Parent: W9.3", "Status: confirmed",
                           "Tags: shared", "Approach: " + "word " * 150, "Determination: Confirmed: held."))
        t = self.tree()
        ids = ["W9.%d" % i for i in range(10, 24)]
        whole = th.fit(t, ids, allowance=10 ** 9)
        joined = sum(len(x) for x in whole) // 4
        alone = sum(len(th.pull(t, i, seen=set())) for i in ids) // 4
        self.assertLess(joined, th.HISTORY_ALLOWANCE)                           # the joined text fits
        self.assertGreater(alone, th.HISTORY_ALLOWANCE)                         # each node's own text added up does not
        self.assertEqual(th.fit(t, ids), whole)

    def test_when_the_joined_text_is_over_the_allowance_the_oldest_are_cut_and_it_fits(self):
        for i in range(10, 70):
            self.grow(node("W9.%d" % i, "shared %d" % i, "Parent: W9.3", "Status: confirmed",
                           "Tags: shared", "Approach: " + "word " * 150, "Determination: Confirmed: held."))
        t = self.tree()
        ids = ["W9.%d" % i for i in range(10, 70)]
        out = th.fit(t, ids)
        self.assertLessEqual(sum(len(x) for x in out) // 4, th.HISTORY_ALLOWANCE)
        self.assertGreater(sum(len(x) for x in out) // 4, th.HISTORY_ALLOWANCE * 0.7)   # and uses most of it
        self.assertIn("Approach: " + "word " * 5, out[-1])                      # the newest is whole
        self.assertNotIn("Approach:", out[0])                                   # the oldest is cut

    def test_a_task_within_the_allowance_is_given_everything_as_before(self):
        out = th.fit(self.tree(), ["W9.3", "W9.6"])
        self.assertEqual(out[0], th.pull(self.tree(), "W9.3", seen=set()))
        self.assertNotIn("cut to fit", "\n".join(out))
        self.assertEqual(th.fit(self.tree(), ["W9.3", "W9.3"])[1], "(W9.3: given above)")

    def test_the_outline_of_a_moved_task_cuts_each_determination_and_an_old_one_does_not(self):
        self.grow(node("W9.10", "long one", "Parent: W9.3", "Status: confirmed",
                       "Determination: Confirmed: " + "long " * 100))
        b = dfs_state.critic_brief("W9")
        line = [l for l in b.split("### The tree")[1].splitlines() if "W9.10 [" in l][0]
        self.assertLess(len(line), 400)
        self.assertTrue(line.endswith("…"))
        old = "# W9 · old\n\n## Goal\n\nx\n\n## Tree\n\n### W9.1 · a\nStatus: confirmed\nApproach: do it.\n" \
              "Determination: Confirmed: " + "long " * 100 + "\n"
        for n in ("W9.md", "W9.history.md"):
            (self.root / ".dfs/items" / n).unlink()
        (self.root / ".dfs/items/W9.md").write_text(old)
        line = [l for l in dfs_state.critic_brief("W9").split("### The tree")[1].splitlines() if "W9.1 [" in l][0]
        self.assertGreater(len(line), 500)
        self.assertFalse(line.endswith("…"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
