#!/usr/bin/env python3
"""The two-tier pair rule on the replays: quality first (docs/dfs-quality-scores.jsonl), then credits.

  python3 docs/dfs-quality-pairs.py REPLAYS_DIR docs/dfs-quality-scores.jsonl [--margin 1.5]

A pair is better when the changed tree's mean score exceeds the control's by more than the margin (in
clauses), worse when it falls short by more; within the margin quality ties and credits decide: better at
0.70 of the control's credits or less, worse at 1.43 times or more, else same. Prints the no-change rate at
which step 5's sequence keeps (pairs of two different control replays, both orders) and the candidate-1 pairs.
"""
import collections, importlib.util, json, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("v", Path(__file__).with_name("dfs-replay-verdicts.py"))
v = importlib.util.module_from_spec(spec); spec.loader.exec_module(v)
rows = v.replays(sys.argv[1])
q = collections.defaultdict(list)
for l in open(sys.argv[2]):
    r = json.loads(l); q[r["replay"]].append(r["met"] / r["n"])      # a share of the rubric's clauses
n_of = {k.split("-")[0]: json.loads(l)["n"] for k in q for l in open(sys.argv[2]) if json.loads(l)["replay"] == k}
M = float(sys.argv[sys.argv.index("--margin") + 1]) if "--margin" in sys.argv else 1.5


def verdict(ch, ct, f=1.0):
    dq = (sum(q[f"{ch['task']}-{ch['tag']}"]) - sum(q[f"{ct['task']}-{ct['tag']}"])) / 2 * n_of[ch["task"]]
    if abs(dq) > M:
        return ("better" if dq > 0 else "worse"), "quality"
    r = ch["credits"] * f / ct["credits"]
    return ("better" if r <= 0.70 + 1e-9 else "worse" if r >= 1.43 - 1e-9 else "same"), "credits"


ctl = [r for r in rows if r["tag"][0] == "c"]
for f in (1.0, 0.7, 0.5):
    cnt = collections.Counter(); tasks = sorted({r["task"] for r in ctl})
    for t in tasks:
        vs = [r for r in ctl if r["task"] == t]; k = len(vs) * (len(vs) - 1)
        for i, a in enumerate(vs):
            for j, b in enumerate(vs):
                if i != j: cnt[verdict(a, b, f)[0]] += 1 / k / len(tasks)
    d = {"b": cnt["better"], "s": cnt["same"], "w": cnt["worse"]}
    print(f"no-change pairs, changed credits x{f}: {d['b']:.0%}/{d['s']:.0%}/{d['w']:.0%} better/same/worse, kept {100 * v.sequence(d):.1f}%")
print("\ncandidate 1 (b*) against its same-sitting control:")
for t, c in (("W5", "c2a"), ("W36", "c4a"), ("W22", "c2a"), ("W11", "c2a")):
    ch = next(r for r in rows if r["task"] == t and r["tag"][0] == "b"); ct = next(r for r in rows if r["task"] == t and r["tag"] == c)
    mean = lambda x: sum(q[f"{x['task']}-{x['tag']}"]) / 2 * n_of[t]
    print(f"  {t}: {verdict(ch, ct)} | clauses {mean(ch):.1f} vs {mean(ct):.1f} of {n_of[t]} | credits {ch['credits']}{'+' if ch['unpriced'] else ''} vs {ct['credits']}")
