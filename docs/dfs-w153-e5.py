#!/usr/bin/env python3
"""W153 E5 accounting: the tooling-fault raises, read by hand, and what a draft task would have changed.

  python3 docs/dfs-w153-e5.py [--at REV]      (default d595c44a, the Background's state; arguments of dfs-w153-mine.py)

The raises are the ones `classify_raise` calls tooling (26 at d595c44a). The class and the way each
was answered are a reader's, recorded in CLASS below by the order they print in (raise number
1..26 as listed by the script); a reader can check each against `tree.py` text in the task's log.
Classes:
  blocks   the fault stopped the task's next commit or its completion (E5 keeps these as raises)
  free     the raise itself says the task's Goal and next node were not stopped (E5 would file a draft task)
  alarm    the runner's "the walk suspects a problem" check, not a tooling fault found by a session
  other    not a fault: a decision, a forecast or a missing device
Answer ways: bypass (go around, --no-verify, ignore), fix-first (fix the tooling now or in the task),
draft (capture as another task), continue, other.
"""
import importlib.util, sys, statistics
from datetime import datetime
from pathlib import Path
spec = importlib.util.spec_from_file_location("mine", Path(__file__).with_name("dfs-w153-mine.py"))
mine = importlib.util.module_from_spec(spec); spec.loader.exec_module(mine)
CLASS = {1: ("blocks", "bypass"), 2: ("blocks", "bypass"), 3: ("blocks", "continue"), 4: ("blocks", "bypass"),
         5: ("free", "draft"), 6: ("blocks", "bypass"), 7: ("blocks", "bypass"), 8: ("other", "fix-first"),
         9: ("other", "other"), 10: ("free", "other"), 11: ("blocks", "bypass"), 12: ("blocks", "bypass"),
         13: ("blocks", "bypass"), 14: ("free", "other"), 15: ("free", "fix-first"), 16: ("free", "fix-first"),
         17: ("other", "continue"), 18: ("other", "other"), 19: ("blocks", "fix-first"), 20: ("blocks", "fix-first"),
         21: ("alarm", "other"), 22: ("other", "other"), 23: ("blocks", "fix-first"), 24: ("blocks", "bypass"),
         25: ("alarm", "continue"), 26: ("blocks", "bypass")}
argv = sys.argv[1:] or ["--at", "d595c44a"]
a = mine.args_of(argv); P = mine.load(a)
ts = lambda s: datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
rows = []
for proj, T in P.items():
    for tid, t in T.items():
        log = t["log"]
        for k, e in enumerate(log):
            if e["kind"] == "raise" and mine.classify_raise(e["body"]) == "tooling":
                ans = next((x for x in log[k + 1:] if x["kind"] == "answer"), None)
                wait = (ts(ans["ts"]) - ts(e["ts"])).total_seconds() / 3600 if ans else None
                rows.append((proj, tid, e["args"][0] if e["args"] else "", e["ts"], wait, " ".join((ans or {"body": ""})["body"].split())[:90]))
assert len(rows) == 26, len(rows)
print("n\tproject\ttask\tnode\traise ts\twait h\tclass\tanswered\tanswer")
for i, r in enumerate(rows, 1):
    c, w = CLASS[i]
    print(f"{i}\t{r[0]}\t{r[1]}\t{r[2]}\t{r[3]}\t{r[4]:.1f}\t{c}\t{w}\t{r[5]}")
print()
for c in ("blocks", "free", "alarm", "other"):
    ix = [i for i in range(1, 27) if CLASS[i][0] == c]
    ws = [rows[i - 1][4] for i in ix]
    ans = {}
    for i in ix: ans[CLASS[i][1]] = ans.get(CLASS[i][1], 0) + 1
    print(f"{c}: {len(ix)} raises, median wait {statistics.median(ws):.1f} h, answered {ans}")
