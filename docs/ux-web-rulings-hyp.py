#!/usr/bin/env python3
"""Of the artefact pages a Hypothesis names, how many post both a Confirm and a Refute (W131.53).

    python3 docs/ux-web-rulings-hyp.py        (run from the repository root)

Reads every `Hypothesis:` line in `.dfs/items/*.md` and every `**<id> Hypothesis**:` line in
`.dfs/archive/items/*.md`, takes the `.dfs/artefacts/<uuid>.html` pages they name, and looks each up in
`docs/ux-web-rulings-out/rulings.tsv` (W131.46's run of the real server in chromium). Prints the counts.
"""
import csv, glob, re
pages = set()
for path in glob.glob(".dfs/items/*.md") + glob.glob(".dfs/archive/items/*.md"):
    for line in open(path, encoding="utf-8"):
        if re.match(r"(\*\*[\w.@]+ )?Hypothesis\*{0,2}:", line):
            pages |= set(re.findall(r"\.dfs/artefacts/([0-9a-f-]{36})\.html", line))
rows = {r["page"][:-5]: r for r in csv.DictReader(open("docs/ux-web-rulings-out/rulings.tsv"), delimiter="\t")}
named = [p for p in pages if p in rows]
both = [p for p in named if rows[p]["result"] == "posts both"]
print("pages a Hypothesis names: %d; in the rulings run: %d; post both: %d; cannot: %d; named but not in the run: %d"
      % (len(pages), len(named), len(both), len(named) - len(both), len(pages) - len(named)))
