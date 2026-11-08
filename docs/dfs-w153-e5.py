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
# n: (kind, answered, A, B, why). kind: tooling fault the worker found ("fault") or not E5's ("alarm": the runner's
# own check; "other": no tooling fault). A/B = "yes" when E5 could have removed the raise under that reading.
#   A (the Goal's test): a fault blocks the Goal only if the author's answer had to be a fix, or an act only the
#     author can do, before the task could finish; an answer of bypass, ignore or a filed task shows it did not.
#   B (commit-refused): a fault blocks if it refused the task's next commit or its finish.
# "perm" marks a yes under A that needs the worker to be allowed to bypass a refusing hook (the law does not say so).
Y, N = "yes", "no"
CLASS = {
 1: ("fault", "bypass", Y, Y, "tree.py effective() pruned confirmed nodes; no commit refused; author: ignore tooling issues"),
 2: ("fault", "bypass", Y, N, "size rule refused W75.md commits; author: --no-verify and capture elsewhere (perm)"),
 3: ("fault", "author-act", N, N, "size rule would refuse next commit; author answered option 1, whose answer moves the compact cutoff"),
 4: ("fault", "bypass", Y, N, "guard refused every W75 commit; author: add a task, work around with --no-verify (perm)"),
 5: ("fault", "draft", Y, Y, "two faults not touching the Goal; author: a new task of their own (option a)"),
 6: ("fault", "bypass", Y, N, "check refused commits in a rebase; author took option 3 = --no-verify, which the raise said it would not do unasked (perm)"),
 7: ("fault", "bypass", Y, N, "status stuck open with no node to fix, task cannot finish; author: work around, complete manually (perm)"),
 8: ("fault", "draft", Y, Y, "review of the logs listed tooling suggestions; no refusal; author: fix all of them (as tasks)"),
 9: ("fault", "draft", Y, Y, "review note cannot be answered; no refusal; author took option a, a change to the brief and law"),
10: ("fault", "draft", Y, Y, "law says fifth session, code counts work sessions; no refusal; author: option b, reword the law"),
11: ("fault", "bypass", Y, N, "same stuck status as 7; author: check another task exists, complete this manually (perm)"),
12: ("fault", "bypass", Y, N, "check refused rebase commits; author took option 2, a rebase that skips the hook (perm)"),
13: ("fault", "bypass", Y, N, "check refused rebase commits; author: create a task if not fixed, work around with no-verify (perm)"),
14: ("fault", "draft", Y, Y, "two replay.py faults, neither stops the next node; author: option a, fix in a new task"),
15: ("fault", "fix-in-task", Y, Y, "replay.py score misses a verdict; no refusal; author: fix tooling as it arises in this task (a draft would go against that)"),
16: ("fault", "fix-in-task", Y, Y, "a stale owner name in a note; Goal met, no refusal; author: fix it (a draft would go against that)"),
17: ("other", "ok", N, N, "report that an earlier answer was not yet acted on; not a tooling fault"),
18: ("fault", "author-act", N, N, "log cannot be moved, budget runs out in 3 or 4 sessions; author: compact the log, which an answer enables"),
19: ("fault", "fix-first", N, N, "check_history bug refused every .dfs commit; author: option a, fix check_history first"),
20: ("fault", "fix-first", N, N, "same bug as 19 on W19; author: fix it"),
21: ("alarm", "other", N, N, "the runner's walk-suspects alarm, not a fault a session found"),
22: ("other", "scope", N, N, "no Android device; not a tooling fault, a missing resource; author: option 3, narrow the scope"),
23: ("fault", "fix-first", N, N, "hook exits nonzero on warnings in W14.md and refused W39's commit; author: fix W14 first"),
24: ("fault", "bypass", Y, N, "check refused W92 commits for faults in other tasks; author: tooling handled elsewhere, go around (perm)"),
25: ("alarm", "continue", N, N, "the runner's walk-suspects alarm"),
26: ("fault", "bypass", Y, N, "same refusal as 24; author: bypass for now (perm)"),
}
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
print("n\tproject\ttask\tnode\traise ts\twait h\tkind\tanswered\tA removable\tB removable\treason")
for i, r in enumerate(rows, 1):
    k, w, A, B, why = CLASS[i]
    print(f"{i}\t{r[0]}\t{r[1]}\t{r[2]}\t{r[3]}\t{r[4]:.1f}\t{k}\t{w}\t{A}\t{B}\t{why}")
print()
for name, ix_ in (("A (Goal test)", 2), ("B (commit-refused)", 3)):
    yes = [i for i in range(1, 27) if CLASS[i][ix_] == Y]
    no = [i for i in range(1, 27) if CLASS[i][ix_] == N and CLASS[i][0] == "fault"]
    ex = [i for i in range(1, 27) if CLASS[i][0] != "fault"]
    print(f"reading {name}: removable {len(yes)} of 26 {yes}; keep as raises {len(no)} {no}; not tooling faults {len(ex)} {ex}")
perm = [i for i in range(1, 27) if CLASS[i][2] == Y and CLASS[i][3] == N]
print(f"A-only (need a permission to bypass a refusing hook, or to complete by hand): {len(perm)} {perm}")
fixin = [i for i in range(1, 27) if CLASS[i][1] == "fix-in-task"]
print(f"removable but the author chose a fix inside the task: {fixin}")
tasks = lambda ix: sorted({(rows[i - 1][0], rows[i - 1][1]) for i in ix})
yesA = [i for i in range(1, 27) if CLASS[i][2] == Y]; yesB = [i for i in range(1, 27) if CLASS[i][3] == Y]
print(f"tasks holding a removable raise: A {len(tasks(yesA))}, B {len(tasks(yesB))}; per task of 238: A {len(yesA)/238:.2f}, B {len(yesB)/238:.2f}; per task that holds one: A {len(yesA)/len(tasks(yesA)):.2f}, B {len(yesB)/len(tasks(yesB)):.2f}")
for nm, ix in (("A-removable", yesA), ("B-removable", yesB), ("kept under A", [i for i in range(1, 27) if CLASS[i][0] == "fault" and CLASS[i][2] == N])):
    ws = [rows[i - 1][4] for i in ix]
    print(f"{nm}: median wait {statistics.median(ws):.1f} h over {len(ix)}")

# W153.49: which of the 17 reading-A raises the CURRENT tool can still raise, and an E5 arm could pass with no new
# permission. Each row has the command or file that put it in its group (the probes were run on the installed
# scripts, roadmap-dfs d02e23e, on 2026-11-08, or read from the node named).
#   fixed        the cause is gone from the current scripts or law
#   not-firing   the refusal does not fire on the current hook
#   not-tooling  not a tooling fault at all (a flaw in the project's own content)
#   size-guard   still fires, but it refuses an E5 arm's commit as much as a control's: passing needs a permission
#   possible     still happens, and nothing refuses a commit, so an E5 arm goes on and a control raises
NOW = {
 1: ("possible", "probe on a scratch tree: after 'correct W60.3 confirmed' then 'correct W60.4 confirmed' W60.4 is pruned (W153.46). A replay logs no corrections, so no replay control meets it; real use does"),
 2: ("size-guard", "check.py task-file budget still refuses a commit of an over-budget task file (W153.46: raised in this task on 2026-11-07)"),
 4: ("size-guard", "same guard as 2"),
 5: ("fixed", "the non-atomic task-file write: tree.py writes through a temp file and os.replace (W113 says fixed in W114; W153.46 read tree.py)"),
 6: ("fixed", "the rebase-commit refusal, fixed 28 Oct (W127.1, W127.2)"),
 7: ("fixed", "status stuck open with no node: probe of status_of on a review ok then issues with no node reads the task as done (W153.46)"),
 8: ("fixed", "its four faults: S1 peak 0 -> `context.py` prints 'context 75,714' in this session (W113.2); S2 compaction offered by the briefing (W113.4, `tree.py compact` ran in W153.48); S3 `tree.py log W9 review issues <<< none` exits 2 'names no node' (probe, 2026-11-08; W113.3); S4 sentence in SKILL.md 'name the version or object' (W113.5). S5 was not a tooling fault"),
 9: ("fixed", "review note cannot be answered: the skill now sends a determined node's note to a new child (W113.6/W113.7; W153.46)"),
10: ("fixed", "law says 'fifth work session', as the code counts (W113.10; W153.46)"),
11: ("fixed", "same stuck status as 7"),
12: ("fixed", "same rebase refusal as 6"),
13: ("fixed", "same rebase refusal as 6"),
14: ("fixed", "the two replay.py faults: history.py second verdict pattern, tool_docs clears the folder first (W153.46)"),
15: ("fixed", "replay.py score misses a verdict over two lines: history.py second pattern (W153.46)"),
16: ("not-tooling", "shop-app W8.2: the model file sync-flow.sysml gives a closed task as owner of an edge, a stale pointer in the project's own model (optimisation-data/shop-app/.dfs/items/W8.md line 166); no roadmap-dfs script is at fault, so E5 (a *tooling* fault is filed as a draft) does not apply, and the author's answer was 'fix it'"),
24: ("not-firing", "a hook refusing for faults in other tasks: only staged files are judged, this session's commit printed 'W48.md is over the budget. Not grown here.' and passed (W153.46)"),
26: ("not-firing", "same refusal as 24"),
}
yesA = [i for i in range(1, 27) if CLASS[i][2] == Y]
assert sorted(NOW) == yesA, (sorted(NOW), yesA)
print()
for g in ("possible", "size-guard", "fixed", "not-firing", "not-tooling"):
    ix = [i for i in NOW if NOW[i][0] == g]
    print(f"on the current tool, reading A, {g}: {len(ix)} {ix}")
for i in NOW:
    print(f"  row {i}: {NOW[i][0]} - {NOW[i][1]}")
gone = [i for i in NOW if NOW[i][0] in ("fixed", "not-firing", "not-tooling")]
print(f"E5-NOW: {len([i for i in NOW if NOW[i][0] == 'possible'])} possible; size-guard {len([i for i in NOW if NOW[i][0] == 'size-guard'])}; gone {len(gone)} {gone}")
