#!/usr/bin/env python3
"""Run `UI.draw` of tui.py against a recording screen at 80x24 and 120x50 (W131.18).

    python3 docs/ux-tui-pane.py [SCRIPTS_DIR]     (run from the repository root)

`ux-tui-render.py` only prints `lines_tree`, so pane height and scrolling were unseen. Here the real
`UI.draw` paints into a fake curses window that records each `addstr(y, x, text)`. The header, the
item list, the footer and the side panel are stubbed out (they need a live curses); the pane's
own geometry, its rule (title and "n-m of N" tag), the scroll and the cursor row are the real code.
Prints, per case: the rule row, the rows the tree shows, and whether the cursor row (the `>`) is in them.
"""
import curses, os, shutil, subprocess, sys, tempfile

here = os.path.dirname(os.path.abspath(__file__))
scripts = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "..", "scripts"))
fixture = os.path.join(here, "tui-ux-review", "items")
work = tempfile.mkdtemp(prefix="tui-pane-")
try:
    os.makedirs(os.path.join(work, ".dfs"))
    shutil.copytree(fixture, os.path.join(work, ".dfs", "items"))
    subprocess.run(["git", "init", "-q", work], check=True)
    os.chdir(work)
    sys.path.insert(0, scripts)
    curses.color_pair = lambda n: 0
    import tui
    tui.look = lambda name: 0

    class Scr:
        def __init__(self, h, w): self.h, self.w, self.cells = h, w, {}
        def getmaxyx(self): return self.h, self.w
        def erase(self): self.cells = {}
        def addstr(self, y, x, s, *a): self.cells[(y, x)] = s
        def move(self, *a): pass
        def refresh(self): pass
        def rows(self):
            out = {}
            for (y, x), s in sorted(self.cells.items()):
                out[y] = out.get(y, "") + (" " + s if x else s)     # a row's cells in column order
            return out

    def case(task, width, height, open_node=None, key=None):
        ui = object.__new__(tui.UI)
        ui.scr = Scr(height, width)
        ui.data = {"items": [{"id": task, "goal": task, "why": "", "status": "open"}]}
        ui.sel, ui.tree_item, ui.tree_sel, ui.scroll = 0, task, 0, 0   # tree_item set, or lines_tree resets the cursor to row 0
        ui.tree_folded, ui.tree_open, ui.tree_flagged = set(), set(), False
        ui._tree_cache, ui.card_panel, ui.pane, ui.msg, ui.input = {}, False, "item", "", None
        ui.open_run, ui.follow = None, False
        ui.tree_focused = lambda: True
        ui.header = lambda: None
        ui.draw_list = lambda top, h: None
        ui.draw_footer = lambda y, w: None
        ui.side_w = lambda w: 0
        ui.list_rows = lambda: [0]
        ui.current = lambda: ui.data["items"][0]
        ui.cursor_y = lambda top, h: None
        t, _, _ = ui.tree_of(task)
        entries = [r for r in tui.tree_entries(t)]
        if open_node:
            ui.tree_open = {r["key"] for r in entries if r["kind"] == "node" and r["node"]["id"] == open_node}
            ui.tree_sel = next(i for i, r in enumerate(ui.tree_rows()) if r["key"] in ui.tree_open)
        if key == "G":
            ui.tree_sel = len(ui.tree_rows()) - 1
        ui.draw()
        rows = ui.scr.rows()
        return rows

    for width, height in ((80, 24), (120, 50)):
        for label, task, node, key in (("W9 closed, cursor on first row", "W9", None, None),
                                       ("W9.2 opened", "W9", "W9.2", None),
                                       ("W9 cursor sent to the last row (G)", "W9", None, "G")):
            try:
                rows = case(task, width, height, node, key)
            except Exception as e:                      # a stub that is not enough: say so, do not guess
                print("== %dx%d %s: could not draw: %s: %s" % (width, height, label, type(e).__name__, e))
                continue
            ys = sorted(rows)
            rule = [rows[3]] if 3 in rows else []
            body = [(y, rows[y]) for y in ys if y >= 4 and y < height - 2]
            cur = [y for y, s in body if s.lstrip().startswith((">", tui.BAR))]
            print("== %dx%d %s: rule %r; %d tree rows drawn (rows %s-%s); cursor row on screen: %s" % (
                width, height, label, rule[0].strip()[:70] if rule else None, len(body),
                body[0][0] if body else None, body[-1][0] if body else None, bool(cur)))
            print("     first row: %r | last row: %r | cursor row: %r" % (
                body[0][1].strip()[:50], body[-1][1].strip()[:50], [s.strip()[:40] for y, s in body if y in cur][:1]))
finally:
    os.chdir("/")
    shutil.rmtree(work, ignore_errors=True)
