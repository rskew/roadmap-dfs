#!/usr/bin/env python3
"""The control and changed replays of W130, scored two ways, and the pair rule on the corrected counts.

    python3 docs/dfs-replay-verdicts.py REPLAYS_DIR            # table; REPLAYS_DIR holds dfs-replay-W<n>-<tag>
    python3 docs/dfs-replay-verdicts.py REPLAYS_DIR --pairs    # also the no-change pair table of step 5

For each replay it prints what `replay.py score` says its first review was, what the replay's own
task file says (the log's first `review · ok|issues` entry, which `tree.py log` wrote when the
review finished), the number of issues reviews in the task file, and the credits `score` prints.
The task file is the record; `score` reads the verdict out of the review session's command with
history.VERDICT, which needs `tree.py` and `log` on one line, and misses a review that sets a
shell variable for `tree.py` first (`S=…/tree.py` then `python3 $S log W22 review issues`).

The pair table is step 5 of docs/dfs-optimization.md: a pair is better if the changed replay has
fewer issues reviews, or the same number and at most (1 - margin) of the control's credits.
"""
import glob
import os
import re
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def task_verdicts(dest, task):
    """Verdicts of the replay's reviews, in order, from the task file's log."""
    text = Path(dest, ".dfs", "items", task + ".md").read_text()
    return re.findall(r"^- \S+ · review · (ok|issues)\s*$", text, re.M)


def score(dest, task):
    out = subprocess.run([sys.executable, str(SCRIPTS / "replay.py"), "score", dest, task],
                         capture_output=True, text=True).stdout
    first = re.search(r"^first review\s+\S+\s+(\S+)", out, re.M)
    cred = re.search(r"^credits \(kiro\)\s+-\s+(\S+)", out, re.M)
    unpriced = "+" in (cred.group(1) if cred else "")
    return (first.group(1) if first else "?", float(re.match(r"[\d.]+", cred.group(1)).group(0)) if cred else None, unpriced)


def replays(root):
    rows = []
    for d in sorted(glob.glob(os.path.join(root, "dfs-replay-W*-[bc][0-9][abc]"))):
        task, tag = os.path.basename(d).split("-")[2:4]
        v = task_verdicts(d, task)
        s, credits, unpriced = score(d, task)
        rows.append({"task": task, "tag": tag, "score": s, "log": v[0], "issues": v.count("issues"),
                     "credits": credits, "unpriced": unpriced})
    return rows


def verdict(ch, ct, m):
    if ch["issues"] != ct["issues"]:
        return "better" if ch["issues"] < ct["issues"] else "worse"
    r = ch["credits"] / ct["credits"]
    return "better" if r <= 1 - m + 1e-9 else "worse" if r >= 1 / (1 - m) - 1e-9 else "same"


def sequence(d):
    """Chance that the rule keeps a change whose pairs read better/same/worse with these odds:
    3 of 3 better, or two better and one same in the first three and then 4 of 5 better, none worse."""
    keep = 0
    for a in "bsw":
        for b in "bsw":
            for c in "bsw":
                s3 = a + b + c
                p = d[a] * d[b] * d[c]
                if s3 == "bbb":
                    keep += p
                elif s3.count("b") == 2 and "w" not in s3:
                    for x in "bsw":
                        for y in "bsw":
                            s5 = s3 + x + y
                            if s5.count("b") >= 4 and "w" not in s5:
                                keep += p * d[x] * d[y]
    return keep


def main():
    root = sys.argv[1]
    rows = replays(root)
    print(f"{'task':4} {'tag':4} {'score first':>11} {'task file first':>16} {'issues reviews':>15} {'credits':>8}")
    for r in rows:
        flag = "   <- score misses the issues review" if r["score"] != r["log"] else ""
        print(f"{r['task']:4} {r['tag']:4} {r['score']:>11} {r['log']:>16} {r['issues']:>15} "
              f"{r['credits']:>8}{'+' if r['unpriced'] else ''}{flag}")
    ctl = [r for r in rows if r["tag"].startswith("c1")]  # the first sitting, three per task
    print("\ncontrol c1a-c1c, first review passed (task file):",
          f"{sum(r['log'] == 'ok' for r in ctl)} of {len(ctl)}; by score: {sum(r['score'] == 'ok' for r in ctl)} of {len(ctl)}")
    for t in sorted({r['task'] for r in ctl}):
        v = [r for r in ctl if r['task'] == t]
        print(f"   {t}: task file {sum(r['log'] == 'ok' for r in v)} of {len(v)}, score {sum(r['score'] == 'ok' for r in v)} of {len(v)}")
    if "--pairs" in sys.argv:
        print("\npairs of two different control replays of one task (both orders); the changed replay's credits are")
        print("multiplied by f (f=1 is a change that does nothing); kept = the sequence of step 5")
        tasks = sorted({r["task"] for r in ctl})
        for m in (0.2, 0.3, 0.4, 0.5):
            out = []
            for f in (1.0, 0.7, 0.5):
                cnt = {"better": 0, "same": 0, "worse": 0}
                for t in tasks:
                    v = [r for r in rows if r["task"] == t and r["tag"].startswith("c")]
                    k = len(v) * (len(v) - 1)
                    for i, ch in enumerate(v):
                        for j, ct in enumerate(v):
                            if i != j:
                                cnt[verdict(dict(ch, credits=ch["credits"] * f), ct, m)] += 1 / k / len(tasks)
                d = {"b": cnt["better"], "s": cnt["same"], "w": cnt["worse"]}
                out.append(f"f={f}: {d['b']:.0%}/{d['s']:.0%}/{d['w']:.0%} better/same/worse, kept {100 * sequence(d):.1f}%")
            print(f"   margin {m:.0%}: " + "; ".join(out))

if __name__ == "__main__":
    main()
