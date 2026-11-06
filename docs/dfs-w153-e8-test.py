#!/usr/bin/env python3
"""E8 paired test: does the blind reviewer name fewer of a tree's unmet rubric clauses than the informed one?

  python3 docs/dfs-w153-e8-test.py FINDINGS.jsonl[,FINDINGS2.jsonl...] SCORES.jsonl[,SCORES2.jsonl...]

A TARGET is a (tree, clause) the quality judge scored unmet in BOTH its judgings of that tree (the
stricter truth), and a reviewer CATCHES it when one of its findings names the clause (the matching of
docs/dfs-w153-e8-score.py). Both reviewers see the same targets, so the comparison is paired: the
exact two-sided sign test on the targets only one reviewer caught. Prints the pool, the paired table,
the catch share of each reviewer with an exact (Clopper-Pearson) 95% interval, the test, and the
power of the test at assumed catch rates (independent catches, which is the most power the pool
could have), including the pool size that gives 80% power. Also the same table on clauses unmet in
EITHER judging, for the record.
"""
import json, math, os, sys
from collections import defaultdict


def binom_p2(k, n):
    """Exact two-sided sign test: P(a split at least as uneven as k of n | p = 1/2)."""
    if n == 0:
        return 1.0
    m = min(k, n - k)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(m + 1)) / 2 ** n)


def clopper(k, n, a=0.05):
    """Exact 95% interval for k of n, by bisection on the binomial tail."""
    def tail_ge(p):   # P(X >= k)
        return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k, n + 1))
    def tail_le(p):   # P(X <= k)
        return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(0, k + 1))
    lo = 0.0
    if k > 0:
        a_, b_ = 0.0, 1.0
        for _ in range(60):
            mid = (a_ + b_) / 2
            a_, b_ = (a_, mid) if tail_ge(mid) > a / 2 else (mid, b_)
        lo = b_
    hi = 1.0
    if k < n:
        a_, b_ = 0.0, 1.0
        for _ in range(60):
            mid = (a_ + b_) / 2
            a_, b_ = (mid, b_) if tail_le(mid) > a / 2 else (a_, mid)
        hi = a_
    return lo, hi


def power(n_targets, pi, pb, alpha=0.05):
    """P(reject) for n_targets paired targets, informed catch rate pi, blind pb, independent catches."""
    d_p = pi * (1 - pb) + pb * (1 - pi)
    q = pi * (1 - pb) / d_p if d_p else 0.5
    total = 0.0
    for d in range(n_targets + 1):
        pd = math.comb(n_targets, d) * d_p ** d * (1 - d_p) ** (n_targets - d)
        rej = sum(math.comb(d, k) * q ** k * (1 - q) ** (d - k) for k in range(d + 1) if binom_p2(k, d) < alpha)
        total += pd * rej
    return total


def main(argv):
    findings = [json.loads(l) for f in argv[0].split(",") for l in open(f)]
    runs = defaultdict(list)
    for f in argv[1].split(","):
        for l in open(f):
            r = json.loads(l)
            runs[r["replay"]].append({c for c, v in r["clauses"].items() if not v["met"]})
    named = {}
    for r in findings:
        named[(r["replay"], r["arm"])] = set().union(*[set(x) for x in r["lines"] if x]) if r["lines"] else set()
    skip = set(filter(None, os.environ.get("E8_EXCLUDE", "").split(",")))   # trees left out, named by the caller
    trees = sorted({k[0] for k in named} - skip)
    for label, agg in (("BOTH judgings", set.intersection), ("EITHER judging", set.union)):
        rows = []
        for t in trees:
            if len(runs[t]) < 2:
                continue
            for c in sorted(agg(*runs[t])):
                rows.append((t, c, c in named.get((t, "informed"), set()), c in named.get((t, "blind"), set())))
        n = len(rows)
        both = sum(1 for r in rows if r[2] and r[3])
        i_only = sum(1 for r in rows if r[2] and not r[3])
        b_only = sum(1 for r in rows if r[3] and not r[2])
        ni, nb = both + i_only, both + b_only
        print(f"\n== targets: clauses unmet in {label}: {n} over {len({r[0] for r in rows})} of {len(trees)} trees")
        print(f"caught by both {both}, informed only {i_only}, blind only {b_only}, neither {n - both - i_only - b_only}")
        for name, k in (("informed", ni), ("blind", nb)):
            lo, hi = clopper(k, n) if n else (0, 0)
            print(f"{name} caught {k} of {n} ({k / n:.0%}; 95% interval {lo:.0%} to {hi:.0%})" if n else name)
        d = i_only + b_only
        print(f"sign test on {d} discordant targets: informed-only {i_only}, p = {binom_p2(i_only, d):.3f}")
        if label.startswith("BOTH"):
            print("per target:", "; ".join(f"{t}/{c} {'I' if i else '-'}{'B' if b else '-'}" for t, c, i, b in rows))
            for pi, pb in ((0.6, 0.3), (0.5, 0.25), (0.8, 0.4)):
                need = next((m for m in range(5, 400) if power(m, pi, pb) >= 0.8), None)
                print(f"power at catch {pi:.0%} informed vs {pb:.0%} blind: {power(n, pi, pb):.0%} on {n} targets; "
                      f"80% needs {need} targets")
            print(f"smallest sign-test win: 6 targets caught by one reviewer only and none by the other (p {binom_p2(6, 6):.3f})")


if __name__ == "__main__":
    main(sys.argv[1:])
