#!/usr/bin/env python3
"""W153.25: an E2 judge that reads the repository at the node's commit, tested on roadmap-dfs's critic backtracks.

  python3 docs/dfs-w153-judge-repo.py cases [--out docs/dfs-w153-judge-repo-cases.jsonl] [--seed 153]
  python3 docs/dfs-w153-judge-repo.py run   CASES.jsonl OUT.jsonl [--runs 2] [--jobs 4] [--limit N]
  python3 docs/dfs-w153-judge-repo.py report CASES.jsonl OUT.jsonl      (dfs-w153-judge.py report + stats)

`cases` takes the 12 roadmap-dfs backtracks of docs/dfs-w153-judge-cases.jsonl, finds each node's commit in this
repository (the oldest commit whose subject starts with "<node id>:"), and draws one control for each: a confirmed
node of the same task (else another task a critic read) that no critic or author challenged, nor any ancestor, with
the same Hypothesis/no-Hypothesis kind, and a commit found the same way. The 13 backtracks of shop-app and
farm-app are out: their repositories are not here.
`run` gives each case, alone, to a fresh `kiro-cli chat --no-interactive --trust-tools=fs_read,grep,glob`
whose directory holds `git archive <node commit>` (no .git, no .kiro) and NODE_COMMIT.txt (that commit's
`git show`), with docs/dfs-w153-judge-prompt-repo.md. The judge sees the node text, that tree and that commit, and
nothing later: no critic or author text, and no history.
"""
import importlib.util, json, random, subprocess, sys, tempfile, tarfile, io
import concurrent.futures as cf
from pathlib import Path

HERE = Path(__file__).resolve().parent
def _load(name, f):
    s = importlib.util.spec_from_file_location(name, HERE / f); m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
j = _load("judge", "dfs-w153-judge.py")
mine = j.mine
import re
j.VERDICT = re.compile(r"VERDICT\s*\|\s*(PASS|FLAG|UNSHOWN)\s*\|\s*([a-f]+|none)\s*\|\s*(.*)", re.I)  # this judge may also say f
REPO = mine.main_checkout()
MAXDIFF = 120_000


def git(*a, text=True):
    return subprocess.run(["git", "-C", str(REPO), *a], capture_output=True, text=text, check=True).stdout


_subjects = None
def commit_for(node):
    """Oldest commit (by commit time) whose subject starts with '<node>:' ; None if there is none."""
    global _subjects
    if _subjects is None:
        _subjects = [l.split("\t", 2) for l in git("log", "--all", "--format=%H\t%ct\t%s").splitlines()]
    hits = [(int(ct), h) for h, ct, s in _subjects if s.startswith(node + ":")]
    return min(hits)[1] if hits else None


def build(argv):
    opt = lambda k, d: argv[argv.index(k) + 1] if k in argv else d
    out, seed = Path(opt("--out", HERE / "dfs-w153-judge-repo-cases.jsonl")), int(opt("--seed", 153))
    old = [json.loads(l) for l in (HERE / "dfs-w153-judge-cases.jsonl").read_text().splitlines() if l.strip()]
    bts = [c for c in old if c["kind"] == "backtrack" and c["project"] == "roadmap-dfs"]
    P = mine.load(mine.args_of(["--at", "d595c44a"]))["roadmap-dfs"]
    import tree
    challenged, critic_tasks = set(), set()
    for tid, t in P.items():
        if any(e["kind"] == "session" and e["args"][:1] == ["critic"] for e in t["log"]):
            critic_tasks.add(tid)
        by = {n["id"]: n for n in t["nodes"]}
        for r in tree.corrections(t):
            if r["node"] not in by:
                continue
            challenged.add((tid, r["node"]))
            for d in t["nodes"]:
                p, k = d.get("parent"), 0
                while p and k < 50:
                    if p == r["node"]:
                        challenged.add((tid, d["id"])); break
                    p = (by.get(p) or {}).get("parent"); k += 1
    cases = []
    for c in bts:
        sha = commit_for(c["node"])
        assert sha, c["node"]
        cases.append(dict(c, commit=sha))
    rng, taken = random.Random(seed), {(c["task"], c["node"]) for c in cases}
    controls = []
    for c in sorted(cases, key=lambda c: (c["task"], c["node"])):
        for same in (True, False):
            cand = []
            for tid, t in P.items():
                if tid == "W153" or (same and tid != c["task"]) or (not same and tid not in critic_tasks):
                    continue
                for n in t["nodes"]:
                    k = (tid, n["id"])
                    if k in challenged or k in taken or not j.usable(n) or bool(mine.hypothesis(n)) != c["hyp"]:
                        continue
                    if commit_for(n["id"]):
                        cand.append((k, n))
            if cand:
                break
        k, n = rng.choice(sorted(cand, key=lambda x: x[0]))
        taken.add(k)
        controls.append(dict(project="roadmap-dfs", task=k[0], node=k[1], kind="control", label="PASS", hyp=c["hyp"],
                             status=n["status"], why="", text=j.node_text(n), commit=commit_for(k[1]),
                             matched_to=f'{c["task"]}.{c["node"]}', same_task=k[0] == c["task"]))
    allc = cases + controls
    rng.shuffle(allc)
    for i, c in enumerate(allc, 1):
        c["case"] = f"r{i:03d}"
    out.write_text("".join(json.dumps(c) + "\n" for c in allc))
    print(out, len(allc), "backtracks", len(cases), "controls", len(controls),
          "same task", sum(c["same_task"] for c in controls), "with Hypothesis", sum(c["hyp"] for c in cases))


def workdir(case, d):
    """The repository as it stood at the node's commit, plus that commit's message and diff."""
    raw = subprocess.run(["git", "-C", str(REPO), "archive", case["commit"]], capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(raw)) as tf:
        members = [m for m in tf.getmembers() if not m.name.startswith((".kiro", ".claude", ".git"))
                   and m.name not in ("AGENTS.md", "CLAUDE.md")]
        tf.extractall(d, members=members, filter="data")
    show = git("show", "--stat", "--patch", "--no-color", case["commit"])
    if len(show) > MAXDIFF:
        show = show[:MAXDIFF] + "\n[diff cut at %d characters]\n" % MAXDIFF
    (Path(d) / "NODE_COMMIT.txt").write_text(show)


def ask(case, run, prompt):
    with tempfile.TemporaryDirectory(prefix="w153r") as d:
        workdir(case, d)
        r = subprocess.run(["kiro-cli", "chat", "--no-interactive", "--trust-tools=fs_read,grep,glob",
                            prompt + "\n" + case["text"] + "\n"], cwd=d, capture_output=True, text=True, timeout=1200)
    raw = j.ANSI.sub("", r.stdout + "\n" + r.stderr)
    m, cr = j.VERDICT.findall(raw), j.CREDITS.findall(raw)
    v = m[-1] if m else None
    return dict(case=case["case"], run=run, verdict=v[0].upper() if v else None, why_class=v[1].lower() if v else None,
                reason=v[2].strip() if v else None, credits=sum(map(float, cr)) if cr else None, raw=raw[-1500:])


def run(argv):
    opt = lambda k, d: argv[argv.index(k) + 1] if k in argv else d
    cases = [json.loads(l) for l in Path(argv[0]).read_text().splitlines() if l.strip()]
    out = Path(argv[1])
    done = {(r["case"], r["run"]) for r in map(json.loads, out.read_text().splitlines())} if out.exists() else set()
    runs, jobs, limit = int(opt("--runs", 2)), int(opt("--jobs", 4)), int(opt("--limit", 10**6))
    prompt = (HERE / "dfs-w153-judge-prompt-repo.md").read_text()
    todo = [(c, k) for c in cases[:limit] for k in range(1, runs + 1) if (c["case"], k) not in done]
    with cf.ThreadPoolExecutor(jobs) as ex, out.open("a") as f:
        for res in ex.map(lambda a: ask(a[0], a[1], prompt), todo):
            f.write(json.dumps(res) + "\n"); f.flush()
            print(res["case"], res["run"], res["verdict"], res["why_class"], res["credits"], flush=True)


def report(argv):
    j.report([argv[0], argv[1], "--tsv", str(HERE / "dfs-w153-judge-repo-errors.tsv")])
    j.stats(argv[:2])


if __name__ == "__main__":
    {"cases": build, "run": run, "report": report}[sys.argv[1]](sys.argv[2:])
