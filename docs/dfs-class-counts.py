#!/usr/bin/env python3
"""Which failure class each optimisation candidate addresses, counted one way (W130).

    python3 docs/dfs-class-counts.py                      # recount from docs/dfs-class-lines.tsv
    python3 docs/dfs-class-counts.py --build OUT.tsv \
        --repo roadmap-dfs=DIR --repo shop-app=DIR --repo farm-app=DIR \
        [--history history.json]                          # rebuild the lines from the checkouts

The lines file has one row per task (`task`: its first review's verdict, its count of issues
reviews, the units spent after each of its issues reviews) and one per finding line of every
review that said issues (`line`), as `audit.py findings` and `audit.py rounds` read them.

The unit is a review that said issues (a round). A finding is a *blocking* line when it is a
defect or an unmarked assumption; a `minor` line is the batch of lesser findings, which an `ok`
review also carries. A candidate is a predicate on a blocking line, and each is counted three ways:

  lines      blocking lines the predicate matches, of all blocking lines
  touched    issues reviews with at least one such line, of all issues reviews
  removed    issues reviews whose every blocking line matches (so the round would not have
             happened had the candidate's class been absent), of all issues reviews;
             and the same for first reviews alone, the measure a replay moves
  units      the work, review and critic units spent from a removed review to the next review
             (or the end), of all the units in the tasks the history holds a transcript of

The predicates:

  1  break it (narrow)  a defect line with a word of the cases the candidate's text names:
                        repeated, interrupted, empty, a short screen
  1  break it (wide)    narrow plus failure paths and races: concurrent, race, stale, crash, kill,
                        cut off, simultaneous, mid-way, no commit yet
  2  assumptions        a line of the class `assumption`
  3  one review         a defect line of a later issues review that `audit.py rounds` places as
                        `present`: every name it points at was in the code, unchanged, at the
                        earlier review. A lower bound; `unplaced` lines are not counted

Candidate 1's predicate is a word list and the others are classes, so only it needs a baseline:
the rate of the same words in lines that are not defects (minor and assumption), where a word
that means nothing about a defect's class matches as often as it does in a defect.
Candidates 4 and 5 do not change a review and are counted from the history (`--history`).
"""
import argparse
import collections
import csv
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LINES = HERE / "dfs-class-lines.tsv"

NARROW = re.compile(r"repeat|twice|again|re-?run|idempoten|interrupt|empty|short screen|narrow screen", re.I)
WIDE = re.compile(NARROW.pattern + r"|concurren|\brace|\bracing|stale|crash|killed|\bkill|cut off|simultaneous|"
                  r"mid-?way|mid-|no commit yet|zero commits", re.I)


def build(repos, out, history):
    sys.path.insert(0, str(HERE.parent / "scripts"))
    import audit
    sessions = collections.defaultdict(list)
    if history:
        for s in json.load(open(history))["sessions"]:
            if s["task"] and s["kind"] in ("work", "review", "critic"):
                sessions[(s["project"], s["task"])].append(s)
    rows = []
    for name, repo in repos:
        tasks = audit.load_tasks(repo)
        finding = audit.finding_rows(tasks)
        later = {(r[0], r[1], r[3]): r[2] for r in audit.round_rows(tasks, audit.node_commits(repo), repo)}
        for k, t in tasks.items():
            revs = [e for e in t["log"] if e["kind"] == "review"]
            if not revs:
                continue
            issues = [e["ts"] for e in revs if e["args"][:1] == ["issues"]]
            sess = sorted((s for (p, tk), v in sessions.items() if tk == k and p.endswith("-" + name) for s in v),
                          key=lambda s: s["start"])
            units = ""
            if sess:
                units = ",".join(str(round(round_units(sess, ts))) for ts in issues) + ";" + str(round(sum(s["units"] for s in sess)))
            # task row: review = its first review, first_or_later = that review's verdict,
            # round = its count of issues reviews, class = units after each issues review ; all units
            rows.append(["task", name, k, revs[0]["ts"], revs[0]["args"][0], str(len(issues)), units, ""])
        for k, ts, cls, line in finding:
            revs = [e for e in tasks[k]["log"] if e["kind"] == "review"]
            issues = [e["ts"] for e in revs if e["args"][:1] == ["issues"]]
            place = later.get((k, ts, line))
            rows.append(["line", name, k, ts, "first" if ts == revs[0]["ts"] else "later", str(issues.index(ts) + 1),
                         cls + ("/" + place if place else ""), line.replace("\t", " ").replace("\n", " ")])
    with open(out, "w") as fh:
        fh.write("row\tproject\ttask\treview\tfirst_or_later\tround\tclass\tline\n")
        for r in rows:
            fh.write("\t".join(r) + "\n")


def round_units(sess, ts):
    """Units from the review session that wrote the log entry at `ts` to the next review session
    (inclusive), or to the task's end."""
    from datetime import datetime, timedelta
    t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    p = lambda x: datetime.fromisoformat(x.replace("Z", "+00:00"))
    idx = next((i for i, s in enumerate(sess) if s["kind"] == "review"
                and p(s["start"]) - timedelta(seconds=2) <= t <= p(s["end"]) + timedelta(seconds=15)), None)
    if idx is None:
        return 0
    total = 0
    for s in sess[idx + 1:]:
        total += s["units"]
        if s["kind"] == "review":
            break
    return total


def read():
    tasks, lines = [], []
    with open(LINES) as fh:
        for r in csv.DictReader(fh, delimiter="\t", quoting=csv.QUOTE_NONE):
            (tasks if r["row"] == "task" else lines).append(r)
    return tasks, lines


def pct(a, b):
    return f"{a}/{b} {100 * a / b:.0f}%" if b else f"{a}/0"


def cls(r):
    return r["class"].split("/")[0].split("+")[0]


PREDS = {
    "1 break it (narrow)": lambda r: cls(r) == "defect" and NARROW.search(r["line"]),
    "1 break it (wide)": lambda r: cls(r) == "defect" and WIDE.search(r["line"]),
    "2 assumptions": lambda r: cls(r) == "assumption",
    "3 one review (present)": lambda r: cls(r) == "defect" and r["class"].endswith("/present"),
}


def removed_rounds(lines):
    """{(project, task, review): True if the round is first} for rounds each candidate would remove."""
    rounds = collections.defaultdict(list)
    for r in lines:
        rounds[(r["project"], r["task"], r["review"])].append(r)
    return rounds


def history_report(path):
    """Candidates 4 and 5 and the first-review rate, from `history.py --json` (transcripts on this box)."""
    d = json.load(open(path))
    S = [s for s in d["sessions"] if s["kind"] in ("work", "review", "critic") and s["task"]]
    total = sum(s["units"] for s in S)
    print(f"{len(S)} dfs sessions, {total:,.0f} weighted-token units")
    print("\ncandidate 4: sessions with a refused commit, and with the file:line coordinate refusal, by period")
    for lo, hi in (("2026-09-29", "2026-10-19"), ("2026-10-19", "2026-10-22"), ("2026-10-22", "2026-10-30")):
        v = [s for s in S if lo <= s["start"][:10] < hi]
        print(f"   {lo}..{hi}: {len(v)} sessions, refused {pct(sum(bool(s['refused']) for s in v), len(v))}, "
              f"coordinate {pct(sum('coordinate in a task file' in s['reasons'] for s in v), len(v))}")
    ref = sum(s["units"] for s in S if s["refused"])
    print(f"   units of the sessions that had a refusal (an upper bound; a retry is part of a session): {pct(round(ref), round(total))}")
    print("\nfirst review said issues, by project (tasks with a transcript)")
    for p in sorted({t["project"] for t in d["tasks"]}):
        v = [t for t in d["tasks"] if t["project"] == p and t["first_review"]]
        print(f"   {p}: {pct(sum(t['first_review'] == 'issues' for t in v), len(v))}")
    print("\ncandidate 5: units after the third review that said issues, and after the first")
    by = collections.defaultdict(list)
    for s in S:
        by[(s["project"], s["task"])].append(s)
    after = {1: 0, 3: 0}
    for v in by.values():
        v.sort(key=lambda s: s["start"])
        n = 0
        for s in v:
            if n >= 1:
                after[1] += s["units"]
            if n >= 3:
                after[3] += s["units"]
            n += any(x[0] == "review" and x[1] == "issues" for x in s["verdicts"])
    print(f"   after the first issues review: {pct(round(after[1]), round(total))}; after the third: {pct(round(after[3]), round(total))}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build")
    ap.add_argument("--repo", action="append", default=[])
    ap.add_argument("--history")
    ap.add_argument("--history-report", help="history.py --json output: counts for candidates 4 and 5")
    a = ap.parse_args()
    if a.history_report:
        history_report(a.history_report)
        return
    if a.build:
        build([tuple(r.split("=", 1)) for r in a.repo], a.build, a.history)
        return
    tasks, lines = read()
    projects = sorted({r["project"] for r in tasks})
    for scope in ["all"] + projects:
        sel = [r for r in lines if scope in ("all", r["project"])]
        tsel = [r for r in tasks if scope in ("all", r["project"])]
        rounds = removed_rounds(sel)
        blocking = [r for r in sel if cls(r) in ("defect", "assumption")]
        first = {k for k, v in rounds.items() if v[0]["first_or_later"] == "first"}
        print(f"\n== {scope}: {len(tsel)} tasks with a review, first review said issues on "
              f"{pct(sum(t['first_or_later'] == 'issues' for t in tsel), len(tsel))}; {len(rounds)} issues reviews "
              f"({len(first)} first), {len(sel)} finding lines {dict(collections.Counter(cls(r) for r in sel))}, "
              f"{len(blocking)} blocking")
        print("   issues reviews with no blocking line (only the lesser batch):",
              sum(not any(cls(r) in ("defect", "assumption") for r in v) for v in rounds.values()))
        print(f"   {'candidate':24} {'lines':>12} {'touched':>12} {'removed':>12} {'removed, first':>14}")
        for name, p in PREDS.items():
            m = [r for r in blocking if p(r)]
            touched = {k for k, v in rounds.items() if any(p(r) for r in v)}
            gone = {k for k, v in rounds.items()
                    if [r for r in v if cls(r) in ("defect", "assumption")]
                    and all(p(r) for r in v if cls(r) in ("defect", "assumption"))}
            print(f"   {name:24} {pct(len(m), len(blocking)):>12} {pct(len(touched), len(rounds)):>12} "
                  f"{pct(len(gone), len(rounds)):>12} {pct(len(gone & first), len(first)):>14}")
        for nm, rx in (("narrow", NARROW), ("wide", WIDE)):
            hit = lambda xs: sum(bool(rx.search(r["line"])) for r in xs)
            d = [r for r in sel if cls(r) == "defect"]
            o = [r for r in sel if cls(r) != "defect"]
            print(f"   words {nm}: in defects {pct(hit(d), len(d))}; in non-defects {pct(hit(o), len(o))} "
                  f"(minor {pct(hit([r for r in sel if cls(r) == 'minor']), sum(cls(r) == 'minor' for r in sel))}, "
                  f"assumption {pct(hit([r for r in sel if cls(r) == 'assumption']), sum(cls(r) == 'assumption' for r in sel))})")
    print("\n== units spent in the rounds a candidate's class alone started (tasks with a transcript on this box)")
    spend = {}
    for t in tasks:
        if ";" in t["class"]:
            each, whole = t["class"].split(";")
            spend[(t["project"], t["task"])] = ([int(x) for x in each.split(",") if x], int(whole))
    for scope in ["all"] + projects:
        keys = [k for k in spend if scope in ("all", k[0])]
        whole = sum(spend[k][1] for k in keys)
        after = sum(sum(spend[k][0]) for k in keys)
        print(f"   {scope}: {len(keys)} tasks, {whole:,} units in all, {after:,} ({pct(after, whole)}) after an issues review")
        for name, p in PREDS.items():
            u = 0
            for k in keys:
                stamps = sorted({r["review"] for r in lines if (r["project"], r["task"]) == k})
                for i, ts in enumerate(stamps):
                    b = [r for r in lines if (r["project"], r["task"], r["review"]) == (k[0], k[1], ts)
                         and cls(r) in ("defect", "assumption")]
                    if b and all(p(r) for r in b) and i < len(spend[k][0]):
                        u += spend[k][0][i]
            print(f"      {name:24} {u:>12,} units  {pct(u, whole)}")


if __name__ == "__main__":
    main()
