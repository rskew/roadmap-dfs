#!/usr/bin/env python3
"""W153 E6 accounting: Determination length, and what a word budget in check.py would refuse.

  python3 docs/dfs-w153-e6.py [--at REV] [--sample N]   (arguments of dfs-w153-mine.py)

Counts every Determination (whitespace words, archives restored) of the three projects,
by project and by the node's effective status, then for budgets of 40, 60 and 80 words
the share refused and the words the author would no longer read. `--sample N` prints the
N longest-over-p90 Determinations in a fixed order (every k-th) for reading by hand.
"""
import importlib.util, statistics, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("mine", Path(__file__).with_name("dfs-w153-mine.py"))
mine = importlib.util.module_from_spec(spec); spec.loader.exec_module(mine)

argv = sys.argv[1:]
sample = 0
if "--sample" in argv:
    i = argv.index("--sample"); sample = int(argv[i + 1]); del argv[i:i + 2]
a = mine.args_of(argv)
P = mine.load(a)
import tree
rows = []
for proj, T in P.items():
    for tid, t in T.items():
        status, pruned = tree.effective(t)
        for n in t["nodes"]:
            d = mine.determination(n)
            if d:
                rows.append((proj, tid, n["id"], "withdrawn" if n["id"] in pruned else status.get(n["id"], n["status"]), mine.words(d), d))
def q(v): return f"n={len(v)} median={statistics.median(v)} p90={mine.pct(v, .9)} max={max(v)} mean={statistics.mean(v):.1f}"
print("Determination words")
for proj in P: print(f"  {proj}: {q([r[4] for r in rows if r[0] == proj])}")
for st in sorted({r[3] for r in rows}): print(f"  status {st}: {q([r[4] for r in rows if r[3] == st])}")
print(f"  all: {q([r[4] for r in rows])}")
tot = sum(r[4] for r in rows)
print("\nbudget  refused  share   words over budget (cut)  share of all words read")
for b in (40, 60, 80, 108):
    over = [r for r in rows if r[4] > b]
    cut = sum(r[4] - b for r in over)
    print(f"  {b:>4}  {len(over):>6}  {100*len(over)/len(rows):4.1f}%  {cut:>8}  {100*cut/tot:4.1f}%")
print(f"\nwords read in all Determinations: {tot}")
print("\nrefused by budget, by project (40/60/80):")
for proj in P:
    v = [r[4] for r in rows if r[0] == proj]
    print(f"  {proj}: " + " / ".join(f"{sum(w > b for w in v)}" for b in (40, 60, 80)) + f" of {len(v)}")
if sample:
    p90 = mine.pct([r[4] for r in rows], .9)
    over = sorted((r for r in rows if r[4] > p90), key=lambda r: (r[0], r[1], r[2]))
    k = max(1, len(over) // sample)
    print(f"\nSAMPLE over p90={p90}: {len(over)} Determinations, every {k}th")
    for r in over[::k][:sample]:
        print(f"\n[{r[0]} {r[2]} {r[3]} {r[4]}w] {r[5]}")
