#!/usr/bin/env python3
"""Is a raise that names an answer page answered faster, like with like? (W131.29)

    python3 docs/ux-answer-page.py [ITEMS_DIR] > docs/ux-answer-page.txt

Reads the raises and answers through docs/ux-raises.py's parsing (run as a module-free copy of
the same regex), joins each session-written raise to its class in docs/ux-raises-classes.tsv
and compares, per class, the raises that name an answer page with those that do not:
n, median/min/max hours to answer, median answer words. Then the same on raises from the
first page raise onward only (earlier raises could not name a page the way later ones do),
and a nearest-in-time pairing: each page raise against the nearest-in-time same-class raise
without a page. Reads only.
"""
import csv, glob, os, re, statistics as st, sys
from datetime import datetime

d = sys.argv[1] if len(sys.argv) > 1 else ".dfs/items"
ent = re.compile(r"^- (\d{4}-\d\d-\d\dT[\d:]+Z) · (\w+)(?: · ([^\n]*))?$")
ts = lambda s: datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
raises, answers = {}, {}
arch = os.path.join(d, "..", "archive", "items")
for f in sorted(glob.glob(os.path.join(d, "W*.md")) + glob.glob(os.path.join(arch, "W*.md"))):
    t = os.path.basename(f)[:-3]
    text = open(f, encoding="utf-8").read()
    text = text.split("## Log")[-1] if "## Log" in text else text
    cur = None
    for line in text.split("\n"):
        m = ent.match(line)
        if m:
            cur = [m[1], m[2], m[3] or "", []]
            if m[2] == "raise": raises[(t, m[1])] = cur
            elif m[2] == "answer": answers[(t, (m[3] or "").split(" ")[0])] = cur
        elif cur is not None and (line.startswith("  ") or not line.strip()):
            if line.strip(): cur[3].append(line.strip())
        else:
            cur = None
cls = {}
for r in csv.reader(open("docs/ux-raises-classes.tsv", encoding="utf-8"), delimiter="\t"):
    cls[r[0]] = r[1]
rows = []
for (t, r), e in raises.items():
    a = answers.get((t, r))
    if not e[2] or not a: continue          # session-written (has a node) and answered
    body = " ".join(e[3])
    rows.append(dict(t=t, node=e[2], at=ts(r), cls=cls.get(e[2], cls.get(t, "?")), page="Answer with:" in body,
                     hrs=(ts(a[0]) - ts(r)).total_seconds() / 3600, aw=len(" ".join(a[3]).split())))
rows.sort(key=lambda r: r["at"])
med = lambda xs: st.median(xs) if xs else float("nan")


def line(lab, sub):
    if not sub:
        return f"  {lab:<14} n=0"
    h = [r["hrs"] for r in sub]
    return (f"  {lab:<14} n={len(sub):<2} hours median {med(h):5.1f} min {min(h):5.1f} max {max(h):6.1f}"
            f"   answer words median {med([r['aw'] for r in sub]):.0f}   [{', '.join(r['node'] for r in sub)}]")


first = min(r["at"] for r in rows if r["page"])
print(f"session-written, answered raises: {len(rows)}; classed: {sum(r['cls'] != '?' for r in rows)}; "
      f"with a page: {sum(r['page'] for r in rows)}; first page raise {first:%Y-%m-%d}")
print("\nall classes")
print(line("with page", [r for r in rows if r["page"]]))
print(line("without page", [r for r in rows if not r["page"]]))
print("\nsince the first page raise")
late = [r for r in rows if r["at"] >= first]
print(line("with page", [r for r in late if r["page"]]))
print(line("without page", [r for r in late if not r["page"]]))
for c in sorted({r["cls"] for r in rows}):
    sub = [r for r in rows if r["cls"] == c]
    if not any(r["page"] for r in sub): continue
    print(f"\nclass {c}")
    print(line("with page", [r for r in sub if r["page"]]))
    print(line("without page", [r for r in sub if not r["page"]]))
    print(line("  since first", [r for r in sub if not r["page"] and r["at"] >= first]))
print("\nnearest-in-time same-class raise without a page, for each page raise")
diffs = []
for p in (r for r in rows if r["page"]):
    pool = [r for r in rows if not r["page"] and r["cls"] == p["cls"] and r["node"] != p["node"]]
    if not pool:
        print(f"  {p['node']:<8} {p['cls']:<18} {p['hrs']:6.1f} h   (no raise of the class without a page)")
        continue
    q = min(pool, key=lambda r: abs((r["at"] - p["at"]).total_seconds()))
    diffs.append(p["hrs"] - q["hrs"])
    print(f"  {p['node']:<8} {p['cls']:<18} {p['hrs']:6.1f} h vs {q['node']:<8} {q['hrs']:6.1f} h   page - other {p['hrs'] - q['hrs']:+6.1f}")
print(f"pairs {len(diffs)}; page raise faster in {sum(x < 0 for x in diffs)}; median difference {med(diffs):+.1f} h")

# How much could chance explain? An exact permutation test on the medians' difference: every way of
# choosing which 6 of the raises since the first page raise are the "page" ones (24 choose 6 is 134,596).
from itertools import combinations
hrs = [r["hrs"] for r in late]
k = sum(r["page"] for r in late)
obs = med([r["hrs"] for r in late if not r["page"]]) - med([r["hrs"] for r in late if r["page"]])
hit = tot = 0
for idx in combinations(range(len(hrs)), k):
    s = set(idx)
    a = [hrs[i] for i in s]; b = [hrs[i] for i in range(len(hrs)) if i not in s]
    tot += 1; hit += (med(b) - med(a)) >= obs
print(f"\nsince the first page raise: median hours without a page minus with one = {obs:.1f}; "
      f"in {hit} of {tot} ways to pick {k} of {len(hrs)} raises as the page ones it is at least that large (p = {hit/tot:.2f})")

if "--json" in sys.argv:
    import json
    print(json.dumps([[r["node"], r["cls"], int(r["page"]), round(r["hrs"], 1), r["aw"]] for r in late]))
