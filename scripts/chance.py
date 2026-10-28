#!/usr/bin/env python3
"""How often the replay loop's keep rule keeps a change, whatever the change does.

The rule (docs/dfs-optimization.md section 4, step 5) keeps a candidate when
  test 1: the changed arm passes the first review in at least MARGIN more of the
          TASKS*REPS replays than the control, and no task is worse by more than one replay
          of REPS; and
  test 2: among the replays whose `score` shows none of the original's conclusions in a
          result ("unexposed"), each arm has at least MIN_UNEXPOSED, and the changed arm's
          pass fraction is not below the control's.
Test 1 is summed exactly over every outcome (`keep_exact`). Test 2 depends on which
replays were exposed, so it is simulated with a fixed seed (`keep_rate`).

  python3 chance.py            # prints both tables
"""
import random
import sys
from collections import defaultdict
from math import comb

TASKS, REPS, MARGIN, MIN_UNEXPOSED = 4, 3, 4, 2


def pmf(n, p):
    return [comb(n, k) * p ** k * (1 - p) ** (n - k) for k in range(n + 1)]


def keep_exact(p0, p1, tasks=TASKS, reps=REPS, margin=MARGIN):
    """P(test 1) when every replay passes alone with probability p0 (control) or p1
    (changed). Returns (lead alone, lead and the per-task guard)."""
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


def keep_rate(control, changed, exposure, runs=100_000, seed=97, tasks=TASKS, reps=REPS,
              margin=MARGIN, min_unexposed=MIN_UNEXPOSED):
    """Simulated share of runs of the loop that are kept, as (test 1 alone, both tests,
    both tests decidable). `control` and `changed` are (p unexposed, p exposed): the chance
    a replay passes its first review. `exposure` is the chance a replay is shown the
    original's conclusions. The last figure is the share of runs in which each arm has
    min_unexposed unexposed replays, the only runs test 2 can decide."""
    rnd = random.Random(seed)
    first = both = decidable = 0
    for _ in range(runs):
        lead, ok = 0, True
        pu, nu = [0, 0], [0, 0]
        for _ in range(tasks):
            tot = [0, 0]
            for arm, (p_u, p_x) in enumerate((control, changed)):
                for _ in range(reps):
                    exposed = rnd.random() < exposure
                    passed = rnd.random() < (p_x if exposed else p_u)
                    tot[arm] += passed
                    if not exposed:
                        nu[arm] += 1
                        pu[arm] += passed
            lead += tot[1] - tot[0]
            ok = ok and tot[0] - tot[1] <= 1
        if not (lead >= margin and ok):
            continue
        first += 1
        if min(nu) >= min_unexposed:
            decidable += 1
            if pu[1] * nu[0] >= pu[0] * nu[1]:
                both += 1
    return first / runs, both / runs, decidable / runs


def decidable(exposure, arm_size=TASKS * REPS, min_unexposed=MIN_UNEXPOSED):
    """Exact P(each arm has at least min_unexposed unexposed replays)."""
    one = sum(pmf(arm_size, 1 - exposure)[min_unexposed:])
    return one * one


def main():
    w = sys.stdout.write
    w("test 1, exact (12 replays an arm; p = chance a replay passes its first review)\n")
    for p in (0.5, 0.7):
        w("  null p=%.1f: lead alone %.1f%%, with the per-task guard %.1f%%\n" % ((p,) + tuple(100 * x for x in keep_exact(p, p))))
    for p0, p1 in ((0.5, 0.75), (0.5, 0.9)):
        w("  p %.2f -> %.2f: kept %.1f%%\n" % (p0, p1, 100 * keep_exact(p0, p1)[1]))
    w("\ntests 1 and 2, simulated (100,000 runs, seed 97); exposure = chance a replay is shown the original's conclusions\n")
    w("  each arm has >= %d unexposed replays (decidable) in: " % MIN_UNEXPOSED
      + ", ".join("exposure %.2f %.1f%%" % (e, 100 * decidable(e)) for e in (0.5, 0.75, 1.0)) + "\n")
    w("  %-52s %8s %8s %12s\n" % ("scenario", "exposure", "test 1", "tests 1+2"))
    scenarios = [
        ("null, p 0.5 whether exposed or not", (0.5, 0.5), (0.5, 0.5)),
        ("null, p 0.7 whether exposed or not", (0.7, 0.7), (0.7, 0.7)),
        ("null, exposed replays pass more (0.5 / 0.7)", (0.5, 0.7), (0.5, 0.7)),
        ("real gain 0.5 -> 0.9", (0.5, 0.5), (0.9, 0.9)),
        ("real gain 0.5 -> 0.75", (0.5, 0.5), (0.75, 0.75)),
        ("answer-lookup only: unexposed 0.5 -> 0.5, exposed 0.5 -> 0.9", (0.5, 0.5), (0.5, 0.9)),
    ]
    for name, c, ch in scenarios:
        for e in (0.5, 0.75, 1.0):
            a, b, _ = keep_rate(c, ch, e)
            w("  %-52s %8.2f %7.1f%% %11.1f%%\n" % (name, e, 100 * a, 100 * b))
    return 0


if __name__ == "__main__":
    sys.exit(main())
