#!/usr/bin/env python3
"""What the relay assumes of a claude chat's first message, checked with a REAL claude (the build in
/nix/store, or TEST_CLAUDE) on a scratch HOME. Only the model is faked: a local server answers claude's
`/v1/messages`, so no login is needed and nothing is spent. Skipped where no claude is installed.

The tests here start a fresh claude only. That one resumed from a saved session takes the message as well, and
so does a message with its Enter in one write: both were checked by hand, at start delays of 0.5s to 8s, and are
not in this file.

Run (with TMPDIR inside the repo when /tmp is noexec):  python3 roadmap-dfs/scripts/test_claude_chat.py
"""
import glob
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
import http.server
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ptyrelay  # noqa: E402
from test_agent_interrupt import until  # noqa: E402


def newest_claude():
    found = glob.glob("/nix/store/*-claude-code-2.*/bin/claude")
    found.sort(key=lambda p: [int(n) for n in re.findall(r"claude-code-([\d.]+)/", p)[0].split(".")])
    return os.environ.get("TEST_CLAUDE") or shutil.which("claude") or (found[-1] if found else None)


CLAUDE = newest_claude()
KEY = "sk-ant-fake-0123456789abcdefghijklmnop"


class FakeClaudeModel:
    """Answers each `/v1/messages` call with "fake reply", after `delay` seconds."""

    def __init__(self, delay=0.0):
        self.delay = delay
        self.requests = []
        model = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("content-length") or 0))
                self.send_response(200)
                if "/messages" not in self.path or b'"stream"' not in body:
                    self.send_header("content-type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"input_tokens":1}')
                    return
                model.requests.append(body)
                time.sleep(model.delay)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                for kind, data in (
                        ("message_start", {"message": {"id": "m", "type": "message", "role": "assistant", "model": "claude-sonnet-4-5",
                                                       "content": [], "stop_reason": None, "usage": {"input_tokens": 1, "output_tokens": 1}}}),
                        ("content_block_start", {"index": 0, "content_block": {"type": "text", "text": ""}}),
                        ("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": "fake reply"}}),
                        ("content_block_stop", {"index": 0}),
                        ("message_delta", {"delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}}),
                        ("message_stop", {})):
                    self.wfile.write(("event: %s\ndata: %s\n\n" % (kind, json.dumps(dict(data, type=kind)))).encode())

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def url(self):
        return "http://127.0.0.1:%d" % self.server.server_address[1]

    def close(self):
        self.server.shutdown()


@unittest.skipUnless(CLAUDE, "no claude here")
class FirstMessage(unittest.TestCase):
    def start(self, delay=0.0):
        self.model = FakeClaudeModel(delay)
        self.addCleanup(self.model.close)
        home = Path(tempfile.mkdtemp(dir=os.environ.get("TMPDIR") or None))
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        (home / ".claude").mkdir()
        (home / ".claude.json").write_text(json.dumps({
            "hasCompletedOnboarding": True, "theme": "dark", "customApiKeyResponses": {"approved": [KEY[-20:]], "rejected": []},
            "projects": {str(home): {"hasTrustDialogAccepted": True}}}))
        (home / ".claude" / "settings.json").write_text(json.dumps({"skipDangerousModePermissionPrompt": True}))
        env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "AI_AGENT", "ANTHROPIC"))}
        env.update(HOME=str(home), ANTHROPIC_API_KEY=KEY, ANTHROPIC_BASE_URL=self.model.url(), TERM="xterm-256color",
                   DISABLE_AUTOUPDATER="1")
        self.logged = []
        self.relay = ptyrelay.Relay(agent="claude", log=lambda text, detail="": self.logged.append(text))
        self.relay.start([CLAUDE, "--dangerously-skip-permissions"], env=env, cwd=str(home))
        self.addCleanup(self.relay.close)

    def test_a_first_message_sent_at_once_is_submitted_and_gets_no_second_enter(self):
        self.start()
        self.relay.send("hello")
        self.assertTrue(until(lambda: len(self.model.requests) == 1, 30), "claude was not asked: %s" % self.logged)
        self.assertIn(b"hello", self.model.requests[0])
        time.sleep(ptyrelay.SUBMIT_WAIT + 1)
        self.assertEqual(len(self.model.requests), 1)
        self.assertFalse([t for t in self.logged if "pressed Enter again" in t], self.logged)

    def test_an_enter_at_an_empty_prompt_asks_nothing_idle_or_while_claude_answers(self):
        """What `Relay._resubmit` assumes when its Enter turns out not to be needed."""
        self.start(delay=4.0)
        self.relay.send("hello")
        self.assertTrue(until(lambda: len(self.model.requests) == 1, 30), self.logged)
        for _ in range(2):                          # while the model is still answering
            time.sleep(1)
            os.write(self.relay.fd, b"\r")
        time.sleep(5)                               # the answer is in
        for _ in range(2):                          # at the empty prompt
            os.write(self.relay.fd, b"\r")
            time.sleep(1)
        self.assertEqual(len(self.model.requests), 1)


if __name__ == "__main__":
    unittest.main()
