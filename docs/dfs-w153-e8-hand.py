#!/usr/bin/env python3
"""Count the hand matching in docs/dfs-w153-e8-hand.tsv: which of the 32 targets (clauses the quality
judge scored unmet in both judgings, over 23 reviewed trees) each reviewer's findings name.
P = the clause is the finding's headline, M = a line of the minor-findings node states it unmet,
D = doubtful (rare, very unlikely, or a neighbouring defect). Three readings: headline (P), strict (P or M),
lenient (P, M or D). Exact two-sided sign test on the targets only one reviewer caught.

  python3 docs/dfs-w153-e8-hand.py
"""
import csv
from math import comb
from pathlib import Path

rows = list(csv.DictReader(open(Path(__file__).with_suffix(".tsv"), encoding="utf-8"), delimiter="\t"))


def sign(a, b):
    n, k = a + b, min(a, b)
    return min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n) if n else 1.0


print(f"targets: {len(rows)}")
for name, ok in (("headline (P)", "P"), ("strict (P or M)", "PM"), ("lenient (P, M or D)", "PMD")):
    i = {r["target"] for r in rows if r["informed"] in ok}
    b = {r["target"] for r in rows if r["blind"] in ok}
    print(f"{name}: informed {len(i)}, blind {len(b)}, both {len(i & b)}, informed only {len(i - b)}, "
          f"blind only {len(b - i)}, sign test p = {sign(len(i - b), len(b - i)):.3f}")
