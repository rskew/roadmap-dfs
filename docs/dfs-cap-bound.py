#!/usr/bin/env python3
"""What would a cap of three issues rounds have saved? (W130.47, candidate 5)

Reads the file `history.py --json OUT` writes. For each reviewed task, counts the units and dollars
of every session that started after the task's third issues review (the sessions the cap would not
have run), split by project; then lists the tasks, and for each the review rounds after the third
and what `docs/dfs-review-findings.tsv` (task, review time, class) says those rounds found.

  python3 scripts/history.py --repo roadmap-dfs=/workspace/roadmap-dfs \
      --repo shop-app=/workspace/roadmap-dfs/optimisation-data/shop-app \
      --repo farm-app=/workspace/roadmap-dfs/optimisation-data/farm-app --json /tmp/h.json
  python3 docs/dfs-cap-bound.py /tmp/h.json [CAP]

CAP is the number of issues reviews allowed before the task raises (default 3).
Dollars are the runner's where it recorded them and an estimate from units where not.
"""
import collections
import csv
import json
import os
import sys

d = json.load(open(sys.argv[1]))
CAP = int(sys.argv[2]) if len(sys.argv) > 2 else 3
here = os.path.dirname(os.path.abspath(__file__))

findings = collections.defaultdict(list)  # task -> [(review ts, class)]
with open(os.path.join(here, "dfs-review-findings.tsv")) as f:
    for r in csv.DictReader(f, delimiter="\t"):
        findings[r["task"]].append((r["review"], r["class"]))

by = collections.defaultdict(list)
for s in sorted((s for s in d["sessions"] if s["task"]), key=lambda s: s["start"] or ""):
    by[(s["project"], s["task"])].append(s)

rows = []
for (project, task), ss in by.items():
    issues = reviews = 0
    after = after_usd = total = total_usd = 0.0
    later_rounds = []
    for s in ss:
        total += s["units"]
        total_usd += s["usd"] or 0
        if issues >= CAP:
            after += s["units"]
            after_usd += s["usd"] or 0
        for kind, v in s["verdicts"]:
            if kind == "review":
                reviews += 1
                if v != "ok":
                    issues += 1
                    if issues > CAP:
                        later_rounds.append(s["start"])
    if reviews:
        rows.append(dict(project=project.replace("-workspace-", ""), task=task, issues=issues, units=total,
                         usd=total_usd, after=after, after_usd=after_usd, later=later_rounds))

tot_units = sum(r["units"] for r in rows)
tot_usd = sum(r["usd"] for r in rows)
capped = [r for r in rows if r["issues"] >= CAP]
print(f"cap {CAP}: {len(capped)} of {len(rows)} reviewed tasks reach issues review {CAP}")
print(f"units after it: {sum(r['after'] for r in capped):.0f} of {tot_units:.0f} "
      f"({100*sum(r['after'] for r in capped)/tot_units:.1f}%), "
      f"${sum(r['after_usd'] for r in capped):.1f} of ${tot_usd:.1f} "
      f"({100*sum(r['after_usd'] for r in capped)/tot_usd:.1f}%)")
print()
print("by project")
for p in sorted({r["project"] for r in rows}):
    ps = [r for r in rows if r["project"] == p]
    cs = [r for r in ps if r["issues"] >= CAP]
    u = sum(r["units"] for r in ps)
    a = sum(r["after"] for r in cs)
    print(f"  {p:22s} tasks {len(ps):4d}  reach {CAP}: {len(cs):3d}  units after {a:9.0f} of {u:9.0f} "
          f"({100*a/u if u else 0:4.1f}%)  ${sum(r['after_usd'] for r in cs):6.1f}")
print()
print("what the rounds after the cap found, where docs/dfs-review-findings.tsv and docs/dfs-fix-regressions.tsv cover them")
print("(the tsv files read roadmap-dfs only and hold some of a task's rounds; a round is a distinct review time, the first")
print(f" {CAP} of a task's rounds in the file are taken as the rounds before the cap, which is exact only if the file holds them all)")
fix = collections.defaultdict(list)  # task -> [(review ts, class)]
with open(os.path.join(here, "dfs-fix-regressions.tsv")) as f:
    for r in csv.DictReader(f, delimiter="\t"):
        fix[r["task"]].append((r["review"], r["class"]))
cov_tasks = rounds = 0
counts = collections.Counter()
fixc = collections.Counter()
for r in capped:
    if not r["project"].endswith("roadmap-dfs") or r["task"] not in findings:
        continue
    cov_tasks += 1
    stamps = sorted({rv for rv, _ in findings[r["task"]]} | {rv for rv, _ in fix[r["task"]]})
    late = set(stamps[CAP:])
    rounds += len(late)
    for rv, cl in findings[r["task"]]:
        if rv in late:
            counts[cl] += 1
    for rv, cl in fix[r["task"]]:
        if rv in late:
            fixc[cl] += 1
print(f"  roadmap-dfs tasks past the cap with lines in the files: {cov_tasks}; rounds after the cap in them: {rounds}")
print(f"  finding lines by class: {dict(counts)}")
print(f"  defect lines placed by the fix-regression reading: {dict(fixc)}")
print()
print("task, project, issues reviews, units after the cap, share of the task's units")
for r in sorted(capped, key=lambda r: -r["after"]):
    print(f"  {r['task']:6s} {r['project']:18s} {r['issues']:2d} {r['after']:8.0f} {100*r['after']/r['units']:4.0f}%")
