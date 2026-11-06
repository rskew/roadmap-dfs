#!/usr/bin/env python3
"""Were raises that open with the ask answered faster or in fewer words than the rest? (W131.19)

    python3 docs/ux-ask-first.py          (run from the repository root)

Joins `docs/ux-raise-form.tsv`-style rows (the `asks_first` test of `docs/ux-raise-form.py`) with the hours
from each raise to its answer, and with the cause class in `docs/ux-raises-classes.tsv`, because the raises
that open with the ask are all questions about the author's own device or taste, and tooling raises do not
ask. So it prints the four against everything else, and against the non-tooling raises only.
"""
import os, re, statistics as st, subprocess, sys
from datetime import datetime

here = os.path.dirname(os.path.abspath(__file__))
out = subprocess.run([sys.executable, os.path.join(here, "ux-raise-form.py"), "--tsv"], capture_output=True, text=True, check=True).stdout
rows = [dict(zip(out.split("\n")[0].split("\t"), l.split("\t"))) for l in out.strip().split("\n")[1:]]
cls = {}
for l in open(os.path.join(here, "ux-raises-classes.tsv"), encoding="utf-8").read().split("\n")[1:]:
    if l.strip():
        p = l.split("\t"); cls[p[0]] = p[1]
ts = lambda s: datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")

# hours: the answer is the `answer` entry whose argument is the raise's ts, in that task's log (archive included)
sys.path.insert(0, os.path.join(here, "..", "scripts"))
import tree
for r in rows:
    log = tree.load(r["task"])["log"]
    a = [e for e in log if e["kind"] == "answer" and e["args"] and e["args"][0] == r["ts"]]
    r["hrs"] = (ts(a[0]["ts"]) - ts(r["ts"])).total_seconds() / 3600 if a else None
    r["cls"] = cls.get(r["node"], "?")
    r["ask"] = r["asks_first"] == "1" and not r["first"].startswith("Found:")   # "Found:" opens a summary, not the ask

med = lambda xs: f"{st.median(xs):.1f}" if xs else "-"
def show(label, rs):
    rs = [r for r in rs if r["hrs"] is not None]
    print(f"{label}: n={len(rs)}, hours to answer median {med([r['hrs'] for r in rs])} "
          f"(min {min(r['hrs'] for r in rs):.1f}, max {max(r['hrs'] for r in rs):.1f}), "
          f"answer words median {med([int(r['answer_words']) for r in rs])}, "
          f"raise words median {med([int(r['words']) for r in rs])}")

ask = [r for r in rows if r["ask"]]
rest = [r for r in rows if not r["ask"]]
nontool = [r for r in rest if r["cls"] != "tooling"]
show("opens with the ask", ask)
show("all others", rest)
show("others that are not tooling", nontool)
show("tooling", [r for r in rows if r["cls"] == "tooling"])
print("\nnode\tclass\task\thours\tanswer words\traise words")
for r in sorted(rows, key=lambda r: (not r["ask"], r["cls"], r["node"])):
    print(f"{r['node']}\t{r['cls']}\t{int(r['ask'])}\t{r['hrs']:.1f}\t{r['answer_words']}\t{r['words']}")
