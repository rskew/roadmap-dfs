#!/usr/bin/env python3
"""Hypotheses in the task files: how many, how long, where the falsifier sits (W131.3).

    python3 docs/ux-hypotheses.py          (run from the repository root)

A Hypothesis is the assumption, then `Wrong if`; the screen shows the second half on its own.
Reads every task through `scripts/tree.py load`, so a Hypothesis moved to the archive is counted
in full. Counts `Wrong if`, an answer page, and the words before the falsifier.
"""
import glob, os, re, statistics as st, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import tree
rows = []
for f in sorted(glob.glob(".dfs/items/W*.md")):
    t = tree.load(os.path.basename(f)[:-3])
    for nd in t["nodes"]:
        h = " ".join((nd["fields"].get("Hypothesis") or "").split())
        if h:
            rows.append((int(nd["id"][1:].split(".")[0]), nd["id"], h))
n = len(rows)
print(f"hypotheses {n}; archived text left unread: {sum(h == 'archived.' for *_, h in rows)}")
for lo, hi in ((0, 60), (60, 100), (100, 1000)):
    r = [h for t, _, h in rows if lo <= t < hi]
    w = [h for h in r if re.search(r"wrong if", h, re.I)]
    b = [len(re.split(r"wrong if", h, flags=re.I)[0].split()) for h in w]
    print(f"W{lo}-W{hi - 1}: n={len(r)} with `Wrong if` {len(w)} with an artefact path "
          f"{sum('.dfs/artefacts/' in h for h in r)}; median words {st.median(len(h.split()) for h in r):.0f}; "
          f"words before `Wrong if` median {st.median(b):.0f}, over 40: {sum(x > 40 for x in b)}")
