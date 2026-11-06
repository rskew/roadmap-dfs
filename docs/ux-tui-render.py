#!/usr/bin/env python3
"""Draw the TUI's tree pane to plain text without a terminal (W131.12).

    python3 docs/ux-tui-render.py [SCRIPTS_DIR]     (run from the repository root)

There is no tmux or terminal emulator in the sandbox, so the screen cannot be looked at.
`tui.py` builds the tree pane as a list of `(text, attr[, guides])` lines in
`UI.lines_tree(width)`, before curses paints them. This calls that function on the
fixture in `docs/tui-ux-review/items/` (the one the old review used) at width 80 and 120
and prints the text, with each line's width, so a clipped or missing line shows up.
It does NOT draw the item list, the footer, the side panel or scrolling: those are
curses calls. SCRIPTS_DIR is the roadmap-dfs `scripts` directory to test (default: this
repository's). The installed copy and this repository's give identical output (checked
with `diff`); which of them the author runs was not checked.
"""
import curses, os, shutil, subprocess, sys, tempfile

here = os.path.dirname(os.path.abspath(__file__))
scripts = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "..", "scripts"))
fixture = os.path.join(here, "tui-ux-review", "items")

work = tempfile.mkdtemp(prefix="tui-render-")
try:
    os.makedirs(os.path.join(work, ".dfs"))
    shutil.copytree(fixture, os.path.join(work, ".dfs", "items"))
    subprocess.run(["git", "init", "-q", work], check=True)
    os.chdir(work)
    sys.path.insert(0, scripts)
    curses.color_pair = lambda n: 0          # no initscr here, so no colour pairs
    import tui
    tui.look = lambda name: 0

    def screen(task, width, open_node=None, flagged=False, card_panel=False):
        ui = object.__new__(tui.UI)          # a screen built without __init__, as the old tests did
        ui.data = {"items": [{"id": task, "goal": task, "why": "", "status": "open"}]}
        ui.sel, ui.tree_item, ui.tree_sel = 0, None, 0
        ui.tree_folded, ui.tree_open, ui.tree_flagged = set(), set(), flagged
        ui._tree_cache, ui.card_panel = {}, card_panel
        ui.tree_focused = lambda: True
        t, _, _ = ui.tree_of(task)
        if open_node:
            ui.tree_open = {r["key"] for r in tui.tree_entries(t) if r["kind"] == "node"
                            and r["node"]["id"] == open_node}
        return ui.lines_tree(width)

    def show(title, lines, width):
        print("=" * 8, title, "(width %d)" % width)
        for ln in lines:
            print("%3d|%s" % (len(ln[0]), ln[0]))
        over = [ln[0] for ln in lines if len(ln[0]) > width]
        print("-- %d lines, %d wider than %d columns" % (len(lines), len(over), width))

    for width in (80, 120):
        for task in ("W2", "W3", "W7", "W9", "W10"):
            show("%s as the tree draws it, no side panel" % task, screen(task, width), width)
        show("W9.2 opened", screen("W9", width, open_node="W9.2"), width)
        show("W9 with the node panel at the right (card_panel)", screen("W9", width, card_panel=True), width)
finally:
    os.chdir("/")
    shutil.rmtree(work, ignore_errors=True)
