#!/usr/bin/env python3
"""Tier 1 of the replay measure: score a replay's delivered tree against its task's rubric.

  python3 docs/dfs-quality-judge.py REPLAYS_DIR OUT.jsonl [--runs 2] [--jobs 4] [--only W22-c1a,...]

For each replay (`dfs-replay-<task>-<tag>`) it builds a blind snapshot: the tree at HEAD without `.dfs`,
`CHANGES.diff` (HEAD against the replay's starting commit, without `.dfs`), and `RUBRIC.md`
(docs/dfs-rubrics/<task>.md), in a directory with a neutral name. A fresh kiro-cli session, with only
read tools, is given docs/dfs-rubrics/judge-prompt.md and answers one line per clause. One JSON line per
judging: replay, run, met clauses, per clause verdicts, the raw answer, credits if kiro printed them.
"""
import concurrent.futures as cf, json, os, re, shutil, subprocess, sys, tempfile
from pathlib import Path

DOCS = Path(__file__).resolve().parent
LINE = re.compile(r"\b(C\d+)\s*\|\s*(not met|met)\s*\|\s*([^|]*?)\s*\|", re.I)   # the first line can follow a tool note without a newline
CREDITS = re.compile(r"Credits:\s*([\d.]+)")


def tip_of(clone, task):
    """The repository the delivered tree is read from: a tree the chain never merged has its work in the
    nested worktree .dfs/worktrees/<task> (branch dfs/<task>); the main copy is the starting tree (W153.20)."""
    nested = Path(clone) / ".dfs" / "worktrees" / task
    return nested if (nested / ".git").exists() else Path(clone)


def start_of(clone, task):
    out = subprocess.run(["git", "-C", str(clone), "log", "--format=%H", f"--grep=^replay: {task} as the author left it"],
                         capture_output=True, text=True, check=True).stdout.split()
    return out[-1]


def snapshot(clone, task, dest):
    """Blind copy of the delivered tree: no .dfs, no history, no arm name."""
    clone = tip_of(clone, task)
    start = start_of(clone, task)
    tree = Path(dest) / "work"
    tree.mkdir(parents=True)
    tar = subprocess.run(["git", "-C", str(clone), "archive", "HEAD"], capture_output=True, check=True).stdout
    subprocess.run(["tar", "-x", "-C", str(tree)], input=tar, check=True)
    shutil.rmtree(tree / ".dfs", ignore_errors=True)
    diff = subprocess.run(["git", "-C", str(clone), "diff", start, "HEAD", "--", ".", ":!.dfs"],
                          capture_output=True, text=True, check=True).stdout
    (tree / "CHANGES.diff").write_text(diff)
    shutil.copy(DOCS / "dfs-rubrics" / f"{task}.md", tree / "RUBRIC.md")
    return tree


def parse(answer, n):
    seen = {}
    for c, v, where in LINE.findall(answer):
        seen.setdefault(c.upper(), (v.lower() == "met", where))
    return {f"C{i}": seen.get(f"C{i}", (False, "missing")) for i in range(1, n + 1)}


def judge(clone, task, run):
    with tempfile.TemporaryDirectory(prefix="j") as d:
        tree = snapshot(clone, task, d)
        n = len(re.findall(r"^\| C\d+ ", (tree / "RUBRIC.md").read_text(), re.M))
        prompt = (DOCS / "dfs-rubrics" / "judge-prompt.md").read_text()
        r = subprocess.run(["kiro-cli", "chat", "--no-interactive", "--trust-tools=fs_read,glob,grep", prompt],
                           cwd=tree, capture_output=True, text=True, timeout=1500)
        raw = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", r.stdout + "\n" + r.stderr)
        verdicts = parse(raw, n)
        cr = CREDITS.findall(raw)
        return dict(replay=f"{task}-{clone.name.split('-')[-1]}", run=run, n=n,
                    met=sum(v[0] for v in verdicts.values()),
                    clauses={c: dict(met=v[0], at=v[1]) for c, v in verdicts.items()},
                    credits=sum(map(float, cr)) if cr else None, raw=raw)


def main(argv):
    root, out = Path(argv[0]), Path(argv[1])
    opt = lambda k, d: argv[argv.index(k) + 1] if k in argv else d
    runs, jobs = int(opt("--runs", 2)), int(opt("--jobs", 4))
    only = set(opt("--only", "").split(",")) - {""}
    todo = []
    for clone in sorted(root.glob("dfs-replay-W*-[bck][0-9]*")):
        task, tag = clone.name[len("dfs-replay-"):].rsplit("-", 1)
        if only and f"{task}-{tag}" not in only:
            continue
        todo += [(clone, task, k) for k in range(1, runs + 1)]
    with cf.ThreadPoolExecutor(jobs) as ex, out.open("a") as f:
        for res in ex.map(lambda a: judge(*a), todo):
            f.write(json.dumps(res) + "\n"); f.flush()
            print(res["replay"], res["run"], f'{res["met"]}/{res["n"]}', res["credits"], flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
