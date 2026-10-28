#!/usr/bin/env python3
"""Recounts behind the claims in docs/dfs-optimization.md sections 1 and 2, from the task
files and the transcripts, each one checkable from its output.

    audit.py nodes    [--repo DIR]              node statuses as the tree reads them, backtracks, corrections
    audit.py findings [--repo DIR] [--out FILE] every review finding, one row each, with its class
    audit.py cost     [--projects GLOB] [--repo P=DIR]   price error on held-out sessions; shares by dollars and by units

`nodes` and `findings` read the task files of a checkout (default: this directory's repo),
as `tree.py` does, so the numbers are the ones the screen shows. Nothing here edits a task.
"""
import collections
import glob
import hashlib
import os
import re
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import history  # noqa: E402
import tree  # noqa: E402


def read(p):
    try:
        with open(p, errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def task_files(repo):
    """{task: (task file text, archive text)} for a checkout, tasks in numeric order."""
    names = {os.path.basename(f)[:-3] for d in ("items", os.path.join("archive", "items"))
             for f in glob.glob(os.path.join(repo, ".dfs", d, "*.md"))}
    names = sorted((n for n in names if re.fullmatch(r"W\d+", n)), key=lambda n: int(n[1:]))
    return {n: (read(os.path.join(repo, ".dfs", "items", n + ".md")),
                read(os.path.join(repo, ".dfs", "archive", "items", n + ".md"))) for n in names}


def load_tasks(repo):
    return {k: tree.combine(k, [("", a, b)]) for k, (a, b) in task_files(repo).items()}


# ---------------------------------------------------------------- nodes

def node_counts(tasks):
    """What the trees say about backing up. `raw` is the `Status:` line of each node;
    `effective` applies the author's corrections and the critic's backtracks the way the
    screen does (`tree.effective`), where a corrected-to-refuted node is refuted and a
    backtracked one is withdrawn."""
    raw, eff = collections.Counter(), collections.Counter()
    disp = collections.Counter()
    touched = collections.defaultdict(set)
    for k, t in tasks.items():
        status, pruned = tree.effective(t)
        for nd in t["nodes"]:
            raw[nd["status"]] += 1
            eff["withdrawn" if nd["id"] in pruned else status[nd["id"]]] += 1
            if nd["status"] == "refuted":
                touched["a node whose own Status is refuted"].add(k)
                touched["any refutation or backtrack (own Status, ruling or backtrack)"].add(k)
        for c in tree.corrections(t):
            disp[c["disp"]] += 1
            if c["disp"] in ("refuted", "backtrack"):
                touched["any refutation or backtrack (own Status, ruling or backtrack)"].add(k)
            touched[("critic backtrack" if c["disp"] == "backtrack" else "author ruling " + c["disp"])].add(k)
        if any(nd["status"] == "parked" for nd in t["nodes"]):
            touched["a node still parked"].add(k)
    return raw, eff, disp, touched


def report_nodes(repo):
    tasks = load_tasks(repo)
    raw, eff, disp, touched = node_counts(tasks)
    w = sys.stdout.write
    n = sum(raw.values())
    w(f"{len(tasks)} tasks, {n} nodes in {repo}\n")
    w(f"  as written (Status line):   {dict(raw)}\n")
    w(f"  as the tree reads them:     {dict(eff)}  (author corrections and critic backtracks applied)\n")
    w(f"  author corrections and critic backtracks, by ruling: {dict(disp)}\n")
    for why, ids in sorted(touched.items()):
        w(f"  tasks with {why}: {len(ids)}\n")
    w("  (a parked node later taken up is no longer parked, so the final state cannot say how many were)\n")


# ---------------------------------------------------------------- findings

NODE_LINE = re.compile(r"^(?:Added )?(W\d+\.\d+)\b")
ASSUMPTION = re.compile(r"^(?:unmarked assumption|assumptions? (?:with|without)|for the author)", re.I)
MINOR = re.compile(r"\bminor findings?\b|\bthe minor\b|\blesser\b", re.I)
REPRODUCED = re.compile(r"reproduc|probed by hand|ran it|run by hand|printed|shows? the", re.I)
TEST_GAP = re.compile(r"\bno test\b|untested|not tested|never tested|no flow|no check\b|"
                      r"missing test|tests? (?:pass|pins?) nothing|never run|not run|"
                      r"no (?:regression )?test|no guard", re.I)


def finding_rows(tasks):
    """One row per finding in every review that said issues: (task, review ts, class, line).
    A finding is a log line that opens with a node id (the node the review added, or the one
    it found wrong; "Added W10.6 (...)" too) or with an assumption for the author ("Unmarked
    assumption", "Assumption(s) with/without a Hypothesis", "For the author"); lines that
    report a test run, `none` or a wrapped continuation are not findings, and a line that adds
    two nodes counts once. Classes:
      minor        the batch of lesser findings (wording, a missing test for a guard, a nit)
      assumption   an unmarked assumption the review left for the author
      defect       the work does not do what the Goal or its own node says
    and two marks on a defect line: `reproduced` (the review says it ran the fault) and
    `test-gap` (the line itself says a test or check is absent). The class is decided by the
    patterns above, not by a reader; `findings --sample` prints rows to check them against."""
    rows = []
    for k, t in tasks.items():
        for e in t["log"]:
            if e["kind"] != "review" or e["args"][:1] != ["issues"]:
                continue
            for line in e["body_lines"]:
                line = line.strip()
                if ASSUMPTION.match(line):
                    cls = "assumption"
                elif NODE_LINE.match(line):
                    cls = "minor" if MINOR.search(line) else "defect"
                else:
                    continue
                marks = [m for m, rx in (("reproduced", REPRODUCED), ("test-gap", TEST_GAP)) if cls == "defect" and rx.search(line)]
                rows.append((k, e["ts"], cls + ("+" + "+".join(marks) if marks else ""), line))
    return rows


def report_findings(repo, out=None, sample=0):
    tasks = load_tasks(repo)
    rows = finding_rows(tasks)
    reviews = sum(1 for t in tasks.values() for e in t["log"] if e["kind"] == "review" and e["args"][:1] == ["issues"])
    c = collections.Counter(r[2].split("+")[0] for r in rows)
    w = sys.stdout.write
    w(f"{reviews} reviews said issues in {repo}; {len(rows)} finding lines: {dict(c)}\n")
    d = [r for r in rows if r[2].startswith("defect")]
    w(f"  of {len(d)} defects: {sum('reproduced' in r[2] for r in d)} say the review reproduced the fault, "
      f"{sum('test-gap' in r[2] for r in d)} say a test or check is absent\n")
    if out:
        with open(out, "w") as fh:
            fh.write("task\treview\tclass\tline\n")
            for r in rows:
                fh.write("\t".join((r[0], r[1], r[2], r[3].replace("\t", " ").replace("\n", " "))) + "\n")
        w(f"wrote {out}\n")
    if sample:
        import random
        random.seed(0)
        for r in random.sample(rows, min(sample, len(rows))):
            w(f"{r[0]} {r[2]:24} {r[3][:200]}\n")


# ---------------------------------------------------------------- cost

def half(sid):
    return int(hashlib.md5(sid.encode()).hexdigest(), 16) % 2


def fit(sessions, costs):
    rate, overall, _ = history.calibrate([s for s in sessions], costs)
    return rate, overall


def held_out_error(sessions, costs):
    """Fit the per-model rate on one half of the runner-priced sessions and measure the
    relative error on the other half, both ways round. Returns the errors per project."""
    priced = [s for s in sessions if s["sid"] in costs and costs[s["sid"]] and s["units"]]
    out = collections.defaultdict(list)
    for h in (0, 1):
        train = [s for s in priced if half(s["sid"]) == h]
        rate, overall = fit(train, costs)
        for s in priced:
            if half(s["sid"]) != h:
                est = s["units"] * rate.get(s["model"], overall or 0)
                out[s["project"]].append(abs(est - costs[s["sid"]]) / costs[s["sid"]])
    return out


def pct(v, p):
    v = sorted(v)
    return v[min(int(len(v) * p), len(v) - 1)] if v else float("nan")


def shares(sessions, key):
    """Shares of all dfs spend by session kind, and of the spend after a review said issues,
    in the unit `key` gives (dollars or weighted tokens)."""
    dfs = [s for s in sessions if s["kind"] in ("work", "review", "critic")]
    total = sum(key(s) for s in dfs) or 1
    after = collections.Counter()
    seen = set()
    for s in sorted(dfs, key=lambda s: s["start"] or ""):
        tk = (s["project"], s["task"])
        if s["task"] and tk in seen:
            after["all"] += key(s)
            after[s["kind"]] += key(s)
            after["estimated" if not s["priced"] else "priced"] += key(s)
        for kind, value in s["verdicts"]:
            if kind == "review" and value != "ok":
                seen.add(tk)
    kinds = {k: sum(key(s) for s in dfs if s["kind"] == k) / total for k in ("work", "review", "critic")}
    return total, kinds, after


def report_cost(pattern, repos):
    sessions, tasks, rate, n_priced = history.load(pattern, repos)
    costs = {}
    for r in repos.values():
        costs.update(history.runner_costs(r))
    dfs = [s for s in sessions if s["kind"] in ("work", "review", "critic")]
    w = sys.stdout.write
    err = held_out_error(sessions, costs)
    w(f"{len(dfs)} dfs sessions, {sum(s['priced'] for s in dfs)} with a runner price "
      f"({sum(s['priced'] for s in dfs) / len(dfs):.1%})\n")
    w("Price error, fitted on one half of the runner-priced sessions and measured on the other:\n")
    for p, v in sorted(err.items()):
        w(f"  {p[-30:]:30} n={len(v):4} median {st.median(v):6.1%}  p90 {pct(v, .9):6.1%}\n")
    allv = [x for v in err.values() for x in v]
    w(f"  {'all':30} n={len(allv):4} median {st.median(allv):6.1%}  p90 {pct(allv, .9):6.1%}\n")
    w("Projects with any runner-priced session: "
      f"{sorted({s['project'][-20:] for s in dfs if s['priced']})}\n")
    for label, key in (("dollars", lambda s: s["usd"]), ("weighted tokens", lambda s: s["units"])):
        total, kinds, after = shares(sessions, key)
        w(f"\nBy {label}: " + ", ".join(f"{k} {v:.1%}" for k, v in kinds.items()) + "\n")
        w(f"  spend after a review said issues: {after['all'] / total:.1%} of all "
          f"(work {after['work'] / total:.1%}, review {after['review'] / total:.1%}, critic {after['critic'] / total:.1%})\n")
        if label == "dollars":
            w(f"  of that, runner-priced {after['priced'] / (after['all'] or 1):.1%}, "
              f"estimated from tokens {after['estimated'] / (after['all'] or 1):.1%}\n")
            for k in ("work", "review", "critic"):
                tot = sum(s["usd"] for s in dfs if s["kind"] == k) or 1
                est = sum(s["usd"] for s in dfs if s["kind"] == k and not s["priced"])
                w(f"  {k}: {est / tot:.1%} of its dollars are estimated\n")
    rp = [s for s in dfs if s["project"].endswith("roadmap-dfs")]
    total, _, after = shares(rp, lambda s: s["usd"])
    w(f"\nroadmap-dfs alone (the only project with runner prices): after issues {after['all'] / total:.1%} "
      f"of its dollars, {after['estimated'] / (after['all'] or 1):.1%} of that estimated\n")


def main(argv):
    if not argv or argv[0] not in ("nodes", "findings", "cost"):
        sys.stdout.write(__doc__)
        return 2
    cmd, opts, repos = argv[0], {}, {}
    it = iter(argv[1:])
    for a in it:
        if a == "--repo":
            v = next(it)
            if "=" in v:
                k, _, v = v.partition("=")
                repos[k] = v
            else:
                opts["repo"] = v
        elif a.startswith("--"):
            opts[a[2:]] = next(it)
    repo = opts.get("repo") or os.getcwd()
    if cmd == "nodes":
        report_nodes(repo)
    elif cmd == "findings":
        report_findings(repo, opts.get("out"), int(opts.get("sample", 0)))
    else:
        base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
        report_cost(opts.get("projects") or os.path.join(base, "projects", "*", "*.jsonl"), repos)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
