#!/usr/bin/env python3
"""Second, arm-blind reading of the E8 hand matching (tests the Hypothesis of W153.39).

The first reading (docs/dfs-w153-e8-hand.tsv) was made by the working session with the `informed` and `blind`
columns side by side. Here one fresh kiro-cli session per tree gets the rubric's clause table, the clauses that
are targets for that tree, and two reviews called X and Y (which arm is which is drawn from a seeded shuffle and
never shown), and says for each target and each review whether it is named: P (a finding's headline says the
clause is unmet), M (a line of a "Minor findings" node says it), D (doubtful: a neighbouring or very rare
defect), or - (not named). It never sees the first reading. Output: docs/dfs-w153-e8-second.tsv (same columns as
the hand table, plus `agree`), and the three counts of dfs-w153-e8-hand.py on both readings.

  python3 docs/dfs-w153-e8-second.py            # read (skips trees already in the .raw.jsonl) and compare
  python3 docs/dfs-w153-e8-second.py --compare  # compare only
  SEED=154 python3 docs/dfs-w153-e8-second.py   # a third reading with the other order drawn
"""
import csv, json, random, re, subprocess, sys
from math import comb
from pathlib import Path

DOCS = Path(__file__).resolve().parent
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
HAND = DOCS / "dfs-w153-e8-hand.tsv"
import os
SEED = int(os.environ.get("SEED", "153"))             # a rerun with another SEED reads again with another X/Y order
SUF = "" if SEED == 153 else f"-seed{SEED}"
RAW = DOCS / f"dfs-w153-e8-second{SUF}.raw.jsonl"
OUT = DOCS / f"dfs-w153-e8-second{SUF}.tsv"

PROMPT = """You read two code reviews against a rubric. Below is the rubric's clause table, then the clauses to
check, then Review X and Review Y. Each review is the list of findings a reviewer added to a task after
reading the finished work (or "NO FINDINGS"). For each clause to check and for each review, say whether
that review names the clause as not delivered:
 P = a finding's headline (its title) says what would make the clause not met;
 M = a line inside a finding titled "Minor findings" (or a plain line of an ok review) says it;
 D = doubtful: a finding about a neighbouring defect, or a rare case, that only might make the clause not met;
 - = no finding says it.
A finding names a clause when what it says is wrong or missing would make that clause not met, whether or not
it uses the clause's words. Do not read any file and do not run anything. Answer with exactly one line per
clause to check, nothing else, in this form:
C4 | X=P | Y=-

RUBRIC CLAUSES
{clauses}

CLAUSES TO CHECK
{targets}

REVIEW X
{x}

REVIEW Y
{y}
"""


def clause_table(task):
    rows = re.findall(r"(?m)^\|\s*(C\d+)\s*\|\s*(.*?)\s*\|\s*$", (DOCS / "dfs-rubrics" / f"{task}.md").read_text())
    return {c: t for c, t in rows}


def reviews():
    """{(tree, arm): text} from the extraction files (nodes the review added) and the unmerged runs."""
    out = {}
    for f in ("dfs-w153-e8-findings.jsonl", "dfs-w153-e8-new.findings.jsonl"):
        for l in (DOCS / f).open():
            d = json.loads(l)
            out[(d["replay"], d["arm"])] = "\n\n".join(d["nodes"]) or "NO FINDINGS"
    # the two unmerged trees, scored on their nested worktrees: the review's added nodes or its `ok` lines
    for l in (DOCS / "dfs-w153-e8-unmerged-runs.jsonl").open():
        d = json.loads(l)
        t = d["task_file_added"]
        if "### " in t:
            nodes = [n.strip() for n in re.split(r"(?m)^(?=### )", t) if n.startswith("### W17.2")]
            nodes = [re.sub(r"(?m)^- \d{4}-.*$", "", n).strip() for n in nodes if "Status: open" in n]
            out[(d["replay"], d["arm"])] = "\n\n".join(nodes) or "NO FINDINGS"
        else:
            out[(d["replay"], d["arm"])] = re.sub(r"^- \d{4}-\S+ · review · ok\n", "", t).strip() or "NO FINDINGS"
    return out


def read_all():
    hand = list(csv.DictReader(HAND.open(encoding="utf-8"), delimiter="\t"))
    trees = {}
    for r in hand:
        tree, c = r["target"].split("/")
        trees.setdefault(tree, []).append(c)
    rev = reviews()
    done = {}
    if RAW.exists():
        for l in RAW.open():
            r = json.loads(l); done[r["tree"]] = r
    rng = random.Random(SEED)
    with RAW.open("a") as f:
        for tree, cs in sorted(trees.items()):
            flip = rng.random() < 0.5                      # drawn for every tree, so a rerun keeps the same order
            if tree in done:
                continue
            task = tree.rsplit("-", 1)[0]
            clauses = clause_table(task)
            inf, bl = rev[(tree, "informed")], rev[(tree, "blind")]
            x, y = (bl, inf) if flip else (inf, bl)        # X is blind when flip
            prompt = PROMPT.format(
                clauses="\n".join(f"{c}: {clauses[c]}" for c in sorted(clauses, key=lambda s: int(s[1:]))),
                targets="\n".join(f"{c}: {clauses[c]}" for c in sorted(cs, key=lambda s: int(s[1:]))),
                x=x, y=y)
            r = subprocess.run(["kiro-cli", "chat", "--no-interactive", "--trust-tools=", prompt],
                               capture_output=True, text=True, timeout=900)
            out = ANSI.sub("", r.stdout)
            got = {}
            for m in re.finditer(r"\b(C\d+)\s*\|\s*X\s*=\s*([PMD-])\s*\|\s*Y\s*=\s*([PMD-])", out):
                got[m.group(1)] = (m.group(2), m.group(3))
            row = {"tree": tree, "x_is": "blind" if flip else "informed", "targets": cs, "got": got, "out": out[-1500:]}
            f.write(json.dumps(row) + "\n"); f.flush()
            print(tree, row["x_is"], got, flush=True)


def sign(a, b):
    n, k = a + b, min(a, b)
    return min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n) if n else 1.0


def compare():
    hand = {r["target"]: r for r in csv.DictReader(HAND.open(encoding="utf-8"), delimiter="\t")}
    second = {}
    for l in RAW.open():
        r = json.loads(l)
        for c, (px, py) in r["got"].items():
            inf, bl = (py, px) if r["x_is"] == "blind" else (px, py)
            second[f'{r["tree"]}/{c}'] = (inf, bl)
    rows, cell, cell_ok, tgt_ok, missing = [], 0, 0, 0, []
    for t, h in hand.items():
        if t not in second:
            missing.append(t); continue
        inf, bl = second[t]
        a_i, a_b = inf == h["informed"], bl == h["blind"]
        cell += 2; cell_ok += a_i + a_b
        tgt_ok += a_i and a_b
        rows.append((t, h["informed"], h["blind"], inf, bl, "yes" if a_i and a_b else "no"))
    with OUT.open("w", encoding="utf-8") as f:
        f.write("target\tfirst_informed\tfirst_blind\tsecond_informed\tsecond_blind\tagree\n")
        for r in rows:
            f.write("\t".join(r) + "\n")
    n = len(rows)
    print(f"targets read twice: {n}, missing {len(missing)} {missing}")
    print(f"cells (target x arm) agreeing exactly: {cell_ok} of {cell}; targets with both cells agreeing: {tgt_ok} of {n}")
    for name, reading in (("first", lambda r: (r[1], r[2])), ("second", lambda r: (r[3], r[4]))):
        for label, ok in (("headline (P)", "P"), ("strict (P or M)", "PM"), ("lenient (P, M or D)", "PMD")):
            i = {r[0] for r in rows if reading(r)[0] in ok}
            b = {r[0] for r in rows if reading(r)[1] in ok}
            print(f"{name:6} {label:20} informed {len(i):2} blind {len(b):2} informed only {len(i - b)} blind only {len(b - i)} p = {sign(len(i - b), len(b - i)):.3f}")
    # the sign of each count: informed minus blind, per reading
    for label, ok in (("headline", "P"), ("strict", "PM"), ("lenient", "PMD")):
        d1 = sum(r[1] in ok for r in rows) - sum(r[2] in ok for r in rows)
        d2 = sum(r[3] in ok for r in rows) - sum(r[4] in ok for r in rows)
        print(f"{label}: informed minus blind, first {d1:+d}, second {d2:+d}")


if __name__ == "__main__":
    if "--compare" not in sys.argv:
        read_all()
    compare()
