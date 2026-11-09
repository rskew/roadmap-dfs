#!/usr/bin/env python3
"""W153 E2 case: how many Determinations in each W17 replay tree state a before-and-after observation.

  python3 docs/dfs-w153-e2-count.py [REPLAYS_DIR]

REPLAYS_DIR defaults to the first .dfs/runs/replays found above this file; a task worktree has none, so
pass the main checkout's (/workspace/roadmap-dfs/.dfs/runs/replays here). Reads `Determination:` lines of
dfs-replay-W153-W17-{C1,C2,C4,P1}/.dfs/items/W17.md. C3 is not scored (its task file is the bare
Goal, no node was written) and the W17 controls the quality judge scored (5.0, 7.5, 8.0 clauses) are
C1, C2 and C4; P1 is the pooled arm (E1, E2, E6, E7 together).

Three counts per arm, each stated by its method:
  narrow  case-insensitive match of \\b(red|before|would have)\\b
  wide    narrow, or one of: fails without, without the change, went red, was red, removed
  hand    the Determination names what the claim's thing did before the change and after it
          (a test red then green, a helper listed before and gone after). Marked by reading
          the text of each line in HAND below: the line numbers in W17.md that count.
"""
import re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARMS = ["C1", "C2", "C4", "P1"]
NARROW = re.compile(r"\b(red|before|would have)\b", re.I)
WIDE = re.compile(r"\b(red|before|would have|fails without|without the change|went red|was red|removed)\b", re.I)
# Determination line numbers a reader judged to state a before-and-after observation
HAND = {
    "C1": {47, 63, 69},
    "C2": {20, 29, 38, 66, 72},
    "C4": {43, 58, 143},
    "P1": {26, 32, 38, 44, 60, 72, 78},
}

def find_replays():
    if len(sys.argv) > 1:
        return Path(sys.argv[1])
    for base in [HERE.parent, *HERE.parent.parents]:
        p = base / ".dfs" / "runs" / "replays"
        if p.is_dir():
            return p
    sys.exit("no .dfs/runs/replays found; pass the directory")

def main():
    root = find_replays()
    print(f"{'arm':4} {'dets':>4} {'narrow':>6} {'wide':>4} {'hand':>4}")
    tot = {"C": [0, 0, 0, 0], "P": [0, 0, 0, 0]}
    for arm in ARMS:
        f = root / f"dfs-replay-W153-W17-{arm}" / ".dfs" / "items" / "W17.md"
        dets = [(n, l) for n, l in enumerate(f.read_text().splitlines(), 1) if l.startswith("Determination:")]
        a = sum(1 for _, l in dets if NARROW.search(l))
        b = sum(1 for _, l in dets if WIDE.search(l))
        h = sum(1 for n, _ in dets if n in HAND[arm])
        missing = HAND[arm] - {n for n, _ in dets}
        assert not missing, (arm, missing)
        print(f"{arm:4} {len(dets):>4} {a:>6} {b:>4} {h:>4}")
        t = tot[arm[0]]
        for i, v in enumerate((len(dets), a, b, h)):
            t[i] += v
    print(f"controls (C1 C2 C4) {tot['C'][0]} Determinations: narrow {tot['C'][1]}, wide {tot['C'][2]}, hand {tot['C'][3]}")
    print(f"pooled (P1) {tot['P'][0]} Determinations: narrow {tot['P'][1]}, wide {tot['P'][2]}, hand {tot['P'][3]}")

if __name__ == "__main__":
    main()
