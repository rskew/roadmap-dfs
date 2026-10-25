#!/usr/bin/env python3
"""The author's messages to a running session: `runs.py` leaves them in a run's inbox and
`inbox.py`, claude's hook, hands them over. The last tests run a REAL claude (the build in
/nix/store, or TEST_CLAUDE) under `-p` with the hooks run.sh gives it; only the model is
faked, and they are skipped where no claude is installed.

Run (with TMPDIR inside the repo when /tmp is noexec):  python3 roadmap-dfs/scripts/test_inbox.py
"""
import http.server
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import inbox  # noqa: E402
import runs as dfs_runs  # noqa: E402
from test_claude_chat import CLAUDE, KEY  # noqa: E402


class Inbox(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.box = os.path.join(self.d, "inbox")

    def hook(self, event):
        out = io.StringIO()
        old_env, old_in = os.environ.get("DFS_INBOX"), sys.stdin
        os.environ["DFS_INBOX"] = self.box
        sys.stdin = io.StringIO('{"hook_event_name": "x"}')
        try:
            with redirect_stdout(out):
                self.assertEqual(inbox.main(["inbox.py", event]), 0)
        finally:
            sys.stdin = old_in
            if old_env is None:
                os.environ.pop("DFS_INBOX")
            else:
                os.environ["DFS_INBOX"] = old_env
        return json.loads(out.getvalue()) if out.getvalue().strip() else None

    def test_a_message_waits_until_a_hook_hands_it_over_then_reads_as_delivered(self):
        dfs_runs.post_message(self.d, "  look at the web page too \n")
        self.assertEqual([(m["text"], m["state"]) for m in dfs_runs.messages(self.d)],
                         [("look at the web page too", "waiting")])
        said = self.hook("post")["hookSpecificOutput"]
        self.assertEqual(said["hookEventName"], "PostToolUse")
        self.assertIn("look at the web page too", said["additionalContext"])
        [m] = dfs_runs.messages(self.d)
        self.assertEqual(m["state"], "delivered")
        self.assertGreaterEqual(m["delivered"], m["sent"] - 1)
        self.assertIsNone(self.hook("post"), "a delivered message was handed over twice")

    def test_the_stop_hook_blocks_the_stop_with_every_waiting_message_in_order(self):
        dfs_runs.post_message(self.d, "first")
        dfs_runs.post_message(self.d, "second")
        out = self.hook("stop")
        self.assertEqual(out["decision"], "block")
        self.assertLess(out["reason"].index("first"), out["reason"].index("second"))

    def test_with_nothing_waiting_or_no_inbox_the_hooks_say_nothing(self):
        self.assertIsNone(self.hook("post"))
        self.assertIsNone(self.hook("stop"))
        os.makedirs(self.box)
        self.assertIsNone(self.hook("stop"))

    def test_an_empty_message_is_refused(self):
        with self.assertRaises(ValueError):
            dfs_runs.post_message(self.d, " \n ")

    def test_two_hooks_at_once_never_hand_over_one_message_twice(self):
        for i in range(40):
            dfs_runs.post_message(self.d, "m%d" % i)
        got = []
        ts = [threading.Thread(target=lambda: got.extend(inbox.claim(self.box))) for _ in range(4)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(sorted(got), sorted("m%d" % i for i in range(40)))

    def test_the_settings_name_this_hook_for_both_events(self):
        hooks = json.loads(inbox.settings())["hooks"]
        self.assertEqual(hooks["PostToolUse"][0]["hooks"][0]["command"], "python3 %s post" % (HERE / "inbox.py"))
        self.assertEqual(hooks["Stop"][0]["hooks"][0]["command"], "python3 %s stop" % (HERE / "inbox.py"))


class Model:
    """A stand-in `/v1/messages`: the n-th main-loop call gets `answers[n]`, ("tool", a Bash
    command) or ("text", a reply). Side calls (no tools offered) get a bare text."""

    def __init__(self, answers):
        self.answers, self.requests = answers, []
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
                req = json.loads(body)
                kind, val = "text", "side"
                if "tools" in req:
                    model.requests.append(req)
                    kind, val = model.answers[min(len(model.requests), len(model.answers)) - 1]
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                if kind == "tool":
                    block = {"type": "tool_use", "id": "toolu_%d" % len(model.requests), "name": "Bash", "input": {}}
                    delta = {"type": "input_json_delta", "partial_json": json.dumps({"command": val, "description": "wait"})}
                    stop = "tool_use"
                else:
                    block, delta, stop = {"type": "text", "text": ""}, {"type": "text_delta", "text": val}, "end_turn"
                for k, d in (("message_start", {"message": {"id": "m", "type": "message", "role": "assistant", "model": "claude-sonnet-4-5",
                                                            "content": [], "stop_reason": None, "usage": {"input_tokens": 1, "output_tokens": 1}}}),
                             ("content_block_start", {"index": 0, "content_block": block}),
                             ("content_block_delta", {"index": 0, "delta": delta}),
                             ("content_block_stop", {"index": 0}),
                             ("message_delta", {"delta": {"stop_reason": stop}, "usage": {"output_tokens": 2}}),
                             ("message_stop", {})):
                    self.wfile.write(("event: %s\ndata: %s\n\n" % (k, json.dumps(dict(d, type=k)))).encode())

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@unittest.skipUnless(CLAUDE, "no claude here")
class RealClaude(unittest.TestCase):
    def session(self, answers, meanwhile):
        """Run `claude -p` as run.sh does for a work session, call `meanwhile(model, run_dir)`
        while it runs, and return its result."""
        model = Model(answers)
        self.addCleanup(model.close)
        home = Path(tempfile.mkdtemp(dir=os.environ.get("TMPDIR") or None))
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        (home / ".claude").mkdir()
        (home / ".claude.json").write_text(json.dumps({
            "hasCompletedOnboarding": True, "customApiKeyResponses": {"approved": [KEY[-20:]], "rejected": []},
            "projects": {str(home): {"hasTrustDialogAccepted": True}}}))
        run_dir = home / "dfs_run_x"
        run_dir.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "AI_AGENT", "ANTHROPIC"))}
        env.update(HOME=str(home), ANTHROPIC_API_KEY=KEY, ANTHROPIC_BASE_URL="http://127.0.0.1:%d" % model.server.server_address[1],
                   DISABLE_AUTOUPDATER="1", DFS_INBOX=str(run_dir / "inbox"))
        p = subprocess.Popen([CLAUDE, "-p", "work the task", "--output-format", "json", "--dangerously-skip-permissions",
                              "--settings", inbox.settings()],
                             env=env, cwd=str(home), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        meanwhile(model, run_dir)
        out, err = p.communicate(timeout=120)
        self.assertEqual(p.returncode, 0, err)
        return model, run_dir, json.loads(out)["result"]

    def test_a_message_sent_during_a_tool_call_is_in_the_next_model_call(self):
        def send(model, run_dir):
            end = time.time() + 60
            while not model.requests and time.time() < end:
                time.sleep(0.05)
            time.sleep(0.5)                 # the Bash call is running
            dfs_runs.post_message(str(run_dir), "W104-PING also check the README")
        model, run_dir, result = self.session([("tool", "sleep 3"), ("text", "done")], send)
        self.assertEqual(len(model.requests), 2)
        self.assertNotIn("W104-PING", json.dumps(model.requests[0]))
        self.assertIn("W104-PING also check the README", json.dumps(model.requests[1]))
        self.assertEqual(result, "done")
        self.assertEqual([m["state"] for m in dfs_runs.messages(str(run_dir))], ["delivered"])

    def test_a_message_waiting_when_the_session_would_stop_gets_one_more_turn(self):
        def send(model, run_dir):
            dfs_runs.post_message(str(run_dir), "W104-PING one more thing")
        model, run_dir, result = self.session([("text", "finished"), ("text", "did the one more thing")], send)
        self.assertEqual(len(model.requests), 2)
        self.assertIn("W104-PING one more thing", json.dumps(model.requests[1]))
        self.assertEqual(result, "did the one more thing")


if __name__ == "__main__":
    unittest.main()
