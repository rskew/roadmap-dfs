#!/usr/bin/env python3
"""Draw this project's background pictures from the app icon (scripts/web/icon.svg).

The icon is a stepped path with a node, a stopped branch and an end. Here that
drawing repeats across the page, each cell mirrored or flipped against its
neighbours so the rows do not read as a grid of one stamp.

Run:  python3 make_background.py <out-dir>      writes background-light.svg and -dark.svg
Render to png with headless chromium (see render.mjs) and save as
.dfs/background-image-light and .dfs/background-image-dark.
"""
import sys
from pathlib import Path

W, H = 1920, 1200
CELL = 240          # one motif's square, in pixels of the picture
PALETTE = {
    "light": dict(ground="#efede7", ink="#16191c", amber="#c99f30", red="#b94c40", blue="#3f6aa1"),
    "dark": dict(ground="#171a1d", ink="#bfc2c6", amber="#d9b04a", red="#d0675b", blue="#6f95c9"),
}


def motif(c: dict) -> str:
    """The icon's drawing in its own 512 space."""
    return (
        '<path d="M150 140H256V236H362V336M256 236V318" fill="none" stroke="%(ink)s" '
        'stroke-width="26" stroke-linejoin="miter"/>'
        '<rect x="128" y="118" width="44" height="44" fill="%(ink)s"/>'
        '<circle cx="256" cy="236" r="30" fill="%(amber)s"/>'
        '<rect x="208" y="318" width="96" height="30" fill="%(red)s"/>'
        '<circle cx="362" cy="352" r="40" fill="%(blue)s"/>' % c
    )


def picture(mode: str) -> str:
    c = PALETTE[mode]
    k = CELL / 512
    cells = []
    # flips[(row + col) % 4]: the drawing, mirrored across x, across y, or both
    flips = [(1, 1), (-1, 1), (-1, -1), (1, -1)]
    for r in range(H // CELL + 2):
        for q in range(W // CELL + 2):
            sx, sy = flips[(q + 2 * r) % 4]
            # the (sx, sy) flip is about the cell's centre
            x, y = q * CELL - (CELL // 2 if r % 2 else 0), r * CELL
            cells.append(
                '<g transform="translate(%g %g) scale(%g %g) translate(-256 -256)">%s</g>'
                % (x + CELL / 2, y + CELL / 2, sx * k, sy * k, motif(c))
            )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">'
        '<rect width="%d" height="%d" fill="%s"/>%s</svg>\n' % (W, H, W, H, W, H, c["ground"], "".join(cells))
    )


if __name__ == "__main__":
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for m in PALETTE:
        (out / ("background-%s.svg" % m)).write_text(picture(m))
