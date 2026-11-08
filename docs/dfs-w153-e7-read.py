#!/usr/bin/env python3
"""W153 E7, three readings of which verdict body the author reads.

  python3 docs/dfs-w153-e7-read.py --at d595c44a      (arguments of dfs-w153-mine.py)

E7 puts a review's minor findings in the review's own body (`ok` or `issues`), not in a
"Minor findings" node. The author reads a node's Determination today; it does not know which log
entries the author opens. Three readings of what the author reads of those lines:

  ALL   every minor line (the reading of docs/dfs-w153-e7.py): + line words - Determination words.
  OK    `ok` bodies only. A line of an `issues` review is read only if a LATER `ok` review of the
        same task repeats it (shares a distinctive name or file with one of its lines); the rest is
        read by nobody. Cost: the repeated lines' words - Determination words.
  NONE  no verdict body is read. Cost: - Determination words.

It also counts how many of the 484 lines came from an `issues` review (a minor node is added by a
review that logged `issues`; one the review logged `ok` is counted apart), and how many lines the
OK reading would lose to the author (not repeated by a later `ok` body).
The repeat test is the distinctive-name test of dfs-w153-e7.py, so it can over-count (a name
mentioned in passing) and under-count (a line repeated in other words).
"""
import collections, importlib.util, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("mine", HERE / "dfs-w153-mine.py")
mine = importlib.util.module_from_spec(spec); spec.loader.exec_module(mine)

MINOR = re.compile(r"minor findings", re.I)
FILE = re.compile(r"^[\w./-]+\.(?:py|sh|md|js|css|html|json|nix|toml)$")
TOKEN = re.compile(r"`([^`]{3,60})`|\b([\w./-]+\.(?:py|sh|md|js|css|html|json|nix|toml))\b")

a = mine.args_of([x for x in sys.argv[1:] if x != "--sample"])
P = mine.load(a)


def lines_of(n):
    t = (n["fields"].get("Approach") or "").strip()
    if not t or t in mine.ARCHIVED:
        return []
    segs = [x.strip() for x in re.split(r"(?:^|\s)-\s+", t) if x.strip()]
    if len(segs) > 1 and mine.words(segs[0]) <= 14 and re.search(r"[:.]\s*$", segs[0]):
        segs = segs[1:]
    elif len(segs) > 1 and not t.startswith("-") and mine.words(segs[0]) > 14:
        segs = [t]
    return segs


def tokens(line):
    s = set()
    for m in TOKEN.finditer(line):
        tok = (m.group(1) or m.group(2)).strip().strip("./").lower()
        if len(tok) >= 4 and not tok.isdigit():
            s.add(tok)
    return s


def verdict(e):
    return e["head"].split("·")[-1].strip().split()[0]


rows = []
for proj, T in P.items():
    for tid, t in T.items():
        reviews = [e for e in t["log"] if e["kind"] == "review"]
        for n in t["nodes"]:
            if not MINOR.search(n["title"]):
                continue
            at = next((i for i, e in enumerate(reviews) if re.search(re.escape(n["id"]) + r"\b", e["body"])), None)
            rows.append({"proj": proj, "task": tid, "id": n["id"], "lines": lines_of(n),
                         "det": mine.determination(n), "at": at, "reviews": reviews})

df = collections.Counter(); nl = 0
for r in rows:
    for l in r["lines"]:
        nl += 1; df.update(tokens(l))
common = {k for k, v in df.items() if v / nl > 0.03}

by_verdict = collections.Counter()
lines_total = line_words = det_words = rep_words = 0
rep_lines = untok = 0
later_ok_missing = 0
tasks = set()
for r in rows:
    tasks.add((r["proj"], r["task"]))
    det_words += mine.words(r["det"])
    v = verdict(r["reviews"][r["at"]]) if r["at"] is not None else "unknown"
    by_verdict[v] += len(r["lines"])
    later_ok = [e for e in r["reviews"][r["at"] + 1:] if verdict(e) == "ok"] if r["at"] is not None else []
    if not later_ok:
        later_ok_missing += 1
    for l in r["lines"]:
        w = mine.words(l)
        lines_total += 1; line_words += w
        tk = tokens(l) - common
        if not tk:
            untok += 1
            continue
        if any(any(t in e["body"].lower() for t in tk) for e in later_ok):
            rep_lines += 1; rep_words += w

if "--sample" in sys.argv:    # print 12 matched (line, `ok` sentence) pairs, seed 153, to read by hand
    import random
    random.seed(153)
    hits = []
    for r in rows:
        if r["at"] is None:
            continue
        for l in r["lines"]:
            tk = tokens(l) - common
            for e in [e for e in r["reviews"][r["at"] + 1:] if verdict(e) == "ok"]:
                m = [t for t in tk if t in e["body"].lower()]
                if m:
                    hits.append((r["proj"], r["id"], l, m[0], [x for x in e["body"].split("\n") if m[0] in x.lower()][0]))
                    break
    for h in random.sample(hits, 12):
        print(f"## {h[0]} {h[1]} LINE: {h[2][:150]}\n   NAME {h[3]} | OK BODY: {h[4].strip()[:160]}")
    sys.exit()

base = 120271   # words read in Determinations and Hypotheses at d595c44a (docs/dfs-w153-author-unit.txt)
nt = len(tasks)
print(f"minor nodes {len(rows)}, lines {lines_total}, tasks {nt}")
print("lines by the verdict of the review that named the node:", dict(by_verdict))
print(f"minor nodes with no later `ok` review in the task: {later_ok_missing} of {len(rows)}")
print(f"lines with no distinctive name (cannot be matched, counted as not repeated): {untok}")
print(f"lines a later `ok` body repeats (shared name): {rep_lines} of {lines_total}; their words {rep_words} of {line_words}")
print(f"Determination words saved (the node no longer exists): {det_words}")
print()
print("reading   change in words read     share of 120,271   lines the author never reads")
for name, delta, lost in (("ALL", line_words - det_words, 0),
                          ("OK", rep_words - det_words, lines_total - rep_lines),
                          ("NONE", -det_words, lines_total)):
    print(f"{name:8}  {delta:+8d}            {100 * delta / base:+6.1f}%            {lost}")
print()
print("verdict by margin (a gain/cost is outside when |share| >= margin):")
for name, delta in (("ALL", line_words - det_words), ("OK", rep_words - det_words), ("NONE", -det_words)):
    s = abs(100 * delta / base)
    print(f"  {name:5}", "  ".join(f"{m}%: {'outside' if s >= m else 'inside'}" for m in (10, 15, 20)))
