#!/usr/bin/env python3
"""Score the E8 review-only run: which of a tree's unmet rubric clauses each reviewer's findings name.

  python3 docs/dfs-w153-e8-score.py REPLAYS_DIR OUT_PREFIX            # extract + judge + table

For each tree and arm (informed `-inf`, blind `-bl`) it reads the nodes the review added to the copy's
task file (`git diff HEAD` of .dfs/items/<task>.md: the copy's HEAD is the tree as the reviewer got it),
its verdict (the new `review · ok|issues` log line), and asks one kiro-cli session, which sees only the
task's rubric and the added nodes, which clause each finding is about. The truth is the quality judge's
scores (docs/dfs-quality-scores.jsonl, two judgings per tree): a clause is unmet "in both" or "in either".
The judge never sees which arm wrote the findings. Output: OUT_PREFIX.findings.jsonl (the extraction and
the judge's lines) and OUT_PREFIX.tsv (one row per tree and arm).
"""
import json, re, subprocess, sys
from pathlib import Path

DOCS = Path(__file__).resolve().parent
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
PROMPT = """You match review findings to rubric clauses. Below is a rubric (clauses C1, C2, ...) and a list of findings F1, F2, ...
a code reviewer wrote about work on that task. For each finding say which clauses of the rubric it says are
not delivered (a finding names a clause when what it says is wrong or missing would make that clause not met,
whether or not it uses the clause's words). A finding about something no clause covers gets none. A finding
that lists several small points ("Minor findings") is judged point by point and may name several clauses.
Do not read any file and do not run anything. Answer with exactly one line per finding, nothing else:
F1 | C3,C4
F2 | none

RUBRIC
{rubric}

FINDINGS
{findings}
"""


def added_nodes(copy, task):
    diff = subprocess.run(["git", "-C", str(copy), "diff", "HEAD", "--", f".dfs/items/{task}.md"],
                          capture_output=True, text=True).stdout
    plus = "\n".join(l[1:] for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))
    nodes = re.split(r"(?m)^(?=### )", plus)
    nodes = [n.strip() for n in nodes if n.startswith("### ")]
    nodes = [re.sub(r"(?m)^- \d{4}-.*$", "", n).strip() for n in nodes]      # the log lines ride after the last node
    verdict = re.findall(r"· review · (ok|issues)", plus)
    return nodes, (verdict[-1] if verdict else "none")


def judge(rubric, nodes):
    findings = "\n\n".join(f"F{i+1}: {n}" for i, n in enumerate(nodes))
    r = subprocess.run(["kiro-cli", "chat", "--no-interactive", "--trust-tools=", PROMPT.format(rubric=rubric, findings=findings)],
                       capture_output=True, text=True, timeout=900)
    out = ANSI.sub("", r.stdout)
    got = {}
    for m in re.finditer(r"\bF(\d+)\s*\|\s*([^\n|]*)", out):
        got[int(m.group(1))] = sorted(set(re.findall(r"C\d+", m.group(2))))
    return [got.get(i + 1) for i in range(len(nodes))], out


def truth(replay):
    runs = [json.loads(l) for l in open(DOCS / "dfs-quality-scores.jsonl") if json.loads(l)["replay"] == replay]
    unmet = [{c for c, v in r["clauses"].items() if not v["met"]} for r in runs]
    return set.intersection(*unmet), set.union(*unmet)


def main(argv):
    root, prefix = Path(argv[0]), argv[1]
    done = {}
    fp = Path(prefix + ".findings.jsonl")
    if fp.exists():
        for l in fp.open():
            r = json.loads(l); done[(r["replay"], r["arm"])] = r
    names = sorted({p.name[len("dfs-replay-"):-4] for p in root.glob("dfs-replay-*-inf")})
    with fp.open("a") as f:
        for name in names:
            task = name.rsplit("-", 1)[0]
            rubric = (DOCS / "dfs-rubrics" / f"{task}.md").read_text()
            for arm, suf in (("informed", "inf"), ("blind", "bl")):
                if (name, arm) in done:
                    continue
                nodes, verdict = added_nodes(root / f"dfs-replay-{name}-{suf}", task)
                lines, raw = judge(rubric, nodes) if nodes else ([], "")
                rec = dict(replay=name, arm=arm, verdict=verdict, nodes=[n[:4000] for n in nodes], lines=lines)
                f.write(json.dumps(rec) + "\n"); f.flush(); done[(name, arm)] = rec
    rows = ["replay\tarm\tverdict\tfindings\tmissing_judge_lines\tboth_total\tboth_named\teither_total\teither_named\tnames_a_met_clause\tno_clause"]
    for (name, arm), r in sorted(done.items()):
        both, either = truth(name)
        named = set().union(*[set(l) for l in r["lines"] if l]) if r["lines"] else set()
        met_named = sorted(named - either)
        none = sum(1 for l in r["lines"] if l == [])
        miss = sum(1 for l in r["lines"] if l is None)
        rows.append("\t".join(map(str, [name, arm, r["verdict"], len(r["nodes"]), miss, len(both), len(both & named),
                                        len(either), len(either & named), ",".join(met_named) or "-", none])))
    Path(prefix + ".tsv").write_text("\n".join(rows) + "\n")
    print("\n".join(rows))


if __name__ == "__main__":
    main(sys.argv[1:])
