#!/usr/bin/env python3
"""Draw this project's background pictures as one branching tree in the app icon's vocabulary.

The icon (scripts/web/icon.svg) is a stepped path from a square, through an amber node,
to a red stopped branch and a blue end. Here that grows into a single tree across the
whole picture: a square root at the centre, orthogonal stepped runs, amber nodes where a
run forks, and every branch ending stopped (red bar) or arrived (blue disc). The picture
is square and the page draws it once ("cover", no repeat), so nothing tiles, the tree is
continuous, and a portrait phone, which shows only the middle of it, still sees the root.

Run, from this directory (writes background-light.svg and -dark.svg; seed default SEED):
  python3 make_background.py /tmp/bg [seed]
  for m in light dark; do bash render.sh /tmp/bg/background-$m.svg ../background-image-$m; done
"""
import random
import sys
from collections import deque
from pathlib import Path

W, H = 1680, 1680
STEP = 120                      # one grid step, in pixels of the picture
COLS, ROWS = W // STEP, H // STEP
PALETTE = {
    "light": dict(ground="#efede7", ink="#16191c", amber="#c99f30", red="#b94c40", blue="#3f6aa1"),
    "dark": dict(ground="#171a1d", ink="#bfc2c6", amber="#d9b04a", red="#d0675b", blue="#6f95c9"),
}
SEED = 2460                  # a seed whose tree reaches every edge of the square, with many branches in the middle columns a phone shows
DIRS = [(1, 0), (0, 1), (-1, 0), (0, -1)]


def grow(seed: int):
    """Grow the tree on the grid; returns its edges, each a pair of neighbouring cells, and the root."""
    rnd = random.Random(seed)
    root = (COLS // 2, ROWS // 2)
    used = {root}               # every cell a path passes through
    edges = []
    queue = deque((root, d) for d in DIRS)

    def enter(c, origin):       # a cell the path may enter: on the page, free, and touching no other path
        return (0 <= c[0] < COLS and 0 <= c[1] < ROWS and c not in used and
                all((c[0] + dx, c[1] + dy) == origin or (c[0] + dx, c[1] + dy) not in used for dx, dy in DIRS))

    while queue:
        pos, d = queue.popleft()
        # a run of one to three cells in direction d, stopping short of anything in the way
        cur = pos
        for _ in range(rnd.choice([1, 2, 2, 3])):
            nxt = (cur[0] + d[0], cur[1] + d[1])
            if not enter(nxt, cur):
                break
            used.add(nxt)
            edges.append((cur, nxt))
            cur = nxt
        if cur == pos:
            continue
        # a node turns either way, and may also go straight on
        turns = [(d[1], d[0]), (-d[1], -d[0])]
        rnd.shuffle(turns)
        kids = turns + ([d] if rnd.random() < 0.5 else [])
        r = rnd.random()
        if r < 0.45:
            queue.extend((cur, t) for t in kids)
        elif r < 0.90:
            queue.append((cur, kids[0]))
    return edges, root


def picture(mode: str, seed: int) -> str:
    c = PALETTE[mode]
    edges, root = grow(seed)
    rnd = random.Random(seed + 1)
    px = lambda cell: ((cell[0] + 0.5) * STEP, (cell[1] + 0.5) * STEP)
    nbr = {}
    for a, b in edges:
        nbr.setdefault(a, []).append(b)
        nbr.setdefault(b, []).append(a)
    d = "".join("M%g %gL%g %g" % (*px(a), *px(b)) for a, b in edges)
    out = ['<path d="%s" fill="none" stroke="%s" stroke-width="26"/>' % (d, c["ink"])]
    for cell in nbr:            # a square at every joint fills the corner the strokes leave open
        x, y = px(cell)
        out.append('<rect x="%g" y="%g" width="26" height="26" fill="%s"/>' % (x - 13, y - 13, c["ink"]))
    rx, ry = px(root)
    out.append('<rect x="%g" y="%g" width="44" height="44" fill="%s"/>' % (rx - 22, ry - 22, c["ink"]))
    for cell, ns in nbr.items():
        x, y = px(cell)
        if len(ns) >= 3 and cell != root:        # a fork: an amber node
            out.append('<circle cx="%g" cy="%g" r="30" fill="%s"/>' % (x, y, c["amber"]))
        elif len(ns) == 1 and cell != root:     # a branch's last cell: stopped (a red bar) or arrived (a blue disc)
            if rnd.random() < 0.5:
                out.append('<circle cx="%g" cy="%g" r="40" fill="%s"/>' % (x, y, c["blue"]))
            else:
                nx, ny = px(ns[0])
                dx, dy = (x - nx) / STEP, (y - ny) / STEP       # away from the rest of the tree
                bx, by = x + dx * 15, y + dy * 15
                w, h = (30, 96) if dx else (96, 30)
                out.append('<rect x="%g" y="%g" width="%d" height="%d" fill="%s"/>' % (bx - w / 2, by - h / 2, w, h, c["red"]))
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">'
        '<rect width="%d" height="%d" fill="%s"/>%s</svg>\n' % (W, H, W, H, W, H, c["ground"], "".join(out))
    )


if __name__ == "__main__":
    out = Path(sys.argv[1])
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else SEED
    out.mkdir(parents=True, exist_ok=True)
    for m in PALETTE:
        (out / ("background-%s.svg" % m)).write_text(picture(m, seed))
