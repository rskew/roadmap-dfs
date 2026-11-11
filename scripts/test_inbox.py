#!/usr/bin/env python3
"""The author's messages to a running session: `runs.py` leaves them in a run's inbox and
`inbox.py`, claude's hook, hands them over. The last tests run a REAL claude (the build in
/nix/store, or TEST_CLAUDE) under `-p` with the hooks run.sh gives it; only the model is
faked, and they are skipped where no claude is installed.

kiro-cli 2.3x (or TEST_KIRO) runs the `dfs-inbox` agent `inbox.install_kiro_agent` writes, against test_kiro_model's stand-in.

The same goes for codex (the build in /nix/store, or TEST_CODEX) with the `-c` overrides
`inbox.codex_args` gives it, and for opencode 2.1.1 (or TEST_OPENCODE) with the plugin
`inbox.opencode_config` names.

Run (with TMPDIR inside the repo when /tmp is noexec):  python3 roadmap-dfs/scripts/test_inbox.py
"""
import glob
import http.server
import io
import json
import os
import shutil
import socket
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
from test_agent_models import FakeCodexModel, FakeOpenAIModel, sse  # noqa: E402
from test_claude_chat import CLAUDE, KEY  # noqa: E402
from test_kiro_model import METERING, FakeKiroModel, event  # noqa: E402

KIRO = os.environ.get("TEST_KIRO") or next(iter(sorted(glob.glob("/nix/store/*-kiro-cli-unwrapped-2.3*/bin/kiro-cli"))[::-1]), None)
CODEX = os.environ.get("TEST_CODEX") or next(iter(sorted(glob.glob("/nix/store/*-codex-0.16*/bin/codex"))[::-1]), None) or shutil.which("codex")


class Inbox(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.box = os.path.join(self.d, "inbox")

    def hook(self, event, stdin='{"hook_event_name": "x"}'):
        out = io.StringIO()
        old_env, old_in = os.environ.get("DFS_INBOX"), sys.stdin
        os.environ["DFS_INBOX"] = self.box
        sys.stdin = io.StringIO(stdin)
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

    def test_the_kiro_hook_holds_the_call_back_with_the_messages_on_stderr_and_exits_2(self):
        err, old = io.StringIO(), sys.stderr
        os.environ["DFS_INBOX"] = self.box
        try:
            self.assertEqual(self.run_pre(), (0, ""))           # nothing waiting: the call runs
            dfs_runs.post_message(self.d, "first")
            dfs_runs.post_message(self.d, "second")
            code, said = self.run_pre()
            self.assertEqual(code, 2)
            self.assertIn("held back", said)
            self.assertLess(said.index("first"), said.index("second"))
            self.assertEqual([m["state"] for m in dfs_runs.messages(self.d)], ["delivered", "delivered"])
            self.assertEqual(self.run_pre(), (0, ""))           # and the held call, made again, runs
        finally:
            os.environ.pop("DFS_INBOX")

    def test_a_hook_fired_inside_a_subagent_claims_nothing_and_the_main_sessions_does(self):
        dfs_runs.post_message(self.d, "for the main session")
        sub = json.dumps({"hook_event_name": "PostToolUse", "agent_id": "a1", "agent_type": "general-purpose"})
        for event in ("post", "stop"):
            self.assertIsNone(self.hook(event, sub))
        self.assertEqual(self.run_pre(sub), (0, ""))
        self.assertEqual([m["state"] for m in dfs_runs.messages(self.d)], ["waiting"])
        self.assertIn("for the main session", json.dumps(self.hook("post", '{"hook_event_name": "PostToolUse"}')))
        self.assertEqual([m["state"] for m in dfs_runs.messages(self.d)], ["delivered"])

    def test_the_kiro_hook_refuses_this_agent_as_a_subagent_and_keeps_the_message_for_the_session(self):
        os.environ["DFS_INBOX"] = self.box
        try:
            dfs_runs.post_message(self.d, "for the main session")
            ask = lambda name: json.dumps({"tool_name": "invoke_sub_agent", "tool_input": {"name": name, "prompt": "x"}})
            code, said = self.run_pre(ask(inbox.KIRO_AGENT))
            self.assertEqual(code, 2)
            self.assertIn("own work session", said)
            self.assertNotIn("for the main session", said)
            self.assertEqual([m["state"] for m in dfs_runs.messages(self.d)], ["waiting"])
            code, said = self.run_pre(ask("someone-else"))      # another agent has no such hook, and the call is held for the message as any is
            self.assertEqual(code, 2)
            self.assertIn("for the main session", said)
        finally:
            os.environ.pop("DFS_INBOX")

    def run_pre(self, stdin="{}"):
        err, old_err, old_in = io.StringIO(), sys.stderr, sys.stdin
        sys.stderr, sys.stdin = err, io.StringIO(stdin)
        try:
            code = inbox.main(["inbox.py", "pre"])
        finally:
            sys.stderr, sys.stdin = old_err, old_in
        return code, err.getvalue()

    def test_the_kiro_agent_names_the_pre_hook_for_every_tool_and_stays_out_of_git_status(self):
        agent = json.loads(inbox.kiro_agent())
        self.assertEqual(agent["tools"], ["*"])
        self.assertIn("inbox.py", agent["hooks"]["preToolUse"][0]["command"])
        self.assertTrue(agent["hooks"]["preToolUse"][0]["command"].endswith(" pre"))
        subprocess.run(["git", "init", "-q", self.d], check=True)
        path = inbox.install_kiro_agent(self.d)
        inbox.install_kiro_agent(self.d)                        # twice: one line in the exclude file
        self.assertEqual(json.loads(Path(path).read_text()), agent)
        status = subprocess.run(["git", "-C", self.d, "status", "--porcelain"], capture_output=True, text=True).stdout
        self.assertNotIn(".kiro", status)
        exclude = (Path(self.d) / ".git" / "info" / "exclude").read_text()
        self.assertEqual(exclude.count("dfs-inbox.json"), 1)

    def test_the_settings_name_this_hook_for_both_events(self):
        hooks = json.loads(inbox.settings())["hooks"]
        self.assertEqual(hooks["PostToolUse"][0]["hooks"][0]["command"], "python3 %s post" % (HERE / "inbox.py"))
        self.assertEqual(hooks["Stop"][0]["hooks"][0]["command"], "python3 %s stop" % (HERE / "inbox.py"))

    def test_a_hook_that_names_its_box_reads_that_inbox_and_not_the_environments(self):
        import shlex
        box = os.path.join(self.d, "inbox with a space")
        elsewhere = os.path.join(self.d, "elsewhere")
        dfs_runs.post_message(self.d, "in the named box")
        os.rename(self.box, box)
        hooks = json.loads(inbox.settings(box))["hooks"]
        self.assertTrue(hooks["PostToolUse"][0]["hooks"][0]["command"].endswith("inbox.py post " + shlex.quote(box)))
        self.assertTrue(any(a.startswith("hooks.Stop=") and shlex.quote(box) in a for a in inbox.codex_args(box)))
        self.assertIn(shlex.quote(box), json.loads(inbox.kiro_agent(box))["hooks"]["preToolUse"][0]["command"])
        run = subprocess.run(shlex.split(hooks["PostToolUse"][0]["hooks"][0]["command"]), input="{}", capture_output=True, text=True,
                             env=dict(os.environ, DFS_INBOX=elsewhere))
        self.assertIn("in the named box", run.stdout)
        self.assertEqual([m["state"] for m in dfs_runs.messages(self.d)], ["delivered"])

    def test_peek_leaves_the_messages_waiting_until_they_are_acked(self):
        dfs_runs.post_message(self.d, "first")
        dfs_runs.post_message(self.d, "second")
        os.environ["DFS_INBOX"] = self.box
        try:
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(inbox.main(["inbox.py", "peek"]), 0)
            peeked = json.loads(out.getvalue())
            self.assertEqual(len(peeked["names"]), 2)
            self.assertLess(peeked["context"].index("first"), peeked["context"].index("second"))
            self.assertEqual([m["state"] for m in dfs_runs.messages(self.d)], ["waiting", "waiting"])
            self.assertEqual(inbox.main(["inbox.py", "ack", peeked["names"][0]]), 0)
            self.assertEqual([m["state"] for m in dfs_runs.messages(self.d)], ["delivered", "waiting"])
            with redirect_stdout(io.StringIO()):
                self.assertEqual(inbox.main(["inbox.py", "ack", peeked["names"][0], peeked["names"][1]]), 0)     # a second ack of one is harmless
            self.assertEqual([m["state"] for m in dfs_runs.messages(self.d)], ["delivered", "delivered"])
        finally:
            os.environ.pop("DFS_INBOX")


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
                    kind, val = model.pick(req)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                if kind in ("tool", "agent"):
                    block = {"type": "tool_use", "id": "toolu_%d" % model.calls, "name": "Bash" if kind == "tool" else "Agent", "input": {}}
                    args = {"command": val, "description": "wait"} if kind == "tool" else {
                        "description": "look into it", "prompt": val, "subagent_type": "general-purpose"}
                    delta = {"type": "input_json_delta", "partial_json": json.dumps(args)}
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

    def pick(self, req):
        """The answer to a request that offers tools, and a note of it in `requests`."""
        self.requests.append(req)
        self.calls = getattr(self, "calls", 0) + 1
        return self.answers[min(len(self.requests), len(self.answers)) - 1]

    def close(self):
        self.server.shutdown()


class SubagentModel(Model):
    """`Model` for a main loop that starts a subagent: the main loop's answers are `answers` (an
    ("agent", prompt) one starts the subagent), the subagent, told apart by `MARK` in its first
    message, gets `inner`'s in order. `requests` are the main loop's, `inner_requests` the subagent's."""
    MARK = "SUBAGENT-MARK"

    def __init__(self, answers, inner):
        super().__init__(answers)
        self.inner, self.inner_requests = inner, []

    def pick(self, req):
        first = json.dumps(req["messages"][0])
        if self.MARK in first:
            self.inner_requests.append(req)
            self.calls = getattr(self, "calls", 0) + 1
            return self.inner[min(len(self.inner_requests), len(self.inner)) - 1]
        return super().pick(req)


@unittest.skipUnless(CLAUDE, "no claude here")
class RealClaude(unittest.TestCase):
    def session(self, answers, meanwhile, model=None):
        """Run `claude -p` as run.sh does for a work session, call `meanwhile(model, run_dir)`
        while it runs, and return its result."""
        model = model or Model(answers)
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
                   DISABLE_AUTOUPDATER="1")      # the hooks name the box: the agent's environment does not
        p = subprocess.Popen([CLAUDE, "-p", "work the task", "--output-format", "json", "--dangerously-skip-permissions",
                              "--settings", inbox.settings(str(run_dir / "inbox"))],
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

    def test_a_message_sent_during_a_subagents_tool_call_waits_for_the_main_session(self):
        def send(model, run_dir):
            end = time.time() + 60
            while not model.inner_requests and time.time() < end:
                time.sleep(0.05)
            time.sleep(1)                   # the subagent's Bash call is running; the main loop's comes after
            dfs_runs.post_message(str(run_dir), "W104-PING for the main session")
        model = SubagentModel([("agent", SubagentModel.MARK + " look around"), ("tool", "sleep 8"), ("text", "main done")],
                              [("tool", "sleep 4"), ("text", "sub done")])
        model, run_dir, result = self.session(None, send, model)
        self.assertNotIn("W104-PING", json.dumps(model.inner_requests))
        self.assertIn("W104-PING for the main session", json.dumps(model.requests))
        self.assertEqual([m["state"] for m in dfs_runs.messages(str(run_dir))], ["delivered"])

    def test_a_message_waiting_when_the_session_would_stop_gets_one_more_turn(self):
        def send(model, run_dir):
            dfs_runs.post_message(str(run_dir), "W104-PING one more thing")
        model, run_dir, result = self.session([("text", "finished"), ("text", "did the one more thing")], send)
        self.assertEqual(len(model.requests), 2)
        self.assertIn("W104-PING one more thing", json.dumps(model.requests[1]))
        self.assertEqual(result, "did the one more thing")
OPENCODE = os.environ.get("TEST_OPENCODE") or next(iter(sorted(glob.glob("/nix/store/*-opencode-2.*/bin/opencode"))[::-1]), None)


class ToolCodexModel(FakeCodexModel):
    """A stand-in Responses model: its first answer is a call of codex's `exec_command` with
    `command`, every later one the text `say`."""

    def __init__(self, command, say="done"):
        super().__init__(say)
        self.command = command

    def answer(self, h, request, slow):
        if len(self.requests) > 1:
            return super().answer(h, request, slow)
        w, call = h.wfile, dict(type="function_call", id="fc_1", call_id="call_1", name="exec_command",
                                arguments=json.dumps({"cmd": self.command}), status="completed")
        w.write(sse(dict(type="response.created", response=dict(id="resp_1", status="in_progress", output=[])), "response.created"))
        w.write(sse(dict(type="response.output_item.done", output_index=0, item=call), "response.output_item.done"))
        w.write(sse(dict(type="response.completed", response=dict(id="resp_1", status="completed", output=[call], usage=dict(
            input_tokens=1, output_tokens=1, total_tokens=2, input_tokens_details=dict(cached_tokens=0),
            output_tokens_details=dict(reasoning_tokens=0)))), "response.completed"))
        w.flush()


class SubagentCodexModel(FakeCodexModel):
    """A stand-in Responses model whose main loop spawns a sub-agent, then runs `sleep 8` and answers; the
    sub-agent, told apart by `MARK` in a request that holds no `spawn_agent` call, runs `sleep 4` and answers.
    `main` and `sub` are the bodies each was sent."""
    MARK = "SUBAGENT-MARK"

    def __init__(self):
        super().__init__("done")
        self.main, self.sub = [], []

    def answer(self, h, request, slow):
        body = json.dumps(request)
        sub = self.MARK in body and '"call_id": "call_spawn"' not in body
        (self.sub if sub else self.main).append(body)
        n = len(self.sub if sub else self.main)
        call = {(False, 1): ("spawn_agent", {"message": self.MARK + " look around"}), (False, 2): ("exec_command", {"cmd": "sleep 8"}),
                (True, 1): ("exec_command", {"cmd": "sleep 4"})}.get((sub, n))
        if not call:
            return super().answer(h, request, slow)
        name, args = call
        item = dict(type="function_call", id="fc_%d" % len(self.requests), name=name, arguments=json.dumps(args), status="completed",
                    call_id="call_spawn" if name == "spawn_agent" else "call_%d" % len(self.requests))
        if name == "spawn_agent":
            item["namespace"] = "multi_agent_v1"
        w = h.wfile
        w.write(sse(dict(type="response.created", response=dict(id="r%d" % len(self.requests), status="in_progress", output=[])), "response.created"))
        w.write(sse(dict(type="response.output_item.done", output_index=0, item=item), "response.output_item.done"))
        w.write(sse(dict(type="response.completed", response=dict(id="r%d" % len(self.requests), status="completed", output=[item], usage=dict(
            input_tokens=1, output_tokens=1, total_tokens=2, input_tokens_details=dict(cached_tokens=0),
            output_tokens_details=dict(reasoning_tokens=0)))), "response.completed"))
        w.flush()


@unittest.skipUnless(CLAUDE, "no claude here")
class EndToEnd(unittest.TestCase):
    """A whole chain (run.sh) around a real claude and a stand-in model, a message sent to the live run
    through the page's endpoint (a real web.py), and the message found in the session's own transcript."""

    def test_a_message_sent_from_the_page_to_a_live_chain_is_in_its_session_transcript(self):
        import urllib.request
        from test_chain import ROADMAP, TASK
        base = Path(tempfile.mkdtemp(dir=os.environ.get("TMPDIR") or None))
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        root, home = base / "repo", base / "home"
        (root / ".dfs" / "items").mkdir(parents=True)
        (root / ".dfs" / "ROADMAP.md").write_text(ROADMAP)
        (root / ".dfs" / "items" / "W1.md").write_text(TASK)
        (root / ".gitignore").write_text(".dfs/runs/\n")
        subprocess.run("git init -q && git add -A && git -c user.name=t -c user.email=t@t commit -qm init", shell=True, cwd=root, check=True)
        (home / ".claude").mkdir(parents=True)
        (home / ".claude.json").write_text(json.dumps({"hasCompletedOnboarding": True,
                                                       "customApiKeyResponses": {"approved": [KEY[-20:]], "rejected": []}}))
        model = Model([("tool", "sleep 6"), ("text", "done")])
        self.addCleanup(model.close)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "AI_AGENT", "ANTHROPIC", "DFS_", "RUNDIR"))}
        env.update(HOME=str(home), CLAUDE_CMD=CLAUDE, ANTHROPIC_API_KEY=KEY, DISABLE_AUTOUPDATER="1",
                   ANTHROPIC_BASE_URL="http://127.0.0.1:%d" % model.server.server_address[1])
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        web = subprocess.Popen([sys.executable, str(HERE / "web.py"), "--no-work", "--port", str(port)], cwd=root, env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(web.wait)
        self.addCleanup(web.kill)
        chain = subprocess.Popen([str(HERE / "run.sh"), "W1", "1"], cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(chain.wait)
        self.addCleanup(chain.kill)

        def call(method, path, body=None):
            req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), method=method,
                                         data=json.dumps(body).encode() if body is not None else None,
                                         headers={"content-type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read() or b"{}")

        end = time.time() + 90
        while not model.requests and time.time() < end:
            time.sleep(0.1)
        time.sleep(0.5)                                         # the model's Bash call is running
        run = sorted(p.name for p in (root / ".dfs" / "runs").glob("dfs_run_*"))[-1]
        status, sent = call("POST", "/api/run/message", dict(run=run, message="W104-PING from the page"))
        self.assertEqual(status, 200, sent)
        self.assertEqual([m["state"] for m in sent["messages"]], ["waiting"])
        out, _ = chain.communicate(timeout=120)
        self.assertEqual(len(model.requests), 2, out)
        self.assertIn("W104-PING from the page", json.dumps(model.requests[1]))
        status, log = call("GET", "/api/run/" + run)
        self.assertEqual([m["state"] for m in log["messages"]], ["delivered"], log)
        transcripts = list((home / ".claude" / "projects").glob("*/*.jsonl"))
        self.assertTrue(any("W104-PING from the page" in t.read_text() for t in transcripts), transcripts)


@unittest.skipUnless(CLAUDE, "no claude here")
class RealClaudeChat(unittest.TestCase):
    """The page's chat (chat.py `Chats`, run.sh `--chat-turn`) around a real claude and a stand-in model:
    a message sent while a turn answers is in that turn's next request."""

    def setUp(self):
        import paths as dfs_paths
        import chat as dfs_chat
        self.base = Path(tempfile.mkdtemp(dir=os.environ.get("TMPDIR") or None))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        root, home = self.base / "repo", self.base / "home"
        (root / ".dfs" / "items").mkdir(parents=True)
        (root / ".dfs" / "ROADMAP.md").write_text("# Roadmap\n")
        (home / ".claude").mkdir(parents=True)
        (home / ".claude.json").write_text(json.dumps({"hasCompletedOnboarding": True,
                                                       "customApiKeyResponses": {"approved": [KEY[-20:]], "rejected": []}}))
        subprocess.run("git init -q -b main && git add -A && git -c user.name=t -c user.email=t@t commit -qm x", shell=True, cwd=root, check=True)
        keys = ("CLAUDE_CMD", "HOME", "DFS_RUN_DIR", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "DISABLE_AUTOUPDATER")
        saved = {k: os.environ.get(k) for k in keys}
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in saved.items()])
        self.model = Model([("tool", "sleep 4"), ("text", "done")])
        self.addCleanup(self.model.close)
        os.environ.update(CLAUDE_CMD=CLAUDE, HOME=str(home), DFS_RUN_DIR=str(root / ".dfs" / "runs"), ANTHROPIC_API_KEY=KEY,
                          DISABLE_AUTOUPDATER="1", ANTHROPIC_BASE_URL="http://127.0.0.1:%d" % self.model.server.server_address[1])
        self.paths, work = dfs_paths, dfs_paths.work_root
        dfs_paths.work_root = lambda: root
        self.addCleanup(setattr, dfs_paths, "work_root", work)
        self.chats = dfs_chat.Chats()

    def settle(self):
        end = time.time() + 90
        while time.time() < end:
            st = self.chats.state("project")
            if not st["busy"]:
                return st
            time.sleep(0.2)
        self.fail("the turn did not end")

    def test_a_message_sent_while_a_turn_answers_steers_it_and_shows_as_delivered(self):
        self.chats.send("project", "look at the code")
        end = time.time() + 60
        while not self.model.requests and time.time() < end:
            time.sleep(0.05)
        time.sleep(0.5)                                 # the Bash call is running
        sent = self.chats.send("project", "W141-STEER use the other file")
        self.assertEqual([m.get("steer") for m in sent["messages"] if m["role"] == "you"][-1], "waiting")
        st = self.settle()
        self.assertEqual(len(self.model.requests), 2)
        self.assertNotIn("W141-STEER", json.dumps(self.model.requests[0]))
        self.assertIn("W141-STEER use the other file", json.dumps(self.model.requests[1]))
        roles = [(m["role"], m["text"], m.get("steer")) for m in st["messages"] if m["role"] != "prompt"]
        self.assertIn(("you", "W141-STEER use the other file", "delivered"), roles)
        self.assertEqual(roles[-1][:2], ("agent", "done"))


@unittest.skipUnless(CODEX, "no codex here")
class RealCodex(unittest.TestCase):
    def session(self, command, meanwhile, model=None):
        """Run `codex exec` as run.sh does for a work session, call `meanwhile(model, run_dir)`
        while it runs, and return the model, the run directory and codex's stream."""
        model = model or ToolCodexModel(command)
        model.__enter__()
        self.addCleanup(model.__exit__, None, None, None)
        home = Path(tempfile.mkdtemp(dir=os.environ.get("TMPDIR") or None))
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        (home / ".codex").mkdir()
        (home / ".codex" / "config.toml").write_text('[projects."%s"]\ntrust_level = "trusted"\n' % home)
        run_dir = home / "dfs_run_x"
        run_dir.mkdir()
        env = dict(os.environ, HOME=str(home), **model.env())
        env.pop("DFS_INBOX", None)
        for k in ("CODEX_HOME", "CODEX_THREAD_ID", "CODEX_SESSION_ID"):
            env.pop(k, None)
        p = subprocess.Popen([CODEX, *model.args(), *inbox.codex_args(str(run_dir / "inbox")), "exec", "--json", "--dangerously-bypass-approvals-and-sandbox",
                              "work the task"], env=env, cwd=str(home), stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        meanwhile(model, run_dir)
        out, err = p.communicate(timeout=120)
        self.assertEqual(p.returncode, 0, err)
        return model, run_dir, out

    def test_a_message_sent_during_a_tool_call_is_in_the_next_model_call(self):
        def send(model, run_dir):
            end = time.time() + 60
            while not model.requests and time.time() < end:
                time.sleep(0.05)
            time.sleep(1)                   # the command is running
            dfs_runs.post_message(str(run_dir), "W104-PING also check the README")
        model, run_dir, out = self.session("sleep 4", send)
        bodies = [b.decode() for _, b in model.requests]
        self.assertEqual(len(bodies), 2, bodies)
        self.assertNotIn("W104-PING", bodies[0])
        self.assertIn("W104-PING also check the README", bodies[1])
        self.assertEqual([m["state"] for m in dfs_runs.messages(str(run_dir))], ["delivered"])
        self.assertIn('"done"', out)

    def test_a_message_sent_during_a_subagents_tool_call_waits_for_the_main_session(self):
        def send(model, run_dir):
            end = time.time() + 60
            while not model.sub and time.time() < end:
                time.sleep(0.05)
            time.sleep(1)                   # the sub-agent's command is running; the main loop's comes after
            dfs_runs.post_message(str(run_dir), "W104-PING for the main session")
        model, run_dir, out = self.session(None, send, SubagentCodexModel())
        self.assertNotIn("W104-PING", "".join(model.sub))
        self.assertIn("W104-PING for the main session", "".join(model.main))
        self.assertEqual([m["state"] for m in dfs_runs.messages(str(run_dir))], ["delivered"])

    def test_a_message_waiting_when_the_session_would_stop_gets_one_more_turn(self):
        def send(model, run_dir):
            dfs_runs.post_message(str(run_dir), "W104-PING one more thing")
        model, run_dir, out = self.session("true", send)
        bodies = [b.decode() for _, b in model.requests]
        self.assertGreaterEqual(len(bodies), 2)
        self.assertIn("W104-PING one more thing", bodies[-1])
        self.assertEqual([m["state"] for m in dfs_runs.messages(str(run_dir))], ["delivered"])

    def test_the_codex_flags_name_this_hook_for_both_events(self):
        args = inbox.codex_args()
        self.assertIn("features.hooks=true", args)
        self.assertIn("--dangerously-bypass-hook-trust", args)
        self.assertTrue(any(a.startswith("hooks.PostToolUse=") and "inbox.py post" in a for a in args))
        self.assertTrue(any(a.startswith("hooks.Stop=") and "inbox.py stop" in a for a in args))


class ToolOpenAIModel(FakeOpenAIModel):
    """A stand-in chat completions model: it answers a request that carries tools and no tool result
    with a call of opencode's `shell` running `command`, and every other request with the text `say`."""

    def __init__(self, command, say="done", answer_after=0):
        super().__init__(say)
        self.command = command
        self.answer_after = answer_after      # seconds the answer to the tool's result takes

    def answer(self, h, request, slow):
        called = any(m.get("role") == "tool" for m in request.get("messages", []))
        if not request.get("tools") or called:
            if called:
                time.sleep(self.answer_after)
            return super().answer(h, request, slow)
        call = dict(index=0, id="call_1", type="function", function=dict(name="shell", arguments=json.dumps({"command": self.command})))
        for delta, finish in ((dict(role="assistant", tool_calls=[call]), None), ({}, "tool_calls")):
            h.wfile.write(sse(dict(id="c1", object="chat.completion.chunk", created=1, model="fake-model",
                                   choices=[dict(index=0, delta=delta, finish_reason=finish)])))
        h.wfile.write(b"data: [DONE]\n\n")
        h.wfile.flush()


@unittest.skipUnless(OPENCODE, "no opencode 2 here")
class RealOpencode(unittest.TestCase):
    def session(self, command, meanwhile, answer_after=0):
        """Run `opencode run` as run.sh does for a work session, call `meanwhile(model, run_dir)` while it
        runs, and return the model, the run directory and opencode's stream."""
        model = ToolOpenAIModel(command, answer_after=answer_after)
        model.__enter__()
        self.addCleanup(model.__exit__, None, None, None)
        home = Path(tempfile.mkdtemp(dir=os.environ.get("TMPDIR") or None))
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        run_dir = home / "dfs_run_x"
        run_dir.mkdir()
        env = dict(os.environ, HOME=str(home), DFS_INBOX=str(run_dir / "inbox"), XDG_DATA_HOME=str(home / "data"),
                   XDG_CONFIG_HOME=str(home / "cfg"), XDG_CACHE_HOME=str(home / "cache"), XDG_STATE_HOME=str(home / "state"),
                   OPENCODE_DISABLE_AUTOUPDATE="true", **model.env())
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(dict(json.loads(env["OPENCODE_CONFIG_CONTENT"]), plugins=[]))
        env["OPENCODE_CONFIG_CONTENT"] = subprocess.run([sys.executable, str(HERE / "inbox.py"), "--opencode-config"], env=env,
                                                        capture_output=True, text=True, check=True).stdout.strip()
        p = subprocess.Popen([OPENCODE, "run", "--standalone", "--format", "json", "work the task"], env=env, cwd=str(home),
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        meanwhile(model, run_dir)
        out, _ = p.communicate(timeout=120)
        self.assertEqual(p.returncode, 0, out)
        return model, run_dir, out

    def test_a_message_sent_during_a_tool_call_is_in_the_next_model_call(self):
        def send(model, run_dir):
            end = time.time() + 60
            while len(model.requests) < 2 and time.time() < end:
                time.sleep(0.05)
            time.sleep(1)                   # the command is running
            dfs_runs.post_message(str(run_dir), "W104-PING also check the README")
        model, run_dir, out = self.session("sleep 4", send)
        bodies = [b.decode() for _, b in model.requests]
        self.assertEqual(len(bodies), 3, bodies)
        self.assertNotIn("W104-PING", bodies[1])
        self.assertIn("W104-PING also check the README", bodies[2])
        self.assertEqual([m["state"] for m in dfs_runs.messages(str(run_dir))], ["delivered"])

    def test_a_message_sent_while_the_model_writes_its_last_answer_gets_one_more_turn(self):
        def send(model, run_dir):
            end = time.time() + 60
            while len(model.requests) < 3 and time.time() < end:
                time.sleep(0.02)
            dfs_runs.post_message(str(run_dir), "W104-PING one more thing")
        model, run_dir, out = self.session("true", send, answer_after=3)
        bodies = [b.decode() for _, b in model.requests]
        self.assertEqual(len(bodies), 4, bodies)
        self.assertNotIn("W104-PING", bodies[2])
        self.assertIn("W104-PING one more thing", bodies[3])
        self.assertEqual([m["state"] for m in dfs_runs.messages(str(run_dir))], ["delivered"])


class ChatSteer:
    """The page's chat (chat.py `Chats`, run.sh `--chat-turn`) around a real codex or opencode and a stand-in
    model whose first step is a slow tool call: a message sent while the turn answers is in a later request of
    that same turn, the first request did not hold it, and the page shows it delivered."""
    agent = ""

    def make(self, model, env):
        """Set the scope up: `model` is the stand-in, `env` what the agent needs in the environment
        (a dict, or a function of the home directory)."""
        import paths as dfs_paths
        import chat as dfs_chat
        self.base = Path(tempfile.mkdtemp(dir=os.environ.get("TMPDIR") or None))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.root, self.home = self.base / "repo", self.base / "home"
        (self.root / ".dfs" / "items").mkdir(parents=True)
        (self.root / ".dfs" / "ROADMAP.md").write_text("# Roadmap\n")
        self.home.mkdir()
        subprocess.run("git init -q -b main && git add -A && git -c user.name=t -c user.email=t@t commit -qm x",
                       shell=True, cwd=self.root, check=True)
        self.model = model
        model.__enter__()
        self.addCleanup(model.__exit__, None, None, None)
        env = dict(env(self.home) if callable(env) else env, HOME=str(self.home), DFS_RUN_DIR=str(self.root / ".dfs" / "runs"), AGENT_NIXPKGS_REV="none")
        saved = {k: os.environ.get(k) for k in list(env) + ["DFS_INBOX", "CHAT_INBOX"]}
        os.environ.update(env)
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in saved.items()])
        for k in ("CODEX_HOME", "CODEX_THREAD_ID", "CODEX_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "DFS_INBOX", "CHAT_INBOX", "CHAT_SID", "CHAT_RESUME", "CHAT_SHARE"):
            os.environ.pop(k, None)
        work = dfs_paths.work_root
        dfs_paths.work_root = lambda: self.root
        dfs_paths._TAG_CACHE.clear()
        self.addCleanup(setattr, dfs_paths, "work_root", work)
        Path(dfs_chat.agent_file()).parent.mkdir(parents=True, exist_ok=True)
        Path(dfs_chat.agent_file()).write_text(self.agent + "\n")
        self.chats = dfs_chat.Chats(on_change=lambda: None)

    def settle(self):
        end = time.time() + 300
        while time.time() < end:
            st = self.chats.state("project")
            if not st["busy"]:
                return st
            time.sleep(0.2)
        self.fail("the turn did not end")

    def steer(self, started):
        """Send a first message, wait until `started()` says the tool call is running, send the steering one."""
        self.chats.send("project", "look at the code")
        end = time.time() + 90
        while not started() and time.time() < end:
            time.sleep(0.05)
        time.sleep(1)                                   # the command is running
        sent = self.chats.send("project", "W141-STEER use the other file")
        self.assertEqual([m.get("steer") for m in sent["messages"] if m["role"] == "you"][-1], "waiting")
        st = self.settle()
        bodies = [b.decode() for _, b in self.model.requests]
        self.assertNotIn("W141-STEER", bodies[0])
        self.assertIn("W141-STEER use the other file", bodies[-1])
        self.assertEqual(sum("W141-STEER" in b for b in bodies[-1:]), 1)
        roles = [(m["role"], m["text"], m.get("steer")) for m in st["messages"] if m["role"] != "prompt"]
        self.assertIn(("you", "W141-STEER use the other file", "delivered"), roles)
        self.assertEqual(roles[-1][:2], ("agent", "done"))
        self.assertEqual(len([r for r in roles if r[0] == "you"]), 2, roles)      # sent once: not again as a turn of its own


@unittest.skipUnless(CODEX, "no codex here")
class RealCodexChat(ChatSteer, unittest.TestCase):
    agent = "codex"

    def setUp(self):
        model = ToolCodexModel("sleep 4")
        self.make(model, dict(model.env(), CODEX_CMD=""))
        (self.home / ".codex").mkdir()
        (self.home / ".codex" / "config.toml").write_text('[projects."%s"]\ntrust_level = "trusted"\n' % self.root)
        cmd = self.base / "codex-with-fake-model"       # CODEX_CMD is split on spaces: the model's arguments live in a script
        cmd.write_text("#!/bin/sh\nexec %s %s \"$@\"\n" % (CODEX, " ".join("'%s'" % a for a in model.args())))
        cmd.chmod(0o755)
        os.environ["CODEX_CMD"] = str(cmd)

    def test_a_message_sent_while_a_turn_answers_steers_it_and_shows_as_delivered(self):
        self.steer(lambda: self.model.requests)


@unittest.skipUnless(OPENCODE, "no opencode 2 here")
class RealOpencodeChat(ChatSteer, unittest.TestCase):
    agent = "opencode"

    def setUp(self):
        model = ToolOpenAIModel("sleep 4")
        self.make(model, lambda home: dict(model.env(), OPENCODE_CMD=OPENCODE, OPENCODE_DISABLE_AUTOUPDATE="true",
                                           XDG_DATA_HOME=str(home / "data"), XDG_CONFIG_HOME=str(home / "cfg"),
                                           XDG_CACHE_HOME=str(home / "cache"), XDG_STATE_HOME=str(home / "state")))

    def test_a_message_sent_while_a_turn_answers_steers_it_and_shows_as_delivered(self):
        self.steer(lambda: len(self.model.requests) >= 2)


class SubagentKiroModel(FakeKiroModel):
    """A stand-in kiro model that first asks to invoke the roadmap's own agent as a sub-agent, then, once that
    is refused, lists a directory, asks again if that call is held back, and answers. `conversations` is the
    id of each conversation a request belonged to: a sub-agent that ran would add a second."""

    def __init__(self):
        super().__init__()
        self.conversations = set()

    def generate(self, request):
        self.conversations.add(request["conversationState"].get("conversationId"))
        current = request["conversationState"]["currentMessage"]["userInputMessage"]
        results = json.dumps(current.get("userInputMessageContext", {}).get("toolResults") or "")
        use = lambda i, name, args: (event("assistantResponseEvent", {"content": "ok "}) + event("toolUseEvent", {
            "toolUseId": "tu%d" % i, "name": name, "input": json.dumps(args), "stop": True}) + METERING + event("metadataEvent", {"stopReason": "tool_use"}))
        if results == '""':
            return use(1, "invoke_sub_agent", {"name": inbox.KIRO_AGENT, "prompt": "SUBAGENT-MARK look around", "explanation": "x"})
        if "own work session" in results or "held back" in results:
            return use(2 + ("held back" in results), "list_directory", {"path": "."})
        return super().generate(request)


@unittest.skipUnless(KIRO, "no kiro-cli 2.3x here")
class RealKiro(unittest.TestCase):
    def session(self, meanwhile, lag=0, model=None):
        """Run `kiro-cli chat --v3` as run.sh does for a work session, under the `dfs-inbox` agent, call
        `meanwhile(model, run_dir)` while the model is slow to ask for its tool, and return the model and the run directory."""
        model = model or FakeKiroModel()
        model.lag = lag
        model.__enter__()
        self.addCleanup(model.__exit__, None, None, None)
        home = Path(tempfile.mkdtemp(dir=os.environ.get("TMPDIR") or None))
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        run_dir, work = home / "dfs_run_x", home / "work"
        run_dir.mkdir()
        work.mkdir()
        subprocess.run(["git", "init", "-q", str(work)], check=True)       # its own repository, so the exclude line lands in it
        inbox.install_kiro_agent(str(work), str(run_dir / "inbox"))
        env = dict(os.environ, PATH=os.path.dirname(KIRO) + ":" + os.environ["PATH"], KIRO_NO_AUTO_UPDATE="1", HOME=str(home), **model.env())
        env.pop("DFS_INBOX", None)
        p = subprocess.Popen([KIRO, "chat", "--v3", "--output-format", "stream-json", "--agent", "dfs-inbox", "--no-interactive",
                              "--trust-all-tools", "use a tool please"], env=env, cwd=str(work), stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        meanwhile(model, run_dir)
        out, _ = p.communicate(timeout=120)
        self.assertEqual(p.returncode, 0, out)
        return model, run_dir, out

    def asked(self, model):
        return [b.decode() for t, b in model.requests if t.endswith("GenerateAssistantResponse")]

    def test_a_message_sent_before_a_tool_call_holds_it_back_and_is_in_the_next_model_call(self):
        def send(model, run_dir):
            end = time.time() + 60
            while not self.asked(model) and time.time() < end:
                time.sleep(0.05)
            dfs_runs.post_message(str(run_dir), "W104-PING also check the README")
        model, run_dir, out = self.session(send, lag=3)
        bodies = self.asked(model)
        self.assertEqual(len(bodies), 3, bodies)               # the call, the held call's result, the call made again
        self.assertNotIn("W104-PING", bodies[0])
        self.assertIn("W104-PING also check the README", bodies[1])
        self.assertIn("held back", bodies[1])
        now = json.loads(bodies[2])["conversationState"]["currentMessage"]["userInputMessage"]["userInputMessageContext"]["toolResults"]
        self.assertEqual([r["toolUseId"] for r in now], ["tu2"])
        self.assertNotIn("held back", json.dumps(now))         # asked again, the call ran
        self.assertEqual([m["state"] for m in dfs_runs.messages(str(run_dir))], ["delivered"])

    def test_the_agent_cannot_be_invoked_as_a_subagent_so_no_sub_agent_takes_the_message(self):
        def send(model, run_dir):
            dfs_runs.post_message(str(run_dir), "W104-PING for the main session")
        model, run_dir, out = self.session(send, lag=1, model=SubagentKiroModel())
        bodies = self.asked(model)
        self.assertEqual(len(model.conversations), 1)           # no sub-agent ran
        self.assertIn("own work session", bodies[1])
        self.assertNotIn("W104-PING", bodies[1])                # the refused call left the message waiting
        self.assertIn("W104-PING for the main session", bodies[2])
        self.assertEqual([m["state"] for m in dfs_runs.messages(str(run_dir))], ["delivered"])

    def test_the_agent_offers_the_tools_the_default_agent_does(self):
        names = lambda body: sorted(t["toolSpecification"]["name"] for t in json.loads(body)["conversationState"]["currentMessage"][
            "userInputMessage"]["userInputMessageContext"]["tools"])
        model, _, _ = self.session(lambda model, run_dir: None)
        mine = names(self.asked(model)[0])
        home = Path(tempfile.mkdtemp(dir=os.environ.get("TMPDIR") or None))
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        plain = FakeKiroModel().__enter__()
        self.addCleanup(plain.__exit__, None, None, None)
        env = dict(os.environ, PATH=os.path.dirname(KIRO) + ":" + os.environ["PATH"], KIRO_NO_AUTO_UPDATE="1", HOME=str(home), **plain.env())
        subprocess.run([KIRO, "chat", "--v3", "--output-format", "stream-json", "--no-interactive", "--trust-all-tools", "use a tool please"],
                       env=env, cwd=str(home), stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
        default = names([b for t, b in plain.requests if t.endswith("GenerateAssistantResponse")][0])
        self.assertEqual(mine, default)

    def test_with_no_message_the_tool_call_runs_as_it_would_without_the_hook(self):
        model, run_dir, out = self.session(lambda model, run_dir: None)
        self.assertEqual(len(self.asked(model)), 2)

    def test_a_message_sent_after_the_last_tool_call_stays_waiting(self):
        def send(model, run_dir):
            end = time.time() + 60
            while len(self.asked(model)) < 2 and time.time() < end:
                time.sleep(0.02)
            dfs_runs.post_message(str(run_dir), "W104-PING too late")
        model, run_dir, out = self.session(send)
        self.assertEqual([m["state"] for m in dfs_runs.messages(str(run_dir))], ["waiting"])
        self.assertNotIn("W104-PING", "".join(self.asked(model)))


MCP_STUB = """import json, sys
for line in sys.stdin:
    try:
        m = json.loads(line)
    except ValueError:
        continue
    i, meth = m.get("id"), m.get("method")
    if meth == "initialize":
        r = {"protocolVersion": m["params"].get("protocolVersion", "2024-11-05"), "capabilities": {"tools": {}}, "serverInfo": {"name": "w141", "version": "1"}}
    elif meth == "tools/list":
        r = {"tools": [{"name": "stubtool_w141", "description": "a stub", "inputSchema": {"type": "object", "properties": {}}}]}
    elif i is None:
        continue
    else:
        r = {}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": i, "result": r}) + "\\n")
    sys.stdout.flush()
"""


@unittest.skipUnless(KIRO, "no kiro-cli here")
class RealKiroChat(ChatSteer, unittest.TestCase):
    """The page's chat around a real kiro-cli: a message sent while a turn waits for its model is held
    against the turn's next tool call, through the agent named by the chat's box. A second chat in the
    same working directory has an agent and a box of its own."""
    agent = "kiro"

    def setUp(self):
        model = FakeKiroModel()
        model.lag = 3
        self.make(model, dict(model.env(), KIRO_CMD=KIRO, KIRO_NO_AUTO_UPDATE="1"))

    def asked(self):
        return [b for t, b in self.model.requests if t.endswith("GenerateAssistantResponse")]

    def test_a_message_sent_while_a_turn_answers_steers_it_and_shows_as_delivered(self):
        self.chats.send("project", "use a tool please")
        end = time.time() + 90
        while not self.asked() and time.time() < end:
            time.sleep(0.05)
        sent = self.chats.send("project", "W141-STEER use the other file")
        self.assertEqual([m.get("steer") for m in sent["messages"] if m["role"] == "you"][-1], "waiting")
        st = self.settle()
        bodies = [b.decode() for b in self.asked()]
        self.assertNotIn("W141-STEER", bodies[0])
        self.assertIn("W141-STEER use the other file", bodies[1])
        self.assertIn(("you", "W141-STEER use the other file", "delivered"),
                      [(m["role"], m["text"], m.get("steer")) for m in st["messages"]])
        agents = sorted(p.name for p in (self.root / ".kiro" / "agents").glob("*.json"))
        self.assertEqual(agents, [inbox.kiro_agent_for(str(self.chats.box("project"))) + ".json"])
        self.assertNotIn("kiro/agents", subprocess.run(["git", "status", "--short"], cwd=self.root, capture_output=True, text=True).stdout)

    def test_the_second_turn_of_a_session_resumed_under_the_chat_agent_is_steered_too(self):
        """Every message after the first is `--resume-id` under the agent; a steered one is read there as well."""
        self.chats.send("project", "hello")
        self.settle()
        self.model.requests_seen = 0                    # the stand-in waits again before the next turn's first answer
        before = len(self.asked())
        self.chats.send("project", "use a tool please")
        end = time.time() + 90
        while len(self.asked()) == before and time.time() < end:
            time.sleep(0.05)
        self.chats.send("project", "W141-RESUMED use the other file")
        st = self.settle()
        bodies = [b.decode() for b in self.asked()][before:]
        self.assertNotIn("W141-RESUMED", bodies[0])
        self.assertIn("W141-RESUMED use the other file", bodies[1])
        self.assertIn(("you", "W141-RESUMED use the other file", "delivered"),
                      [(m["role"], m["text"], m.get("steer")) for m in st["messages"]])

    def test_a_chat_turn_under_the_agent_asks_the_model_for_what_the_default_agent_does(self):
        """The agent file names no resources or MCP servers; kiro still gives the turn the workspace and the user's
        steering files, AGENTS.md, MCP tools and every built-in tool, so only the default agent's own system prompt
        and its built-in sub-agents differ (kiro-cli 2.35.1)."""
        (self.root / "AGENTS.md").write_text("W141-AGENTS-MARK\n")
        (self.root / ".kiro" / "steering").mkdir(parents=True)
        (self.root / ".kiro" / "steering" / "w.md").write_text("W141-STEERING-MARK\n")
        (self.root / ".kiro" / "settings").mkdir(parents=True)
        (self.home / ".kiro" / "steering").mkdir(parents=True)
        (self.home / ".kiro" / "steering" / "g.md").write_text("W141-GLOBAL-MARK\n")
        stub = self.base / "mcp_stub.py"
        stub.write_text(MCP_STUB)
        (self.root / ".kiro" / "settings" / "mcp.json").write_text(json.dumps(
            {"mcpServers": {"w141srv": {"command": "python3", "args": [str(stub)]}}}))
        self.model.lag = 0
        seen = {}
        for label, extra in (("default", []), ("agent", ["--agent", inbox.kiro_agent_for(str(self.chats.box("project")))])):
            inbox.install_kiro_agent(str(self.root), str(self.chats.box("project")), inbox.kiro_agent_for(str(self.chats.box("project"))))
            n = len(self.asked())
            subprocess.run([KIRO, "chat", "--v3", "--output-format", "stream-json", *extra, "--no-interactive", "--trust-all-tools",
                            "--", "hello"], cwd=self.root, capture_output=True, text=True, timeout=240, stdin=subprocess.DEVNULL,
                           env=dict(os.environ))
            request = json.loads(self.asked()[n])
            seen[label] = (sorted(t["toolSpecification"]["name"] for t in
                                  request["conversationState"]["currentMessage"]["userInputMessage"]["userInputMessageContext"]["tools"]),
                           json.dumps(request))
        self.assertIn("stubtool_w141", seen["default"][1])         # the MCP server is loaded at all
        self.assertIn("stubtool_w141", seen["agent"][1])
        self.assertEqual(seen["agent"][0], seen["default"][0])
        for mark in ("W141-AGENTS-MARK", "W141-STEERING-MARK", "W141-GLOBAL-MARK"):
            self.assertIn(mark, seen["default"][1])
            self.assertIn(mark, seen["agent"][1])

    def test_two_boxes_have_two_agents_and_the_chat_agents_cannot_be_invoked_as_subagents(self):
        a, b = inbox.kiro_agent_for("/x/chat/W1/inbox"), inbox.kiro_agent_for("/x/chat/W2/inbox")
        self.assertNotEqual(a, b)
        self.assertEqual(json.loads(inbox.kiro_agent("/x/chat/W1/inbox", a))["name"], a)
        self.assertTrue(inbox.self_as_subagent(dict(tool_name="invoke_sub_agent", tool_input=dict(name=a))))


class OpencodeWiring(unittest.TestCase):
    def test_the_config_adds_the_plugin_to_the_plugins_already_there(self):
        cfg = json.loads(inbox.opencode_config({"OPENCODE_CONFIG_CONTENT": json.dumps({"model": "a/b", "plugins": ["/x"]})}))
        self.assertEqual(cfg["model"], "a/b")
        self.assertEqual(cfg["plugins"][0], "/x")
        self.assertEqual(Path(cfg["plugins"][1]), HERE / "opencode_inbox")
        self.assertTrue((HERE / "opencode_inbox" / "index.js").is_file())
        self.assertEqual(json.loads(inbox.opencode_config({}))["plugins"], [str(HERE / "opencode_inbox")])
        again = inbox.opencode_config({"OPENCODE_CONFIG_CONTENT": inbox.opencode_config({})})      # run under an opencode that has it
        self.assertEqual(json.loads(again)["plugins"], [str(HERE / "opencode_inbox")])

    def test_every_agent_the_chain_runs_takes_a_message_and_an_unknown_one_is_refused_by_name(self):
        for agent in ("claude", "codex", "opencode", "kiro"):
            self.assertTrue(dfs_runs.takes_messages({"agent": agent}), agent)
        self.assertFalse(dfs_runs.takes_messages({"agent": "other"}))
        self.assertIn("a other session cannot take a message", dfs_runs.refusal({"agent": "other"}))


if __name__ == "__main__":
    unittest.main()
