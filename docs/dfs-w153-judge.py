#!/usr/bin/env python3
"""W153 E2 judge: does a node's evidence observe the thing its Determination claims, and could it have failed?

  python3 docs/dfs-w153-judge.py cases  [--out docs/dfs-w153-judge-cases.jsonl] [--seed 153] [--at d595c44a]
  python3 docs/dfs-w153-judge.py run    CASES.jsonl OUT.jsonl [--runs 2] [--jobs 4] [--limit N] [--prompt FILE in docs/]
  python3 docs/dfs-w153-judge.py report CASES.jsonl OUT.jsonl [--tsv docs/dfs-w153-judge-errors.tsv] [--split odd|even]
(a case is in the dev half when its number is odd, in the held-out half when even)
  python3 docs/dfs-w153-judge.py stats  CASES.jsonl OUT.jsonl     (flag rate of backtracked against unchallenged nodes, Fisher test)

`cases` builds the labelled set from the three projects' task files (as `dfs-w153-mine.py` loads them,
roadmap-dfs read as at --at so the 25 critic backtracks of the Background are the ones found):
  * backtrack   the node a critic withdrew (25), label FLAG;
  * ruling      a node with a Hypothesis the author confirmed or refuted by a correction, label FLAG if
                refuted and PASS if confirmed (a weaker label: the author ruled on the assumption, not on the
                evidence);
  * control     a confirmed node nobody challenged, neither it nor any ancestor withdrawn or ruled on, as many
                as the labelled cases, drawn from the same task first (so task, era and writer match), label PASS.
`run` sends each case, alone, to a fresh `kiro-cli chat --no-interactive` with no tools, in an empty directory,
with docs/dfs-w153-judge-prompt.md. The judge sees title, Approach, Hypothesis, Evidence and Determination, and
nothing else: no id, status, parent, log entry, critic or author text, and no later node.
`report` prints misses (a labelled FLAG passed) and false alarms (a control flagged), per kind of case.
"""
import importlib.util, json, random, re, subprocess, sys, tempfile
import concurrent.futures as cf
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("mine", HERE / "dfs-w153-mine.py")
mine = importlib.util.module_from_spec(spec); spec.loader.exec_module(mine)

VERDICT = re.compile(r"VERDICT\s*\|\s*(PASS|FLAG|UNSHOWN)\s*\|\s*([a-e]+|none)\s*\|\s*(.*)", re.I)
CREDITS = re.compile(r"Credits:\s*([\d.]+)")
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def node_text(n):
    """The node as the judge sees it. Nothing that says who looked at it or what became of it."""
    f = n["fields"]
    app = (f.get("Approach") or "").strip()
    hyp = mine.PAGE.sub("", mine.hypothesis(n)).strip()
    ev = "\n".join(n.get("evidence") or [])
    det = mine.determination(n).strip()
    out = [f"Title: {n['title']}", f"Approach: {app}"]
    if hyp:
        out.append(f"Hypothesis: {hyp}")
    if ev.strip() and ev.strip() != "archived.":
        out.append("Evidence:\n" + ev)
    out.append(f"Determination: {det}")
    return "\n".join(out)


def usable(n):
    return (n["status"] == "confirmed" and len(mine.determination(n).split()) >= 8
            and (n["fields"].get("Approach") or "").strip() not in ("", "archived."))


def build(argv):
    opt = lambda k, d: argv[argv.index(k) + 1] if k in argv else d
    seed, out = int(opt("--seed", 153)), Path(opt("--out", HERE / "dfs-w153-judge-cases.jsonl"))
    a = mine.args_of(["--at", opt("--at", "d595c44a")])
    P = mine.load(a)
    import tree
    labelled, challenged_ids, critic_tasks = [], set(), set()
    for proj, T in P.items():
        for tid, t in T.items():
            if tid == "W153":
                continue
            by = {n["id"]: n for n in t["nodes"]}
            if any(e["kind"] == "session" and e["args"][:1] == ["critic"] for e in t["log"]):
                critic_tasks.add((proj, tid))
            for r in tree.corrections(t):
                n = by.get(r["node"])
                if not n:
                    continue
                challenged_ids.add((proj, tid, n["id"]))
                for d in t["nodes"]:      # everything below it was withdrawn or rebuilt on the ruling
                    p, seen = d.get("parent"), 0
                    while p and seen < 50:
                        if p == n["id"]:
                            challenged_ids.add((proj, tid, d["id"])); break
                        p = (by.get(p) or {}).get("parent"); seen += 1
                if r["disp"] == "backtrack":
                    kind, label = "backtrack", "FLAG"
                elif mine.hypothesis(n):
                    kind, label = "ruling", "FLAG" if r["disp"] == "refuted" else "PASS"
                else:
                    continue
                if not mine.determination(n):
                    continue
                labelled.append(dict(project=proj, task=tid, node=n["id"], kind=kind, label=label,
                                     hyp=bool(mine.hypothesis(n)), status=n["status"], why=r["body"].strip()[:600],
                                     text=node_text(n)))
    # one case per node; a backtrack outranks a ruling
    best = {}
    for c in labelled:
        k = (c["project"], c["task"], c["node"])
        if k not in best or c["kind"] == "backtrack":
            best[k] = c
    labelled = list(best.values())
    rng = random.Random(seed)
    controls, taken = [], set(best)
    for c in sorted(labelled, key=lambda c: (c["project"], c["task"], c["node"])):
        def pool(tasks_only, same_task):
            out_ = []
            for proj, T in P.items():
                for tid, t in T.items():
                    if tid == "W153" or (same_task and (proj, tid) != (c["project"], c["task"])):
                        continue
                    if tasks_only and (proj, tid) not in critic_tasks:
                        continue
                    for n in t["nodes"]:
                        k = (proj, tid, n["id"])
                        if k in challenged_ids or k in taken or not usable(n):
                            continue
                        if bool(mine.hypothesis(n)) != c["hyp"]:
                            continue
                        out_.append((k, n))
            return out_
        for tasks_only, same_task in ((True, True), (False, True), (True, False)):
            cand = pool(tasks_only, same_task)
            if cand:
                break
        k, n = rng.choice(sorted(cand, key=lambda x: x[0]))
        taken.add(k)
        controls.append(dict(project=k[0], task=k[1], node=k[2], kind="control", label="PASS", hyp=c["hyp"],
                             status=n["status"], why="", text=node_text(n), matched_to=f'{c["task"]}.{c["node"]}',
                             same_task=(k[0], k[1]) == (c["project"], c["task"]),
                             in_critic_task=(k[0], k[1]) in critic_tasks))
    cases = labelled + controls
    rng.shuffle(cases)
    for i, c in enumerate(cases, 1):
        c["case"] = f"c{i:03d}"
    out.write_text("".join(json.dumps(c) + "\n" for c in cases))
    from collections import Counter
    print(out, len(cases), dict(Counter((c["kind"], c["label"]) for c in cases)))
    print("controls in the same task:", sum(c.get("same_task", False) for c in controls), "of", len(controls),
          "; in a task that had a critic session:", sum(c["in_critic_task"] for c in controls))


def ask(case, run, prompt):
    with tempfile.TemporaryDirectory(prefix="w153j") as d:
        full = prompt + "\n" + case["text"] + "\n"
        r = subprocess.run(["kiro-cli", "chat", "--no-interactive", "--trust-tools=", full], cwd=d,
                           capture_output=True, text=True, timeout=900)
    raw = ANSI.sub("", r.stdout + "\n" + r.stderr)
    m = VERDICT.findall(raw)
    cr = CREDITS.findall(raw)
    v = m[-1] if m else None
    return dict(case=case["case"], run=run, verdict=v[0].upper() if v else None, why_class=v[1].lower() if v else None,
                reason=v[2].strip() if v else None, credits=sum(map(float, cr)) if cr else None, raw=raw[-1500:])


def run(argv):
    opt = lambda k, d: argv[argv.index(k) + 1] if k in argv else d
    cases = [json.loads(l) for l in Path(argv[0]).read_text().splitlines() if l.strip()]
    out = Path(argv[1])
    done = {(json.loads(l)["case"], json.loads(l)["run"]) for l in out.read_text().splitlines() if l.strip()} if out.exists() else set()
    runs, jobs, limit = int(opt("--runs", 2)), int(opt("--jobs", 4)), int(opt("--limit", 10**6))
    prompt = (HERE / opt("--prompt", "dfs-w153-judge-prompt.md")).read_text()
    todo = [(c, k) for c in cases[:limit] for k in range(1, runs + 1) if (c["case"], k) not in done]
    with cf.ThreadPoolExecutor(jobs) as ex, out.open("a") as f:
        for res in ex.map(lambda a: ask(a[0], a[1], prompt), todo):
            f.write(json.dumps(res) + "\n"); f.flush()
            print(res["case"], res["run"], res["verdict"], res["why_class"], res["credits"], flush=True)


def report(argv):
    opt = lambda k, d: argv[argv.index(k) + 1] if k in argv else d
    cases = {c["case"]: c for c in (json.loads(l) for l in Path(argv[0]).read_text().splitlines() if l.strip())}
    res = [json.loads(l) for l in Path(argv[1]).read_text().splitlines() if l.strip()]
    sp = opt("--split", None)
    if sp:
        keep = (lambda cid: int(cid[1:]) % 2 == 1) if sp == "odd" else (lambda cid: int(cid[1:]) % 2 == 0)
        cases = {k: v for k, v in cases.items() if keep(k)}
        res = [r for r in res if r["case"] in cases]
    by = {}
    for r in res:
        by.setdefault(r["case"], []).append(r)
    rows, none = [], 0
    for cid, rs in by.items():
        vs = [r["verdict"] for r in rs]
        none += vs.count(None)
        flags = sum(v == "FLAG" for v in vs)
        # a case is flagged when more than half of its valid judgings flag; a split counts as flagged (the gate would stop and ask)
        valid = [v for v in vs if v]
        flagged = bool(valid) and flags * 2 >= len(valid)
        rows[:] = rows
        rows.append((cases[cid], flagged, flags, len(valid), rs))
    print(f"judgings {len(res)}, unparsed {none}, cases {len(rows)}, credits {sum(r['credits'] or 0 for r in res):.1f}")
    def line(name, sel):
        s = [r for r in rows if sel(r[0])]
        if not s:
            return
        fl = sum(r[1] for r in s)
        agree = sum(1 for r in s if r[2] in (0, r[3]))
        print(f"  {name:34s} n={len(s):3d} flagged={fl:3d} ({100*fl/len(s):.0f}%)  runs agree {agree}/{len(s)}")
    uns = sum(r["verdict"] == "UNSHOWN" for r in res)
    print(f"UNSHOWN judgings (counted as not flagged): {uns} of {len(res)}")
    print("by case kind (flagged = at least half of its judgings said FLAG):")
    line("backtrack (should FLAG)", lambda c: c["kind"] == "backtrack")
    line("ruling refuted (label FLAG)", lambda c: c["kind"] == "ruling" and c["label"] == "FLAG")
    line("ruling confirmed (label PASS)", lambda c: c["kind"] == "ruling" and c["label"] == "PASS")
    line("control (should PASS)", lambda c: c["kind"] == "control")
    line("control with a Hypothesis", lambda c: c["kind"] == "control" and c["hyp"])
    line("control without one", lambda c: c["kind"] == "control" and not c["hyp"])
    line("backtrack with a Hypothesis", lambda c: c["kind"] == "backtrack" and c["hyp"])
    line("backtrack without one", lambda c: c["kind"] == "backtrack" and not c["hyp"])
    bt = [r for r in rows if r[0]["kind"] == "backtrack"]
    miss = [r for r in bt if not r[1]]
    print(f"misses of the backtracks: {len(miss)} of {len(bt)}  (E2 judging is parked if over half)")
    tsv = Path(opt("--tsv", HERE / "dfs-w153-judge-errors.tsv"))
    with tsv.open("w") as f:
        f.write("error\tcase\ttask.node\tkind\tlabel\tflags/judgings\tjudge_class\tjudge_reason\tcritic_or_author_text\n")
        for c, flagged, flags, n, rs in sorted(rows, key=lambda r: r[0]["case"]):
            bad = (c["label"] == "FLAG" and not flagged) or (c["label"] == "PASS" and flagged)
            if bad:
                kind = "miss" if c["label"] == "FLAG" else "false alarm"
                r0 = rs[0]
                f.write("\t".join([kind, c["case"], f'{c["task"]}.{c["node"]}', c["kind"], c["label"], f"{flags}/{n}",
                                   str(r0["why_class"]), (r0["reason"] or "").replace("\t", " "),
                                   c["why"].replace("\n", " ").replace("\t", " ")[:500]]) + "\n")
    print("errors written to", tsv)


def stats(argv):
    """Does the judge flag backtracked nodes more often than unchallenged ones? (one-sided Fisher exact test)"""
    import math
    from collections import defaultdict
    cs = {c["case"]: c for c in map(json.loads, Path(argv[0]).read_text().splitlines() if False else [l for l in Path(argv[0]).read_text().splitlines() if l.strip()])}
    rs = [json.loads(l) for l in Path(argv[1]).read_text().splitlines() if l.strip()]
    def fisher(a, b, c, d):
        n, K, N1 = a + b + c + d, a + c, a + b
        return sum(math.comb(K, i) * math.comb(n - K, N1 - i) for i in range(a, min(K, N1) + 1)) / math.comb(n, N1)
    fl, n = defaultdict(int), defaultdict(int)
    for r in rs:
        n[r["case"]] += 1; fl[r["case"]] += r["verdict"] == "FLAG"
    bt = [c for c in cs if cs[c]["kind"] == "backtrack"]; ct = [c for c in cs if cs[c]["kind"] == "control"]
    for thr, lab in ((1, "flagged in at least 1 of 2 judgings"), (2, "flagged in both judgings")):
        a = sum(fl[c] >= thr for c in bt); c_ = sum(fl[c] >= thr for c in ct)
        print(f"{lab}: backtracks {a}/{len(bt)}, controls {c_}/{len(ct)}, one-sided Fisher p={fisher(a, len(bt)-a, c_, len(ct)-c_):.3f}")
    a = sum(r["verdict"] == "FLAG" for r in rs if cs[r["case"]]["kind"] == "backtrack"); na = sum(cs[r["case"]]["kind"] == "backtrack" for r in rs)
    b = sum(r["verdict"] == "FLAG" for r in rs if cs[r["case"]]["kind"] == "control"); nb = sum(cs[r["case"]]["kind"] == "control" for r in rs)
    print(f"per judging: backtracks {a}/{na} ({100*a/na:.0f}%), controls {b}/{nb} ({100*b/nb:.0f}%)")
    pairs = [(next(x for x in bt if f'{cs[x]["task"]}.{cs[x]["node"]}' == k["matched_to"]), k) for k in cs.values()
             if k["kind"] == "control" and any(f'{cs[x]["task"]}.{cs[x]["node"]}' == k["matched_to"] for x in bt)]
    w = sum(fl[c] > fl[k["case"]] for c, k in pairs); l = sum(fl[c] < fl[k["case"]] for c, k in pairs)
    print(f"matched pairs (backtrack vs its control): backtrack flagged more {w}, less {l}, equal {len(pairs)-w-l}")


if __name__ == "__main__":
    cmd, rest = sys.argv[1], sys.argv[2:]
    {"cases": build, "run": run, "report": report, "stats": stats}[cmd](rest)
