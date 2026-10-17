#!/usr/bin/env python3
"""A message sent while the screen's kiro chat runs its slow headless opening turn waits for the
interactive kiro and reaches the model, with a REAL kiro-cli (`run.sh --kiro --chat`, which runs
`kiro-cli chat --no-interactive` first and then `--resume-id`). Only the model is faked
(test_kiro_model.py), so no login is needed. test_codex_chat and test_opencode_chat do the same
for their agents.

Run (with TMPDIR inside the repo when /tmp is noexec):  python3 roadmap-dfs/scripts/test_kiro_chat.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import paths as dfs_paths   # noqa: E402
import chat as dfs_chat     # noqa: E402
import ptyrelay             # noqa: E402
from test_agent_interrupt import KIRO, until  # noqa: E402
from test_kiro_model import FakeKiroModel  # noqa: E402


@unittest.skipUnless(KIRO, "no kiro-cli here")
class KiroOpening(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(dir=os.environ.get("TMPDIR") or None))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / ".dfs" / "items").mkdir(parents=True)
        (self.root / ".dfs" / "ROADMAP.md").write_text("# Roadmap\n")
        subprocess.run("git init -q -b main && git add -A && git -c user.name=t -c user.email=t@t commit -qm x",
                       shell=True, cwd=self.root, check=True)
        self.home = self.root / "home"
        self.home.mkdir()
        self.model = FakeKiroModel(say="FAKE-ANSWER")
        self.model.__enter__()
        self.addCleanup(self.model.__exit__, None, None, None)
        self.env = dict(os.environ, HOME=str(self.home), KIRO_CMD=KIRO, TERM="xterm-256color",
                        DFS_RUN_DIR=str(self.root / ".dfs" / "runs"), AGENT_NIXPKGS_REV="none", **self.model.env())
        for k in ("KIRO_HOME", "CLAUDE_CODE_SESSION_ID", "CHAT_SID", "CHAT_RESUME", "CHAT_SHARE"):
            self.env.pop(k, None)
        self._saved = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        self.addCleanup(self.restore)
        self._wr = dfs_paths.work_root
        dfs_paths.work_root = lambda: self.root
        dfs_paths._TAG_CACHE.clear()
        self.addCleanup(setattr, dfs_paths, "work_root", self._wr)
        Path(dfs_chat.agent_file()).parent.mkdir(parents=True, exist_ok=True)
        Path(dfs_chat.agent_file()).write_text("kiro\n")
        self.chats = dfs_chat.Chats(on_change=lambda: None)

    def restore(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def heard(self, text):
        return [b for _, b in self.model.requests if text.encode() in b]

    def test_a_message_sent_while_the_opening_turn_runs_waits_for_kiro_and_reaches_it(self):
        self.model.hold = 12            # the opening turn ("take your time") is slow, as a real summary is
        relay = ptyrelay.Relay(agent="kiro").start([str(HERE / "run.sh"), "--kiro", "--chat", "project"],
                                                   env=dict(os.environ, CHAT_SHARE="1", CHAT_PROMPT="take your time over the summary"),
                                                   cwd=str(self.root), size=(40, 120))
        self.addCleanup(relay.close)
        relay.send("EARLY-MSG")         # the author types before the summary is done
        self.assertTrue(until(lambda: relay.opening, 30), "run.sh is running the opening turn")
        self.assertFalse(self.heard("EARLY-MSG"), "nothing is typed into the headless opening turn")
        self.assertTrue(until(lambda: self.heard("EARLY-MSG"), 120),
                        "kiro on the screen asked the model with the early message; its screen:\n%s" % ptyrelay.chatlog.screen_text(relay.tail))
        self.assertTrue(until(lambda: ("you", "EARLY-MSG") in [(m["role"], m["text"]) for m in self.chats.state("project")["messages"]], 30),
                        "and the page reads it from the same session")
        self.assertEqual(json.loads(self.chats.path("project").read_text())["agent"], "kiro")


if __name__ == "__main__":
    unittest.main()
