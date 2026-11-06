#!/usr/bin/env python3
"""The shared loader and the Background counts of W153, from the task files, rerunnable.

  python3 docs/dfs-w153-mine.py [--rm DIR] [--shop DIR] [--farm DIR] [--scripts DIR] [--at REV]

Loads every task of the three projects through `audit.load_tasks` (task files and their
archives, merged as the screen reads them) and prints the counts the W153 Background
quotes, with the Background figure beside each so a gap shows. The other W153
accounting scripts import `load()` and `classify_raise()` from here.

Defaults: --rm is the main checkout of this repository (git's common dir, so it works
from a worktree), --shop and --farm are its `optimisation-data/` copies (gitignored, so
a worktree does not hold them), --scripts is the `scripts/` directory beside docs/.
Nothing here edits a task.
"""
import argparse
import collections
import re
import statistics
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
AUTO = re.compile(r"since you last spoke|implementation reviews? of|the review loop|walk stopped|runner", re.I)
TOOL = re.compile(r"\b(roadmap tooling|tooling|check\.py|tree\.py|run\.sh|state\.py|the guard|hook|dfs_run|supervisor)\b", re.I)
PAGE = re.compile(r"\.dfs/artefacts/[0-9a-f-]{36}\.html")
ARCHIVED = ("archived.", "(archived.)")


def main_checkout():
    out = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=HERE, capture_output=True, text=True).stdout.strip()
    p = Path(out)
    if not p.is_absolute():
        p = (HERE / p).resolve()
    return p.parent


def args_of(argv=None):
    root = main_checkout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--rm", default=str(root))
    ap.add_argument("--shop", default=str(root / "optimisation-data" / "shop-app"))
    ap.add_argument("--farm", default=str(root / "optimisation-data" / "farm-app"))
    ap.add_argument("--scripts", default=str(HERE.parent / "scripts"))
    ap.add_argument("--at", help="read roadmap-dfs's task files as they were at this revision (the Background's mining pass was d595c44a)")
    a = ap.parse_args(argv)
    if a.at:
        import tempfile
        d = tempfile.mkdtemp(prefix="w153-at-")
        git = subprocess.Popen(["git", "-C", a.rm, "archive", a.at, ".dfs/items", ".dfs/archive"], stdout=subprocess.PIPE)
        subprocess.run(["tar", "-x", "-C", d], stdin=git.stdout, check=True)
        a.rm = d
    return a


def load(a):
    """{project: {task id: task}}"""
    sys.path.insert(0, a.scripts)
    import audit
    return {n: audit.load_tasks(p) for n, p in (("roadmap-dfs", a.rm), ("shop-app", a.shop), ("farm-app", a.farm))}


def classify_raise(body):
    """'loop' (the runner's review-loop checkpoint), 'tooling', or 'other' (a decision)."""
    if AUTO.search(body[:300]):
        return "loop"
    return "tooling" if TOOL.search(body) else "other"


def words(s):
    return len((s or "").split())


def determination(n):
    d = n["fields"].get("Determination") or ""
    return "" if (not d or d in ARCHIVED or "(archived.)" in d) else d


def hypothesis(n):
    h = (n["fields"].get("Hypothesis") or "").strip()
    return "" if h in ("", "archived.") else h


def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(p * len(v)))] if v else None


def main(argv=None):
    a = args_of(argv)
    P = load(a)
    tot = collections.Counter()
    det_all = []
    for name, T in P.items():
        c = collections.Counter()
        dw = []
        for tid, t in T.items():
            c["tasks"] += 1
            c["nodes"] += len(t["nodes"])
            for n in t["nodes"]:
                if re.search(r"minor findings", n["title"], re.I):
                    c["minor_nodes"] += 1
                d = determination(n)
                if d:
                    dw.append(words(d))
                h = hypothesis(n)
                if h:
                    c["hyp"] += 1
                    if PAGE.search(h):
                        c["hyp_page"] += 1
                    m = re.search(r"wrong if(.*)", h, re.I | re.S)
                    if m and re.search(r"\bauthor\b", m.group(1), re.I):
                        c["hyp_wrongif_author"] += 1
            for e in t["log"]:
                if e["kind"] == "raise":
                    c["raise"] += 1
                    c["raise_" + classify_raise(e["body"])] += 1
                elif e["kind"] == "answer":
                    c["answer"] += 1
                elif e["kind"] == "correct":
                    c["correct"] += 1
                elif e["kind"] == "backtrack":
                    c["backtrack"] += 1
                elif e["kind"] == "critic":
                    c["critic_entries"] += 1
        det_all += dw
        print(f"{name}: {dict(c)}")
        print(f"  Determination words: n={len(dw)} median={statistics.median(dw) if dw else None} p90={pct(dw, .9)}")
        tot.update(c)
    print(f"TOTAL: {dict(tot)}")
    print(f"  Determination words, all: n={len(det_all)} median={statistics.median(det_all)} p90={pct(det_all, .9)}")
    print()
    print("Background figure -> this run")
    print(f"  nodes 1,519 -> {tot['nodes']}")
    print(f"  raises 124 (50/59/14 by project) -> {tot['raise']}")
    print(f"  Minor findings nodes 169 -> {tot['minor_nodes']}")
    print(f"  Determination words median 61, p90 108 (roadmap-dfs alone; all three projects pooled gives the figure here) -> {statistics.median(det_all)}, {pct(det_all, .9)}")
    r = tot
    print("  roadmap-dfs hypotheses 127, with a page 104, Wrong if naming the author 90 -> see the roadmap-dfs line: hyp, hyp_page, hyp_wrongif_author")
    print(f"  loop 36, tooling 23 (brief; 26 with the three farm-app ones), by regex, so rough -> {r['raise_loop']} / {r['raise_tooling']}; other {r['raise_other']}")
    print(f"  critic backtracks 25 -> {r['backtrack']}")


if __name__ == "__main__":
    main()
