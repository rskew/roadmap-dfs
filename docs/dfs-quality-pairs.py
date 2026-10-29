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
# met[replay] = clauses met in each of the two judgings; all tie tests use these integers
met = collections.defaultdict(list); n_of = {}
for l in open(sys.argv[2]):
    r = json.loads(l); met[r["replay"]].append(r["met"]); n_of[r["replay"].split("-")[0]] = r["n"]
M = float(sys.argv[sys.argv.index("--margin") + 1]) if "--margin" in sys.argv else 1.5
# A difference of two two-judging means exceeds M clauses when the sums over the judgings differ by more than
# 2 * M. Both are whole numbers when M is a multiple of 0.5, so the test is on integers (a mean of 7.5 against
# 9.0 is a difference of exactly 1.5, a tie, which the float mean of shares read as 1.5000000000000007).
LIM = round(2 * M * 2)          # in half-clauses: the sums' difference times 2 must exceed this
rname = lambda r: f"{r['task']}-{r['tag']}"


def mean(r):
    return sum(met[rname(r)]) / len(met[rname(r)])


def verdict(ch, ct, f=1.0):
    """('better'|'worse'|'same'|'unresolved', tier). ch is the changed replay, ct the control."""
    ds = sum(met[rname(ch)]) - sum(met[rname(ct)])          # sums over the two judgings
    if abs(ds) * 2 > LIM:
        return ("better" if ds > 0 else "worse"), "quality"
    return credits_verdict(ch["credits"] * f, ch["unpriced"], ct["credits"], ct["unpriced"]), "credits"


def credits_verdict(a, a_cut, b, b_cut):
    """Credits decide: better at 0.70 of the control's or less, worse at 1.43 times or more, else same.
    A cut-off replay's credits are a lower bound (its true cost is higher), so the ratio a/b is a lower bound
    when the changed arm was cut off and an upper bound when the control was; a verdict that the unknown rest
    could change is `unresolved`."""
    r = a / b
    if a_cut and b_cut:
        return "unresolved"
    if a_cut:
        return "worse" if r >= 1.43 - 1e-9 else "unresolved"
    if b_cut:
        return "better" if r <= 0.70 + 1e-9 else "unresolved"
    return "better" if r <= 0.70 + 1e-9 else "worse" if r >= 1.43 - 1e-9 else "same"


ctl = [r for r in rows if r["tag"][0] == "c"]
print("no-change pairs: two different control replays of one task, both orders, changed credits scaled by f")
for f in (1.0, 0.7, 0.5):
    cnt = collections.Counter(); tasks = sorted({r["task"] for r in ctl})
    for t in tasks:
        vs = [r for r in ctl if r["task"] == t]; k = len(vs) * (len(vs) - 1)
        for i, a in enumerate(vs):
            for j, b in enumerate(vs):
                if i != j: cnt[verdict(a, b, f)[0]] += 1 / k / len(tasks)
    u = cnt["unresolved"]
    # headline: a cut-off replay's lower bound is read as its cost (as W130.28 did); the range puts every
    # unresolved pair first as better (the most a rerun could give) and then as worse
    def keep(extra_b=0.0, extra_w=0.0, extra_s=0.0):
        d = {"b": cnt["better"] + extra_b, "s": cnt["same"] + extra_s, "w": cnt["worse"] + extra_w}
        return d, 100 * v.sequence(d)
    exact = collections.Counter()
    for t in tasks:
        vs = [r for r in ctl if r["task"] == t]; k = len(vs) * (len(vs) - 1)
        for i, a in enumerate(vs):
            for j, b in enumerate(vs):
                if i != j:
                    ds = sum(met[rname(a)]) - sum(met[rname(b)])
                    if abs(ds) * 2 > LIM: x = "better" if ds > 0 else "worse"
                    else:
                        rr = a["credits"] * f / b["credits"]
                        x = "better" if rr <= 0.70 + 1e-9 else "worse" if rr >= 1.43 - 1e-9 else "same"
                    exact[x] += 1 / k / len(tasks)
    dx = {"b": exact["better"], "s": exact["same"], "w": exact["worse"]}
    hi = keep(extra_b=u)[1]; lo = keep(extra_w=u)[1]
    print(f"  f={f}: {dx['b']:.0%}/{dx['s']:.0%}/{dx['w']:.0%} better/same/worse with the bounds read as costs, kept {100 * v.sequence(dx):.1f}%"
          f"; {u:.0%} of pairs unresolved (a cut-off), kept {min(lo, hi):.1f}% to {max(lo, hi):.1f}% if all of those went one way")
qp = [(rname(a), rname(b)) for a in ctl for b in ctl if a is not b and a["task"] == b["task"]
      and abs(sum(met[rname(a)]) - sum(met[rname(b)])) * 2 > LIM]
print(f"control pairs decided by quality (a difference over {M} clauses): {len(qp)} of "
      f"{sum(len([r for r in ctl if r['task'] == t]) * (len([r for r in ctl if r['task'] == t]) - 1) for t in {r['task'] for r in ctl})} ordered pairs: "
      + ", ".join(f"{a} v {b}" for a, b in qp))
print("\ncandidate 1 (b*) against its same-sitting control, and against the mean of the task's control trees:")
# The control run in the same sitting as the changed arm (tag numbers do not give it: W5-b1a ran beside W5-c2a,
# W36-b4a beside W36-c4a). Taken from the sittings' order in W130's log; edit it if a replay is added.
SAME_SITTING = {"W5": "c2a", "W36": "c4a", "W22": "c2a", "W11": "c2a"}
for t, c in SAME_SITTING.items():
    ch = next(r for r in rows if r["task"] == t and r["tag"][0] == "b"); ct = next(r for r in rows if r["task"] == t and r["tag"] == c)
    print(f"  {t}: {verdict(ch, ct)} | clauses {mean(ch):.1f} vs {mean(ct):.1f} of {n_of[t]} | credits {ch['credits']}{'+' if ch['unpriced'] else ''} vs {ct['credits']}")
    cs = [r for r in ctl if r["task"] == t]
    pm = sum(mean(r) for r in cs) / len(cs); pc = sum(r["credits"] for r in cs) / len(cs)
    dq = mean(ch) - pm
    pv = ("better" if dq > M else "worse" if dq < -M else credits_verdict(ch["credits"], ch["unpriced"], pc, any(r["unpriced"] for r in cs)))
    print(f"      against the pooled mean of {len(cs)} controls: {pv} | clauses {mean(ch):.1f} vs {pm:.2f} (controls {', '.join(f'{mean(r):g}' for r in cs)}) | credits {ch['credits']}{'+' if ch['unpriced'] else ''} vs {pc:.1f}{'+' if any(r['unpriced'] for r in cs) else ''}")
