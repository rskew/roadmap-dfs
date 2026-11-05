#!/usr/bin/env python3
"""Do the replays' round counts stand for the loop's? (W130.44)

Reads the file `history.py --json OUT` writes and splits the reviewed tasks by what each cost in
all (dollars, estimated where the runner did not record them). `replay.py candidates` picks the
tasks to replay by that same figure (its default $3, the node's $3.5), so the replays are drawn
from the cheap end by construction: a task that went through several issues rounds is not cheap.

  python3 scripts/history.py --repo roadmap-dfs=/workspace/roadmap-dfs \
      --repo shop-app=/workspace/roadmap-dfs/optimisation-data/shop-app \
      --repo farm-app=/workspace/roadmap-dfs/optimisation-data/farm-app --json /tmp/h.json
  python3 docs/dfs-round-bound-by-size.py /tmp/h.json [LIMIT_USD]

Prints, per group and per size bucket: tasks, tasks with a first, second and third issues review,
and the spend after the first and after the second issues review. The second is what a change
that makes round two pass could save at most; the first is what the document's step 5 calls
the price of the first tree not being finished.
"""
import collections
import json
import sys

d = json.load(open(sys.argv[1]))
LIMIT = float(sys.argv[2]) if len(sys.argv) > 2 else 3.5

by = collections.defaultdict(list)
for s in sorted((s for s in d["sessions"] if s["task"]), key=lambda s: s["start"] or ""):
    by[(s["project"], s["task"])].append(s)

rows = []
for (project, task), ss in by.items():
    n = a1 = a2 = tot = 0.0
    issues = reviews = 0
    for s in ss:
        u = s["usd"] or 0
        tot += u
        if issues >= 1:
            a1 += u
        if issues >= 2:
            a2 += u
        for kind, v in s["verdicts"]:
            if kind == "review":
                reviews += 1
                issues += v != "ok"
    if reviews:
        rows.append(dict(project=project, task=task, usd=tot, issues=issues, a1=a1, a2=a2))


def show(name, ts):
    n = len(ts)
    if not n:
        print(f"  {name:22s} no tasks")
        return
    usd = sum(t["usd"] for t in ts)
    c = lambda k: sum(t["issues"] >= k for t in ts)
    print(f"  {name:22s} tasks {n:3d}  issues review 1+: {c(1):3d} ({100*c(1)/n:3.0f}%)  2+: {c(2):3d} ({100*c(2)/n:3.0f}%)  3+: {c(3):3d}"
          f"  spend ${usd:7.1f}  after 1st issues ${sum(t['a1'] for t in ts):6.1f}  after 2nd issues ${sum(t['a2'] for t in ts):6.1f}")


groups = [("all projects", lambda t: True),
          ("roadmap-dfs", lambda t: t["project"].endswith("roadmap-dfs")),
          ("shop-app and farm-app", lambda t: t["project"].endswith(("shop-app", "farm-app")))]
for name, f in groups:
    ts = [t for t in rows if f(t)]
    print(name)
    show("every task", ts)
    show(f"total <= ${LIMIT}", [t for t in ts if t["usd"] <= LIMIT])
    show(f"total >  ${LIMIT}", [t for t in ts if t["usd"] > LIMIT])
tot = sum(t["usd"] for t in rows)
a2 = sum(t["a2"] for t in rows)
big = sum(t["a2"] for t in rows if t["usd"] > LIMIT)
print(f"spend after a second issues review: ${a2:.1f} of ${tot:.1f} ({100*a2/tot:.1f}%), "
      f"${big:.1f} of it ({100*big/a2:.0f}%) on tasks above ${LIMIT}")
