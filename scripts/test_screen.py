#!/usr/bin/env python3
"""The screen's own conventions: where a cursor lands, which keys exist, and search.

⚠️ The one worth having is `ACursorIsAnIdentity`. The file watch reloads the roadmap
a second after another session writes it, and a cursor kept as a row number stayed on
row 3 while a different item slid under it — so the next `w`, `n` or `r` acted on
something nobody had selected.

Run:  python3 roadmap-dfs/scripts/test_screen.py
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

TUI_SPEC = importlib.util.spec_from_file_location("dfs_tui", HERE / "tui.py")
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

    def test_slash_moves_as_you_type_and_an_empty_one_finds_the_next(self):
        ui = a_screen([item("W1", "parse it"), item("W2", "draw it"),
                       item("W3", "draw again")])
        ui.scr = Keys("dr", "\n", "\n")
        ui.act(ord("/"))
        self.assertEqual(ui.current()["id"], "W2", "landed while typing, kept on ⏎")
        self.assertEqual(ui.last_search, "dr")
        ui.act(ord("/"))
        self.assertEqual(ui.current()["id"], "W3", "an empty / is the next match")

    def test_esc_puts_the_cursor_back(self):
        ui = a_screen([item("W1", "parse it"), item("W2", "draw it")])
        ui.scr = Keys("draw", "\x1b")
        ui.act(ord("/"))
        self.assertEqual(ui.current()["id"], "W1")
        self.assertEqual(ui.last_search, "")


class Keys:
    """A screen that types: `get_wch` hands out the keys given, one at a time, each
    string split into its characters and anything else passed as a key code."""

    def __init__(self, *keys):
        self.keys = []
        for k in keys:
            self.keys += list(k) if isinstance(k, str) and len(k) > 1 else [k]

    def timeout(self, n):
        pass

    def get_wch(self):
        if not self.keys:
            raise AssertionError("read past the keys given")
        return self.keys.pop(0)

    def getmaxyx(self):
        return (24, 100)


class TheLineEditor(unittest.TestCase):
    def read(self, *keys, **kw):
        ui = a_screen([])
        ui.scr = Keys(*keys)
        return ui.read_line("x", **kw)

    def test_typing_and_enter(self):
        self.assertEqual(self.read("abc", "\n"), "abc")

    def test_esc_is_none_at_once(self):
        # ⚠️ The regression it exists for: getstr took Esc as a character.
        self.assertIsNone(self.read("ab", "\x1b"))

    def test_editing(self):
        self.assertEqual(self.read("abc", "\x7f", "d", "\n"), "abd")
        self.assertEqual(self.read("abc", curses.KEY_LEFT, curses.KEY_LEFT, "X", "\n"),
                         "aXbc")
        self.assertEqual(self.read("one two", "\x17", "\n"), "one")
        self.assertEqual(self.read("gone", "\x15", "ok", "\n"), "ok")

    def test_tab_takes_the_picked_row(self):
        got = self.read("rv", "\t", "\n",
                        menu=lambda t: ([("review  r", 0), ("revert  z", 0)], "c"),
                        complete=lambda t, row: row[0].split()[0] + " ")
        self.assertEqual(got, "review")

    def test_the_prompt_maps_esc_to_empty_past_its_default(self):
        ui = a_screen([])
        ui.scr = Keys("\x1b")
        self.assertEqual(ui.prompt("cap [5]:", "5"), "")
        ui.scr = Keys("\n")
        self.assertEqual(ui.prompt("cap [5]:", "5"), "5")


class Commanding(unittest.TestCase):
    def test_fuzzy_prefers_a_prefix_and_finds_a_scatter(self):
        self.assertLess(TUI.fuzzy("rev", "review"), TUI.fuzzy("rev", "chat-review"))
        self.assertIsNotNone(TUI.fuzzy("rv", "review"))
        self.assertIsNone(TUI.fuzzy("zz", "review"))

    def test_every_name_is_one_command(self):
        names = [k[3] for k in TUI.KEYS]
        self.assertEqual(len(names), len(set(names)), "a name means one thing")

    def test_an_item_is_selected_and_the_rest_answers_the_prompt(self):
        ui = a_screen([item("W1"), item("W2"), item("W3")])
        ui.next_action = lambda it: "work"
        started = []
        ui.start_chain = lambda it, cap: started.append((it, cap))
        ui.command_run("work w2 7")
        self.assertEqual(started, [("W2", "7")])
        self.assertEqual(ui.answers, [], "nothing left over for a later prompt")

    def test_an_unambiguous_prefix_runs_and_a_typo_says_what_it_meant(self):
        ui = a_screen([item("W1")])
        ui.command_run("activ")
        self.assertEqual(ui.pane, "activity")
        ui.command_run("reviwe")
        self.assertIn("did you mean", ui.msg)

    def test_go(self):
        ui = a_screen([item("W1"), item("W2")], pane="runs")
        ui.command_run("go W2")
        self.assertEqual((ui.pane, ui.current()["id"]), ("item", "W2"))

    def test_a_confirmation_is_never_answered_for_you(self):
        # `:stop W1 y`-less: K asks, and with only ⏎ typed it must not stop.
        ui = a_screen([item("W1")])
        saved = TUI.dfs_runs.live_for
        TUI.dfs_runs.live_for = lambda it: dict(pid=4242)
        killed = []
        real_kill = TUI.os.killpg
        TUI.os.killpg = lambda pid, sig: killed.append(pid)
        try:
            ui.scr = Keys("\n")
            ui.command_run("stop W1")
        finally:
            TUI.dfs_runs.live_for, TUI.os.killpg = saved, real_kill
        self.assertEqual((killed, ui.msg), ([], "left it running"))


class NeedsYou(unittest.TestCase):
    def items(self):
        return [item("W1"), dict(item("W2", status="blocked")), item("W3"),
                dict(item("W4"), standing=["an assumption"])]

    def test_m_shows_only_what_waits_on_the_author_and_j_walks_it(self):
        ui = a_screen(self.items())
        ui.act(ord("m"))
        self.assertEqual([ui.items[n]["id"] for n in ui.visible()], ["W2", "W4"])
        self.assertEqual(ui.current()["id"], "W2")
        ui.act(ord("j"))
        self.assertEqual(ui.current()["id"], "W4")
        ui.act(ord("m"))
        self.assertEqual(len(ui.visible()), 4)

    def test_nothing_to_show_leaves_it_off(self):
        ui = a_screen([item("W1")])
        ui.act(ord("m"))
        self.assertFalse(ui.only_mine)

    def test_moves_are_refused_while_filtered(self):
        ui = a_screen(self.items(), only_mine=True, sel=1)
        self.assertFalse(ui.retree([("W2", 0)], ui.current(), ""))
        self.assertIn("filtered", ui.msg)


class Undoing(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "order.md"
        self.real = (TUI.dfs_paths.order, TUI.dfs_order.write_order)
        TUI.dfs_paths.order = lambda: self.path
        TUI.dfs_order.write_order = lambda after: self.path.write_text(
            "\n".join("- " + n for n, _ in after) + "\n")

    def tearDown(self):
        TUI.dfs_paths.order, TUI.dfs_order.write_order = self.real

    def screen(self):
        ui = a_screen([item("W1"), item("W2")])
        ui.reload = lambda note="": None
        return ui

    def test_z_puts_back_what_was_there(self):
        self.path.write_text("- W1\n- W2\n")
        ui = self.screen()
        ui.retree([("W2", 0), ("W1", 0)], ui.current(), "")
        self.assertEqual(self.path.read_text(), "- W2\n- W1\n")
        ui.act(ord("z"))
        self.assertEqual(self.path.read_text(), "- W1\n- W2\n")

    def test_a_file_that_did_not_exist_is_removed_again(self):
        ui = self.screen()
        ui.retree([("W2", 0), ("W1", 0)], ui.current(), "")
        ui.act(ord("z"))
        self.assertFalse(self.path.exists())

    def test_it_refuses_over_somebody_elses_edit(self):
        self.path.write_text("- W1\n- W2\n")
        ui = self.screen()
        ui.retree([("W2", 0), ("W1", 0)], ui.current(), "")
        self.path.write_text("- W2\n- W1\n- W3\n")          # another session
        ui.act(ord("z"))
        self.assertEqual(self.path.read_text(), "- W2\n- W1\n- W3\n")
        self.assertIn("not undone", ui.msg)


class TheActivityLog(unittest.TestCase):
    def test_a_started_chain_and_a_stopped_walk_are_recorded(self):
        ui = a_screen([item("W1")], walk_on=True, walk_dir=None, walk_until=0.0,
                      walk_note="")
        ui.walk_off("W1: failed")
        kinds = [(k, t) for _, k, t in ui.events]
        self.assertIn(("stop", "walk off — W1: failed"), kinds)

    def test_a_test_screen_writes_no_file_and_rings_no_bell(self):
        ui = a_screen([])
        self.assertFalse(ui.real)
        ui.event("run", "$ x")
        ui.notify("x")                       # would raise without initscr if it rang

    def test_lines_carry_the_day_and_the_time(self):
        ui = a_screen([])
        ui.events = [(0.0, "run", "$ run.sh W1 5")]
        lines = [t for t, _ in ui.activity_lines(60)]
        self.assertTrue(lines[0][:3].isalpha(), lines)
        self.assertRegex(lines[1], r"^\d\d:\d\d ")
        self.assertTrue(lines[1].endswith(" $ run.sh W1 5"), lines[1])

    def test_the_file_is_read_back(self):
        import tempfile
        f = Path(tempfile.mkdtemp()) / "activity.log"
        f.write_text("100\trun\t$ a\nnot a line\n200\tstop\twalk off — x\n")
        self.assertEqual(TUI.read_activity(str(f)),
                         [(100.0, "run", "$ a"), (200.0, "stop", "walk off — x")])


class TheAgentIsRemembered(unittest.TestCase):
    """`x`'s choice outlives the screen: the next one launches the same agent."""

    def test_the_file_is_read_back(self):
        import tempfile
        f = Path(tempfile.mkdtemp()) / "agent"
        self.assertEqual(TUI.read_agent(str(f)), "claude")      # no file yet
        f.write_text("kiro\n")
        self.assertEqual(TUI.read_agent(str(f)), "kiro")
        f.write_text("gemini\n")                               # one we do not run
        self.assertEqual(TUI.read_agent(str(f)), "claude")

    def test_x_writes_what_the_next_screen_reads(self):
        import os
        import tempfile
        root = tempfile.mkdtemp()
        old = (TUI.dfs_runs.run_root, TUI.dfs_runs.ensure_run_root)
        TUI.dfs_runs.run_root = TUI.dfs_runs.ensure_run_root = lambda: root
        try:
            ui = a_screen([])
            ui.real, ui.agent, ui.pane = True, "claude", "item"
            ui.act(ord("x"))
            self.assertEqual(ui.agent, "codex")
            self.assertEqual(TUI.read_agent(TUI.agent_file()), "codex")
            self.assertTrue(os.path.exists(os.path.join(root, "agent")))
        finally:
            TUI.dfs_runs.run_root, TUI.dfs_runs.ensure_run_root = old


class Ringing(unittest.TestCase):
    """The bell is for the ends the walk exists to bring back, and nothing else."""

    def setUp(self):
        import os
        import tempfile
        self.rang = []
        self.saved = (TUI.curses.beep, TUI.dfs_runs.ensure_run_root,
                      os.environ.get("DFS_NOTIFY"))
        TUI.curses.beep = lambda: self.rang.append(1)
        tmp = tempfile.mkdtemp()
        self.log = Path(tmp) / "activity.log"
        TUI.dfs_runs.ensure_run_root = lambda: tmp
        os.environ.pop("DFS_NOTIFY", None)

    def tearDown(self):
        import os
        TUI.curses.beep, TUI.dfs_runs.ensure_run_root, notify = self.saved
        if notify is None:
            os.environ.pop("DFS_NOTIFY", None)
        else:
            os.environ["DFS_NOTIFY"] = notify

    def screen(self):
        return a_screen([item("W1")], real=True, walk_on=True, walk_dir=None,
                        walk_until=0.0, walk_note="")

    def test_a_walk_that_stopped_itself_rings_and_is_written_down(self):
        self.screen().walk_off("W1: failed")
        self.assertEqual(self.rang, [1])
        self.assertIn("walk off — W1: failed", self.log.read_text())

    def test_a_walk_stopped_by_hand_does_not(self):
        self.screen().walk_off("stopped by hand")
        self.assertEqual(self.rang, [])

    def test_a_chain_ending_rings(self):
        ui = self.screen()
        ui.chain_ended(dict(item="W1", rc="0"))
        self.assertEqual(self.rang, [1])
        self.assertEqual(ui.events[-1][1], "done")

    def test_off_is_off(self):
        import os
        os.environ["DFS_NOTIFY"] = "off"
        self.screen().walk_off("W1: failed")
        self.assertEqual(self.rang, [])


class ThePlan(unittest.TestCase):
    def test_the_first_is_the_pin_else_next_and_held_items_say_by_what(self):
        items = [item("W1"), dict(item("W2"), blocked_by="W1"), item("W3"),
                 dict(item("W4", status="blocked"))]
        ui = a_screen(items, walk_next="W3")
        ui.data["next_item"] = "W1"
        text = "\n".join(t for t, _ in ui.lines_plan(100))
        self.assertLess(text.index("W3"), text.index("W1"))
        self.assertIn("a raise above it, on W1", text)
        self.assertIn("waiting on your answer: W4", text)


class TheKeysPane(unittest.TestCase):
    def test_every_key_act_answers_to_is_listed(self):
        # ⚠️ The drift guard: `act` is where a key exists, `KEYS` is where it is
        # seen, and a key in one and not the other is a key nobody finds.
        src = inspect.getsource(TUI.UI.act)
        handled = set(re.findall(r'ord\("(.)"\)', src))
        listed = set()
        for _, label, _, _, _ in TUI.KEYS:
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
        # and gives up `: command` before it
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
            ui = a_screen([item("W1")], agent="claude")
            ui.reload = lambda note="": None
            ui.start_chain("W1", "5")
        finally:
            (TUI.dfs_runs.live_for, TUI.dfs_runs.ensure_run_root,
             TUI.subprocess.Popen) = saved
        self.assertTrue(ui.msg.startswith("$ %s W1 5" % TUI.RUNNER), ui.msg)
        self.assertIn("pid 4242", ui.msg)


class KeepInView(unittest.TestCase):
    """The scroll that follows the cursor's row, and what is under it."""

    def test_a_row_above_the_fold_is_scrolled_to(self):
        self.assertEqual(TUI.keep_in_view(10, 4, 4, 20), 4)

    def test_a_single_line_row_below_the_fold_lands_on_the_last_line(self):
        self.assertEqual(TUI.keep_in_view(0, 25, 25, 20), 6)

    def test_an_expanded_row_brings_its_whole_block_up(self):
        # Header at 15 of a 20-line pane, block to 29: the header alone was in view,
        # so opening the bottom node showed none of what it said.
        self.assertEqual(TUI.keep_in_view(0, 15, 29, 20), 10)

    def test_a_block_taller_than_the_pane_opens_at_its_header(self):
        self.assertEqual(TUI.keep_in_view(0, 15, 80, 20), 15)

    def test_a_block_already_in_view_does_not_move(self):
        self.assertEqual(TUI.keep_in_view(5, 8, 20, 20), 5)


TREE = """# W1 · the thing

## Goal

The thing.

## Tree

""" + "".join("""### W1.%d · step %d
Status: confirmed
Determination: step %d is done.

""" % (n, n, n) for n in range(1, 13)) + """## Log
"""


class TheTreeKeepsItsExpandedNodeInView(unittest.TestCase):
    def screen(self, open_key):
        ui = a_screen([item("W1")], pane="item", focus="tree", tree_open=set())
        ui.tree_of = lambda task: (TUI.dfs_tree.parse(TREE, "W1"), "", {})
        ui.status_attr = lambda status: 0
        ui._tree_cache = {}
        rows = TUI.tree_entries(TUI.dfs_tree.parse(TREE, "W1"), ui.tree_folded)
        ui.tree_item = "W1"
        bottom = max(n for n, r in enumerate(rows) if r["kind"] not in ("section", "raise"))
        ui.tree_sel = bottom
        if open_key:
            ui.tree_open = {rows[bottom]["key"]}
        return ui

    def test_the_cursor_row_ends_where_its_detail_ends(self):
        closed, opened = self.screen(False), self.screen(True)
        lines_closed = closed.lines_tree(80)
        lines_open = opened.lines_tree(80)
        # A closed node is its row and the one line of what it concluded.
        self.assertEqual(closed._sel_end, closed._sel_row + 1)
        self.assertGreater(opened._sel_end, opened._sel_row + 2)
        # The bottom node's block is the last thing in the pane, bar any section after.
        self.assertGreaterEqual(len(lines_open) - 1, opened._sel_end)
        text = [l[0] for l in lines_open[opened._sel_row:opened._sel_end + 1]]
        self.assertTrue(any("step 12 is done" in t for t in text), text)

    def test_opening_the_bottom_node_scrolls_its_detail_into_view(self):
        ui = self.screen(True)
        lines = ui.lines_tree(80)
        body_h = 12
        scroll = TUI.keep_in_view(0, ui._sel_row, ui._sel_end, body_h)
        shown = [l[0] for l in lines[scroll:scroll + body_h]]
        self.assertTrue(any("step 12 is done" in t for t in shown), shown)
        self.assertTrue(any(t.startswith(">") for t in shown), shown)


class TheNodePanel(unittest.TestCase):
    def test_it_replaces_the_activity_column_only_while_the_tree_has_the_keys(self):
        tree_ui = a_screen([item("W1")], pane="item", focus="tree")
        list_ui = a_screen([item("W1")], pane="item", focus="list")
        self.assertGreater(tree_ui.side_w(120), 0)
        self.assertEqual(tree_ui.side_w(NARROW := TUI.NODE_AT - 1), 0)
        self.assertEqual(list_ui.side_w(120), 0, "the activity column waits for SIDE_AT")
        self.assertGreater(list_ui.side_w(TUI.SIDE_AT), 0)

    def test_a_node_scrolls_to_the_top_when_the_cursor_moves_to_another(self):
        ui = a_screen([item("W1")], pane="item", focus="tree")
        ui.card_panel = True
        ui.act(4)
        self.assertEqual(ui.card_scroll, 6)
        ui.act(21)
        self.assertEqual(ui.card_scroll, 0)


class AddingABreakAsks(unittest.TestCase):
    def screen(self, had, answers):
        ui = a_screen([dict(item("W2"), depth=0)], answers=list(answers))
        ui.nodes = lambda: []
        wrote = []
        ui.retree = lambda after, it, refusal: wrote.append(after) or after is not None
        ui.said = lambda it, did: None
        self.saved = (TUI.dfs_order.has_break_above, TUI.dfs_order.break_toggled)
        TUI.dfs_order.has_break_above = lambda nodes, i: had
        TUI.dfs_order.break_toggled = lambda nodes, i: "tree"
        self.addCleanup(lambda: (setattr(TUI.dfs_order, "has_break_above", self.saved[0]),
                                 setattr(TUI.dfs_order, "break_toggled", self.saved[1])))
        return ui, wrote

    def test_adding_one_needs_a_yes(self):
        ui, wrote = self.screen(False, ["y"])
        ui.toggle_break()
        self.assertEqual(wrote, ["tree"])

    def test_anything_else_adds_nothing(self):
        for answer in ("", "n", "x"):
            ui, wrote = self.screen(False, [answer])
            ui.toggle_break()
            self.assertEqual((wrote, ui.msg), ([], "no break added"))

    def test_taking_one_away_does_not_ask(self):
        ui, wrote = self.screen(True, [])
        ui.toggle_break()
        self.assertEqual(wrote, ["tree"])


class LOpensTheLastRunAtItsEnd(unittest.TestCase):
    def test_l_reads_the_selected_items_newest_run_from_the_bottom(self):
        runs = [dict(path="/r/b/console.log", item="W2", run={"live": False}),
                dict(path="/r/a/console.log", item="W1", run={"live": False}),
                dict(path="/r/c/console.log", item="W1", run={"live": False})]
        saved = TUI.discover_runs
        TUI.discover_runs = lambda: list(runs)
        try:
            ui = a_screen([item("W1"), item("W2")])
            ui.act(ord("l"))
        finally:
            TUI.discover_runs = saved
        self.assertEqual((ui.pane, ui.open_run["path"]), ("agentlog", "/r/a/console.log"))
        self.assertEqual(ui.scroll, 10 ** 6, "the clamp in draw lands it on the last line")
        ui.act(27)
        self.assertEqual(ui.pane, "item", "esc goes back to the item, not a list it skipped")

    def test_a_run_picked_from_the_list_opens_at_the_bottom_too(self):
        ui = a_screen([item("W1")], pane="runs", run_sel=0,
                      runs=[dict(path="/r/a/console.log", item="W1", run={"live": False})])
        ui.act(10)
        self.assertEqual((ui.pane, ui.scroll), ("agentlog", 10 ** 6))
        ui.act(27)
        self.assertEqual(ui.pane, "runs")


class AnAssumptionStandsOut(unittest.TestCase):
    """Most nodes say something was done; one that states a Hypothesis is marked, in
    its own colour, with the assumption under it where it is read without opening."""

    HYP = "the author means v2 only, and nothing reads the old cursor file"

    def lines(self, open_it=False):
        text = TREE.replace("### W1.2 · step 2\nStatus: confirmed\n",
                            "### W1.2 · step 2\nStatus: confirmed\nHypothesis: %s.\n" % self.HYP)
        ui = a_screen([item("W1")], pane="item", focus="tree", tree_open=set())
        ui.tree_of = lambda task: (TUI.dfs_tree.parse(text, "W1"), "", {})
        ui.status_attr = lambda status: 0
        ui._tree_cache = {}
        ui.tree_item = "W1"
        TUI.LOOK["amber"] = 7000
        if open_it:
            ui.tree_open = {"W1.2"}
        return ui.lines_tree(100)

    def test_it_is_marked_coloured_and_says_what_it_assumes(self):
        lines = self.lines()
        head = next(l for l in lines if "W1.2" in l[0])
        self.assertIn("⚑ assumption", head[0])
        self.assertTrue(head[1] & 7000 and head[1] & curses.A_BOLD, head)
        self.assertTrue(any(self.HYP in l[0] and l[1] == 7000 for l in lines))

    def test_a_plain_node_is_not(self):
        plain = next(l for l in self.lines() if "W1.3" in l[0])
        self.assertNotIn("⚑", plain[0])
        self.assertFalse(plain[1] & 7000, plain)

    def test_opened_it_is_not_said_twice_in_a_row(self):
        said = [l[0] for l in self.lines(open_it=True) if self.HYP in l[0]]
        self.assertEqual(len(said), 1, said)


class ADoneTaskRecedesInTheList(unittest.TestCase):
    """The list told a finished task from an open one by the colour of one word."""

    class Rec:
        def __init__(self):
            self.puts = []

        def getmaxyx(self):
            return (24, 100)

        def addstr(self, y, x, text, attr=0):
            self.puts.append((y, x, text, attr))

    def draw(self):
        items = [item("W1", status="done"), item("W2", status="open"),
                 item("W3", status="done")]
        ui = a_screen(items, sel=2, list_scroll=0)
        ui.scr = self.Rec()
        ui.run_tag = lambda iid: ("", 0)
        ui.list_rows = lambda: [("item", n) for n in range(3)]
        ui.status_attr = lambda status: 7
        saved = dict(TUI.LOOK)
        TUI.LOOK.clear()
        TUI.LOOK.update(chrome=curses.A_DIM)
        try:
            ui.draw_list(0, 10)
        finally:
            TUI.LOOK.clear()
            TUI.LOOK.update(saved)
        return ui.scr.puts

    def test_the_whole_row_is_dim_and_the_status_says_done_with_a_tick(self):
        puts = self.draw()
        row = lambda y: [p for p in puts if p[0] == y]
        done, open_ = row(0)[0], row(1)[0]
        self.assertTrue(done[3] & curses.A_DIM, "a done row is drawn dim")
        self.assertFalse(open_[3] & curses.A_DIM, "an open row is not")
        self.assertIn("✓ done", [p[2].strip() for p in row(0)])
        self.assertIn("open", [p[2].strip() for p in row(1)])
        self.assertFalse(row(2)[0][3] & curses.A_DIM, "the cursor row keeps its band")


if __name__ == "__main__":
    curses.color_pair = lambda n: 0       # no initscr here; attrs are opaque
    unittest.main()
