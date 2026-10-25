#!/usr/bin/env python3
"""Draw this project's background pictures as one tree in the app icon's vocabulary.

The icon (scripts/web/icon.svg) is a path from a square, through an amber node, to a red
stopped branch and a blue end. Here that grows into a tree rather than a network: a trunk
rises from a root square at the foot of the picture and splits, again and again, into two
or three branches at angles off any grid, each shorter and thinner than the one it grew
from. Every fork is an amber node; a branch that would cross another, or is cut short,
ends in a red bar across it (stopped); one that reaches its last depth ends in a blue disc
(arrived). The picture is square and the page draws it once ("cover", no repeat): a
portrait phone, which shows only its middle columns, sees the trunk and the crown above
it, and a landscape screen, which shows its middle rows, sees the crown across its width.

Run, from this directory (writes background-light.svg and -dark.svg; seed default SEED):
  python3 make_background.py /tmp/bg [seed]
  for m in light dark; do bash render.sh /tmp/bg/background-$m.svg ../background-image-$m; done
"""
import math
import random
import sys
from pathlib import Path

W, H = 1680, 1680
PALETTE = {
    "light": dict(ground="#efede7", ink="#16191c", amber="#c99f30", red="#b94c40", blue="#3f6aa1"),
    "dark": dict(ground="#171a1d", ink="#bfc2c6", amber="#d9b04a", red="#d0675b", blue="#6f95c9"),
}
SEED = 3                        # of seeds 0-14, one whose crown reaches the top and both sides of the square, evenly
DEPTH = 8                       # forks along the longest path from the trunk to a blue end
TRUNK = 400                     # the trunk's length, in pixels of the picture; each depth is shorter
SHRINK = 0.78                   # a branch's length against its parent's
WIDTH = 60                      # the trunk's stroke width; each depth is thinner
THIN = 0.7                      # a branch's width against its parent's
MARGIN = 40                     # how near a branch may come to the picture's edge


def _cross(p, q, r, s, gap):
    """Whether segments pq and rs pass within gap of each other (sharing an end does not count)."""
    def dist(a, b, c):          # from point a to segment bc
        bx, by = c[0] - b[0], c[1] - b[1]
        t = max(0.0, min(1.0, ((a[0] - b[0]) * bx + (a[1] - b[1]) * by) / ((bx * bx + by * by) or 1)))
        return math.hypot(a[0] - b[0] - t * bx, a[1] - b[1] - t * by)
    if p == r or p == s or q == r or q == s:
        return False
    return min(dist(q, r, s), dist(r, p, q), dist(s, p, q)) < gap


def grow(seed: int):
    """Grow the tree; returns its branches, each (start, end, width, how it ends), and the root.

    A branch ends "fork" where it splits, "stop" where it was cut short and "arrive" at its
    last depth. Branches are grown breadth first, so a short one near the trunk is never
    blocked by a twig that a deeper branch put down first."""
    rnd = random.Random(seed)
    root = (W / 2, H - 40)
    branches = []
    level = [(root, -math.pi / 2, TRUNK, WIDTH, 0)]
    while level:
        nxt = []
        for start, ang, length, width, depth in level:
            end = (start[0] + length * math.cos(ang), start[1] + length * math.sin(ang))
            gap = 16 + 1.5 * width                     # room for this branch's stroke and its end's mark
            ok = MARGIN < end[0] < W - MARGIN and MARGIN < end[1] < H - MARGIN
            if not ok or any(_cross(start, end, a, b, gap) for a, b, _, _ in branches):
                # in the way: shorten it to half, and if even that is blocked, drop it
                end = (start[0] + length / 2 * math.cos(ang), start[1] + length / 2 * math.sin(ang))
                if any(_cross(start, end, a, b, gap) for a, b, _, _ in branches) or not (0 < end[0] < W and 0 < end[1] < H):
                    continue
                branches.append((start, end, width, "stop"))
                continue
            if depth == DEPTH:
                branches.append((start, end, width, "arrive"))
                continue
            if depth >= 2 and rnd.random() < 0.12:      # a branch given up on early
                branches.append((start, end, width, "stop"))
                continue
            branches.append((start, end, width, "fork"))
            n = 3 if rnd.random() < 0.25 else 2
            spread = rnd.uniform(0.38, 0.62)
            angles = [ang - spread, ang + spread] if n == 2 else [ang - spread * 1.2, ang + rnd.uniform(-0.12, 0.12), ang + spread * 1.2]
            for a in angles:
                a += rnd.uniform(-0.12, 0.12)
                a += 0.12 * math.sin(-math.pi / 2 - a)      # a little lift, so the crown spreads up and out, not down
                nxt.append((end, a, length * SHRINK * rnd.uniform(0.85, 1.12), max(width * THIN, 8), depth + 1))
        level = nxt
    # a fork whose every branch was dropped is an end after all
    starts = {b[0] for b in branches}
    branches = [(a, b, w, "arrive" if how == "fork" and b not in starts else how) for a, b, w, how in branches]
    return branches, root


def picture(mode: str, seed: int) -> str:
    c = PALETTE[mode]
    branches, root = grow(seed)
    out = []
    for (x1, y1), (x2, y2), w, _ in branches:
        out.append('<path d="M%.1f %.1fL%.1f %.1f" stroke="%s" stroke-width="%.1f" stroke-linecap="round"/>' % (x1, y1, x2, y2, c["ink"], w))
    rx, ry = root
    out.append('<rect x="%g" y="%g" width="%d" height="%d" fill="%s"/>' % (rx - 50, ry - 50, 100, 100, c["ink"]))
    for (x1, y1), (x2, y2), w, how in branches:
        if how == "fork":
            out.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="%s"/>' % (x2, y2, max(w * 0.85, 14), c["amber"]))
        elif how == "arrive":
            out.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="%s"/>' % (x2, y2, min(max(w * 1.6, 18), 30), c["blue"]))
        else:                   # a red bar across the branch's end, as the icon's stopped branch
            ang = math.degrees(math.atan2(y2 - y1, x2 - x1))
            bw, bh = max(w * 0.9, 12), max(w * 3.2, 42)
            out.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s" transform="rotate(%.1f %.1f %.1f)"/>'
                       % (x2 - bw / 2, y2 - bh / 2, bw, bh, c["red"], ang, x2, y2))
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
