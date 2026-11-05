#!/usr/bin/env python3
"""ttyio.py: the prompts of `run.sh --open` and `run.sh --review` edit the line.

They run their Python as a heredoc, so they read the keyboard from /dev/tty. Left arrow used to
arrive as the text `^[[D` (it was saved into a task's name). These tests drive the real
`run.sh` on a pty and press the keys.

Run:  python3 <scripts>/test_ttyio.py
"""
import os
import pty
import select
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

LEFT = b"\x1b[D"
TASK = "# W1 · first\n\n## Goal\n\ng\n\n## Tree\n\n## Log\n"


class Pty:
    """A process on a pty: wait for what it asked, send keys, read until it exits."""

    def __init__(self, argv, cwd):
        self.out = b""
        self.seen = 0
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.chdir(cwd)
            os.execv(argv[0], argv)

    def drain(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            if select.select([self.fd], [], [], 0.05)[0]:
                try:
                    chunk = os.read(self.fd, 4096)
                except OSError:
                    return
                if not chunk:
                    return
                self.out += chunk

    def expect(self, prompt, timeout=60):
        """Wait until `prompt` has been drawn since the last expect. A slow machine only waits longer."""
        end = time.time() + timeout
        while prompt not in self.out[self.seen:]:
            if time.time() > end:
                raise AssertionError("never saw %r; it drew %r" % (prompt, self.out[self.seen:]))
            self.drain(0.1)
        self.seen = len(self.out)

    def send(self, *keys):
        """Keys go one at a time; the terminal echoes each one back before the next is sent."""
        for k in keys:
            os.write(self.fd, k)
            self.drain(0.15)

    def finish(self):
        self.drain(1.0)
        try:
            os.waitpid(self.pid, 0)
        except ChildProcessError:
            pass
        os.close(self.fd)


class Prompts(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / ".dfs" / "items").mkdir(parents=True)
        (self.root / ".dfs" / "ROADMAP.md").write_text("# Roadmap\n")
        (self.root / ".dfs" / "items" / "W1.md").write_text(TASK)
        subprocess.run("git init -q && git add -A && git -c user.name=t -c user.email=t@t commit -qm init",
                       shell=True, cwd=self.root, check=True)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_open_left_arrow_edits_the_name(self):
        p = Pty([str(HERE / "run.sh"), "--open", "T1"], self.root)
        p.expect(b"short name: ")
        p.send(b"abd", LEFT, b"c\r")
        p.expect(b"lone '.' ends it")
        p.send(b"goal\r", b".\r")
        p.expect(b"[y/N]: ")
        p.send(b"n\r")
        p.expect(b"dfs_run: wrote")
        p.finish()
        text = (self.root / ".dfs" / "items" / "T1.md").read_text()
        self.assertTrue(text.startswith("# T1 · abcd\n"), text[:40])
        self.assertNotIn(b"^[[D", p.out)

    def test_open_home_and_delete_edit_the_goal_line(self):
        p = Pty([str(HERE / "run.sh"), "--open", "T2"], self.root)
        p.expect(b"short name: ")
        p.send(b"name\r")
        p.expect(b"lone '.' ends it")
        p.send(b"xyz", b"\x01", b"\x1b[3~", b"\r", b".\r")      # ^A (home), then delete
        p.expect(b"[y/N]: ")
        p.send(b"n\r")
        p.expect(b"dfs_run: wrote")
        p.finish()
        text = (self.root / ".dfs" / "items" / "T2.md").read_text()
        self.assertIn("\n\nyz\n", text)

    def test_review_left_arrow_edits_the_node_id(self):
        p = Pty([str(HERE / "run.sh"), "--review", "W1"], self.root)
        p.expect(b"its id, or empty to finish: ")
        p.send(b"W1.9", LEFT, b"\x7f", b"\r")             # "W1.9", left, backspace: "W19"
        p.expect(b"(no node W1.W19)")
        p.send(b"\r")                      # an empty id finishes the review
        p.finish()
        self.assertIn(b"(no node W1.W19)", p.out)


if __name__ == "__main__":
    unittest.main()
