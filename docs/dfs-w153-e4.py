#!/usr/bin/env python3
"""W153 E4 accounting: the review-loop checkpoint raises, what each showed, and what followed.

  python3 docs/dfs-w153-e4.py [--at REV]      (default d595c44a; other arguments as dfs-w153-mine.py)

Checkpoints are the raises the runner (or a session) put at a point where the author must decide
whether the review loop goes on. `loopish()` takes the regex "loop" class of dfs-w153-mine.py (35),
adds four it files under "other" (shop-app W29 x2, W32 last, W39 "Ten work sessions have passed"),
and by hand drops three that are tooling faults (W75 twice, W19: their numbers in TOOLING below).
That is 36, the Background's figure. The 36 split into REVIEW (N implementation reviews found
issues), SESSIONS (ten work sessions without the author) and DRIFT (W116, a session's own
scope-drift raise).

Per checkpoint it prints: the findings (new fix nodes, minor-findings nodes left out) of the last
two reviews before it, counted from the review text; the author's answer; and what followed in the
task (the first review after the answer, issues reviews to the end, later raises).
CAUSED is a reader's class of the LAST review's findings against the one before it, from the
finding titles only (first ~130 characters): C = caused or left by the previous fix (a regression,
an incomplete fix, an unmarked assumption of it), N = a new defect, - = not classifiable (fewer
than two issues reviews, or no finding text). ANSWER: stop (accept / close), continue (continue,
option 1), steer (the author wrote a direction: not a yes/no).
"""
import importlib.util, re, sys, collections
from pathlib import Path
spec = importlib.util.spec_from_file_location("mine", Path(__file__).with_name("dfs-w153-mine.py"))
mine = importlib.util.module_from_spec(spec); spec.loader.exec_module(mine)
argv = sys.argv[1:] or ["--at", "d595c44a"]
a = mine.args_of(argv); P = mine.load(a)

TOOLING = {12, 13, 19}  # ordinals (in the order printed by cands()) that are tooling faults, not loop checkpoints
# A reader's classes by ordinal (1..39 before TOOLING is dropped). See CAUSED in the docstring.
CAUSED = {1: "C", 2: "C", 3: "C", 4: "N", 5: "-", 6: "C", 7: "C", 8: "N", 9: "C", 10: "N", 11: "N", 14: "N", 15: "C", 16: "C",
          17: "N", 18: "N", 20: "-", 21: "N", 22: "-", 23: "-", 24: "C", 25: "N", 26: "C", 27: "C", 28: "C", 29: "C", 30: "C",
          31: "C", 32: "C", 33: "C", 34: "-", 35: "N", 36: "N", 37: "N", 38: "-", 39: "N"}
ANSWER = {1: "stop", 17: "stop", 16: "continue", 18: "steer", 20: "steer", 21: "steer", 22: "steer", 23: "steer"}  # all others: continue
KIND_RE = [("SESSIONS", r"work sessions|sessions without you|work sessions have passed"), ("DRIFT", r"scope drift"),
           ("REVIEW", r"reviews?")]
NODE = re.compile(r"(?:^|\n|\. )(W\d+\.\d+)(?: added:|:| ·| \()")


def cands():
    for proj, T in P.items():
        for tid, t in sorted(T.items()):
            L = t["log"]
            for k, e in enumerate(L):
                if e["kind"] != "raise":
                    continue
                b = e["body"]
                if mine.classify_raise(b) == "loop" or re.search(r"reviews? since the author|second consecutive implementation review|Ten work sessions have passed", b[:300], re.I):
                    yield proj, tid, k, e


def findings(x):
    """(real fix nodes, has a minor-findings node) from a review's text."""
    body = x["body"]
    ids = []
    for m in NODE.finditer(body):
        line_end = body.find("\n", m.end()); line = body[m.start(): line_end if line_end > 0 else None]
        if re.search(r"minor findings", line, re.I):
            continue
        if m.group(1) not in ids:
            ids.append(m.group(1))
    return len(ids), bool(re.search(r"minor findings", body, re.I))


rows = []
for i, (proj, tid, k, e) in enumerate(cands(), 1):
    if i in TOOLING:
        continue
    L = P[proj][tid]["log"]
    kind = next((n for n, r in KIND_RE if re.search(r, e["body"][:120], re.I)), "REVIEW")
    rv = [x for x in L[:k] if x["kind"] == "review"][-2:]
    fc = [findings(x) if x["args"][0] == "issues" else (0, False) for x in rv]
    ans = next((x for x in L[k + 1:] if x["kind"] == "answer"), None)
    ai = L.index(ans) if ans else None
    rest = [x for x in L[ai + 1:] if x["kind"] == "review"] if ans else []
    iss = sum(1 for x in rest if x["args"][0] == "issues")
    first = rest[0]["args"][0] if rest else "-"
    rows.append(dict(n=i, proj=proj, task=tid, ts=e["ts"][:10], kind=kind,
                     last2=[f[0] for f in fc] if len(fc) == 2 else None, caused=CAUSED.get(i, "-"),
                     answer=ANSWER.get(i, "continue"), first=first, issues_after=iss, reviews_after=len(rest),
                     later_raises=sum(1 for x in L[ai + 1:] if x["kind"] == "raise") if ans else 0,
                     ended_ok=bool(rest and rest[-1]["args"][0] == "ok")))
assert len(rows) == 36, len(rows)
print("n\tproject\ttask\tdate\tkind\tfindings last two reviews\tcaused\tanswer\tfirst review after\tissues reviews after\tlater raises\tlast review ok")
for r in rows:
    print("\t".join(str(v) for v in (r["n"], r["proj"], r["task"], r["ts"], r["kind"], r["last2"], r["caused"], r["answer"], r["first"], r["issues_after"], r["later_raises"], r["ended_ok"])))
print()
c = collections.Counter(r["kind"] for r in rows); print("kinds:", dict(c), "by project:", dict(collections.Counter(r["proj"] for r in rows)))
print("answers:", dict(collections.Counter(r["answer"] for r in rows)))
cont = [r for r in rows if r["answer"] == "continue"]
print(f"'continue' answers {len(cont)}; followed by >=4 issues reviews to the end of the task: {sum(r['issues_after'] >= 4 for r in cont)}; by kind {dict(collections.Counter(r['kind'] for r in cont if r['issues_after'] >= 4))}")
print(f"all 36: followed by >=4 issues reviews: {sum(r['issues_after'] >= 4 for r in rows)}; first review after was ok: {sum(r['first']=='ok' for r in rows)}, issues: {sum(r['first']=='issues' for r in rows)}, none: {sum(r['first']=='-' for r in rows)}")
print()
# Rule A: stop when the finding count did not fall between the last two reviews.
A = [r for r in rows if r["last2"] and r["last2"][1] and (r["last2"][0] or r["last2"][1])]
fire = [r for r in A if r["last2"][1] >= r["last2"][0]]
print(f"rule A (no fall in findings between the last two reviews -> default stop): scorable {len(A)} of 36 (two issues reviews with text), fires on {len(fire)}")
yes_no = [r for r in rows if r["answer"] in ("stop", "continue")]
print(f"  author said stop {sum(r['answer']=='stop' for r in rows)}, continue {sum(r['answer']=='continue' for r in rows)}, steer {sum(r['answer']=='steer' for r in rows)}")
print(f"  default 'stop' agrees with the author's answer on {sum(r['answer']=='stop' for r in fire)} of the {len(fire)} it fires on; default 'continue' where it does not fire agrees on {sum(r['answer']=='continue' for r in A if r not in fire)} of {len(A)-len(fire)}")
# Rule B: stop when the last review's findings were caused or left by the previous fix.
B = [r for r in rows if r["caused"] in ("C", "N")]
fb = [r for r in B if r["caused"] == "C"]
print(f"rule B (last review's findings caused or left by the previous fix -> default stop): classified {len(B)} of 36, fires on {len(fb)}")
print(f"  author said stop on {sum(r['answer']=='stop' for r in fb)} of those {len(fb)}; continue where it does not fire: {sum(r['answer']=='continue' for r in B if r['caused']=='N')} of {len(B)-len(fb)}")
# What continue bought: next review found issues (more fixes) or was ok.
for name, grp in (("rule A fires", fire), ("rule A silent", [r for r in A if r not in fire]), ("rule B fires (C)", fb), ("rule B silent (N)", [r for r in B if r["caused"] == "N"])):
    g = [r for r in grp if r["answer"] in ("continue", "steer")]
    print(f"  {name}: continued on {len(g)}; first review after found issues {sum(r['first']=='issues' for r in g)}, ok {sum(r['first']=='ok' for r in g)}; median issues reviews to the end {sorted(r['issues_after'] for r in g)[len(g)//2] if g else None}; ran on to >=4: {sum(r['issues_after']>=4 for r in g)}")
