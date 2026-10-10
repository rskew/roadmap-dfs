#!/usr/bin/env python3
"""ptyrelay: agents that run in the background, are typed into by the page, can be looked at
from the terminal (attach) and left running (detach), and a host that keeps many of them.

Run:  python3 roadmap-dfs/scripts/test_ptyrelay.py
"""
import fcntl
import os
import pty
import re
import select
import struct
import shutil
import sys
import tempfile
import termios
import threading
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ptyrelay  # noqa: E402


_RUNS = []


def setUpModule():
    """The chat logs go to a run root of their own, not the project's `.dfs/runs/chat/`."""
    _RUNS.append(tempfile.mkdtemp(prefix="dfs-test-runs-"))
    _RUNS.append(os.environ.get("DFS_RUN_DIR"))
    os.environ["DFS_RUN_DIR"] = _RUNS[0]


def tearDownModule():
    if _RUNS[1] is None:
        os.environ.pop("DFS_RUN_DIR", None)
    else:
        os.environ["DFS_RUN_DIR"] = _RUNS[1]
    shutil.rmtree(_RUNS[0], ignore_errors=True)

# an "agent": says it is ready, then logs each line typed at it (paste markers stripped) and echoes it
AGENT = r'''echo ready; while IFS= read -r l; do l=$(printf '%s' "$l" | sed -e 's/\x1b\[20[01]~//g'); echo "$l" >> "$LOG"; echo "heard: $l"; [ "$l" = quit ] && exit 3; done'''


def until(fn, timeout=8):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.05)
    return False


class Background(unittest.TestCase):
    def setUp(self):
        self.log = Path(tempfile.mkdtemp()) / "log"
        self.log.write_text("")
        self.env = dict(os.environ, LOG=str(self.log))
        self.relay = ptyrelay.Relay().start(["bash", "-c", AGENT], env=self.env)
        self.addCleanup(self.relay.close)

    def lines(self):
        return self.log.read_text().splitlines()

    def test_the_page_types_into_it_with_nobody_looking(self):
        self.assertTrue(self.relay.live)
        self.assertTrue(self.relay.send("first"))
        self.assertTrue(self.relay.send("second\nline"))        # a paste: one message
        self.assertTrue(until(lambda: self.lines()[:1] == ["first"]))
        self.assertTrue(until(lambda: len(self.lines()) >= 2))
        self.assertIn("second", self.lines()[1])

    def test_it_waits_for_a_fresh_agent_to_have_drawn_itself(self):
        slow = ptyrelay.Relay().start(["bash", "-c", "sleep 1.5; " + AGENT], env=self.env)
        self.addCleanup(slow.close)
        t0 = time.time()
        slow.send("early")
        self.assertTrue(until(lambda: "early" in self.lines(), 15))
        self.assertGreater(time.time() - t0, 1.5)               # not typed into the void before it was up

    def test_activity_is_output_in_the_last_moments(self):
        self.relay.send("hi")
        self.assertTrue(until(lambda: self.relay.active))
        self.assertTrue(until(lambda: not self.relay.active, 6))

    def test_an_agent_that_ends_says_it_is_not_running(self):
        self.relay.send("quit")
        self.assertTrue(until(lambda: not self.relay.live))
        self.assertEqual(self.relay.rc, 3)
        self.assertFalse(self.relay.send("late"))

    def test_interrupt_presses_escape_and_the_agent_goes_on(self):
        agent = r'''echo ready; while IFS= read -rsn1 c; do if [ "$c" = $'\e' ]; then echo esc >> "$LOG"; else echo "c:$c" >> "$LOG"; fi; done'''
        r = ptyrelay.Relay().start(["bash", "-c", agent], env=self.env)
        self.addCleanup(r.close)
        self.assertTrue(until(lambda: r.first_out))
        self.assertEqual(r.interrupt(), 0, "nothing was waiting to be typed")
        self.assertTrue(until(lambda: self.lines() == ["esc"]))
        self.assertTrue(r.live, "an interrupt stops the turn, not the agent")
        r.send("x")
        self.assertTrue(until(lambda: "c:x" in self.lines()), "and it still hears what is sent next")

    def test_interrupt_drops_a_message_that_has_not_been_typed(self):
        agent = r'''sleep 1.5; echo ready; while IFS= read -rsn1 c; do echo "c:$c" >> "$LOG"; done'''
        r = ptyrelay.Relay().start(["bash", "-c", agent], env=self.env)
        self.addCleanup(r.close)
        r.send("late")                  # waiting for the agent to draw itself
        time.sleep(0.3)
        self.assertEqual(r.interrupt(), 1, "it says the message was dropped untyped")
        time.sleep(3)
        self.assertNotIn("c:l", self.lines(), "stopped before it was typed, so never typed")

    def test_interrupt_of_an_ended_agent_says_so(self):
        self.relay.send("quit")
        self.assertTrue(until(lambda: not self.relay.live))
        self.assertIsNone(self.relay.interrupt())

    def test_close_ends_it(self):
        self.relay.close()
        self.assertFalse(self.relay.live)
        self.assertTrue(until(lambda: self.relay.rc is not None))


class Looking(unittest.TestCase):
    """The terminal: a pipe for the keys and one for the display."""

    def setUp(self):
        self.keys_r, self.keys_w = os.pipe()
        self.out_r, self.out_w = os.pipe()
        self._io = sys.stdin, sys.stdout
        sys.stdin, sys.stdout = os.fdopen(self.keys_r, "r"), os.fdopen(self.out_w, "w")
        self.log = Path(tempfile.mkdtemp()) / "log"
        self.log.write_text("")
        self.relay = ptyrelay.Relay().start(["bash", "-c", AGENT], env=dict(os.environ, LOG=str(self.log)))
        self.addCleanup(self.relay.close)
        self.result = []
        self.t = threading.Thread(target=lambda: self.result.append(self.relay.attach()), daemon=True)

    def tearDown(self):
        sys.stdin, sys.stdout = self._io
        for fd in (self.keys_w, self.out_r):
            try:
                os.close(fd)
            except OSError:
                pass

    def seen(self, needle, timeout=5):
        got, end = b"", time.time() + timeout
        while time.time() < end:
            if select.select([self.out_r], [], [], 0.1)[0]:
                got += os.read(self.out_r, 4096)
                if needle in got:
                    return True
        return False

    def test_attached_it_shows_and_takes_the_keyboard_and_the_page_still_types(self):
        self.t.start()
        self.assertTrue(self.relay.send("from the page"))
        self.assertTrue(self.seen(b"heard: from the page"))
        os.write(self.keys_w, b"from the keys\r")
        self.assertTrue(self.seen(b"heard: from the keys"))

    def test_ctrl_bracket_detaches_and_it_keeps_running(self):
        self.t.start()
        self.assertTrue(self.seen(b"ready"))
        os.write(self.keys_w, b"abc\x1d")
        self.t.join(5)
        self.assertEqual(self.result, [True])
        self.assertTrue(self.relay.live)
        self.assertFalse(self.relay.attached)
        self.relay.send("after")                                # still reachable from the page
        self.assertTrue(until(lambda: "after" in self.log.read_text()))

    def test_when_it_ends_attached_that_is_said(self):
        self.t.start()
        os.write(self.keys_w, b"quit\r")
        self.t.join(8)
        self.assertEqual(self.result, [False])


class Sizing(unittest.TestCase):
    """A chat wraps for the terminal it will be looked at in, not for a fixed 120 columns."""

    def agent(self, size=None):
        out = Path(tempfile.mkdtemp()) / "size"
        script = "stty size > %s; trap 'stty size >> %s' WINCH; while :; do sleep 0.1; done" % (out, out)
        relay = ptyrelay.Relay().start(["bash", "-c", script], size=size)
        self.addCleanup(relay.close)
        return relay, out

    def sizes(self, out):
        return out.read_text().splitlines() if out.exists() else []

    def test_it_starts_at_the_size_of_the_screens_terminal(self):
        real = ptyrelay._terminal_size
        ptyrelay._terminal_size = lambda: (30, 77)
        self.addCleanup(setattr, ptyrelay, "_terminal_size", real)
        _, out = self.agent()
        self.assertTrue(until(lambda: self.sizes(out) == ["30 77"]), self.sizes(out))

    def test_without_a_terminal_it_is_40_by_120(self):
        real = ptyrelay._winsize
        ptyrelay._winsize = lambda fd: b"\0" * 8
        self.addCleanup(setattr, ptyrelay, "_winsize", real)
        self.assertEqual(ptyrelay._terminal_size(), (40, 120))

    def test_a_resize_of_the_screen_reaches_the_chats_in_the_background(self):
        relay, out = self.agent(size=(40, 120))
        self.assertTrue(until(lambda: self.sizes(out) == ["40 120"]))
        host = ptyrelay.ChatHost()
        host.relays["W1"] = relay
        host.resize((24, 60))
        self.assertTrue(until(lambda: self.sizes(out) == ["40 120", "24 60"]), self.sizes(out))

    def test_a_chat_with_a_terminal_attached_is_left_to_it(self):
        relay, out = self.agent(size=(40, 120))
        self.assertTrue(until(lambda: self.sizes(out) == ["40 120"]))
        relay.attached = True
        relay.resize((24, 60))
        time.sleep(0.3)
        self.assertEqual(self.sizes(out), ["40 120"])

    def test_the_screen_tells_the_chats_each_new_size_once_wherever_it_was_resized(self):
        import tui
        sizes, host = [], ptyrelay.ChatHost()
        host.resize = sizes.append
        ui = object.__new__(tui.UI)
        ui.chat_host = host
        real = ptyrelay._terminal_size
        ptyrelay._terminal_size = lambda: self.now
        self.addCleanup(setattr, ptyrelay, "_terminal_size", real)
        for self.now in [(30, 100), (30, 100), (24, 60), (24, 60)]:
            ui.follow_terminal_size()       # the poll tick, which a prompt or dialog does not stop
        self.assertEqual(sizes, [(30, 100), (24, 60)])

    def test_the_poll_tick_follows_the_terminal_size_before_anything_else(self):
        import tui
        calls = []

        class Stop(Exception):
            pass

        def stop():
            raise Stop

        ui = object.__new__(tui.UI)
        ui.follow_terminal_size = lambda: calls.append("follow")
        ui.agent_update_tick = stop         # the next thing `poll` does, so it ends here
        with self.assertRaises(Stop):
            ui.poll()
        self.assertEqual(calls, ["follow"])

    def test_a_resize_key_in_the_main_loop_follows_the_terminal_size(self):
        import curses
        import tui
        calls, keys = [], iter([curses.KEY_RESIZE])

        def getch():
            try:
                return next(keys)
            except StopIteration:
                raise KeyboardInterrupt     # ends `loop` after the one key

        ui = object.__new__(tui.UI)
        ui.scr = types.SimpleNamespace(timeout=lambda ms: None, getch=getch)
        ui.draw = lambda: None
        ui.follow_terminal_size = lambda: calls.append("follow")
        ui.loop()
        self.assertEqual(calls, ["follow"])


class Screen:
    """Just enough terminal to read what an agent left on it: autowrap, CR, LF, cursor moves, erases."""

    def __init__(self, rows, cols):
        self.rows, self.cols, self.r, self.c, self.pend = rows, cols, 0, 0, False
        self.g = [[" "] * cols for _ in range(rows)]

    def feed(self, data):
        d, i = data.decode("utf8", "replace"), 0
        while i < len(d):
            ch = d[i]
            m = re.compile(r"[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]").match(d, i + 2) if d[i:i + 2] == "\x1b[" else None
            if m:
                self.csi(m.group()[:-1], m.group()[-1])
                i = m.end()
                continue
            self.pend = self.pend and ch >= " "
            if ch == "\r":
                self.c = 0
            elif ch == "\n":
                self.down()
            elif ch >= " ":
                if self.pend:
                    self.c = 0
                    self.down()
                self.g[self.r][self.c] = ch
                self.pend = self.c == self.cols - 1
                self.c = min(self.c + 1, self.cols - 1)
            i += 1

    def down(self):
        if self.r == self.rows - 1:
            self.g = self.g[1:] + [[" "] * self.cols]
        else:
            self.r += 1

    def csi(self, p, f):
        n, self.pend = int(p) if p.isdigit() else 1, False
        if f == "A":
            self.r = max(0, self.r - n)
        elif f == "K":
            for x in range(self.cols if p == "2" else self.c, self.cols) if p != "1" else range(self.c + 1):
                self.g[self.r][x] = " "
        elif f == "J" and p == "2":
            self.g = [[" "] * self.cols for _ in range(self.rows)]
        elif f == "H":
            self.r = self.c = 0

    def text(self):
        return "\n".join(("".join(r)).rstrip() for r in self.g).rstrip()


# an inline-redrawing agent: its live region is wrapped lines drawn at the width it believes it
# has; on SIGWINCH it moves up over what it drew, erases it and draws it again
REDRAWER = r"""
import os, signal, time
TEXT = ["w%d " % i + "x" * 90 for i in range(3)] + ["> prompt"]
drawn = 0
def draw(*a):
    global drawn
    w, out = os.get_terminal_size(0).columns, ""
    if drawn:
        out = "\r" + "\x1b[2K\x1b[1A" * (drawn - 1) + "\x1b[2K"
    drawn = sum(max(1, -(-len(t) // w)) for t in TEXT)
    os.write(1, (out + "\r\n".join(TEXT)).encode())
signal.signal(signal.SIGWINCH, draw)
draw()
while True: time.sleep(0.1)
"""


class Redrawing(unittest.TestCase):
    """What a chat that redraws itself in place leaves on a terminal it is looked at in (24x80,
    with five lines of the screen's own above), for the pty it was started at."""
    ROWS, COLS, ABOVE = 24, 80, ["screen %d" % i for i in range(5)]

    def terminal(self):
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", self.ROWS, self.COLS, 0, 0))
        screen = Screen(self.ROWS, self.COLS)
        screen.feed(("\r\n".join(self.ABOVE) + "\r\n").encode())

        def pump():
            while True:
                try:
                    screen.feed(os.read(master, 65536))
                except OSError:
                    return
        threading.Thread(target=pump, daemon=True).start()
        self.addCleanup(lambda: [os.close(fd) for fd in (master, slave)])
        return slave, screen

    def looked_at(self, size):
        """The screen after attaching, a moment later, to a chat started at `size` and drawn at it."""
        slave, screen = self.terminal()
        relay = ptyrelay.Relay().start([sys.executable, "-c", REDRAWER], size=size)
        self.addCleanup(relay.close)
        time.sleep(0.8)
        keys_r, keys_w = os.pipe()
        self.addCleanup(os.close, keys_w)
        saved = sys.stdin, sys.stdout
        sys.stdin, sys.stdout = os.fdopen(keys_r, "r"), os.fdopen(slave, "w", closefd=False)
        try:
            t = threading.Thread(target=relay.attach, daemon=True)
            t.start()
            time.sleep(0.8)
            os.write(keys_w, ptyrelay.DETACH)
            t.join(3)
        finally:
            mine, sys.stdin, sys.stdout = sys.stdin, saved[0], saved[1]
            mine.close()
        return screen.text()

    def test_a_chat_started_at_120_columns_wraps_after_attach_as_one_started_at_the_terminals_width(self):
        wide, fitted = self.looked_at((40, 120)), self.looked_at((self.ROWS, self.COLS))
        frame = lambda text: text[text.index("w0"):]
        self.assertEqual(frame(wide), frame(fitted))
        self.assertEqual(len(frame(wide).splitlines()), 7)  # 3 lines of 94 columns wrap in two, then the prompt

    def test_attaching_redraws_over_the_rows_above_it_either_way(self):
        # the redraw erases as many lines as it drew, which on a screen it never drew on are the
        # screen's own: this is the same at 120 columns and at 80, so it is not the width
        left = lambda text: [l for l in text.splitlines() if l.startswith("screen")]
        wide, fitted = left(self.looked_at((40, 120))), left(self.looked_at((self.ROWS, self.COLS)))
        self.assertLess(len(wide), len(self.ABOVE))
        self.assertLess(len(fitted), len(self.ABOVE))


class Leaving(unittest.TestCase):
    """Ending a chat is a SIGTERM, as ctrl-c is to a claude in a terminal, with a SIGKILL behind
    it; the screen's exit waits, so the backstop is not left to a thread that dies with it."""

    def setUp(self):
        self.pids = Path(tempfile.mkdtemp()) / "pids"
        self.addCleanup(self.sweep)

    def started(self):
        return [int(x) for x in self.pids.read_text().split()] if self.pids.exists() else []

    def sweep(self):
        for p in self.started():
            try:
                os.kill(p, 9)
            except OSError:
                pass

    def gone(self, pid):
        try:
            return Path("/proc/%d/stat" % pid).read_text().rsplit(")", 1)[1].split()[0] == "Z"
        except OSError:
            return True

    def test_sigterm_is_enough_for_an_agent_that_stops_what_it_started(self):
        # what claude does on SIGTERM: stop its tool process, then exit
        script = "sleep 600 & echo $$ $! >> %s; trap 'kill $!; exit' TERM; wait" % self.pids
        relay = ptyrelay.Relay().start(["bash", "-c", script])
        self.assertTrue(until(lambda: len(self.started()) == 2))
        t = time.time()
        relay.close(wait=True)
        self.assertLess(time.time() - t, 1.5)       # before the SIGKILL backstop's two seconds
        self.assertTrue(all(self.gone(p) for p in self.started()), self.started())

    def test_close_all_returns_only_when_an_agent_that_ignores_sigterm_is_gone(self):
        host = ptyrelay.ChatHost()
        script = "trap '' HUP TERM; echo $$ >> %s; while :; do sleep 1; done" % self.pids
        host.ensure("W1", lambda: (["bash", "-c", script], None, None))
        host.ensure("project", lambda: (["bash", "-c", script], None, None))
        self.assertTrue(until(lambda: len(self.started()) == 2))
        host.close_all()
        self.assertTrue(all(self.gone(p) for p in self.started()), self.started())
        self.assertEqual(host.live_scopes(), [])


class Idling(unittest.TestCase):
    def test_only_a_chat_nobody_is_using_is_closed_and_a_dead_one_is_forgotten(self):
        host = ptyrelay.ChatHost()
        self.addCleanup(host.close_all)
        mk = lambda: (["bash", "-c", "echo ready; sleep 30"], None, None)
        quiet, busy, looked, dead = (host.ensure(n, mk) for n in ("quiet", "busy", "looked", "dead"))
        self.assertTrue(until(lambda: all(r.first_out for r in (quiet, busy, looked, dead))))
        looked.attached = True
        dead.close(wait=True)
        later = time.time() + 100
        busy.last_out = later - 1
        self.assertEqual(host.reap_idle(60, now=later), ["quiet"])
        self.assertFalse(quiet.live)
        self.assertTrue(busy.live and looked.live)
        self.assertEqual(sorted(host.relays), ["busy", "looked"])
        busy.outbox.put("typed, not yet sent")
        self.assertEqual(host.reap_idle(60, now=later + 1000), [], "a chat with something waiting is not idle")
        looked.attached = False
        self.assertEqual(host.reap_idle(60, now=later + 1000), ["looked"])
        self.assertFalse(looked.send("too late"), "a closed chat takes no message")


class Recording(unittest.TestCase):
    """A chat nobody is attached to leaves a record: what happened to it, and its last words."""

    def setUp(self):
        self.log = Path(tempfile.mkdtemp()) / "log"
        self.log.write_text("")
        self.env = dict(os.environ, LOG=str(self.log))
        self.said = []

    def start(self, script=AGENT):
        r = ptyrelay.Relay(lambda t, d="": self.said.append((t, d))).start(["bash", "-c", script], env=self.env)
        self.addCleanup(r.close)
        return r

    def has(self, needle):
        return any(needle in t or needle in d for t, d in self.said)

    def test_start_a_message_its_typing_and_the_exit_are_recorded_with_its_last_output(self):
        r = self.start()
        self.assertTrue(until(lambda: self.has("start pid=")))
        r.send("hello")
        self.assertTrue(until(lambda: self.has("queued")))
        self.assertTrue(until(lambda: self.has("typed 5 characters")))
        r.send("quit")
        self.assertTrue(until(lambda: self.has("exit rc=3")))
        self.assertTrue(self.has("nobody closed it"))
        self.assertTrue(self.has("heard: hello"))       # the agent's last words, beneath the exit
        self.assertFalse(r.send("late"))
        self.assertTrue(self.has("refused: the agent is not running"))

    def test_an_agent_that_never_wrote_says_no_output_and_a_bare_close_says_closed(self):
        r = self.start("sleep 30")
        self.assertTrue(until(lambda: self.has("start pid=")))
        r.close(wait=True)
        self.assertTrue(until(lambda: self.has("exit signal")))
        line = [t for t, _ in self.said if t.startswith("exit ")][0]
        self.assertIn("no output (closed)", line)
        self.assertNotIn("-1s", line)

    def test_a_message_the_agent_died_before_taking_is_logged_as_dropped(self):
        r = self.start("sleep 0.3; exit 4")
        r.send("too late")
        self.assertTrue(until(lambda: self.has("exit rc=4")))
        self.assertTrue(until(lambda: self.has("dropped")))

    def test_a_close_says_who_closed_it(self):
        r = self.start()
        self.assertTrue(until(lambda: r.first_out))
        r.close(why="idle 1800s")
        self.assertTrue(until(lambda: self.has("exit signal 15")))
        self.assertTrue(self.has("closed: idle 1800s"))

    def test_silence_after_a_message_is_logged_once_with_the_last_screen(self):
        old = ptyrelay.STALL
        ptyrelay.STALL = 1.0
        self.addCleanup(setattr, ptyrelay, "STALL", old)
        r = self.start("echo ready; cat >/dev/null")          # takes the message and says nothing
        r.send("anyone there")
        self.assertTrue(until(lambda: self.has("quiet for"), 20))
        self.assertTrue(self.has("bytes of output since it was typed"))
        self.assertTrue(self.has("ready"))
        time.sleep(1.5)
        self.assertEqual(sum("quiet for" in t for t, _ in self.said), 1)

    def test_the_host_binds_each_chat_to_its_scope_and_writes_the_files(self):
        root = tempfile.mkdtemp()
        old = os.environ.get("DFS_RUN_DIR")
        os.environ["DFS_RUN_DIR"] = root
        self.addCleanup(lambda: os.environ.pop("DFS_RUN_DIR") if old is None else os.environ.update(DFS_RUN_DIR=old))
        host = ptyrelay.ChatHost()
        host.ensure("project", lambda: (["bash", "-c", AGENT], self.env, None))
        host.close("project", wait=True, why="new chat")
        f = Path(root) / "chat" / "project.log"
        self.assertTrue(until(lambda: f.exists() and "closed: new chat" in f.read_text()))
        self.assertIn("start pid=", f.read_text())


class Hosting(unittest.TestCase):
    def test_many_run_at_once_one_per_scope_and_a_dead_one_is_started_again(self):
        host = ptyrelay.ChatHost()
        self.addCleanup(host.close_all)
        made = []
        mk = lambda tag: (lambda: (made.append(tag), (["bash", "-c", "echo ready; sleep 30"], None, None))[1])
        a = host.ensure("W1", mk("a"))
        b = host.ensure("W2", mk("b"))
        self.assertIs(host.ensure("W1", mk("again")), a)
        self.assertEqual(made, ["a", "b"])
        self.assertEqual(sorted(host.live_scopes()), ["W1", "W2"])
        host.close("W1")
        self.assertIsNone(host.get("W1"))
        self.assertIs(host.get("W2"), b)
        self.assertIsNot(host.ensure("W1", mk("c")), a)
        self.assertEqual(made, ["a", "b", "c"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
