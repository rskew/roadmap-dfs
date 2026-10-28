#!/usr/bin/env python3
"""audit.py: the recounts read the trees as the screen does, and say what each row is."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit  # noqa: E402
import tree  # noqa: E402

TASK = """# W9 · A task

## Goal

Do it.

## Tree

### W9.1 · First
Status: confirmed
Approach: a
Determination: done

### W9.2 · Second
Parent: W9.1
Status: confirmed
Approach: b
Determination: done

### W9.3 · Third
Parent: W9.2
Status: confirmed
Approach: c
Determination: done

### W9.4 · Alternative
Parent: W9.1
Status: parked
Approach: d
Determination: not first

## Log

- 2026-10-01T00:00:00Z · review · issues
  W9.5 added: the merge loses the raise (reproduced with a stub).
  W9.6 · Minor findings from the review (a wording)
  Unmarked assumption: W9.2 chose X with no Hypothesis.
  Tests run: test_x passes.
  none
- 2026-10-02T00:00:00Z · backtrack · W9.2
- 2026-10-03T00:00:00Z · correct · W9.1 · refuted
"""


class NodesTest(unittest.TestCase):
    def test_a_backtrack_and_a_correction_are_counted_though_no_status_says_refuted(self):
        t = tree.combine("W9", [("", TASK, "")])
        raw, eff, disp, touched = audit.node_counts({"W9": t})
        self.assertEqual(raw["refuted"], 0)             # what a count of `Status:` lines sees
        self.assertGreaterEqual(eff["refuted"], 1)      # what the tree says after the author's ruling
        self.assertEqual((disp["backtrack"], disp["refuted"]), (1, 1))
        self.assertEqual(sum(eff.values()), 4)


class FindingsTest(unittest.TestCase):
    def test_each_finding_line_has_a_class_and_the_other_lines_are_not_findings(self):
        t = tree.combine("W9", [("", TASK, "")])
        rows = audit.finding_rows({"W9": t})
        self.assertEqual([r[2] for r in rows], ["defect+reproduced", "minor", "assumption"])
        self.assertTrue(all(r[0] == "W9" for r in rows))


class SplitTest(unittest.TestCase):
    def test_the_split_is_stable_and_uses_both_halves(self):
        halves = {audit.half("s%d" % i) for i in range(40)}
        self.assertEqual(halves, {0, 1})
        self.assertEqual(audit.half("abc"), audit.half("abc"))

    def test_a_rate_fitted_on_one_half_is_tested_on_the_other(self):
        sessions, costs = [], {}
        for i in range(40):
            sid = "s%d" % i
            sessions.append({"sid": sid, "model": "m", "units": 1000.0, "project": "p"})
            costs[sid] = 2.0 * (1 if half_ok(sid) else 1.5)   # the other half prices differently
        err = audit.held_out_error(sessions, costs)
        self.assertGreater(max(err["p"]), 0.2)          # a fit on the same sessions would show none


class RoundsTest(unittest.TestCase):
    def setUp(self):
        import subprocess
        import tempfile
        self.dir = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, self.dir)

        def run(*a, env=None):
            subprocess.run(["git", "-C", self.dir, *a], check=True, capture_output=True, env=env)
        run("init", "-q")
        run("config", "user.name", "t")
        run("config", "user.email", "t@t")
        pad = "".join("# filler %d\n" % i for i in range(30))     # keeps keep_me out of the change's context
        Path(self.dir, "a.py").write_text("def keep_me():\n    pass\n" + pad + "def touched_one():\n    pass\n")
        run("add", "a.py")
        run("commit", "-qm", "W9.1: base")
        Path(self.dir, "a.py").write_text("def keep_me():\n    pass\n" + pad + "def touched_one():\n    return 1\n\ndef brand_new():\n    pass\n")
        run("commit", "-qam", "W9.2: the fix")
        self.window = [audit.git(self.dir, "rev-parse", "HEAD").stdout.strip()]

    def kind(self, line):
        return audit.classify(self.dir, line, self.window)

    def test_a_defect_in_code_the_fix_left_alone_was_there_at_the_earlier_review(self):
        self.assertEqual(self.kind("W9.3 added: `keep_me` drops the raise"), "present")

    def test_a_defect_in_code_the_fix_wrote_or_changed_is_not_counted_as_there(self):
        self.assertEqual(self.kind("W9.3 added: brand_new() loses the raise"), "introduced")
        self.assertEqual(self.kind("W9.3 added: touched_one() returns early"), "maybe-introduced")

    def test_a_line_naming_no_code_or_a_task_with_no_window_is_unplaced(self):
        self.assertEqual(self.kind("W9.3 added: the page stays blank"), "unplaced")
        self.assertEqual(audit.classify(self.dir, "W9.3 added: `keep_me` drops it", []), "unplaced")

    def test_names_come_from_backticks_snake_case_and_calls_only(self):
        self.assertEqual(audit.named_code("W9.3 added: `a.py` and some_name and call() but not Prose or `two words`"),
                         {"a.py", "some_name", "call"})


def half_ok(sid):
    return audit.half(sid) == 0


if __name__ == "__main__":
    unittest.main()
