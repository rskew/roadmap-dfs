#!/usr/bin/env python3
"""How often the replay loop's keep rule keeps a change, whatever the change does.

The rule (docs/dfs-optimization.md section 4, step 5) keeps a candidate when the changed arm
passes the first review in at least MARGIN more of the TASKS*REPS replays than the control,
and no task is worse by more than one replay of REPS. Replays run in a sandbox that hides
the original's evidence, so the rule is the lead alone; it guards against reviewer noise and
small samples. The chance of keeping is summed exactly over every outcome (`keep_exact`).

  python3 chance.py            # prints the table in section 4
"""
import sys
from collections import defaultdict
from math import comb

TASKS, REPS, MARGIN = 4, 3, 4


def pmf(n, p):
    return [comb(n, k) * p ** k * (1 - p) ** (n - k) for k in range(n + 1)]


def keep_exact(p0, p1, tasks=TASKS, reps=REPS, margin=MARGIN):
    """P(keep) when every replay passes alone with probability p0 (control) or p1 (changed).
    Returns (lead alone, lead and the per-task guard)."""
    f0, f1 = pmf(reps, p0), pmf(reps, p1)
    dist = {(0, True): 1.0}
    for _ in range(tasks):
        nd = defaultdict(float)
        for (d, ok), pr in dist.items():
            for a in range(reps + 1):
                for b in range(reps + 1):
                    nd[(d + b - a, ok and (a - b) <= 1)] += pr * f0[a] * f1[b]
        dist = nd
    alone = sum(pr for (d, ok), pr in dist.items() if d >= margin)
    both = sum(pr for (d, ok), pr in dist.items() if d >= margin and ok)
    return alone, both


def main():
    w = sys.stdout.write
    w("kept by the rule, exact (%d tasks x %d replays an arm; p = chance a replay passes its first review)\n" % (TASKS, REPS))
    w("%-14s %12s %8s %22s %12s\n" % ("margin (of 12)", "null p=0.5", "p=0.7", "p goes 0.5 to 0.9", "0.5 to 0.75"))
    for m in (2, 3, 4, 5):
        row = [100 * keep_exact(p0, p1, margin=m)[1] for p0, p1 in ((0.5, 0.5), (0.7, 0.7), (0.5, 0.9), (0.5, 0.75))]
        w("%-14d %11.1f%% %7.1f%% %21.1f%% %11.1f%%\n" % ((m,) + tuple(row)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
