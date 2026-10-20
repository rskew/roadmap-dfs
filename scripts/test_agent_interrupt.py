#!/usr/bin/env python3
"""The page's Stop reaches each agent's real terminal: ptyrelay's interrupt keys stop an answer in
a REAL codex, opencode and kiro-cli (whichever this machine has), each against a stand-in model
that sees when the agent hangs up on an answer it was holding (test_agent_models.py,
test_kiro_model.py). Only the model is faked.

Run:  python3 roadmap-dfs/scripts/test_agent_interrupt.py
"""
import glob
import json
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
from test_agent_models import FakeCodexModel, FakeOpenAIModel  # noqa: E402
from test_kiro_model import FakeKiroModel  # noqa: E402


def newest(pattern):
    return next(iter(sorted(glob.glob(pattern))[::-1]), None)


CODEX = os.environ.get("TEST_CODEX") or newest("/nix/store/*-codex-0.162*/bin/codex") or shutil.which("codex")
OPENCODE = os.environ.get("TEST_OPENCODE") or newest("/nix/store/*-opencode-1.18*/bin/opencode") or shutil.which("opencode")
KIRO = os.environ.get("TEST_KIRO") or newest("/nix/store/*-kiro-cli-unwrapped-*/bin/kiro-cli-chat")


def until(fn, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.1)
    return False


class Interrupt(unittest.TestCase):
    def setUp(self):
        # inside the repo's own temp dir when /tmp is noexec: opencode unpacks a library there
        self.home = Path(tempfile.mkdtemp(prefix="dfs-interrupt-"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.env = dict(os.environ, HOME=str(self.home), TERM="xterm-256color", TMPDIR=os.environ.get("TMPDIR", str(self.home)),
                        XDG_DATA_HOME=str(self.home / "data"), XDG_CONFIG_HOME=str(self.home / "cfg"),
                        XDG_CACHE_HOME=str(self.home / "cache"), XDG_STATE_HOME=str(self.home / "state"))
        for k in ("CODEX_HOME", "KIRO_HOME", "CLAUDE_CODE_SESSION_ID"):
            self.env.pop(k, None)

    def hear(self, model, text, turn=False):
        """The requests carrying `text`; with `turn`, not the ones opencode makes to title the conversation, which
        carry the user's message too and come first when the machine is loaded."""
        return [b for _, b in model.requests if text.encode() in b and not (turn and b"Generate a title" in b)]

    def stop_a_slow_answer(self, argv, model, agent, env=None, settle=2, before=None):
        relay = ptyrelay.Relay(agent=agent).start(argv, env=dict(self.env, **(env or {})), cwd=str(self.home), size=(40, 120))
        self.addCleanup(relay.close)
        self.assertTrue(until(lambda: relay.first_out, 30))
        time.sleep(settle)                                    # a TUI starting up is quiet now and then, and takes no message
        relay.send("please take your time")
        self.assertTrue(until(lambda: self.hear(model, "take your time", turn=True), 60), "the agent asked the model for the answer")
        time.sleep(1.5)                                       # it is part-way through the answer
        self.assertFalse(model.cancelled.is_set())
        if before:
            before(relay)
        self.assertEqual(relay.interrupt(), 0)
        return relay

    def and_goes_on(self, relay, model):
        """Stopped, the agent is still there and answers what is sent next."""
        model.release.set()
        self.assertTrue(relay.live)
        relay.send("and now quickly")
        self.assertTrue(until(lambda: self.hear(model, "and now quickly"), 30), "the agent took the next message")

    @unittest.skipUnless(KIRO, "no kiro-cli here")
    def test_kiro_stops_on_one_escape(self):
        (self.home / "settings").mkdir()
        (self.home / "settings" / "cli.json").write_text(json.dumps({"chat.disableTrustAllConfirmation": True}))
        with FakeKiroModel(say="DONE") as model:
            relay = self.stop_a_slow_answer([KIRO, "chat", "--v3", "--trust-all-tools"], model, "kiro", dict(KIRO_HOME=str(self.home), **model.env()), settle=6)
            # kiro keeps its connection open; what shows it stopped is the turn ending in its session
            # while the model is still holding the answer
            ended = lambda: any('"turn_end"' in p.read_text() for p in self.home.glob("**/messages.jsonl"))
            self.assertTrue(until(ended, 10), "kiro ended the turn it was in")
            self.and_goes_on(relay, model)

    @unittest.skipUnless(CODEX, "no codex here")
    def test_codex_stops_on_one_escape(self):
        (self.home / "codex").mkdir()
        (self.home / "codex" / "config.toml").write_text('[projects."%s"]\ntrust_level = "trusted"\n' % self.home)
        with FakeCodexModel(say="DONE") as model:
            relay = self.stop_a_slow_answer([CODEX, "--dangerously-bypass-approvals-and-sandbox", *model.args()], model, "codex",
                                            dict(CODEX_HOME=str(self.home / "codex"), **model.env()), settle=4)
            self.assertTrue(model.cancelled.wait(10), "codex hung up on the answer it was given")
            self.and_goes_on(relay, model)

    @unittest.skipUnless(OPENCODE, "no opencode here")
    def test_opencode_needs_a_second_escape(self):
        def one_escape(relay):                                # a single ESC only arms its interrupt; the table in ptyrelay says two
            os.write(relay.fd, b"\x1b")
            self.assertFalse(model.cancelled.wait(3), "it is still answering after one ESC")
        with FakeOpenAIModel(say="DONE") as model:
            relay = self.stop_a_slow_answer([OPENCODE], model, "opencode", model.env(), settle=8, before=one_escape)
            self.assertTrue(model.cancelled.wait(10), "opencode hung up on the answer it was given")
            self.and_goes_on(relay, model)


if __name__ == "__main__":
    unittest.main()
