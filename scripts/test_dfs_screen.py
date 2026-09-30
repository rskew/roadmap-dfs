#!/usr/bin/env python3
"""The screen's own conventions: where a cursor lands, which keys exist, and search.

⚠️ The one worth having is `ACursorIsAnIdentity`. The file watch reloads the roadmap
a second after another session writes it, and a cursor kept as a row number stayed on
row 3 while a different item slid under it — so the next `w`, `n` or `r` acted on
something nobody had selected.

Run:  python3 roadmap-dfs/scripts/test_dfs_screen.py
"""
import curses
import importlib.util
import inspect
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

TUI_SPEC = importlib.util.spec_from_file_location("dfs_tui", HERE / "dfs_tui.py")
TUI = importlib.util.module_from_spec(TUI_SPEC)
TUI_SPEC.loader.exec_module(TUI)


def item(iid, goal="", status="open"):
    return dict(id=iid, goal=goal or "goal of " + iid, status=status, why="",
                depth=0)


def a_screen(items, **kw):
    """A UI with only the state these tests read; everything that writes is stubbed."""
    ui = object.__new__(TUI.UI)
    ui.data = {"items": list(items)}
    ui.sel = ui.run_sel = ui.art_sel = ui.key_sel = ui.tree_sel = 0
    ui.scroll, ui.body_h, ui.follow, ui.msg = 0, 20, False, ""
    ui.pane, ui.focus = "item", "list"
    ui.keys_from, ui.last_search = ("item", "list"), ""
    ui.runs, ui.chains, ui.open_run = [], [], None
    ui.tree_item, ui.tree_folded, ui.tree_open = None, set(), set()
    ui._sel_row = ui._list_y = None
    ui.draw = lambda: None
    ui.__dict__.update(kw)
    return ui


class Reselecting(unittest.TestCase):
    def test_the_cursor_follows_its_key(self):
        self.assertEqual(TUI.reselect(["W9", "W1", "W2"], "W1", 0), 1)

    def test_a_key_that_is_gone_falls_back_to_the_row_clamped(self):
        self.assertEqual(TUI.reselect(["W1", "W2"], "W7", 1), 1)
        self.assertEqual(TUI.reselect(["W1"], "W7", 4), 0)
        self.assertEqual(TUI.reselect([], "W7", 4), 0)


class ACursorIsAnIdentity(unittest.TestCase):
    """The real `reload`, over lists that change under it."""

    def setUp(self):
        self.real = {n: getattr(TUI, n) for n in
                     ("load", "roadmap_dirty", "discover_runs", "watch_signature")}
        self.run_dirs = TUI.dfs_runs.run_dirs
        self.disk = {"items": [], "runs": []}
        TUI.load = lambda: {"items": list(self.disk["items"])}
        TUI.roadmap_dirty = lambda: (0, 0)
        TUI.discover_runs = lambda: list(self.disk["runs"])
        TUI.watch_signature = lambda: ()
        TUI.dfs_runs.run_dirs = lambda: []

    def tearDown(self):
        for n, f in self.real.items():
            setattr(TUI, n, f)
        TUI.dfs_runs.run_dirs = self.run_dirs

    def test_an_item_added_above_does_not_move_the_selection(self):
        ui = a_screen([item("W1"), item("W2"), item("W3")], sel=1)
        # Another session scopes W9 and the author's order puts it first.
        self.disk["items"] = [item("W9"), item("W1"), item("W2"), item("W3")]
        ui.reload()
        self.assertEqual(ui.current()["id"], "W2")

    def test_an_item_moved_in_order_md_is_followed(self):
        ui = a_screen([item("W1"), item("W2"), item("W3")], sel=0)
        self.disk["items"] = [item("W2"), item("W3"), item("W1")]
        ui.reload()
        self.assertEqual(ui.current()["id"], "W1")

    def test_an_item_that_is_gone_leaves_the_cursor_where_it_was(self):
        ui = a_screen([item("W1"), item("W2"), item("W3")], sel=2)
        self.disk["items"] = [item("W1"), item("W2")]
        ui.reload()
        self.assertEqual(ui.current()["id"], "W2")

    def test_a_new_live_chain_does_not_move_the_run_cursor(self):
        # A live chain sorts FIRST in the run list (`discover_runs`), so every
        # chain that starts pushes the rest down one row.
        old = [dict(path="/r/a/run-1.json"), dict(path="/r/b/run-1.json")]
        ui = a_screen([], pane="runs", runs=list(old), run_sel=1)
        self.disk["runs"] = [dict(path="/r/c/console.log")] + old
        ui.reload()
        self.assertEqual(ui.runs[ui.run_sel]["path"], "/r/b/run-1.json")


class Searching(unittest.TestCase):
    def test_it_starts_after_the_cursor_and_wraps(self):
        texts = ["W1 a", "W2 b", "W3 a"]
        self.assertEqual(TUI.find_next(texts, "a", 0), (2, False))
        self.assertEqual(TUI.find_next(texts, "a", 2), (0, True))

    def test_lower_case_matches_either_and_a_capital_means_it(self):
        texts = ["W13 thing", "w13 note"]
        self.assertEqual(TUI.find_next(texts, "w13", 1), (0, True))
        self.assertEqual(TUI.find_next(texts, "W13", 0), (0, True))

    def test_nothing_found_is_none(self):
        self.assertEqual(TUI.find_next(["a"], "zz", 0), (None, False))
        self.assertEqual(TUI.find_next([], "a", 0), (None, False))

    def test_slash_lands_on_the_item_and_an_empty_one_finds_the_next(self):
        ui = a_screen([item("W1", "parse it"), item("W2", "draw it"),
                       item("W3", "draw again")])
        typed = ["draw", ""]
        ui.prompt = lambda label, default="": typed.pop(0) or default
        ui.act(ord("/"))
        self.assertEqual(ui.current()["id"], "W2")
        ui.act(ord("/"))
        self.assertEqual(ui.current()["id"], "W3")


class TheKeysPane(unittest.TestCase):
    def test_every_key_act_answers_to_is_listed(self):
        # ⚠️ The drift guard: `act` is where a key exists, `KEYS` is where it is
        # seen, and a key in one and not the other is a key nobody finds.
        src = inspect.getsource(TUI.UI.act)
        handled = set(re.findall(r'ord\("(.)"\)', src))
        listed = set()
        for _, label, _, _ in TUI.KEYS:
            listed.update(" " if t == "space" else t for t in label.split())
        listed |= {chr(c) for c in TUI.KEY_ALIASES}
        missing = {k for k in handled if k not in listed}
        self.assertEqual(missing, set(), "keys `act` takes that `?` does not list")

    def test_an_alias_is_the_key_it_stands_for(self):
        ui = a_screen([item("W1")])
        moved = []
        ui.reorder = moved.append
        ui.reparent = lambda step: moved.append(("branch", step))
        for ch in "-=+<>":
            ui.act(ord(ch))
        self.assertEqual(moved, [-1, 1, 1, ("branch", -1), ("branch", 1)])

    def test_where_you_were_comes_first(self):
        ui = a_screen([item("W1")], keys_from=("runs", "list"))
        self.assertEqual(ui.key_rows()[0][0], "runs")
        ui.keys_from = ("item", "tree")
        self.assertEqual(ui.key_rows()[0][0], "tree")

    def test_question_mark_opens_and_esc_goes_back_where_it_was(self):
        ui = a_screen([item("W1")], pane="todo")
        ui.act(ord("?"))
        self.assertEqual(ui.pane, "keys")
        ui.act(27)
        self.assertEqual(ui.pane, "todo")

    def test_enter_presses_the_key_in_its_own_pane(self):
        ui = a_screen([item("W1")], pane="todo")
        ui.act(ord("?"))
        ui.key_sel = next(i for i, k in enumerate(ui.key_rows())
                          if k[0] == "panes" and k[1] == "3")
        ui.act(10)
        self.assertEqual(ui.pane, "reglog", "3 opens the task log")

    def test_a_list_key_pressed_from_another_pane_is_pressed_on_the_list(self):
        ui = a_screen([item("W1")], pane="runs")
        pinned = []
        ui.walk_pin = pinned.append
        ui.act(ord("?"))
        ui.key_sel = next(i for i, k in enumerate(ui.key_rows()) if k[1] == "n")
        ui.act(10)
        self.assertEqual((ui.pane, [p["id"] for p in pinned]), ("item", ["W1"]))

    def test_the_footer_keeps_question_mark_when_it_narrows(self):
        ui = a_screen([item("W1")])
        ui.next_action = lambda it: "work"
        ui.live = lambda mode="work": []
        ui.walk_on = False
        for w in (200, 60, 30, 10):
            self.assertIn("? keys", ui.footer(w), w)
            self.assertTrue(ui.footer(w).endswith("q quit"), w)


class TheCursorIsParked(unittest.TestCase):
    def test_on_a_panes_cursor_row_when_it_is_on_screen(self):
        ui = a_screen([item("W1")], pane="runs", _sel_row=5, scroll=2, _list_y=4)
        self.assertEqual(ui.cursor_y(body_top=10, body_h=8), 13)

    def test_on_the_selected_item_otherwise(self):
        ui = a_screen([item("W1")], _list_y=4)
        self.assertEqual(ui.cursor_y(body_top=10, body_h=8), 4)

    def test_nowhere_in_particular_on_a_pane_with_no_cursor(self):
        ui = a_screen([item("W1")], pane="reglog", _list_y=4)
        self.assertIsNone(ui.cursor_y(body_top=10, body_h=8))


class TheCapIsANumber(unittest.TestCase):
    def test_enter_starts_a_chain_only_on_a_whole_number_of_sessions(self):
        # "" is what an Esc'd prompt returns; "\x1b" is what it used to hand over.
        for typed, started in (("5", ["5"]), ("12", ["12"]), ("", []),
                               ("\x1b", []), ("abc", []), ("0", [])):
            ui = a_screen([item("W1")])
            ui.next_action = lambda it: "work"
            ui.prompt = lambda label, default="": typed
            got = []
            ui.start_chain = lambda it, cap: got.append(cap)
            ui.act(10)
            self.assertEqual(got, started, repr(typed))


class StartingAChainSaysTheCommand(unittest.TestCase):
    def test_the_message_is_the_runner_line(self):
        import tempfile
        tmp = tempfile.mkdtemp()
        saved = (TUI.dfs_runs.live_for, TUI.dfs_runs.ensure_run_root,
                 TUI.subprocess.Popen)

        class Proc:
            pid = 4242

        TUI.dfs_runs.live_for = lambda item: None
        TUI.dfs_runs.ensure_run_root = lambda: tmp
        TUI.subprocess.Popen = lambda *a, **k: Proc()
        try:
            ui = a_screen([item("W1")], codex=False)
            ui.reload = lambda note="": None
            ui.start_chain("W1", "5")
        finally:
            (TUI.dfs_runs.live_for, TUI.dfs_runs.ensure_run_root,
             TUI.subprocess.Popen) = saved
        self.assertTrue(ui.msg.startswith("$ %s W1 5" % TUI.RUNNER), ui.msg)
        self.assertIn("pid 4242", ui.msg)


if __name__ == "__main__":
    curses.color_pair = lambda n: 0       # no initscr here; attrs are opaque
    unittest.main()
