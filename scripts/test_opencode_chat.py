#!/usr/bin/env python3
"""An opencode conversation crosses between the web page and the screen, with a REAL opencode: a turn the
page runs (`opencode run`, `opencode run --session`) and the screen's chat (`run.sh --opencode --chat`, which
opens `opencode --session <id>` on a pty) read and write one session in opencode.db. Only the model is faked
(test_agent_models.py), so no login is needed.

Run (with TMPDIR inside the repo when /tmp is noexec):  python3 roadmap-dfs/scripts/test_opencode_chat.py
"""
import glob
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import paths as dfs_paths   # noqa: E402
import chat as dfs_chat     # noqa: E402
import ptyrelay             # noqa: E402
from test_agent_models import FakeOpenAIModel  # noqa: E402

OPENCODE = os.environ.get("TEST_OPENCODE") or next(iter(sorted(glob.glob("/nix/store/*-opencode-1.18*/bin/opencode"))[::-1]), None) or shutil.which("opencode")


def until(fn, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.1)
    return False


@unittest.skipUnless(OPENCODE, "no opencode here")
class OpencodeCrossing(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(dir=os.environ.get("TMPDIR") or None))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / ".dfs" / "items").mkdir(parents=True)
        (self.root / ".dfs" / "ROADMAP.md").write_text("# Roadmap\n")
        subprocess.run("git init -q -b main && git add -A && git -c user.name=t -c user.email=t@t commit -qm x",
                       shell=True, cwd=self.root, check=True)
        self.home = self.root / "home"
        self.home.mkdir()
        self.model = FakeOpenAIModel(say="FAKE-ANSWER")
        self.model.__enter__()
        self.addCleanup(self.model.__exit__, None, None, None)
        self.env = dict(os.environ, HOME=str(self.home), OPENCODE_CMD=OPENCODE, TERM="xterm-256color", TMPDIR=os.environ.get("TMPDIR", str(self.root)),
                        XDG_DATA_HOME=str(self.home / "data"), XDG_CONFIG_HOME=str(self.home / "cfg"), XDG_CACHE_HOME=str(self.home / "cache"),
                        XDG_STATE_HOME=str(self.home / "state"), DFS_RUN_DIR=str(self.root / ".dfs" / "runs"), AGENT_NIXPKGS_REV="none", **self.model.env())
        for k in ("CLAUDE_CODE_SESSION_ID", "CHAT_SID", "CHAT_RESUME", "CHAT_SHARE"):
            self.env.pop(k, None)
        self._saved = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        self.addCleanup(self.restore)
        Path(dfs_chat.agent_file()).parent.mkdir(parents=True, exist_ok=True)
        self._wr = dfs_paths.work_root
        dfs_paths.work_root = lambda: self.root
        dfs_paths._TAG_CACHE.clear()
        self.addCleanup(setattr, dfs_paths, "work_root", self._wr)
        Path(dfs_chat.agent_file()).parent.mkdir(parents=True, exist_ok=True)
        Path(dfs_chat.agent_file()).write_text("opencode\n")
        self.chats = dfs_chat.Chats(on_change=lambda: None)

    def restore(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for k in ("CHAT_SID", "CHAT_RESUME", "CHAT_SHARE"):
            os.environ.pop(k, None)

    # ── the page ────────────────────────────────────────────────────────────────────────
    def page_says(self, text):
        sid = self.chats.sid("project")
        if sid:         # the screen's opencode takes a moment to go, and the page will not send while it is there
            self.assertTrue(until(lambda: not dfs_chat.running_elsewhere(sid, agent="opencode"), 20), "the screen's opencode went")
        self.chats.send("project", text)
        self.assertTrue(until(lambda: not self.chats.state("project")["busy"], 60), "the page's turn finished")
        return self.chats.state("project")

    def sessions(self):
        with sqlite3.connect(dfs_chat.opencode_db()) as db:
            return db.execute("select count(*) from session").fetchone()[0]

    def texts(self):
        return [(m["role"], m["text"]) for m in self.chats.state("project")["messages"]]

    def heard(self, text):
        return [b for _, b in self.model.requests if text.encode() in b]

    # ── the screen ──────────────────────────────────────────────────────────────────────
    def screen(self, **env):
        """What `c` does: run.sh --opencode --chat project on a pty, in the background."""
        relay = ptyrelay.Relay(agent="opencode").start([str(HERE / "run.sh"), "--opencode", "--chat", "project"],
                                                    env=dict(os.environ, CHAT_SHARE="1", **env), cwd=str(self.root), size=(40, 120))
        self.addCleanup(relay.close)
        return relay

    def type_on_the_screen(self, relay, text):
        self.assertTrue(until(lambda: relay.first_out, 60), "the screen's opencode drew itself")
        relay.send(text)
        self.assertTrue(until(lambda: self.heard(text), 60), "opencode on the screen asked the model with %r" % text)
        self.assertTrue(until(lambda: ("you", text) in self.texts(), 60), "the page reads %r from the same session" % text)

    def test_a_conversation_the_page_began_is_resumed_on_the_screen_and_back(self):
        st = self.page_says("PAGE-ONE")
        self.assertEqual([m["text"] for m in st["messages"]][-1], "FAKE-ANSWER", "a real opencode run answered: %s" % st["messages"])
        rec = json.loads(self.chats.path("project").read_text())
        self.assertEqual(rec["agent"], "opencode")
        sid, resume = dfs_chat.claim("project", "opencode")
        self.assertEqual((sid, resume), (rec["sid"], True))
        relay = self.screen(CHAT_SID=sid, CHAT_RESUME="1")
        self.type_on_the_screen(relay, "SCREEN-TWO")
        self.assertTrue(until(lambda: ("agent", "FAKE-ANSWER") in self.texts()[2:], 30))
        relay.close()
        self.assertTrue(until(lambda: not relay.live, 10))
        self.page_says("PAGE-THREE")
        last = self.heard("PAGE-THREE")[-1].decode()
        self.assertIn("PAGE-ONE", last, "the page's resumed turn carries the first message")
        self.assertIn("SCREEN-TWO", last, "and what was typed on the screen")
        self.assertEqual([t for r, t in self.texts() if r == "you"], ["PAGE-ONE", "SCREEN-TWO", "PAGE-THREE"])
        self.assertEqual(self.sessions(), 1, "one session in opencode.db holds the whole conversation")

    def test_a_message_sent_while_the_opening_turn_runs_waits_for_opencode_and_reaches_it(self):
        self.model.hold = 30            # a slow opening turn, longer than the 25s the typist waits for a prompt
        relay = self.screen(CHAT_PROMPT="take your time over the summary")
        relay.send("EARLY-MSG")         # the author types before the summary is done
        time.sleep(5)
        self.assertFalse(self.heard("EARLY-MSG"), "nothing is typed into the headless opening turn")
        self.assertTrue(until(lambda: self.heard("EARLY-MSG"), 120),
                        "opencode on the screen asked the model with the early message; its screen:\n%s" % ptyrelay.chatlog.screen_text(relay.tail))
        self.assertTrue(until(lambda: ("you", "EARLY-MSG") in self.texts(), 20), "and the page reads it from the same session")

    def test_a_conversation_the_screen_began_is_read_and_continued_by_the_page(self):
        self.assertEqual(dfs_chat.claim("project", "opencode"), ("", False))
        relay = self.screen()
        self.assertTrue(until(lambda: self.chats.path("project").exists(), 60), "the opening turn was recorded")
        rec = json.loads(self.chats.path("project").read_text())
        self.assertEqual(rec["agent"], "opencode")
        self.type_on_the_screen(relay, "SCREEN-ONE")
        self.assertTrue(until(lambda: ("agent", "FAKE-ANSWER") in self.texts()[-2:], 30), "the screen's opencode finished answering")
        relay.close()
        self.assertTrue(until(lambda: not relay.live, 10))
        self.page_says("PAGE-TWO")
        last = self.heard("PAGE-TWO")[-1].decode()
        self.assertIn("SCREEN-ONE", last)
        # opencode may write the message to its database after the turn ends, more so on a loaded machine
        until(lambda: [t for r, t in self.texts() if r == "you"][-2:] == ["SCREEN-ONE", "PAGE-TWO"], 10)
        self.assertEqual([t for r, t in self.texts() if r == "you"][-2:], ["SCREEN-ONE", "PAGE-TWO"])
        self.assertEqual(json.loads(self.chats.path("project").read_text())["sid"], rec["sid"], "still the one session")
        self.assertEqual(self.sessions(), 1)


if __name__ == "__main__":
    unittest.main()
