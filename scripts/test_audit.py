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


def half_ok(sid):
    return audit.half(sid) == 0


if __name__ == "__main__":
    unittest.main()
