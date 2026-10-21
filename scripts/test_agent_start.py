#!/usr/bin/env python3
"""A message sent to a chat that is still starting reaches the model: ptyrelay waits for a REAL
codex, opencode and kiro-cli (whichever this machine has) to reach their prompt, and answers the
first-launch question codex and kiro ask, where the 1s of quiet it used to go on typed into the
wrong place. Only the model is faked (test_agent_models.py, test_kiro_model.py); the message is
sent the moment the agent is launched, in a home and a folder it has never met.

Run (with TMPDIR inside the repo when /tmp is noexec):  python3 roadmap-dfs/scripts/test_agent_start.py
"""
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ptyrelay  # noqa: E402
from test_agent_interrupt import CODEX, KIRO, OPENCODE  # noqa: E402
from test_agent_models import FakeCodexModel, FakeOpenAIModel, start_until  # noqa: E402
from test_kiro_model import FakeKiroModel  # noqa: E402


class FreshStart(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="dfs-start-", dir=os.environ.get("TMPDIR") or None))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.env = dict(os.environ, HOME=str(self.home), TERM="xterm-256color", TMPDIR=os.environ.get("TMPDIR", str(self.home)),
                        XDG_DATA_HOME=str(self.home / "data"), XDG_CONFIG_HOME=str(self.home / "cfg"),
                        XDG_CACHE_HOME=str(self.home / "cache"), XDG_STATE_HOME=str(self.home / "state"))
        for k in ("CODEX_HOME", "KIRO_HOME", "CLAUDE_CODE_SESSION_ID"):
            self.env.pop(k, None)

    def sent_at_launch_is_heard(self, argv, model, agent, env):
        def start():
            relay = ptyrelay.Relay(agent=agent).start(argv, env=dict(self.env, **env), cwd=str(self.home), size=(40, 120))
            self.addCleanup(relay.close)
            relay.send("HELLO-FRESH")                       # no settling: the chat is still starting
            return relay

        heard = lambda relay: [b for _, b in model.requests if b"HELLO-FRESH" in b]
        relay, ok = start_until(start(), heard, start, 90)  # an opencode that hangs at start-up is started again
        self.assertTrue(ok, "the model was asked with the message; the agent's screen:\n%s" % ptyrelay.chatlog.screen_text(relay.tail))
        self.assertTrue(relay.live, "the agent is still there")

    @unittest.skipUnless(KIRO, "no kiro-cli here")
    def test_kiro_accepts_its_trust_all_question_and_takes_the_message(self):
        with FakeKiroModel(say="DONE") as model:
            self.sent_at_launch_is_heard([KIRO, "chat", "--v3", "--trust-all-tools"], model, "kiro", dict(KIRO_HOME=str(self.home), **model.env()))
        settings = self.home / "settings" / "cli.json"          # kiro writes some of its own; "don't ask again" is not among them
        self.assertNotIn("disableTrustAllConfirmation", settings.read_text() if settings.exists() else "")

    @unittest.skipUnless(CODEX, "no codex here")
    def test_codex_trusts_a_folder_it_has_not_met_and_takes_the_message(self):
        with FakeCodexModel(say="DONE") as model:
            (self.home / "codex").mkdir()
            self.sent_at_launch_is_heard([CODEX, "--dangerously-bypass-approvals-and-sandbox", *model.args()], model, "codex",
                                         dict(CODEX_HOME=str(self.home / "codex"), **model.env()))

    @unittest.skipUnless(OPENCODE, "no opencode here")
    def test_opencode_is_waited_for_until_its_prompt_is_drawn(self):
        with FakeOpenAIModel(say="DONE") as model:
            self.sent_at_launch_is_heard([OPENCODE], model, "opencode", model.env())


if __name__ == "__main__":
    unittest.main()
