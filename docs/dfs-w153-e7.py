#!/usr/bin/env python3
"""W153 E7 accounting: the "Minor findings" nodes, and whether their lines came back as defects.

  python3 docs/dfs-w153-e7.py [--at REV] [--cands]   (arguments of dfs-w153-mine.py)

For every node titled "Minor findings ..." in the three projects it lists the lines of its
Approach (one finding each), the words the author would read if those lines sat in the
review's `ok` instead, and the words of the node's Determination (read today, saved under E7).
It then looks for each line coming back: a later `review ... issues` entry of the same task
whose defect lines (a line that is not itself a "minor findings" line) share a distinctive
token with the minor line. Distinctive = a backticked name or a file name in the minor line
that fewer than 3% of all minor lines carry. A hit is only a candidate: `--cands` prints
each one for reading by hand, and the reading is in docs/dfs-w153-e7.tsv.

For roadmap-dfs it also reports the size of the node's commit (files and lines changed),
found by subject `<node id>:` on the checkout's history; shop-app and farm-app keep no git
history here. Nothing here edits a task.
"""
import collections, importlib.util, re, statistics, subprocess, sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("mine", Path(__file__).with_name("dfs-w153-mine.py"))
mine = importlib.util.module_from_spec(spec); spec.loader.exec_module(mine)

argv = sys.argv[1:]
cands = "--cands" in argv
miss = 0
if "--miss-sample" in argv:
    i = argv.index("--miss-sample"); miss = int(argv[i + 1]); del argv[i:i + 2]
argv = [x for x in argv if x not in ("--cands", "--only")]
a = mine.args_of(argv)
P = mine.load(a)

MINOR = re.compile(r"minor findings", re.I)
FILE = re.compile(r"^[\w./-]+\.(?:py|sh|md|js|css|html|json|nix|toml)$")
TOKEN = re.compile(r"`([^`]{3,60})`|\b([\w./-]+\.(?:py|sh|md|js|css|html|json|nix|toml))\b")


def lines_of(n):
    """The findings of a Minor findings node. The Approach is read as `fields["Approach"]`
    (bullets joined by ' - ', as the loader gives it): a first segment that is only a heading
    ("One line each:", "The minor findings, one per line:") is dropped and each later segment is
    one finding. An Approach with no ' - ' is one finding, even if it holds several sentences."""
    t = (n["fields"].get("Approach") or "").strip()
    if not t or t in mine.ARCHIVED:
        return []
    segs = [x.strip() for x in re.split(r"(?:^|\s)-\s+", t) if x.strip()]
    if len(segs) > 1 and mine.words(segs[0]) <= 14 and re.search(r"[:.]\s*$", segs[0]):
        segs = segs[1:]
    elif len(segs) > 1 and not t.startswith("-") and mine.words(segs[0]) > 14:
        segs = [t]          # a ' - ' inside prose, not a list
    return segs


def tokens(line):
    s = set()
    for m in TOKEN.finditer(line):
        tok = (m.group(1) or m.group(2)).strip().strip("./").lower()
        if len(tok) >= 4 and not tok.isdigit():
            s.add(tok)
    return s


rows = []   # one per node
for proj, T in P.items():
    for tid, t in T.items():
        reviews = [e for e in t["log"] if e["kind"] == "review"]
        for n in t["nodes"]:
            if not MINOR.search(n["title"]):
                continue
            ls = lines_of(n)
            at = next((i for i, e in enumerate(reviews) if re.search(re.escape(n["id"]) + r"\b", e["body"])), None)
            rows.append({"proj": proj, "task": tid, "id": n["id"], "status": n["status"], "lines": ls,
                         "det": mine.determination(n), "review_i": at, "reviews": reviews})

# how common each token is among all minor lines (a name in many lines is not distinctive)
df = collections.Counter()
nlines = 0
for r in rows:
    for l in r["lines"]:
        nlines += 1
        df.update(tokens(l))
common = {k for k, v in df.items() if v / nlines > 0.03}

hits = []
for r in rows:
    r["back"] = []
    if r["review_i"] is None:
        continue
    later = [e for e in r["reviews"][r["review_i"] + 1:] if e["head"].split("·")[-1].strip().startswith("issues")]
    for li, l in enumerate(r["lines"]):
        tk = tokens(l) - common
        if not tk:
            continue
        for e in later:
            for dl in e["body"].split("\n"):
                if MINOR.search(dl) or not dl.strip():
                    continue
                shared = {t for t in tk if t in dl.lower()}
                if shared:
                    strong = {t for t in shared if not FILE.match(t)}
                    r["back"].append((li, e["ts"], sorted(shared), dl.strip()))
                    hits.append((r, li, e, sorted(strong) or sorted(shared), dl.strip(), bool(strong)))
                    break

print(f"Minor findings nodes: {len(rows)}   lines: {nlines}   (a line = one '- ' bullet; a node with none is one line)")
for proj in P:
    rs = [r for r in rows if r["proj"] == proj]
    print(f"  {proj}: {len(rs)} nodes, {sum(len(r['lines']) for r in rs)} lines, tasks {len({r['task'] for r in rs})}")
print("  status:", dict(collections.Counter(r["status"] for r in rows)))
lc = [len(r["lines"]) for r in rows]
print(f"  lines a node: median {statistics.median(lc)} mean {statistics.mean(lc):.1f} max {max(lc)}")
lw = [mine.words(" ".join(r["lines"])) for r in rows]
dw = [mine.words(r["det"]) for r in rows]
print(f"  words in the lines (read in `ok` under E7): total {sum(lw)}, median a node {statistics.median(lw)}")
print(f"  words in the Determination (read today, saved under E7): total {sum(dw)}, median {statistics.median(dw)}")
unlinked = sum(r["review_i"] is None for r in rows)
print(f"  nodes whose review entry was not found in the log: {unlinked}")
tasks_with = {(r["proj"], r["task"]) for r in rows}
allnodes = sum(len(P[p][t]["nodes"]) for p, t in tasks_with)
print(f"  those tasks hold {allnodes} nodes; {len(rows)} of them minor ({100 * len(rows) / allnodes:.0f}%)")
print(f"  common tokens dropped (> 3% of lines): {len(common)}")
# a review whose only new node is the minor-findings node: under E7 it would have passed `ok`
LEFT = re.compile(r"\b(left as|not changed|stays|stay as|history only|unchanged|no change|declin|not fixed|leaves? (?:it|them)|wording only|not touched|kept as is)\b", re.I)
only = 0
withdef = 0
for r in rows:
    if r["review_i"] is None:
        continue
    body = r["reviews"][r["review_i"]]["body"]
    ids = [l for l in body.split("\n") if re.match(r"\s*W\d+\.\d+\b", l)]
    others = [l for l in ids if not MINOR.search(l) and not re.search(r"minor findings?", l, re.I)]
    r["only_minor"] = not others
    only += r["only_minor"]
    withdef += bool(others)
print(f"  reviews that named the minor node: {only + withdef}; by the regex (a line led by a node id) the minor node was their only new node in {only} and they named another in {withdef}."
      " Read by hand, 4 of the 5 also added a fix node in prose (W10.7, W36.6, W67.12, W37.6; `--only` prints them) and the fifth (W60.15) was an `ok` review, so none made a review round of its own.")
nt = len({(r["proj"], r["task"]) for r in rows})
print(f"  author's words, per task with a minor node ({nt} tasks): today the minor nodes' Determinations {sum(dw) / nt:.0f}; under E7 the same lines in `ok` {sum(lw) / nt:.0f} (the lines are 45 words each against a Determination's 69)")
print(f"  minor nodes whose Determination says some line was left as it was: {sum(bool(LEFT.search(r['det'])) for r in rows)}")
for strong in (True, False):
    hs = [h for h in hits if h[5] == strong]
    print(f"  candidate come-backs, {'a shared name (function, flag, test, key)' if strong else 'a shared file name only'}: {len(hs)} (line, later review) pairs on {len({(id(h[0]), h[1]) for h in hs})} lines of {len({h[0]['proj'] + h[0]['id'] for h in hs})} nodes")

# commit size of roadmap-dfs minor nodes against all node commits (checkout history)
def stat(sub_re):
    out = subprocess.run(["git", "-C", a.rm if (Path(a.rm) / ".git").exists() else str(mine.main_checkout()), "log", "--all",
                          "--extended-regexp", "--grep", sub_re, "--shortstat", "--format=@%H"],
                         capture_output=True, text=True).stdout
    ch = 0
    for m in re.finditer(r"(\d+) insertion|(\d+) deletion", out):
        ch += int(m.group(1) or m.group(2))
    return ch
sizes = []
repo = mine.main_checkout()
for r in rows:
    if r["proj"] != "roadmap-dfs":
        continue
    out = subprocess.run(["git", "-C", str(repo), "log", "--all", "--format=%H", "-E", "--grep", "^" + re.escape(r["id"]) + r"\b"],
                         capture_output=True, text=True).stdout.split()
    if not out:
        continue
    st = subprocess.run(["git", "-C", str(repo), "show", "--shortstat", "--format=", out[-1], "--", ".", ":(exclude).dfs"],
                        capture_output=True, text=True).stdout
    c = sum(int(x) for x in re.findall(r"(\d+) (?:insertion|deletion)", st))
    sizes.append(c)
    r["commit"] = c
if sizes:
    print(f"  roadmap-dfs minor-node commits found: {len(sizes)} of {sum(r['proj'] == 'roadmap-dfs' for r in rows)}; lines changed outside .dfs: median {statistics.median(sizes)}, mean {statistics.mean(sizes):.0f}, max {max(sizes)}, none-outside-.dfs {sum(s == 0 for s in sizes)}")

if miss:
    # lines with no shared name, in nodes that have a later issues-review: a fixed-seed sample, with the later defect lines, to read for come-backs the token test missed
    import random
    hitkeys = {(r["proj"], r["id"], li) for r, li, *_ in hits}
    pool = []
    for r in rows:
        if r["review_i"] is None:
            continue
        later = [e for e in r["reviews"][r["review_i"] + 1:] if e["head"].split("·")[-1].strip().startswith("issues")]
        if not later:
            continue
        for li, l in enumerate(r["lines"]):
            if (r["proj"], r["id"], li) not in hitkeys:
                pool.append((r, li, later))
    print(f"\nlines with no shared name and a later issues review: {len(pool)}; sample of {miss} (seed 153)")
    random.Random(153).shuffle(pool)
    for r, li, later in pool[:miss]:
        print(f"\n## {r['proj']} {r['id']} line {li}: {r['lines'][li][:170]}")
        for e in later[:3]:
            for dl in e["body"].split("\n"):
                if dl.strip() and not MINOR.search(dl):
                    print("   later:", dl.strip()[:110])

if cands:
    for r, li, e, shared, dl, strong in sorted(hits, key=lambda h: (not h[5], h[0]['proj'], h[0]['task'], h[0]['n'] if 'n' in h[0] else 0, h[1])):
        print(f"\n## {'S' if strong else 'W'} {r['proj']} {r['id']} line {li} -> review {e['ts']} shared {shared}")
        print("  MINOR:", r["lines"][li][:220])
        print("  LATER:", dl[:260])

# ── the reading ────────────────────────────────────────────────────────────────────────────
# Done by hand over the 63 lines with a candidate (python3 docs/dfs-w153-e7.py --cands) and a
# fixed-seed sample of 20 lines without one (--miss-sample 20), for the state at d595c44a.
# "same" = a later issues-review defect describes the same fault, or a regression of the
# line's own fix. "related" = the same function or class of fault, a different cause or path.
# Every other candidate was read and is "no" (a shared name such as `merge`, `watch`, `main`).
READ = {
    ("farm-app", "W127.13", 0): ("same", "W127.14: a water not heard reads acknowledged when an earlier held water had opened the channel; the line says the same of a gate_open sent to an open gate"),
    ("roadmap-dfs", "W130.23", 3): ("same", "W130.24: history.verdicts misreads a verdict run through S=$DIR/tree.py, a regression of W130.23 in the form the line named"),
    ("roadmap-dfs", "W88.7", 1): ("same", "W88.8: check.py still takes a session's first node, worked in place, for an adoption (the same condition)"),
    ("roadmap-dfs", "W90.6", 1): ("same", "two later reviews raise W90.4's unproven Relay._resubmit guard as an unmarked assumption, the claim the line said to check or mark as assumed"),
    ("shop-app", "W32.9", 0): ("same", "W32.24: W32.5 excludes offline queued edits without a Hypothesis"),
    ("shop-app", "W32.18", 0): ("same", "W32.24 names W32.10 and W32.18 without a Hypothesis, as the line did"),
    ("roadmap-dfs", "W74.6", 0): ("related", "the fixed 600 ms wait in walkpin was found again as a minor line in W74.11; W74.8 (walkpin flaky) had another cause"),
    ("roadmap-dfs", "W75.25", 1): ("related", "later defects W75.27, .39, .42: a task reads done with the branch unmerged, by other paths"),
    ("roadmap-dfs", "W75.28", 2): ("related", "same class as the line above"),
    ("roadmap-dfs", "W75.39", 2): ("related", "same class as the line above"),
    ("roadmap-dfs", "W87.9", 0): ("related", "W87.16: catch_up replaces the shared .dfs/items link with a stale directory; same area, another path"),
    ("farm-app", "W127.6", 2): ("related", "W127.12: a spurious 'not applied' resend by status_verdict, from another cause"),
    ("shop-app", "W39.7", 3): ("related", "W39.17: two other guards failed their first chromium-local attempt"),
    ("roadmap-dfs", "W68.3", 2): ("related", "W68.8: the read-back step added to reorderpending is flaky; whether it came from this line's fix is not shown"),
    # from the miss sample (lines with no shared name)
    ("farm-app", "W142.6", 2): ("related", "sample: W142.7 is a docked-panel sheet fault, same area"),
    ("farm-app", "W142.6", 3): ("related", "sample: as above"),
    ("roadmap-dfs", "W17.8", 0): ("related", "sample: W17.9 is another shutdown path that skips handle.stop()"),
    ("roadmap-dfs", "W114.6", 5): ("related", "sample: W114.7 closes a record whose chain died with a restarted container (the 'lost' path)"),
}


def kind_of(r, li):
    k = READ.get((r["proj"], r["id"], li))
    if k:
        return k[0]
    return "no" if any(h[0] is r and h[1] == li for h in hits) else "no match"


tally = collections.Counter()
for r in rows:
    for li in range(len(r["lines"])):
        tally[kind_of(r, li)] += 1
print("\nReading of the %d lines (state at the --at revision): %s" % (nlines, dict(tally)))
sample_related = sum(1 for k, v in READ.items() if v[1].startswith("sample"))
print(f"  of the lines read as 'related', {sample_related} came from the 20-line sample of unmatched lines (0 'same' there)")

tsv = Path(__file__).with_name("dfs-w153-e7.tsv")
with tsv.open("w") as f:
    f.write("project\ttask\tnode\tstatus\tlines\tline_words\tdet_words\tleft_as_is_in_det\tonly_idled_node_regex\tcommit_lines_outside_dfs\tsame\trelated\tno\tno_match\n")
    for r in rows:
        ks = collections.Counter(kind_of(r, li) for li in range(len(r["lines"])))
        f.write("\t".join(str(x) for x in (r["proj"], r["task"], r["id"], r["status"], len(r["lines"]), mine.words(" ".join(r["lines"])),
                                          mine.words(r["det"]), int(bool(LEFT.search(r["det"]))), int(bool(r.get("only_minor"))), r.get("commit", ""),
                                          ks["same"], ks["related"], ks["no"], ks["no match"])) + "\n")
print(f"  wrote {tsv.name}: one line a node")

if "--only" in sys.argv:
    for r in rows:
        if r.get("only_minor"):
            print(f"\n## only-minor {r['proj']} {r['id']}: {r['reviews'][r['review_i']]['head'][-60:]} | {r['reviews'][r['review_i']]['body'][:350]!r}")
