#!/usr/bin/env python3
"""ptyrelay: agents that run in the background, are typed into by the page, can be looked at
from the terminal (attach) and left running (detach), and a host that keeps many of them.

Run:  python3 roadmap-dfs/scripts/test_ptyrelay.py
"""
import os
import select
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ptyrelay  # noqa: E402

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
