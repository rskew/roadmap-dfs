#!/usr/bin/env python3
"""The terminal's accept prompt and `H` view next to the web Accept dialog, on one real task (W131.47).

    python3 docs/ux-tui-accept.py [TASK] [WIDTH]     (run from the repository root; default W75, 80)

Prints, for the task as `.dfs/items/<task>.md` holds it: the one-line text `tree_accept` asks (captured by
stubbing `prompt`), the lines `H` draws for the task's assumptions at WIDTH columns, and what the web
page's Accept dialog lists for the same assumptions (`web.task_json(task)["assumed"]`). Run it before and
after the `H` change to see the gap: no evidence line under any assumption before, up to four after.
"""
import curses, os, sys
here = os.path.dirname(os.path.abspath(__file__))
root = os.path.abspath(os.path.join(here, ".."))
sys.path.insert(0, os.path.join(root, "scripts"))
os.chdir(root)
curses.color_pair = lambda n: 0
import tui, web
import tree as dfs_tree
tui.look = lambda name: 0
task = sys.argv[1] if len(sys.argv) > 1 else "W75"
width = int(sys.argv[2]) if len(sys.argv) > 2 else 80

ui = object.__new__(tui.UI)
t = dfs_tree.load(task)
arc = web.archive_text(task)
ui.tree_of = lambda _task: (t, arc, {})
ui.data = {"items": [{"id": task, "goal": (t.get("title") or "").split(" · ", 1)[-1], "assumptions": dfs_tree.assumed(t), "status": "done"}]}
items = ui.data["items"]
ui.sel, ui.asm_sel, ui.msg, ui.answers = 0, 0, "", []
ui.current = lambda: items[0]
ui.tree_acceptable = lambda it: True
asked = []
ui.prompt = lambda label, default="": asked.append(label) or "n"
ui.tree_accept(task)
print("TERMINAL accept prompt (%d characters; the footer clips a longer one at the screen edge):\n  %s\n" % (len(asked[0]), asked[0]))

def wrapper(w):
    import textwrap
    out = []
    def para(text, attr, indent=""):
        out.extend((indent + l, attr) for l in textwrap.wrap(text, w - len(indent)) or [indent])
    return out, para
ui.wrapper = wrapper
print("TERMINAL `H` view at %d columns:" % width)
lines = [l for l, *_ in ui.lines_assumptions(width)]
evidence = sum(1 for l in lines if l.lstrip().startswith("·"))
print("\n".join("  " + l for l in lines))
print("\n(%d lines, %d of them evidence)\n" % (len(lines), evidence))

print("WEB Accept dialog, per assumption (evidence lines shown / more on the node):")
for a in web.task_json(task)["assumed"]:
    print("  %s: %d / %d" % (a["id"], len(a["evidence"]["lines"]), a["evidence"]["more"]))
