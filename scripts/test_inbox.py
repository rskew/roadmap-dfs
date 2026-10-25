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
from test_kiro_model import FakeKiroModel  # noqa: E402

KIRO = os.environ.get("TEST_KIRO") or next(iter(sorted(glob.glob("/nix/store/*-kiro-cli-unwrapped-2.3*/bin/kiro-cli"))[::-1]), None)
CODEX = os.environ.get("TEST_CODEX") or next(iter(sorted(glob.glob("/nix/store/*-codex-0.16*/bin/codex"))[::-1]), None) or shutil.which("codex")


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

    def run_pre(self):
        err, old_err, old_in = io.StringIO(), sys.stderr, sys.stdin
        sys.stderr, sys.stdin = err, io.StringIO("{}")
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


@unittest.skipUnless(CODEX, "no codex here")
class RealCodex(unittest.TestCase):
    def session(self, command, meanwhile):
        """Run `codex exec` as run.sh does for a work session, call `meanwhile(model, run_dir)`
        while it runs, and return the model, the run directory and codex's stream."""
        model = ToolCodexModel(command)
        model.__enter__()
        self.addCleanup(model.__exit__, None, None, None)
        home = Path(tempfile.mkdtemp(dir=os.environ.get("TMPDIR") or None))
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        (home / ".codex").mkdir()
        (home / ".codex" / "config.toml").write_text('[projects."%s"]\ntrust_level = "trusted"\n' % home)
        run_dir = home / "dfs_run_x"
        run_dir.mkdir()
        env = dict(os.environ, HOME=str(home), DFS_INBOX=str(run_dir / "inbox"), **model.env())
        for k in ("CODEX_HOME", "CODEX_THREAD_ID", "CODEX_SESSION_ID"):
            env.pop(k, None)
        p = subprocess.Popen([CODEX, *model.args(), *inbox.codex_args(), "exec", "--json", "--dangerously-bypass-approvals-and-sandbox",
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


@unittest.skipUnless(KIRO, "no kiro-cli 2.3x here")
class RealKiro(unittest.TestCase):
    def session(self, meanwhile, lag=0):
        """Run `kiro-cli chat --v3` as run.sh does for a work session, under the `dfs-inbox` agent, call
        `meanwhile(model, run_dir)` while the model is slow to ask for its tool, and return the model and the run directory."""
        model = FakeKiroModel()
        model.lag = lag
        model.__enter__()
        self.addCleanup(model.__exit__, None, None, None)
        home = Path(tempfile.mkdtemp(dir=os.environ.get("TMPDIR") or None))
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        run_dir, work = home / "dfs_run_x", home / "work"
        run_dir.mkdir()
        work.mkdir()
        subprocess.run(["git", "init", "-q", str(work)], check=True)       # its own repository, so the exclude line lands in it
        inbox.install_kiro_agent(str(work))
        env = dict(os.environ, PATH=os.path.dirname(KIRO) + ":" + os.environ["PATH"], KIRO_NO_AUTO_UPDATE="1", HOME=str(home),
                   DFS_INBOX=str(run_dir / "inbox"), **model.env())
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


class OpencodeWiring(unittest.TestCase):
    def test_the_config_adds_the_plugin_to_the_plugins_already_there(self):
        cfg = json.loads(inbox.opencode_config({"OPENCODE_CONFIG_CONTENT": json.dumps({"model": "a/b", "plugins": ["/x"]})}))
        self.assertEqual(cfg["model"], "a/b")
        self.assertEqual(cfg["plugins"][0], "/x")
        self.assertEqual(Path(cfg["plugins"][1]), HERE / "opencode_inbox")
        self.assertTrue((HERE / "opencode_inbox" / "index.js").is_file())
        self.assertEqual(json.loads(inbox.opencode_config({}))["plugins"], [str(HERE / "opencode_inbox")])

    def test_every_agent_the_chain_runs_takes_a_message_and_an_unknown_one_is_refused_by_name(self):
        for agent in ("claude", "codex", "opencode", "kiro"):
            self.assertTrue(dfs_runs.takes_messages({"agent": agent}), agent)
        self.assertFalse(dfs_runs.takes_messages({"agent": "other"}))
        self.assertIn("a other session cannot take a message", dfs_runs.refusal({"agent": "other"}))


if __name__ == "__main__":
    unittest.main()
