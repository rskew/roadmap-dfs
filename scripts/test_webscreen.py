#!/usr/bin/env python3
"""The terminal screen and the web page share ONE walk: the page's controls are run on
the screen's own thread, and what the page shows is read from the screen's own state.

Run:  python3 roadmap-dfs/scripts/test_webscreen.py
"""
import collections
import http.client
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import paths as dfs_paths   # noqa: E402
import tree as dfs_tree     # noqa: E402
import tui as TUI           # noqa: E402
import web as dfs_web       # noqa: E402


def item(i, status="open", goal=None):
    return dict(id=i, goal=goal or "goal " + i, status=status)


def a_screen(items):
    """A screen as the tests elsewhere build one: no curses, the walk's state set by hand."""
    ui = object.__new__(TUI.UI)
    ui.real = False
    ui.msg = ""
    ui.commands = queue.Queue()
    ui.events = collections.deque(maxlen=50)
    ui.agent = "claude"
    ui.walk_on, ui.walk_budget, ui.walk_spent, ui.walk_until = False, 20, 0, 0.0
    ui.walk_note = ""
    ui.walk_next, ui.walk_pending = None, False
    ui.chains = []
    ui.data = dict(next_item=items[0]["id"] if items else None)
    ui._items = items
    TUI.UI.items = property(lambda self: self._items)       # the tests' own items
    ui.walk_tick = lambda: None                              # the policy has its own tests
    ui.reload = lambda: None
    return ui


class Screening:
    """Stand in for the screen's thread: drain what the web asks, as `poll` does."""

    def __init__(self, ui):
        self.ui, self.stop_ = ui, threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        while not self.stop_.is_set():
            self.ui.drain_commands()
            time.sleep(0.01)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.stop_.set()
        self.thread.join(2)


class Controls(unittest.TestCase):
    def setUp(self):
        self._items = TUI.UI.__dict__.get("items")
        self.ui = a_screen([item("W1"), item("W2"), item("W3", "done")])
        self.w = TUI.UiWalker(self.ui)

    def tearDown(self):
        TUI.UI.items = self._items

    def test_the_page_starts_the_screens_own_walk(self):
        with Screening(self.ui):
            self.w.start(7)
        self.assertTrue(self.ui.walk_on)
        self.assertEqual(self.ui.walk_budget, 7)
        self.assertIn("walk on, 7 sessions", [t for _, _, t in self.ui.events])

    def test_a_second_start_and_a_budget_that_is_not_a_walk_are_refused(self):
        with Screening(self.ui):
            self.w.start(3)
            with self.assertRaises(ValueError):
                self.w.start(3)
            for bad in (0, -1, "5", True, 10_000):
                with self.assertRaises(ValueError):
                    self.w.start(bad)

    def test_the_page_stops_it_and_the_screen_shows_it_off(self):
        with Screening(self.ui):
            self.w.start(3)
            self.w.stop()
        self.assertFalse(self.ui.walk_on)
        self.assertEqual(self.ui.walk_note, "stopped by hand")

    def test_pins_are_the_screens_pins(self):
        with Screening(self.ui):
            self.w.pin("W2")
            self.assertEqual(self.ui.walk_next, "W2")
            self.w.pin("W2")                                  # again: the pin goes
            self.assertIsNone(self.ui.walk_next)
            self.w.pin("W1")
            self.w.pin(None)
            self.assertIsNone(self.ui.walk_next)
            for bad in ("W3", "W9"):
                with self.assertRaises(ValueError):
                    self.w.pin(bad)

    def test_the_agent_is_the_screens_agent(self):
        with Screening(self.ui):
            self.w.set_agent("codex")
            with self.assertRaises(ValueError):
                self.w.set_agent("bash")
        self.assertEqual(self.ui.agent, "codex")
        self.assertEqual(self.w.agent(), "codex")

    def test_a_screen_with_a_prompt_open_says_so_rather_than_hanging(self):
        # Nothing drains: the screen is in a prompt.
        t0 = time.time()
        with self.assertRaises(ValueError) as cm:
            self.ui.call_soon(lambda: None, timeout=0.2)
        self.assertIn("busy", str(cm.exception))
        self.assertLess(time.time() - t0, 2)
        self.ui.drain_commands()                              # the late one is dropped, not run

    def test_a_late_command_does_not_run_after_its_caller_gave_up(self):
        ran = []
        with self.assertRaises(ValueError):
            self.ui.call_soon(lambda: ran.append(1), timeout=0.05)
        self.ui.drain_commands()
        self.assertEqual(ran, [])

    def test_a_command_the_screen_has_begun_is_waited_for_not_called_busy(self):
        # The screen takes the command before the caller's timeout, then spends longer than
        # the timeout inside it (a walk's Start spawns the runner and reloads the roadmap).
        ran = []

        def slow():
            time.sleep(0.4)
            ran.append(1)
            return "started"
        drainer = threading.Thread(target=lambda: (time.sleep(0.05), self.ui.drain_commands()))
        drainer.start()
        self.assertEqual(self.ui.call_soon(slow, timeout=0.2), "started")
        drainer.join()
        self.assertEqual(ran, [1])

    def test_a_command_that_exits_the_screen_frees_its_caller(self):
        def die():
            raise SystemExit(1)
        result = []

        def caller():
            try:
                self.ui.call_soon(die, timeout=1.0)
            except Exception as e:
                result.append(e)
        th = threading.Thread(target=caller)
        th.start()
        time.sleep(0.05)
        with self.assertRaises(SystemExit):
            self.ui.drain_commands()
        th.join(2)
        self.assertFalse(th.is_alive())
        self.assertIn("stopped", str(result[0]))

    def test_a_begun_command_that_never_finishes_is_reported_not_hung_on(self):
        release = threading.Event()
        drainer = threading.Thread(target=lambda: (time.sleep(0.05), self.ui.drain_commands()))
        # The command blocks until the test ends; the caller must give up on its own.
        t0 = time.time()
        drainer.start()
        with self.assertRaises(ValueError) as cm:
            self.ui.call_soon(lambda: release.wait(5), timeout=0.2, running_timeout=0.2)
        self.assertIn("still running", str(cm.exception))
        self.assertLess(time.time() - t0, 2)
        release.set()
        drainer.join(2)

    def test_the_snapshot_is_the_screens_state(self):
        with Screening(self.ui):
            self.w.start(5)
            self.w.pin("W2")
        snap = self.w.snapshot()
        self.assertEqual((snap["on"], snap["budget"], snap["pinned"], snap["agent"]),
                         (True, 5, "W2", "claude"))
        self.assertEqual([c["id"] for c in snap["candidates"]], ["W1", "W2"])
        self.assertEqual(snap["auto"], "W1")
        self.assertEqual(snap["events"][0]["kind"], "walk")   # newest first


class OverHttp(unittest.TestCase):
    """Page to screen, through the real server."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / ".dfs" / "items").mkdir(parents=True)
        (self.root / ".dfs" / "ROADMAP.md").write_text("# Roadmap\n")
        self._wr = dfs_paths.work_root
        dfs_paths.work_root = lambda: self.root
        dfs_paths._TAG_CACHE.clear()
        self._items = TUI.UI.__dict__.get("items")
        self.ui = a_screen([item("W1"), item("W2")])
        self.handle = dfs_web.serve("127.0.0.1", 0, walker=TUI.UiWalker(self.ui))
        self.port = int(self.handle.url.rsplit(":", 1)[1].strip("/"))
        self.screen = Screening(self.ui)
        self.screen.__enter__()

    def tearDown(self):
        self.screen.__exit__()
        self.handle.stop()
        dfs_web.WALK.update(enabled=True, walker=None)
        TUI.UI.items = self._items
        dfs_paths.work_root = self._wr
        dfs_paths._TAG_CACHE.clear()
        shutil.rmtree(self.root, ignore_errors=True)

    def call(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request(method, path, json.dumps(body) if body is not None else None,
                     {"Content-Type": "application/json"} if body is not None else {})
        r = conn.getresponse()
        return r.status, json.loads(r.read())

    def test_the_page_controls_the_screens_walk_and_reads_its_state(self):
        status, snap = self.call("GET", "/api/walk")
        self.assertEqual((status, snap["on"], snap["enabled"]), (200, False, True))
        self.assertEqual(self.call("POST", "/api/walk/start", dict(budget=4))[0], 200)
        self.assertTrue(self.ui.walk_on)                        # the SCREEN's state moved
        self.assertEqual(self.call("GET", "/api/walk")[1]["budget"], 4)
        self.assertEqual(self.call("POST", "/api/walk/pin", dict(item="W2"))[1]["pinned"], "W2")
        self.assertEqual(self.ui.walk_next, "W2")
        self.assertEqual(self.call("POST", "/api/walk/agent", dict(agent="kiro"))[1]["agent"], "kiro")
        self.assertEqual(self.ui.agent, "kiro")
        self.assertEqual(self.call("POST", "/api/walk/stop", {})[0], 200)
        self.assertFalse(self.ui.walk_on)
        self.assertEqual(self.call("GET", "/api/walk")[1]["note"], "stopped by hand")

    def test_the_page_starts_the_screens_walk_with_parallel_chains(self):
        self.assertEqual(self.call("POST", "/api/walk/start", dict(budget=6, parallel=2))[0], 200)
        self.assertEqual(self.ui.walk_parallel, 2)
        self.assertEqual(self.call("GET", "/api/walk")[1]["parallel"], 2)

    def test_what_the_screen_does_shows_on_the_page(self):
        self.ui.walk_begin(6)                                   # the `w` key's path
        snap = self.call("GET", "/api/walk")[1]
        self.assertEqual((snap["on"], snap["budget"]), (True, 6))

    def test_a_busy_screen_is_a_conflict_not_a_hang(self):
        self.screen.__exit__()                                  # nothing is draining now
        t0 = time.time()
        orig = TUI.UI.call_soon
        TUI.UI.call_soon = lambda self, fn, timeout=5.0: orig(self, fn, timeout=0.2)
        try:
            status, body = self.call("POST", "/api/walk/start", dict(budget=3))
        finally:
            TUI.UI.call_soon = orig
        self.assertEqual(status, 409)
        self.assertIn("busy", body["error"])
        self.assertLess(time.time() - t0, 3)
        self.screen = Screening(self.ui)
        self.screen.__enter__()


class InAChat(unittest.TestCase):
    """The screen's thread is in a chat (`c`): the page's Start still starts the walk."""

    call = OverHttp.call

    def setUp(self):
        OverHttp.setUp(self)
        self.screen.__exit__()                                  # nothing drains but the chat
        import ptyrelay
        self.keys_r, self.keys_w = os.pipe()
        self.out_r, self.out_w = os.pipe()
        self._io = sys.stdin, sys.stdout
        sys.stdin, sys.stdout = os.fdopen(self.keys_r, "r"), os.fdopen(self.out_w, "w")
        self.drain = threading.Thread(target=self.read_display, daemon=True)
        self.drain.start()
        relay = ptyrelay.Relay().start(["bash", "-c", "echo ready; cat >/dev/null"])
        self.addCleanup(relay.close)
        self.ui.start_chat = lambda scope: relay
        self.ui.poll = self.ui.drain_commands                   # the real poll's part that answers the page
        self.ui.scr = types.SimpleNamespace(clear=lambda: None, refresh=lambda: None)
        self._curses = {n: getattr(TUI.curses, n) for n in ("def_prog_mode", "endwin", "reset_prog_mode")}
        for n in self._curses:
            setattr(TUI.curses, n, lambda: None)
        self.chat = threading.Thread(target=self.ui.open_chat, args=("W1",), daemon=True)
        self.chat.start()

    def read_display(self):
        try:
            while os.read(self.out_r, 4096):
                pass
        except OSError:
            pass

    def tearDown(self):
        os.write(self.keys_w, b"\x1d")                          # ctrl-]: back to the roadmap
        self.chat.join(5)
        self.assertFalse(self.chat.is_alive())
        for n, f in self._curses.items():
            setattr(TUI.curses, n, f)
        sys.stdin, sys.stdout = self._io
        for fd in (self.keys_w, self.out_r):
            os.close(fd)
        self.screen = types.SimpleNamespace(__exit__=lambda: None)
        OverHttp.tearDown(self)

    def test_the_page_starts_the_walk_while_the_screen_is_in_a_chat(self):
        status, body = self.call("POST", "/api/walk/start", dict(budget=3))
        self.assertEqual(status, 200, body)
        self.assertTrue(self.ui.walk_on)
        self.assertTrue(self.chat.is_alive())                   # still in the chat

    # and every other control the page has, as from a screen not in a chat
    test_the_page_controls_the_screens_walk_and_reads_its_state = \
        OverHttp.test_the_page_controls_the_screens_walk_and_reads_its_state


class Reopening(unittest.TestCase):
    """A node added on the page to a finished task reopens it, and the page's "Walk here
    next" reads the walk from the screen: once the screen has reloaded, the page is told."""

    DONE = ("# W1 · one\n\n## Goal\n\none\n\n## Tree\n\n### W1.1 · did it\nStatus: confirmed\n"
            "Approach: x\nDetermination: done\n\n## Log\n\n"
            "- 2026-10-01T00:00:00Z · session · work · dfs_run_a/run-1\n"
            "- 2026-10-02T00:00:00Z · review · ok\n")

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        items = self.root / ".dfs" / "items"
        items.mkdir(parents=True)
        (self.root / ".dfs" / "ROADMAP.md").write_text("# Roadmap\n")
        (self.root / ".dfs" / "order.md").write_text("# Order\n\n- W1\n- W2\n")
        (items / "W1.md").write_text(self.DONE)
        (items / "W2.md").write_text("# W2 · two\n\n## Goal\n\ntwo\n\n## Tree\n\n## Log\n")
        for cmd in (["init", "-q"], ["add", "."], ["-c", "user.name=t", "-c", "user.email=t@t",
                                                   "commit", "-qm", "init"]):
            subprocess.run(["git"] + cmd, cwd=self.root, check=True)
        self._wr, self._root, self._state = dfs_paths.work_root, TUI.ROOT, TUI.STATE
        dfs_paths.work_root = lambda: self.root
        TUI.ROOT = self.root                    # the screen reads `state.py` run there
        TUI.STATE = self.root / ".dfs"          # and `git status` there, not in the CWD's `.dfs`
        dfs_paths._TAG_CACHE.clear()
        self.ui = TUI.UI(None)
        self.ui.real = False
        self.told = []
        self.ui.web = types.SimpleNamespace(notify=lambda: self.told.append(1))

    def tearDown(self):
        dfs_paths.work_root, TUI.ROOT, TUI.STATE = self._wr, self._root, self._state
        dfs_paths._TAG_CACHE.clear()
        shutil.rmtree(self.root, ignore_errors=True)

    def candidates(self):
        return [c["id"] for c in self.ui.walk_snapshot()["candidates"]]

    def test_a_reload_that_reopens_a_task_tells_the_page(self):
        self.assertEqual(self.candidates(), ["W2"])
        self.ui.reload()
        self.assertEqual(self.told, [], "nothing the walk shows changed")
        dfs_tree.add_node("W1", "more work")
        self.assertEqual(self.candidates(), ["W2"], "the screen has not read the file yet")
        self.ui.reload()
        self.assertEqual(self.candidates(), ["W1", "W2"])
        self.assertEqual(self.told, [1], "the page reads the walk again once the screen has it")


class Serving(unittest.TestCase):
    def test_a_taken_port_moves_to_the_next_free_one(self):
        root = Path(tempfile.mkdtemp())
        (root / ".dfs" / "items").mkdir(parents=True)
        (root / ".dfs" / "ROADMAP.md").write_text("# Roadmap\n")
        wr = dfs_paths.work_root
        dfs_paths.work_root = lambda: root
        try:
            first = dfs_web.serve("127.0.0.1", 0, walk_enabled=False)
            port = int(first.url.rsplit(":", 1)[1].strip("/"))
            second = dfs_web.serve("127.0.0.1", port, walk_enabled=False, tries=5)
            try:
                self.assertGreater(int(second.url.rsplit(":", 1)[1].strip("/")), port)
            finally:
                second.stop()
                first.stop()
        finally:
            dfs_paths.work_root = wr
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
