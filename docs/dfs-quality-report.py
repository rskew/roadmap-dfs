#!/usr/bin/env python3
"""Report on docs/dfs-quality-scores.jsonl (docs/dfs-quality-judge.py): the judge's spread, the trees' quality.

  python3 docs/dfs-quality-report.py docs/dfs-quality-scores.jsonl
"""
import collections, json, sys
rows = [json.loads(l) for l in open(sys.argv[1])]
by = collections.defaultdict(dict)
for r in rows:
    by[r["replay"]][r["run"]] = r
print("replay    run1 run2  of   clause flips   mean")
diffs, flips, clauses, tot = [], 0, 0, 0
for k, v in sorted(by.items()):
    a, b = v[1], v[2]
    fl = [c for c in a["clauses"] if a["clauses"][c]["met"] != b["clauses"][c]["met"]]
    diffs.append(abs(a["met"] - b["met"]) / a["n"]); flips += len(fl); clauses += a["n"]
    tot += (a["met"] == b["met"])
    print(f'{k:9} {a["met"]:3} {b["met"]:4} {a["n"]:3}  {",".join(fl) or "-":14} {(a["met"] + b["met"]) / 2:5.1f}')
n = len(by)
print(f"\nexact score agreement {tot} of {n} trees; clause agreement {clauses - flips} of {clauses} ({100 * (clauses - flips) / clauses:.1f}%)")
print(f"|run1-run2| as share of clauses: mean {sum(diffs) / n:.3f}, max {max(diffs):.3f}")
print("\nclause met rate over the two runs (all arms):")
rate = collections.defaultdict(lambda: [0, 0])
for r in rows:
    for c, d in r["clauses"].items():
        rate[(r["replay"].split("-")[0], c)][0] += d["met"]; rate[(r["replay"].split("-")[0], c)][1] += 1
for (t, c), (m, k) in sorted(rate.items()):
    if m != k: print(f"  {t} {c}: {m} of {k}")
