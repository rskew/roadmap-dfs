#!/usr/bin/env python3
"""check.py's rules for a task in the history format (W203.8): the decision lines, the live tree,
the history that only grows, and the one size rule, each refused and then accepted once fixed.

Run:  python3 roadmap-dfs/scripts/test_checkhistory.py
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
import check as dfs_check    # noqa: E402
import paths as dfs_paths    # noqa: E402
import taskhistory as th     # noqa: E402

FX = HERE / "fixtures" / "history"
G = "git -c user.name=t -c user.email=t@t "


class Rules(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / ".dfs" / "items").mkdir(parents=True)
        (self.root / ".dfs" / "ROADMAP.md").write_text("# Roadmap\n")
        self._wr = dfs_paths.work_root
        dfs_paths.work_root = lambda: self.root
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(setattr, dfs_paths, "work_root", self._wr)
        self.git("init -q -b master")
        for n in ("W9.md", "W9.history.md", "W9.log.md"):
            self.put(n, (FX / n).read_text())
        self.git("add -A && " + G + "commit -qm base")

    def git(self, cmd):
        return subprocess.run("git " + cmd if not cmd.startswith("git") else cmd, shell=True, cwd=self.root,
                              check=True, capture_output=True, text=True).stdout

    def put(self, name, text):
        (self.root / ".dfs/items" / name).write_text(text)

    def get(self, name):
        return (self.root / ".dfs/items" / name).read_text()

    def check(self):
        self.git("add -A")
        argv, out = sys.argv, io.StringIO()
        sys.argv = ["check.py", "--staged"]
        try:
            with contextlib.redirect_stdout(out):
                return dfs_check.main(), out.getvalue()
        finally:
            sys.argv = argv

    def refused(self, word):
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn(word, out)

    def ok(self):
        code, out = self.check()
        self.assertEqual(code, 0, out)

    def append(self, nid, *lines):
        self.put("W9.history.md", th.append(self.get("W9.history.md"), nid, list(lines)))

    def add_node(self, nid, title, *lines):
        self.put("W9.history.md", self.get("W9.history.md").rstrip("\n") + "\n\n### %s · %s\n" % (nid, title)
                 + "\n".join(lines) + "\n")

    def status(self):
        """The status file as `tree.py status` would write it from the files on disk."""
        import tree as dfs_tree
        self.git("add -A")
        t = dfs_tree.combine("W9", [("", self.get("W9.md"), "", self.get("W9.log.md"), self.get("W9.history.md"))])
        self.put("W9.md", th.status_text(self.get("W9.md"), t))

    def test_the_fixture_is_clean(self):
        self.ok()

    # rule 1: each decision line cites a node or answer that exists
    def test_a_decision_citing_a_missing_node_or_answer_or_nothing(self):
        md = self.get("W9.md")
        self.put("W9.md", md.replace("(W9.3, 2026-11-20)", "(W9.99, 2026-11-20)"))
        self.refused("W9.99, which does not exist")
        self.put("W9.md", md.replace("(answer 2026-11-21T09:14:02Z)", "(answer 2026-01-01T00:00:00Z)"))
        self.refused("answer 2026-01-01T00:00:00Z, which does not exist")
        self.put("W9.md", md.replace("- four layers (answer 2026-11-21T09:14:02Z)", "- four layers"))
        self.refused("cites no node or answer")
        self.put("W9.md", md)
        self.ok()

    # rule 2: a cited node is confirmed, and one of them is not superseded
    def test_a_decision_citing_a_node_that_is_not_confirmed(self):
        md = self.get("W9.md")
        self.put("W9.md", md.replace("(W9.3, 2026-11-20)", "(W9.4, 2026-11-20)"))
        self.refused("W9.4, which is not confirmed")

    def test_a_decision_citing_a_node_refuted_by_a_correction_is_refused(self):
        """W9.3 says `Status: confirmed` in its file; the author's `correct` entry refutes it, and the
        decision-line check and the briefing's pull both read the corrected status (W203.18)."""
        import tree as dfs_tree
        self.put("W9.log.md", self.get("W9.log.md").rstrip("\n")
                 + "\n\n- 2026-11-22T10:00:00Z · correct · W9.3 · refuted\n  Not the charger.\n")
        self.refused("W9.3, which is not confirmed")
        t = dfs_tree.combine("W9", [("", self.get("W9.md"), "", self.get("W9.log.md"), self.get("W9.history.md"))])
        self.assertEqual(dfs_tree.by_id(t)["W9.3"]["status"], "confirmed")     # the file still says so
        self.assertEqual(dfs_tree.effective(t)[0]["W9.3"], "refuted")
        self.assertIn("refuted", th.pull(t, "W9.3"))

    def test_a_chain_of_nodes_may_be_cited_but_not_only_superseded_ones(self):
        md = self.get("W9.md")
        self.put("W9.md", md.replace("(W9.3, 2026-11-20)", "(W9.1, W9.2, W9.3, 2026-11-20)"))
        self.ok()
        self.git("add -A && " + G + "commit -qm chain")
        self.put("W9.md", md.replace("(W9.3, 2026-11-20)", "(W9.1, W9.2, 2026-11-20)"))
        self.refused("cites only superseded nodes (W9.1, W9.2)")

    # rule 3: the commit that supersedes a cited node rewrites the line
    def test_confirming_a_node_that_supersedes_a_cited_one_rewrites_the_line(self):
        self.append("W9.4", "Supersedes: W9.3", "Status: confirmed", "Determination: Confirmed: boost part chosen.")
        self.status()
        self.refused("still cites W9.3, which W9.4 now supersedes")
        md = self.get("W9.md")
        self.put("W9.md", md.replace("- charger is the BQ25619 (W9.3, 2026-11-20)",
                                     "- charger is the BQ25619, boost part chosen (W9.3, W9.4, 2026-11-26)"))
        self.ok()

    # rule 4: every open, started or parked node is in the live tree and no other
    def test_a_live_tree_that_lacks_a_node_or_holds_a_done_one(self):
        self.append("W9.4", "Status: confirmed", "Determination: Confirmed: done.")
        self.refused("the Live tree holds W9.4")
        self.status()
        self.ok()
        self.git("add -A && " + G + "commit -qm done")
        self.add_node("W9.7", "next", "Parent: W9.3", "Status: open", "Approach: Next.")
        self.refused("the Live tree lacks")

    # rule 5: the history only grows
    def test_a_history_line_removed_or_changed_is_refused_and_a_line_added_is_not(self):
        h = self.get("W9.history.md")
        self.put("W9.history.md", h.replace("Status: confirmed", "Status: refuted", 1))
        self.refused("lost or changed the line")
        self.put("W9.history.md", h)
        self.add_node("W9.7", "later", "Status: parked", "Approach: x", "Determination: Parked: not first.")
        self.status()
        self.ok()

    def test_a_merge_may_interleave_the_history_but_not_drop_a_line(self):
        h = self.get("W9.history.md").splitlines()
        self.assertEqual(dfs_check.check_history_grows("\n".join(reversed(h)), "\n".join(h), merge=True), [])
        self.assertTrue(dfs_check.check_history_grows("\n".join(reversed(h)), "\n".join(h)))
        self.assertTrue(dfs_check.check_history_grows("\n".join(h[1:]), "\n".join(h), merge=True))

    # rule 6: the status file is under budget, and adding a node never fails it
    def test_a_status_file_over_budget_is_refused_only_when_what_is_kept_by_hand_grew(self):
        saved = dfs_check.dfs_state.HANDOFF_BUDGET
        self.addCleanup(setattr, dfs_check.dfs_state, "HANDOFF_BUDGET", saved)
        dfs_check.dfs_state.HANDOFF_BUDGET = len(self.get("W9.md")) // 4 + 2
        self.put("W9.md", self.get("W9.md").replace("## Not taken", "- " + "a long decision " * 20 + "(W9.3, 2026-11-20)\n\n## Not taken"))
        self.refused("over the")
        self.put("W9.md", (FX / "W9.md").read_text())
        self.add_node("W9.7", "new", "Parent: W9.3", "Status: open", "Approach: " + "Grow the live tree. " * 40)
        self.status()
        code, out = self.check()
        self.assertEqual(code, 0, out)
        self.assertIn("over the", out)


if __name__ == "__main__":
    unittest.main()
