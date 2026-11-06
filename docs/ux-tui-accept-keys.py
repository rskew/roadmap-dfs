#!/usr/bin/env python3
"""Drive the terminal's accept prompt on a real task, a key at a time, and print what is on screen (W131.53).

    python3 docs/ux-tui-accept-keys.py [TASK]      (run from the repository root; default W75)

The real `UI.act`, `UI.prompt` and `UI.read_line` run; only curses is a fake screen that hands out the keys
and a stub `draw` that records the footer's text and the pane. `append_log` is stubbed so nothing is written.
Two sequences: the author who reads the evidence before accepting (`A`, `H`, Esc, `A`, `y`, Enter), and the
old behaviour when `H` is typed at the prompt as text (run with the `leave` argument ignored: `--old`).
"""
import curses, os, sys
here = os.path.dirname(os.path.abspath(__file__))
root = os.path.abspath(os.path.join(here, ".."))
sys.path.insert(0, os.path.join(root, "scripts"))
os.chdir(root)
curses.color_pair = lambda n: 0
curses.curs_set = lambda n: None
import tui, web
import tree as dfs_tree
tui.look = lambda name: 0
args = [a for a in sys.argv[1:] if not a.startswith("--")]
task = args[0] if args else "W75"
old = "--old" in sys.argv

logged = []
dfs_tree.append_log = lambda t, kind, a=(), body="": logged.append((t, kind))

ui = object.__new__(tui.UI)
t = dfs_tree.load(task)
arc = web.archive_text(task)
ui.tree_of = lambda _task: (t, arc, {})
item = {"id": task, "goal": (t.get("title") or "").split(" · ", 1)[-1], "assumptions": dfs_tree.assumed(t), "status": "done"}
ui.data = {"items": [item]}
ui.sel, ui.asm_sel, ui.msg, ui.answers, ui.pane, ui.focus = 0, 0, "", [], "item", "tree"
ui.scroll, ui.follow, ui.input, ui.left_with, ui.body_h = 0, False, None, None, 20
ui.current = lambda: item
ui.tree_acceptable = lambda it: True
ui.reload = lambda note="": None
ui.event = lambda kind, text: text
if old:
    real_read = tui.UI.read_line
    ui.read_line = lambda label, **kw: real_read(ui, label)

shown = []
ui.draw = lambda: shown.append(ui.input["label"] if ui.input else None)
queue = []


class Scr:
    def timeout(self, n):
        pass

    def get_wch(self):
        return queue.pop(0)


ui.scr = Scr()


def press(label, keys, ch):
    """One keypress at the tree (`ch`), with `keys` waiting for any prompt it opens."""
    queue[:] = list(keys)
    shown.clear()
    ui.act(ch)
    prompts = [s for s in shown if s]
    print("%-22s pane=%-11s focus=%-5s logged=%s%s%s" % (
        label, ui.pane, ui.focus, logged, ("  footer: " + prompts[-1]) if prompts else "",
        ("  msg: " + ui.msg) if ui.msg else ""))
    if queue:
        print("%-22s (prompt still wants %d key(s) the author has not pressed)" % ("", len(queue)))


print("%s, %d assumptions (%s):" % (task, len(item["assumptions"]), "H typed as text (old)" if old else "H leaves the prompt"))
ui.msg = ""
press("A  then H" + (", Enter" if old else ""), ["H", "\n"] if old else ["H"], ord("A"))
if not old:
    ui.msg = ""
    press("Esc", [], 27)
    ui.msg = ""
    press("A  then y, Enter", ["y", "\n"], ord("A"))
    print("keys from the first A to the accept: A, H, Esc, A, y, Enter = 6 (3 without reading the evidence: A, y, Enter)")
else:
    print("(old: the H was text on the line, the prompt kept waiting for Enter, and 'H' was read as 'not accepted')")
