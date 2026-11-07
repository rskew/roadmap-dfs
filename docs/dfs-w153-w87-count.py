#!/usr/bin/env python3
"""W153.38: per arm of the W87 replay, Determination words, nodes, Hypotheses, and (pooled arm) the commits the
word budget refused, read from the run logs. usage: python3 docs/dfs-w153-w87-count.py REPLAYS_DIR"""
import re, sys, glob, json
from pathlib import Path
root = Path(sys.argv[1])
for arm in ("c1", "c2", "c3", "p1"):
    clone = root / f"dfs-replay-W87-{arm}"
    nested = clone / ".dfs/worktrees/W87/.dfs/items/W87.md"
    f = nested if nested.exists() else clone / ".dfs/items/W87.md"
    txt = f.read_text()
    tree = txt.split("## Tree", 1)[-1].split("\n## Log", 1)[0]
    dets = re.findall(r"^Determination: (.*)$", tree, re.M)
    w = [len(d.split()) for d in dets]
    hyp = len(re.findall(r"^Hypothesis:", tree, re.M))
    page = len(re.findall(r"^Hypothesis:.*\.dfs/artefacts/[0-9a-f-]+\.html", tree, re.M))
    minor = len(re.findall(r"^### .*Minor findings", tree, re.M))
    log = "".join(Path(p).read_text(errors="ignore") for p in glob.glob(str(clone / ".dfs/runs/*/run-*.json")))
    ref = len(re.findall(r"[Dd]etermination[^\"\\]{0,80}(?:more than|over|longer than) 80 words|80 words", log))
    print(f"{arm} nodes={len(w)} det_words={sum(w)} max={max(w) if w else 0} over80={sum(x>80 for x in w)} over60={sum(x>60 for x in w)} hyp={hyp} pages={page} minor_nodes={minor} budget_mentions_in_logs={ref}")
