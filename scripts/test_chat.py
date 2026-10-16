#!/usr/bin/env python3
"""chat.py and `run.sh --chat-turn`: a basic chat about a task or the project, with full
permissions. The agent is a stub that records how it was called and answers in claude's JSON.

Run:  python3 roadmap-dfs/scripts/test_chat.py
"""
import glob
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures" / "kiro"          # output a real kiro-cli 2.27.1 wrote
REAL_KIRO = next(iter(sorted(glob.glob("/nix/store/*-kiro-cli-unwrapped-*/bin/kiro-cli-chat"))[::-1]), None)    # kiro-cli itself, if this machine has it
sys.path.insert(0, str(HERE))
import paths as dfs_paths   # noqa: E402
import chat as dfs_chat     # noqa: E402
from test_kiro_model import FakeKiroModel   # noqa: E402

STUB = r"""#!/usr/bin/env bash
# a claude that records its arguments, keeps a transcript as claude does, and answers in -p JSON
echo "$@" >> "$CHAT_STUB_LOG"
[ -n "${CHAT_STUB_SLEEP:-}" ] && { echo started >> "$CHAT_STUB_LOG"; sleep "$CHAT_STUB_SLEEP"; }
[ -n "${CHAT_STUB_FAIL:-}" ] && { echo "boom: not logged in" >&2; exit 1; }
sid=""; prev=""; prompt=""
for a in "$@"; do
  case "$prev" in --session-id|--resume) sid="$a" ;; -p) prompt="$a" ;; esac
  prev="$a"
done
dir="$HOME/.claude/projects/p"; mkdir -p "$dir"
python3 - "$dir/$sid.jsonl" "$prompt" "${CHAT_STUB_SAY:-Fine.}" <<'PY'
import json, sys, uuid
path, prompt, say = sys.argv[1:4]
with open(path, "a") as f:
    f.write(json.dumps({"type": "user", "message": {"role": "user", "content": prompt}}) + "\n")
    mid = "msg_" + uuid.uuid4().hex[:8]
    f.write(json.dumps({"type": "assistant", "message": {"id": mid, "role": "assistant", "content": [{"type": "thinking", "thinking": ""}]}}) + "\n")
    f.write(json.dumps({"type": "assistant", "message": {"id": mid, "role": "assistant", "content": [{"type": "text", "text": "I read it. " + say}]}}) + "\n")
    f.write(json.dumps({"type": "assistant", "message": {"id": mid, "role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}]}}) + "\n")
PY
printf '{"type":"result","is_error":false,"result":"I read it. %s","session_id":"%s"}\n' "${CHAT_STUB_SAY:-Fine.}" "$sid"
"""

CODEX_STUB = r"""#!/usr/bin/env bash
# a codex that records its arguments and what it was told, keeps a rollout as codex does, and
# prints `exec --json` events. `resume <id>` alone is the interactive chat: it only records itself.
echo "$@" >> "$CHAT_STUB_LOG"
[ -n "${CHAT_STUB_FAIL:-}" ] && { echo '{"type":"error","message":"not logged in"}'; echo '{"type":"turn.failed","error":{"message":"not logged in"}}'; exit 1; }
[ "$1" = exec ] || exit 0
shift; [ "$1" = resume ] && shift
sid=""; for a in "$@"; do case "$a" in -*) ;; *) sid="$a" ;; esac; done
[ "$sid" = - ] && sid=""
prompt="$(cat)"; echo "stdin: $prompt" >> "$CHAT_STUB_LOG"
[ -n "$sid" ] || sid="01a14447-0000-7000-8000-$(python3 -c 'import uuid; print(uuid.uuid4().hex[:12])')"
python3 - "$sid" "$prompt" "${CHAT_STUB_SAY:-Fine.}" <<'PY'
import json, os, sys
sid, prompt, say = sys.argv[1:4]
d = os.path.join(os.environ["HOME"], ".codex", "sessions", "2026", "10", "16"); os.makedirs(d, exist_ok=True)
def item(kind, text, i):
    block = {"type": "text", "text": text, "text_elements": []} if kind == "UserMessage" else {"type": "Text", "text": text}
    return {"type": "event_msg", "payload": {"type": "item_completed", "item": {"type": kind, "id": i, "content": [block]}}}
with open(os.path.join(d, "rollout-2026-10-16T10-30-15-%s.jsonl" % sid), "a") as f:
    f.write(json.dumps(item("UserMessage", prompt, "u")) + "\n" + json.dumps(item("AgentMessage", "I read it. " + say, "a")) + "\n")
print(json.dumps({"type": "thread.started", "thread_id": sid}))
print(json.dumps({"type": "turn.started"}))
print(json.dumps({"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": "Reading."}}))
print(json.dumps({"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": "I read it. " + say}}))
print(json.dumps({"type": "turn.completed", "usage": {}}))
PY
"""

OPENCODE_STUB = r"""#!/usr/bin/env bash
# an opencode that records its arguments and what it was told, keeps its sessions in a sqlite store
# as opencode does, and prints `run --format json` events. Without `run` it is the interactive chat.
echo "$@" >> "$CHAT_STUB_LOG"
[ "$1" = run ] || exit 0
[ -n "${CHAT_STUB_FAIL:-}" ] && { echo '{"type":"error","sessionID":"ses_x","error":{"name":"APIError","data":{"message":"not logged in","statusCode":401}}}'; exit 1; }
shift
sid=""; while [ $# -gt 0 ]; do [ "$1" = --session ] && { sid="$2"; shift; }; shift; done
prompt="$(cat)"; echo "stdin: $prompt" >> "$CHAT_STUB_LOG"
[ -n "$sid" ] || sid="ses_$(python3 -c 'import uuid; print(uuid.uuid4().hex[:20])')"
python3 - "$sid" "$prompt" "${CHAT_STUB_SAY:-Fine.}" <<'PY'
import json, os, sqlite3, sys, time
sid, prompt, say = sys.argv[1:4]
d = os.path.join(os.environ["HOME"], ".local", "share", "opencode"); os.makedirs(d, exist_ok=True)
db = sqlite3.connect(os.path.join(d, "opencode.db"))
db.execute("create table if not exists session (id text primary key, time_updated integer not null)")
db.execute("create table if not exists message (id text primary key, session_id text not null, time_created integer not null, data text not null)")
db.execute("create table if not exists part (id text primary key, message_id text not null, session_id text not null, time_created integer not null, data text not null)")
n = db.execute("select count(*) from message").fetchone()[0]
now = int(time.time() * 1000)
db.execute("insert or replace into session values (?, ?)", (sid, now))
def message(role, parts):
    global n
    n += 1
    mid = "msg_%04d" % n
    db.execute("insert into message values (?, ?, ?, ?)", (mid, sid, now + n, json.dumps({"role": role})))
    for i, p in enumerate(parts):
        db.execute("insert into part values (?, ?, ?, ?, ?)", ("prt_%04d_%d" % (n, i), mid, sid, now + n, json.dumps(p)))
message("user", [{"type": "text", "text": prompt}])
message("assistant", [{"type": "step-start"}, {"type": "tool", "tool": "read"}, {"type": "text", "text": "Reading."},
                      {"type": "text", "text": "I read it. " + say}, {"type": "step-finish", "reason": "stop"}])
db.commit()
def ev(kind, **part):
    print(json.dumps({"type": kind, "sessionID": sid, "part": part}))
ev("step_start", type="step-start")
ev("tool_use", type="tool", tool="read")
ev("step_finish", type="step-finish", reason="tool-calls")
ev("step_start", type="step-start")
ev("text", type="text", text="I read it. " + say)
ev("step_finish", type="step-finish", reason="stop")
PY
"""


KIRO_STUB = r"""#!/usr/bin/env bash
# a kiro-cli that records its arguments, keeps a session under ~/.kiro/sessions and prints
# `--output-format stream-json` events, all in the shapes a real kiro-cli 2.27.1 wrote
# (fixtures/kiro, and test_kiro_real.py runs the real one). Without `--output-format` it is the
# interactive chat.
echo "$@" >> "$CHAT_STUB_LOG"
case "$*" in *--output-format*) ;; *) exit 0 ;; esac
if [ -n "${CHAT_STUB_FAIL:-}" ]; then
  echo '{"type":"runError","data":{"sessionId":null,"stage":"auth","message":"Not logged in. Set the KIRO_API_KEY environment variable or run `kiro-cli login` first."}}'
  exit 0
fi
sid=""; prompt=""; prev=""; rest=0
for a in "$@"; do
  if [ "$rest" = 1 ]; then prompt="$a"; continue; fi
  [ "$a" = -- ] && { rest=1; continue; }
  [ "$prev" = --resume-id ] && sid="$a"
  prev="$a"
done
echo "prompt: $prompt" >> "$CHAT_STUB_LOG"
[ -n "$sid" ] || sid="sess_$(python3 -c 'import uuid; print(uuid.uuid4())')"
python3 - "$sid" "$prompt" "${CHAT_STUB_SAY:-Fine.}" <<'PY'
import json, os, sys, uuid
sid, prompt, say = sys.argv[1:4]
d = os.path.join(os.environ["HOME"], ".kiro", "sessions", "bucket-1", sid); os.makedirs(d, exist_ok=True)
with open(os.path.join(d, "session.json"), "w") as f:
    f.write(json.dumps({"schemaVersion": "1.0.0", "id": sid}))
run = str(uuid.uuid4())
def line(id, **payload):
    return json.dumps({"id": id, "timestamp": "2026-10-16T15:04:17.292Z", "payload": payload})
with open(os.path.join(d, "messages.jsonl"), "a") as f:
    f.write(line(str(uuid.uuid4()), type="user", content=prompt, documents=[], images=[]) + "\n")
    f.write(line(run + "-turn-start", type="turn_start", executionId=run) + "\n")
    f.write(line(str(uuid.uuid4()) + "-say", type="assistant", content="Let me look. ", operationType="Say", executionId=run) + "\n")
    f.write(line("tu1-call", type="tool_call", toolCallId="tu1", toolName="list_directory", args={}, status="approved") + "\n")
    f.write(line("tu1-result", type="tool_result", toolCallId="tu1", content="[DIR] .dfs", success=True) + "\n")
    f.write(line(str(uuid.uuid4()) + "-say", type="assistant", content="I read it. " + say, operationType="Say", executionId=run) + "\n")
    f.write(line(run + "-turn-end", type="turn_end", stopReason="end_turn", executionId=run) + "\n")
def update(**u):
    print(json.dumps({"type": "sessionUpdate", "data": {"sessionId": sid, "update": u}}))
print(json.dumps({"type": "runStarted", "data": {"payloadSchema": "acp", "acpProtocolVersion": 1, "engine": "v3"}}))
update(sessionUpdate="agent_message_chunk", content={"type": "text", "text": "Let me look. "})
update(sessionUpdate="tool_call", toolCallId="tu1", title="List Directory", kind="search")
update(sessionUpdate="agent_message_chunk", content={"type": "text", "text": "I read it. " + say})
print(json.dumps({"type": "runFinished", "data": {"sessionId": sid, "status": "success", "stopReason": "end_turn",
                                                  "finalText": "Let me look. I read it. " + say, "finalTextTruncated": False}}))
PY
"""


class Chat(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / ".dfs" / "items").mkdir(parents=True)
        (self.root / ".dfs" / "ROADMAP.md").write_text("# Roadmap\n")
        (self.root / ".dfs" / "items" / "W1.md").write_text("# W1 · sample\n\n## Goal\n\nDo it.\n")
        subprocess.run("git init -q -b main && git add -A && git -c user.name=t -c user.email=t@t commit -qm x",
                       shell=True, cwd=self.root, check=True)
        self.stub = self.root / "claude-stub"
        self.stub.write_text(STUB)
        self.stub.chmod(self.stub.stat().st_mode | stat.S_IEXEC)
        self.log = self.root / "stub.log"
        self._env = {k: os.environ.get(k) for k in ("CLAUDE_CMD", "CODEX_CMD", "OPENCODE_CMD", "KIRO_CMD", "KIRO_HOME", "XDG_DATA_HOME", "CHAT_SHARE", "CHAT_SID", "CHAT_RESUME", "CHAT_STUB_LOG", "CHAT_STUB_FAIL", "CHAT_STUB_SAY", "CHAT_STUB_SLEEP", "HOME", "DFS_RUN_DIR", "PATH")}
        os.environ.update(CLAUDE_CMD=str(self.stub), CHAT_STUB_LOG=str(self.log), HOME=str(self.root / "home"),
                          DFS_RUN_DIR=str(self.root / ".dfs" / "runs"))     # the chat logs are this project's, not the real one's
        (self.root / "home").mkdir()
        self.codex = self.root / "codex-stub"
        self.codex.write_text(CODEX_STUB)
        self.codex.chmod(self.codex.stat().st_mode | stat.S_IEXEC)
        os.environ["CODEX_CMD"] = str(self.codex)
        self.opencode = self.root / "opencode-stub"
        self.opencode.write_text(OPENCODE_STUB)
        self.opencode.chmod(self.opencode.stat().st_mode | stat.S_IEXEC)
        os.environ["OPENCODE_CMD"] = str(self.opencode)
        self.kiro = self.root / "kiro-stub"
        self.kiro.write_text(KIRO_STUB)
        self.kiro.chmod(self.kiro.stat().st_mode | stat.S_IEXEC)
        os.environ["KIRO_CMD"] = str(self.kiro)
        os.environ.pop("KIRO_HOME", None)           # .kiro is under HOME, as kiro-cli finds it
        os.environ.pop("XDG_DATA_HOME", None)       # the store is under HOME, as opencode finds it
        for k in ("CHAT_SHARE", "CHAT_SID", "CHAT_RESUME"):
            os.environ.pop(k, None)
        os.environ.pop("CHAT_STUB_FAIL", None)
        os.environ.pop("CHAT_STUB_SLEEP", None)
        self._wr = dfs_paths.work_root
        dfs_paths.work_root = lambda: self.root
        dfs_paths._TAG_CACHE.clear()
        self.changes = 0
        self.chats = dfs_chat.Chats(on_change=lambda: setattr(self, "changes", self.changes + 1))

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        dfs_paths.work_root = self._wr
        dfs_paths._TAG_CACHE.clear()
        shutil.rmtree(self.root, ignore_errors=True)

    def settle(self, scope, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            st = self.chats.state(scope)
            if not st["busy"]:
                return st
            time.sleep(0.05)
        self.fail("the turn did not finish")

    def calls(self):
        return self.log.read_text().splitlines()

    def test_stop_ends_a_turn_that_is_answering_and_the_conversation_goes_on(self):
        os.environ["CHAT_STUB_SLEEP"] = "60"
        self.chats.send("W1", "a long one")
        end = time.time() + 10
        while not (self.log.exists() and "started" in self.log.read_text()) and time.time() < end:
            time.sleep(0.05)
        self.assertTrue(self.chats.state("W1")["busy"])
        t0 = time.time()
        self.chats.interrupt("W1")
        st = self.settle("W1")
        self.assertLess(time.time() - t0, 20, "the turn ended at once, not at its timeout")
        self.assertEqual([m["role"] for m in st["messages"]], ["you", "note"], "stopped is not an error: %s" % st["messages"])
        self.assertTrue(self.chats.sid("W1"), "the first turn's session is kept, so the conversation survives a stop")
        os.environ.pop("CHAT_STUB_SLEEP")
        self.chats.send("W1", "and now?")
        st = self.settle("W1")
        self.assertEqual([m["role"] for m in st["messages"]][-2:], ["you", "agent"])
        self.assertNotIn("note", [m["role"] for m in st["messages"]], "the note goes once it moves on")

    def test_stop_is_pressed_in_the_screens_chat_when_it_is_the_one_answering(self):
        class Screen:
            has_screen = True
            def __init__(s): s.pressed = []
            def chat_live(s, scope): return True
            def chat_active(s, scope): return True
            def chat_interrupt(s, scope): s.pressed.append(scope); return 0
        screen = Screen()
        chats = dfs_chat.Chats(walker=lambda: screen)
        st = chats.interrupt("W1")
        self.assertEqual(screen.pressed, ["W1"])
        self.assertNotIn("note", [m["role"] for m in st["messages"]], "the screen's chat leaves its own mark in the transcript")

    def test_stop_before_the_screens_chat_typed_a_message_does_not_leave_it_shown_as_sent(self):
        class Screen:
            has_screen = True
            def chat_live(s, scope): return True
            def chat_active(s, scope): return False
            def chat_send(s, scope, text): return True
            def chat_interrupt(s, scope): return 1      # one message was dropped untyped
        chats = dfs_chat.Chats(walker=lambda: Screen())
        chats.send("W1", "never typed")
        self.assertEqual([m["text"] for m in chats.state("W1")["messages"]], ["never typed"])
        st = chats.interrupt("W1")
        self.assertEqual([m["role"] for m in st["messages"]], ["note"], st["messages"])
        chats.send("W1", "again")
        self.assertEqual([m["role"] for m in chats.state("W1")["messages"]], ["you"], "the note goes once it moves on")

    def test_stop_after_the_agent_has_answered_adds_no_note(self):
        class Done:
            def poll(s): return 0
        self.chats.busy["W1"] = time.time()
        self.chats.procs["W1"] = Done()
        self.chats.interrupt("W1")
        self.assertNotIn("W1", self.chats.stopped, "an answer already given is not stopped")

    def test_stop_with_nothing_answering_changes_nothing(self):
        st = self.chats.interrupt("W1")
        self.assertFalse(st["busy"])
        self.assertEqual(st["messages"], [])

    def test_a_turn_is_answered_and_kept(self):
        st = self.chats.send("W1", "Where is the work?")
        self.assertTrue(st["busy"])
        st = self.settle("W1")
        self.assertEqual([(m["role"], m["text"]) for m in st["messages"]],
                         [("you", "Where is the work?"), ("agent", "I read it. Fine.")])
        self.assertGreaterEqual(self.changes, 2, "the page is told when it starts and when it ends")
        self.assertTrue(self.chats.path("W1").exists(), "the session id is kept under the run root, not in the roadmap")
        self.assertEqual(list(self.chats.path("W1").parts[-3:-1]), ["runs", "chat"])

    def test_it_has_full_permissions_like_the_screens_chat(self):
        self.chats.send("W1", "hello")
        self.settle("W1")
        first = self.calls()[0]
        self.assertIn("--dangerously-skip-permissions", first)
        self.assertNotIn("--allowedTools", first)
        self.assertNotIn("--disallowedTools", first)
        self.assertIn("--output-format json", first)
        self.assertIn("task W1", first)                       # the framing, on the first turn
        self.assertNotIn("You may only read", first)
        self.assertNotIn("Do not edit", first)
        self.assertIn("hello", first)

    def test_the_second_turn_resumes_the_session_and_is_just_the_message(self):
        self.chats.send("W1", "first")
        self.settle("W1")
        self.chats.send("W1", "and then?")
        st = self.settle("W1")
        first, second = self.calls()
        sid = first.split("--session-id ")[1].split()[0]
        self.assertIn("--resume " + sid, second)
        self.assertNotIn("--session-id", second)
        self.assertNotIn("You are talking with the author", second)
        self.assertEqual(len(st["messages"]), 4)

    def test_the_page_and_the_screen_are_one_conversation(self):
        sid, resume = dfs_chat.claim("W1")                    # the screen's c, before it opens
        self.assertFalse(resume)
        self.assertEqual(dfs_chat.claim("W1")[0], sid, "the same session while it is the scope's")
        self.chats.send("W1", "from the page")
        self.settle("W1")
        self.assertIn("--session-id " + sid, self.calls()[0], "the page starts the session the screen claimed")
        self.assertTrue(dfs_chat.claim("W1")[1], "and now the screen resumes it")
        # something said at the screen lands in the same transcript and shows on the page
        t = dfs_chat.dfs_context.transcript_path(sid, "claude")
        with open(t, "a") as f:
            f.write(json.dumps({"type": "user", "message": {"role": "user", "content": "typed at the screen"}}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"id": "m9", "role": "assistant",
                    "content": [{"type": "text", "text": "answered at the screen"}]}}) + "\n")
        texts = [m["text"] for m in self.chats.state("W1")["messages"]]
        self.assertEqual(texts[-2:], ["typed at the screen", "answered at the screen"])
        self.chats.send("W1", "back on the page")
        self.settle("W1")
        self.assertIn("--resume " + sid, self.calls()[1])

    def test_the_screens_opening_prompt_and_the_harness_notes_are_not_the_conversation(self):
        sid, _ = dfs_chat.claim("W1")
        t = Path(dfs_chat.dfs_context.transcript_path(sid, "claude"))
        t.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            {"type": "user", "isMeta": True, "message": {"role": "user", "content": "<local-command-caveat>x</local-command-caveat>"}},
            {"type": "user", "message": {"role": "user", "content": "Read .dfs/ROADMAP.md and task W1's parts. Discuss task W1 with the author."}},
            {"type": "assistant", "message": {"id": "a1", "role": "assistant", "content": [{"type": "text", "text": "Here is where it stands."}]}},
            {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "x"}]}},
            {"type": "user", "message": {"role": "user", "content": "What next?"}},
            {"type": "queue-operation"},
        ]
        t.write_text("".join(json.dumps(l) + "\n" for l in lines))
        self.assertEqual([(m["role"], m["text"]) for m in self.chats.state("W1")["messages"]],
                         [("agent", "Here is where it stands."), ("you", "What next?")])

    def codex_rollout(self, sid, records):
        """A codex rollout as codex-cli 0.162.0 writes one: an envelope per line, the
        conversation in `item_completed` events. Only the ids and the words are made up."""
        os.environ.pop("CODEX_HOME", None)
        d = self.root / "home" / ".codex" / "sessions" / "2026" / "10" / "16"
        d.mkdir(parents=True, exist_ok=True)
        (d / ("rollout-2026-10-16T10-30-15-%s.jsonl" % sid)).write_text("".join(json.dumps(r) + "\n" for r in records))

    def test_a_codex_conversation_is_read_from_its_rollout(self):
        sid = "01a14443-2085-7d12-8fb4-d7b6245af0c1"
        def said(kind, text, i):
            block = {"type": "text", "text": text, "text_elements": []} if kind == "UserMessage" else {"type": "Text", "text": text}
            return {"type": "event_msg", "payload": {"type": "item_completed", "thread_id": sid, "turn_id": "t%d" % i,
                                                     "item": {"type": kind, "id": "i%d" % i, "content": [block]}}}
        self.codex_rollout(sid, [
            {"type": "session_meta", "payload": {"id": sid}},
            {"type": "response_item", "payload": {"type": "message", "role": "developer",
                                                  "content": [{"type": "input_text", "text": "<permissions instructions>"}]}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
                                                  "content": [{"type": "input_text", "text": "<environment_context>x</environment_context>"}]}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
                                                  "content": [{"type": "input_text", "text": "You are talking with the author about task W1. The author writes: how is it?"}]}},
            said("UserMessage", "You are talking with the author about task W1. The author writes: how is it?", 1),
            said("AgentMessage", "Let me read the tree.", 2),
            {"type": "event_msg", "payload": {"type": "item_completed", "item": {"type": "CommandExecution", "command": ["cat", "x"]}}},
            said("AgentMessage", "It is open at W1.2.", 3),
            {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                                                  "content": [{"type": "output_text", "text": "It is open at W1.2."}]}},
            said("UserMessage", "and next?", 4),
            said("AgentMessage", "W1.3.", 5),
        ])
        self.chats.save_sid("W1", sid, "codex")
        st = self.chats.state("W1")
        self.assertEqual(st["agent"], "codex")
        self.assertEqual([(m["role"], m["text"]) for m in st["messages"]],
                         [("you", "how is it?"), ("agent", "Let me read the tree."), ("agent", "It is open at W1.2."),
                          ("you", "and next?"), ("agent", "W1.3.")])
        self.assertEqual([c["count"] for c in self.chats.list() if c["scope"] == "W1"], [5])
        self.assertEqual([c["agent"] for c in self.chats.list() if c["scope"] == "W1"], ["codex"])   # the page's list names it

    def test_a_conversation_keeps_its_agent_and_a_new_one_takes_the_screens(self):
        Path(dfs_chat.agent_file()).parent.mkdir(parents=True, exist_ok=True)
        Path(dfs_chat.agent_file()).write_text("codex\n")
        self.assertEqual(self.chats.agent("W1"), "codex")             # nothing begun: x's choice
        self.chats.path("W1").parent.mkdir(parents=True, exist_ok=True)
        self.chats.path("W1").write_text(json.dumps(dict(sid="s-old")))
        self.assertEqual(self.chats.agent("W1"), "claude")            # a record from before the agent was kept
        self.chats.save_sid("W1", "s-new", "claude")
        self.assertEqual(self.chats.agent("W1"), "claude")            # x says codex; the conversation is claude's
        self.chats.save_sid("W1", "s-new", "codex")
        with self.assertRaises(ValueError):
            dfs_chat.claim("W1")                                      # the screen's claude cannot resume codex's session

    def use_codex(self):
        Path(dfs_chat.agent_file()).parent.mkdir(parents=True, exist_ok=True)
        Path(dfs_chat.agent_file()).write_text("codex\n")

    def test_a_codex_turn_is_answered_kept_and_resumed_by_the_id_codex_made(self):
        self.use_codex()
        self.chats.send("W1", "Where is the work?")
        st = self.settle("W1")
        self.assertEqual([(m["role"], m["text"]) for m in st["messages"]],
                         [("you", "Where is the work?"), ("agent", "I read it. Fine.")])
        rec = json.loads(self.chats.path("W1").read_text())
        self.assertEqual(rec["agent"], "codex")
        self.assertTrue(rec["sid"].startswith("01a14447-"), "the id is the one codex printed, not one chosen here")
        self.chats.send("W1", "-and then?")
        st = self.settle("W1")
        first, first_in, second, second_in = self.calls()
        self.assertEqual(first, "exec --json --dangerously-bypass-approvals-and-sandbox -")
        self.assertIn("The author writes: Where is the work?", first_in)
        self.assertEqual(second, "exec resume --json --dangerously-bypass-approvals-and-sandbox %s -" % rec["sid"])
        self.assertEqual(second_in, "stdin: -and then?", "a message that starts with a dash is the prompt, not a flag")
        self.assertEqual([m["text"] for m in st["messages"] if m["role"] == "you"], ["Where is the work?", "-and then?"])

    def test_a_failed_codex_turn_says_why_and_leaves_no_conversation(self):
        self.use_codex()
        os.environ["CHAT_STUB_FAIL"] = "1"
        self.chats.send("W1", "hi")
        st = self.settle("W1")
        self.assertEqual([m["role"] for m in st["messages"]], ["you", "error"])
        self.assertIn("not logged in", st["messages"][-1]["text"])
        self.assertFalse(self.chats.path("W1").exists())

    def test_the_screens_codex_chat_makes_its_opening_turn_then_resumes_the_session_the_page_reads(self):
        self.use_codex()
        self.assertEqual(dfs_chat.claim("W1", "codex"), ("", False), "codex has no id to name for a new conversation")
        self.assertFalse(self.chats.path("W1").exists())
        run = lambda **env: subprocess.run([str(HERE / "run.sh"), "--codex", "--chat", "W1"], cwd=self.root, text=True,
                                           capture_output=True, env=dict(os.environ, CHAT_SHARE="1", **env))
        r = run()
        self.assertEqual(r.returncode, 0, r.stderr)
        rec = json.loads(self.chats.path("W1").read_text())
        self.assertEqual(rec["agent"], "codex")
        opening, opening_in, interactive = self.calls()
        self.assertEqual(opening, "exec --json --dangerously-bypass-approvals-and-sandbox -")
        self.assertIn("Discuss task W1 with the author", opening_in)
        self.assertEqual(interactive, "resume %s --dangerously-bypass-approvals-and-sandbox" % rec["sid"])
        self.assertEqual(self.chats.state("W1")["messages"][-1]["text"], "I read it. Fine.")
        # opened again: the conversation exists, so it is resumed and no new opening turn runs
        sid, resume = dfs_chat.claim("W1", "codex")
        self.assertEqual((sid, resume), (rec["sid"], True))
        r = run(CHAT_SID=sid, CHAT_RESUME="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls()[3:], ["resume %s --dangerously-bypass-approvals-and-sandbox" % sid])
        with self.assertRaises(ValueError):
            dfs_chat.claim("W1", "claude")      # claude cannot resume codex's session

    def test_a_codex_stream_is_read_to_the_one_line_a_turn_is_read_from(self):
        ok = dfs_chat.codex_result(['{"type":"thread.started","thread_id":"t1"}', "not json",
                                    '{"type":"item.completed","item":{"type":"agent_message","text":"a"}}',
                                    '{"type":"item.completed","item":{"type":"command_execution"}}',
                                    '{"type":"item.completed","item":{"type":"agent_message","text":"b"}}',
                                    '{"type":"turn.completed"}'])
        self.assertEqual((ok["session_id"], ok["is_error"], ok["result"]), ("t1", False, "b"))
        bad = dfs_chat.codex_result(['{"type":"thread.started","thread_id":"t1"}', '{"type":"turn.started"}',
                                     '{"type":"turn.failed","error":{"message":"rate limited"}}'])
        self.assertEqual((bad["is_error"], bad["result"]), (True, "rate limited"))
        died = dfs_chat.codex_result([], "kept")
        self.assertEqual((died["session_id"], died["is_error"], died["result"]), ("kept", True, ""))

    def use_opencode(self):
        Path(dfs_chat.agent_file()).parent.mkdir(parents=True, exist_ok=True)
        Path(dfs_chat.agent_file()).write_text("opencode\n")

    def test_an_opencode_conversation_is_read_from_its_store_without_its_tools_and_notes(self):
        sid = "ses_abc"
        self.assertFalse(dfs_chat.has_transcript(sid, "opencode"))
        self.assertEqual(dfs_chat.transcript_messages(sid, "opencode"), [])        # no store at all
        subprocess.run([str(self.opencode), "run", "--session", sid], input="You are talking with the author about task W1. The author writes: hello",
                       text=True, capture_output=True, check=True, env=dict(os.environ))
        db = sqlite3.connect(self.root / "home" / ".local" / "share" / "opencode" / "opencode.db")
        db.execute("insert into message values ('msg_9000', ?, 1, '{\"role\":\"user\"}')", (sid,))
        db.execute("insert into part values ('prt_9000', 'msg_9000', ?, 1, ?)", (sid, json.dumps({"type": "text", "text": "opencode's own note", "synthetic": True})))
        db.commit()
        db.close()
        self.assertTrue(dfs_chat.has_transcript(sid, "opencode"))
        self.assertEqual(dfs_chat.transcript_messages(sid, "opencode"),
                         [dict(role="you", text="hello"), dict(role="agent", text="Reading.\nI read it. Fine.")])
        self.assertAlmostEqual(dfs_chat.transcript_mtime(sid, "opencode"), time.time(), delta=60)

    def test_an_opencode_turn_is_answered_kept_and_resumed_by_the_id_opencode_made(self):
        self.use_opencode()
        self.chats.send("W1", "Where is the work?")
        st = self.settle("W1")
        self.assertEqual([(m["role"], m["text"]) for m in st["messages"]],
                         [("you", "Where is the work?"), ("agent", "Reading.\nI read it. Fine.")])
        rec = json.loads(self.chats.path("W1").read_text())
        self.assertEqual(rec["agent"], "opencode")
        self.assertTrue(rec["sid"].startswith("ses_"), "the id is the one opencode printed, not one chosen here")
        self.chats.send("W1", "-and then?")
        st = self.settle("W1")
        first, first_in, second, second_in = self.calls()
        self.assertEqual(first, "run --format json --auto")
        self.assertIn("The author writes: Where is the work?", first_in)
        self.assertEqual(second, "run --format json --auto --session %s" % rec["sid"])
        self.assertEqual(second_in, "stdin: -and then?", "the message goes in on stdin: opencode quotes one given as an argument")
        self.assertEqual([m["text"] for m in st["messages"] if m["role"] == "you"], ["Where is the work?", "-and then?"])

    def test_a_failed_opencode_turn_says_why_and_leaves_no_conversation(self):
        self.use_opencode()
        os.environ["CHAT_STUB_FAIL"] = "1"
        self.chats.send("W1", "hi")
        st = self.settle("W1")
        self.assertEqual([m["role"] for m in st["messages"]], ["you", "error"])
        self.assertIn("not logged in", st["messages"][-1]["text"])
        self.assertFalse(self.chats.path("W1").exists())

    def test_the_screens_opencode_chat_makes_its_opening_turn_then_resumes_the_session_the_page_reads(self):
        self.use_opencode()
        self.assertEqual(dfs_chat.claim("W1", "opencode"), ("", False), "opencode has no id to name for a new conversation")
        run = lambda **env: subprocess.run([str(HERE / "run.sh"), "--opencode", "--chat", "W1"], cwd=self.root, text=True,
                                           capture_output=True, env=dict(os.environ, CHAT_SHARE="1", **env))
        r = run()
        self.assertEqual(r.returncode, 0, r.stderr)
        rec = json.loads(self.chats.path("W1").read_text())
        self.assertEqual(rec["agent"], "opencode")
        opening, opening_in, interactive = self.calls()
        self.assertEqual(opening, "run --format json --auto")
        self.assertIn("Discuss task W1 with the author", opening_in)
        self.assertEqual(interactive, "--session %s --auto" % rec["sid"])
        self.assertEqual(self.chats.state("W1")["messages"][-1]["text"], "Reading.\nI read it. Fine.")
        sid, resume = dfs_chat.claim("W1", "opencode")
        self.assertEqual((sid, resume), (rec["sid"], True))
        r = run(CHAT_SID=sid, CHAT_RESUME="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls()[3:], ["--session %s --auto" % sid])

    def test_an_opencode_stream_is_read_to_the_one_line_a_turn_is_read_from(self):
        step = lambda kind, **p: json.dumps({"type": kind, "sessionID": "ses_1", "part": p})
        ok = dfs_chat.opencode_result([step("step_start"), step("text", text="before the tool"), step("tool_use", tool="bash"),
                                       step("step_finish", reason="tool-calls"), "not json", step("step_start"),
                                       step("text", text="the answer"), step("step_finish", reason="stop")])
        self.assertEqual((ok["session_id"], ok["is_error"], ok["result"]), ("ses_1", False, "the answer"))
        bad = dfs_chat.opencode_result([step("step_start"), json.dumps({"type": "error", "sessionID": "ses_1", "error": {
            "name": "APIError", "data": {"message": "rate limited", "statusCode": 429}}})])
        self.assertEqual((bad["is_error"], bad["result"]), (True, "rate limited"))
        died = dfs_chat.opencode_result([], "kept")
        self.assertEqual((died["session_id"], died["is_error"], died["result"]), ("kept", True, ""))

    def use_kiro(self):
        Path(dfs_chat.agent_file()).parent.mkdir(parents=True, exist_ok=True)
        Path(dfs_chat.agent_file()).write_text("kiro\n")

    def test_a_kiro_conversation_is_read_from_its_session_files_without_its_tools(self):
        sid = "sess_abc"
        self.assertFalse(dfs_chat.has_transcript(sid, "kiro"))
        self.assertEqual(dfs_chat.transcript_messages(sid, "kiro"), [])           # no sessions at all
        subprocess.run([str(self.kiro), "chat", "--v3", "--output-format", "stream-json", "--resume-id", sid, "--",
                        "You are talking with the author about task W1. The author writes: hello"],
                       text=True, capture_output=True, check=True, env=dict(os.environ))
        self.assertTrue(dfs_chat.has_transcript(sid, "kiro"))
        self.assertEqual(dfs_chat.transcript_messages(sid, "kiro"),
                         [dict(role="you", text="hello"), dict(role="agent", text="Let me look."), dict(role="agent", text="I read it. Fine.")])
        self.assertAlmostEqual(dfs_chat.transcript_mtime(sid, "kiro"), time.time(), delta=60)
        self.assertIsNone(dfs_chat.kiro_messages("../../etc"), "an id is a name, not a path")

    def test_the_words_of_a_real_kiro_session_are_its_user_and_assistant_messages(self):
        # fixtures/kiro/messages.jsonl is a real kiro-cli 2.27.1 session (a turn that used a tool,
        # then a resumed one) less its session_start line, the system prompt.
        words = [w for line in (FIXTURES / "messages.jsonl").read_text().splitlines() for w in dfs_chat.kiro_turns(json.loads(line))]
        self.assertEqual([(role, text) for role, text, _ in words],
                         [("you", "please use a tool to list the folder"), ("agent", "Let me look. "), ("agent", "It has one file."),
                          ("you", "-and then?"), ("agent", "It has one file.")],
                         "tool calls and results, turn markers and kiro's notes are not words")
        self.assertEqual(len({key for _, _, key in words}), 5, "each message has the id kiro gave it")
        read = dfs_chat.kiro_turns
        said = lambda **p: {"id": "m", "payload": dict(type="assistant", content="x", **p)}
        self.assertEqual(read(said(operationType="Reasoning")), [], "reasoning is not said")
        self.assertEqual(read({"id": "m", "payload": {"type": "user", "content": "n", "_meta": {"kiro": {"syntheticUserMessageReason": "r"}}}}), [])
        self.assertEqual(read({"id": "m", "payload": {"type": "user", "content": "h", "source": "hook"}}), [])

    @unittest.skipUnless(REAL_KIRO, "no kiro-cli here")
    def test_a_real_kiro_answers_the_page_and_its_session_is_read_back_and_resumed(self):
        # The real kiro-cli, its model a stand-in server (test_kiro_model): the events and session
        # files are kiro's own, the answers are not a model's.
        self.use_kiro()
        os.environ.update(KIRO_CMD="kiro-cli", PATH=os.path.dirname(REAL_KIRO) + os.pathsep + os.environ["PATH"])
        with FakeKiroModel(say="It has one file.") as model:
            os.environ.update(model.env())
            try:
                self.chats.send("W1", "please use a tool to list the folder")
                st = self.settle("W1", timeout=90)
                self.assertEqual([(m["role"], m["text"]) for m in st["messages"]],
                                 [("you", "please use a tool to list the folder"), ("agent", "Let me look."), ("agent", "It has one file.")])
                sid = json.loads(self.chats.path("W1").read_text())["sid"]
                self.assertTrue(sid.startswith("sess_"))
                self.chats.send("W1", "-and then?")
                st = self.settle("W1", timeout=90)
                self.assertEqual([(m["role"], m["text"]) for m in st["messages"]][3:], [("you", "-and then?"), ("agent", "It has one file.")])
                self.assertEqual(json.loads(self.chats.path("W1").read_text())["sid"], sid, "the second turn resumed the first's session")
            finally:
                for k in model.env():
                    os.environ.pop(k, None)

    @unittest.skipUnless(REAL_KIRO, "no kiro-cli here")
    def test_a_real_kiro_that_cannot_reach_its_model_says_so_and_leaves_no_conversation(self):
        self.use_kiro()
        os.environ.update(KIRO_CMD="kiro-cli", PATH=os.path.dirname(REAL_KIRO) + os.pathsep + os.environ["PATH"],
                          KIRO_API_KEY="any", KIRO_KAS_ENDPOINT="http://127.0.0.1:1", KIRO_KAS_CONTROL_PLANE_ENDPOINT="http://127.0.0.1:1")
        try:
            self.chats.send("W1", "hi")
            st = self.settle("W1", timeout=90)
        finally:
            for k in ("KIRO_API_KEY", "KIRO_KAS_ENDPOINT", "KIRO_KAS_CONTROL_PLANE_ENDPOINT"):
                os.environ.pop(k, None)
        self.assertEqual([m["role"] for m in st["messages"]], ["you", "error"])
        self.assertIn("could not load the available models", st["messages"][-1]["text"])
        self.assertFalse(self.chats.path("W1").exists())

    def test_a_kiro_turn_is_answered_kept_and_resumed_by_the_id_kiro_made(self):
        self.use_kiro()
        self.chats.send("W1", "Where is the work?")
        st = self.settle("W1")
        self.assertEqual([(m["role"], m["text"]) for m in st["messages"]],
                         [("you", "Where is the work?"), ("agent", "Let me look."), ("agent", "I read it. Fine.")])
        rec = json.loads(self.chats.path("W1").read_text())
        self.assertEqual(rec["agent"], "kiro")
        self.assertTrue(rec["sid"].startswith("sess_"), "the id is the one kiro printed, not one chosen here")
        self.chats.send("W1", "-and then?")
        st = self.settle("W1")
        first, first_prompt, second, second_prompt = self.calls()
        self.assertEqual(first, "chat --v3 --output-format stream-json --no-interactive --trust-all-tools -- %s" % first_prompt[len("prompt: "):])
        self.assertIn("The author writes: Where is the work?", first_prompt)
        self.assertEqual(second, "chat --v3 --output-format stream-json --no-interactive --trust-all-tools --resume-id %s -- -and then?" % rec["sid"])
        self.assertEqual(second_prompt, "prompt: -and then?", "`--` keeps a message that starts with a dash from being a flag")
        self.assertEqual([m["text"] for m in st["messages"] if m["role"] == "you"], ["Where is the work?", "-and then?"])

    def test_a_failed_kiro_turn_says_why_and_leaves_no_conversation(self):
        self.use_kiro()
        os.environ["CHAT_STUB_FAIL"] = "1"
        self.chats.send("W1", "hi")
        st = self.settle("W1")
        self.assertEqual([m["role"] for m in st["messages"]], ["you", "error"])
        self.assertIn("Not logged in", st["messages"][-1]["text"])
        self.assertFalse(self.chats.path("W1").exists())

    def test_the_screens_kiro_chat_makes_its_opening_turn_then_resumes_the_session_the_page_reads(self):
        self.use_kiro()
        self.assertEqual(dfs_chat.claim("W1", "kiro"), ("", False), "kiro has no id to name for a new conversation")
        run = lambda **env: subprocess.run([str(HERE / "run.sh"), "--kiro", "--chat", "W1"], cwd=self.root, text=True,
                                           capture_output=True, env=dict(os.environ, CHAT_SHARE="1", **env))
        r = run()
        self.assertEqual(r.returncode, 0, r.stderr)
        rec = json.loads(self.chats.path("W1").read_text())
        self.assertEqual(rec["agent"], "kiro")
        opening, opening_prompt, interactive = self.calls()
        self.assertIn("--output-format stream-json --no-interactive", opening)
        self.assertIn("Discuss task W1 with the author", opening_prompt)
        self.assertEqual(interactive, "chat --v3 --resume-id %s --trust-all-tools" % rec["sid"])
        self.assertEqual(self.chats.state("W1")["messages"][-1]["text"], "I read it. Fine.")
        sid, resume = dfs_chat.claim("W1", "kiro")
        self.assertEqual((sid, resume), (rec["sid"], True))
        r = run(CHAT_SID=sid, CHAT_RESUME="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls()[3:], ["chat --v3 --resume-id %s --trust-all-tools" % sid])

    def test_a_real_kiro_stream_is_read_to_the_one_line_a_turn_is_read_from(self):
        lines = lambda name: (FIXTURES / name).read_text().split("\n")
        sid = "sess_f50c96cf-19c1-437c-a19a-74ae279f2da5"
        ok = dfs_chat.kiro_result(lines("turn-with-a-tool.stream.jsonl"))
        self.assertEqual((ok["session_id"], ok["is_error"], ok["result"]), (sid, False, "Let me look. It has one file."))
        resumed = dfs_chat.kiro_result(lines("resumed-turn.stream.jsonl"))
        self.assertEqual((resumed["session_id"], resumed["is_error"], resumed["result"]), (sid, False, "It has one file."))
        chunks = dfs_chat.kiro_result([l for l in lines("turn-with-a-tool.stream.jsonl") if '"runFinished"' not in l])
        self.assertEqual((chunks["is_error"], chunks["result"]), (False, "It has one file."),
                         "without a final text the answer is what was said since the last tool call")
        out = dfs_chat.kiro_result(lines("not-logged-in.stream.jsonl"))
        self.assertEqual((out["session_id"], out["is_error"]), ("", True))
        self.assertTrue(out["result"].startswith("Not logged in."), out["result"])
        down = dfs_chat.kiro_result(lines("model-unavailable.stream.jsonl"))
        self.assertEqual((down["is_error"], down["session_id"].startswith("sess_")), (True, True))
        self.assertIn("could not load the available models", down["result"])
        # the `kiro-cli` wrapper, logged out, tries a browser instead: its spinner and escapes are not the reason
        login = dfs_chat.kiro_result((FIXTURES / "browser-login.output").read_text().split("\n"), "kept")
        self.assertEqual((login["session_id"], login["is_error"]), ("kept", True))
        self.assertTrue(login["result"].startswith("Failed to open browser for authentication."), repr(login["result"]))
        self.assertNotIn("\x1b", login["result"])
        self.assertEqual(dfs_chat.kiro_result([])["result"], "the turn ended without an answer")

    def test_the_screens_chat_env_names_what_the_agent_can_share(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("dfs_tui", HERE / "tui.py")
        tui = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tui)
        ui = object.__new__(tui.UI)
        ui.agent = "claude"
        env = ui.chat_env("W1")
        self.assertTrue(env["CHAT_SID"])
        self.assertNotIn("CHAT_SHARE", env)
        self.chats.path("W1").unlink()
        ui.agent = "codex"
        env = ui.chat_env("W1")
        self.assertNotIn("CHAT_SID", env)                    # codex makes the id; run.sh records it
        self.assertEqual(env["CHAT_SHARE"], "1")
        ui.agent = "opencode"
        env = ui.chat_env("W1")
        self.assertNotIn("CHAT_SID", env)                    # opencode makes the id too
        self.assertEqual(env["CHAT_SHARE"], "1")
        ui.agent = "kiro"
        env = ui.chat_env("W1")
        self.assertNotIn("CHAT_SID", env)                    # and kiro
        self.assertEqual(env["CHAT_SHARE"], "1")
        ui.agent = "someagent"
        self.assertIsNone(ui.chat_env("W1"))                 # one with no chat here

    def test_the_screens_chat_runs_the_conversations_agent_not_what_x_names_now(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("dfs_tui", HERE / "tui.py")
        tui = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tui)
        started = []
        class Host:
            def ensure(s, scope, make, agent="claude"):
                argv, env, cwd = make()
                started.append((scope, argv, env, agent))
                return s
            def send(s, text):
                started.append(("sent", text))
                return True
        ui = object.__new__(tui.UI)
        ui.chat_host = Host()
        ui.agent = "claude"
        sid, _ = dfs_chat.claim("W1")                        # a claude conversation, begun
        ui.agent = "codex"                                   # then `x` names another agent
        self.assertEqual(ui.chat_agent("W1"), "claude")
        self.assertEqual(ui.chat_agent("W2"), "codex")       # nothing begun there: `x` decides
        ui.start_chat("W1")
        scope, argv, env, agent = started[-1]
        self.assertEqual(agent, "claude")
        self.assertNotIn("--codex", argv)
        self.assertEqual(env.get("CHAT_SID"), sid)           # the page's session, not a new one
        self.assertNotIn("CHAT_SHARE", env)
        # the page's message reaches that chat while the agents differ
        self.assertTrue(tui.UiWalker(ui).chat_send("W1", "hello"))
        self.assertEqual(started[-1], ("sent", "hello"))
        # and a codex conversation stays codex's when the screen's agent is claude
        self.chats.save_sid("W3", "thread-1", "codex")
        ui.agent = "claude"
        self.assertEqual(ui.chat_agent("W3"), "codex")
        ui.start_chat("W3")
        self.assertEqual((started[-1][3], "--codex" in started[-1][1]), ("codex", True))

    def test_a_turn_records_the_agent_with_the_session(self):
        self.chats.send("W1", "hello")
        self.settle("W1")
        self.assertEqual(json.loads(self.chats.path("W1").read_text())["agent"], "claude")

    def test_it_will_not_send_while_the_screens_chat_is_open_on_the_session(self):
        sid, _ = dfs_chat.claim("W1")
        # a bash under the screen's name, not sleep: a multicall sleep (nix coreutils) refuses to run as
        # "claude", and without the "; :" bash would exec the sleep and lose the name
        screen = subprocess.Popen(["bash", "-c", 'exec -a "claude --session-id %s" bash -c "sleep 30; :"' % sid])
        try:
            time.sleep(0.3)
            self.assertTrue(self.chats.state("W1")["elsewhere"])
            with self.assertRaises(ValueError) as cm:
                self.chats.send("W1", "hi")
            self.assertIn("another terminal has this conversation open", str(cm.exception))
            self.assertIn("message refused, another terminal has session", self.record("W1"))
        finally:
            screen.kill()
            screen.wait()
        time.sleep(0.2)
        self.assertFalse(self.chats.state("W1")["elsewhere"])

    def test_the_pages_message_is_typed_into_the_screens_live_chat(self):
        class Screen:
            has_screen = True
            def __init__(s): s.typed, s.opened, s.live, s.closed, s.talking = [], [], False, [], False
            def chat_live(s, scope): return s.live
            def chat_active(s, scope): return s.talking
            def chat_close(s, scope): s.closed.append(scope)
            def chat_open(s, scope): s.opened.append(scope); return True
            def chat_send(s, scope, text):
                if not s.live: return False
                s.typed.append((scope, text)); return True
        screen = Screen()
        chats = dfs_chat.Chats(walker=lambda: screen)
        # opening on the page opens it on the screen
        st = chats.open("W1")
        self.assertEqual(screen.opened, ["W1"])
        self.assertTrue(st["screen"])
        self.assertFalse(st["live"])
        # not open there yet: the page's own turn runs, in the one session
        chats.send("W1", "first")
        end = time.time() + 20
        while chats.state("W1")["busy"] and time.time() < end:
            time.sleep(0.05)
        self.assertEqual(screen.typed, [])
        self.assertEqual(len(self.calls()), 1)
        # open there: the message is typed in, no second agent starts, and it shows at once
        screen.live = True
        sid, _ = dfs_chat.claim("W1")
        proc = subprocess.Popen(["bash", "-c", 'exec -a "claude --session-id %s" sleep 30' % sid])
        try:
            time.sleep(0.3)
            st = chats.send("W1", "second")
            self.assertEqual(screen.typed, [("W1", "second")])
            self.assertEqual(len(self.calls()), 1)
            self.assertTrue(st["live"])
            self.assertFalse(st["elsewhere"])           # the screen's own claude is not "elsewhere"
            self.assertEqual(st["messages"][-1], dict(role="you", text="second"))
        finally:
            proc.kill()
            proc.wait()

    def test_the_chats_section_lists_every_conversation_newest_first(self):
        class Screen:
            has_screen = True
            live = {"W1"}
            talking = set()
            def chat_live(s, scope): return scope in s.live
            def chat_active(s, scope): return scope in s.talking
            def chat_close(s, scope): s.live.discard(scope)
            def chat_open(s, scope): return True
            def chat_send(s, scope, text): return False
        screen = Screen()
        chats = dfs_chat.Chats(walker=lambda: screen)
        self.assertEqual(chats.list(), [])                      # nothing said, nothing running elsewhere
        (self.root / ".dfs" / "items" / "W2.md").write_text("# W2 · other\n\n## Goal\n\nMore.\n")
        subprocess.run("git add -A && git -c user.name=t -c user.email=t@t commit -qm y", shell=True, cwd=self.root, check=True)
        chats.send("W1", "about one")
        self.settle_on(chats, "W1")
        time.sleep(1.1)
        chats.send("W2", "about two")
        self.settle_on(chats, "W2")
        rows = chats.list()
        self.assertEqual([r["scope"] for r in rows], ["W2", "W1"])
        self.assertEqual(rows[0]["role"], "agent")
        self.assertTrue(rows[0]["last"])
        self.assertTrue(rows[1]["live"] and not rows[0]["live"])
        screen.talking.add("W1")
        self.assertTrue([r for r in chats.list() if r["scope"] == "W1"][0]["busy"])
        # a new chat ends the agent running on it, and it leaves the list once it is empty
        screen.talking.clear()
        chats.clear("W1")
        self.assertNotIn("W1", screen.live)
        self.assertEqual([r["scope"] for r in chats.list()], ["W2"])

    def test_a_new_chat_opens_with_the_summary_of_assumptions_and_raises_and_an_old_one_does_not(self):
        import walk as dfs_walk
        chats = dfs_chat.Chats(walker=lambda: dfs_walk.HeadlessWalk())
        st = chats.open("W1")
        self.assertFalse(st["screen"])
        self.settle_on(chats, "W1")
        self.assertEqual(len(self.calls()), 1)
        asked = self.calls()[0]
        for want in ("concise summary", "assumption", "raise", "refuted", "what would make it wrong"):
            self.assertIn(want, asked)
        st = chats.state("W1")
        self.assertEqual([m["role"] for m in st["messages"]], ["agent"])    # the opening prompt is the tool's, not yours
        chats.open("W1")                                                     # begun already: left as it is
        self.settle_on(chats, "W1")
        self.assertEqual(len(self.calls()), 1)
        chats.send("W1", "hi")
        self.settle_on(chats, "W1")
        self.assertEqual([m["role"] for m in chats.state("W1")["messages"]], ["agent", "you", "agent"])
        self.assertNotIn("concise summary", self.calls()[1])                 # the second turn is just the message
        # a new chat is a new opening
        chats.clear("W1")
        chats.open("W1")
        self.settle_on(chats, "W1")
        self.assertEqual(len(self.calls()), 3)

    def test_the_projects_chat_opens_with_the_roadmap_not_a_task_tree(self):
        import walk as dfs_walk
        chats = dfs_chat.Chats(walker=lambda: dfs_walk.HeadlessWalk())
        chats.open("project")
        self.settle_on(chats, "project")
        self.assertIn("summarising the roadmap", self.calls()[0])
        self.assertNotIn("assumption", self.calls()[0])

    def settle_on(self, chats, scope, timeout=20):
        end = time.time() + timeout
        while time.time() < end and chats.state(scope)["busy"]:
            time.sleep(0.05)

    def test_the_project_has_its_own_chat(self):
        self.chats.send("project", "how are we doing?")
        st = self.settle("project")
        self.assertIn("the project as a whole", self.calls()[0])
        self.assertEqual(self.chats.state("W1")["messages"], [])
        self.assertEqual(len(st["messages"]), 2)

    def test_a_failed_turn_says_so_and_the_chat_can_go_on(self):
        os.environ["CHAT_STUB_FAIL"] = "1"
        self.chats.send("W1", "hello")
        st = self.settle("W1")
        self.assertEqual(st["messages"][-1]["role"], "error")
        self.assertIn("not logged in", st["messages"][-1]["text"])
        os.environ.pop("CHAT_STUB_FAIL")
        self.chats.send("W1", "again")
        st = self.settle("W1")
        self.assertEqual(st["messages"][-1]["role"], "agent")
        self.assertNotIn("error", [m["role"] for m in st["messages"]], "the error goes once the chat moves on")

    def record(self, scope):
        return (self.chats.path(scope).with_suffix(".log")).read_text()

    def test_a_turn_leaves_a_record_and_a_failed_one_keeps_its_reason_past_a_restart(self):
        self.chats.send("W1", "hello")
        self.settle("W1")
        log = self.record("W1")
        self.assertIn("turn: message, 5 characters, session (new)", log)
        self.assertIn("turn: answered after", log)
        os.environ["CHAT_STUB_FAIL"] = "1"
        self.chats.send("W1", "again")
        self.settle("W1")
        log = self.record("W1")
        self.assertIn("turn: FAILED after", log)
        self.assertIn("rc=1", log)
        self.assertIn("boom: not logged in", log)           # the reason is on disk, not only in `notes`
        self.assertIn("| ", log)

    def test_a_timed_out_turn_logs_what_the_agent_had_said(self):
        for k in ("TURN_TIMEOUT", "TURN_GRACE"):
            self.addCleanup(setattr, dfs_chat, k, getattr(dfs_chat, k))
        dfs_chat.TURN_TIMEOUT, dfs_chat.TURN_GRACE = 1, 1
        self.chats.argv = lambda: ["bash", "-c", "echo reading the roadmap; echo waiting on a dialog >&2; exec sleep 30", "x"]
        self.chats.send("W1", "hello")
        self.settle("W1")
        log = self.record("W1")
        self.assertIn("turn: FAILED after", log)
        self.assertIn("no answer in", log)
        self.assertIn("reading the roadmap", log)
        self.assertIn("waiting on a dialog", log)

    def test_a_refused_send_and_a_new_chat_are_recorded(self):
        self.chats.busy["W1"] = time.time()
        with self.assertRaises(ValueError):
            self.chats.send("W1", "second")
        self.chats.busy.clear()
        self.assertIn("message refused, the last turn", self.record("W1"))
        self.chats.clear("W1")
        self.assertIn("page: new chat", self.record("W1"))

    def test_one_turn_at_a_time_and_clear_waits_for_it(self):
        os.environ["CHAT_STUB_SAY"] = "slowly"
        self.chats.busy["W1"] = time.time()                  # a turn in flight
        with self.assertRaises(ValueError):
            self.chats.send("W1", "second")
        with self.assertRaises(ValueError):
            self.chats.clear("W1")
        self.chats.busy.clear()

    def test_clear_starts_over(self):
        self.chats.send("W1", "hello")
        self.settle("W1")
        old = self.chats.sid("W1")
        self.assertEqual(self.chats.clear("W1")["messages"], [])
        self.chats.send("W1", "fresh")
        self.settle("W1")
        self.assertIn("--session-id", self.calls()[-1], "a new session, not a resume of the old one")
        self.assertNotEqual(self.chats.sid("W1"), old)

    def test_what_is_not_a_chat_is_refused(self):
        for scope in ("", "../x", "W9", "a b", None):
            with self.assertRaises(ValueError, msg=repr(scope)):
                self.chats.send(scope, "hi")
        for message in ("", "   ", "x" * 4001):
            with self.assertRaises(ValueError):
                self.chats.send("W1", message)

    def test_under_another_agent_it_says_so_rather_than_run_it(self):
        Path(dfs_paths.state() / "runs").mkdir(parents=True, exist_ok=True)
        (dfs_paths.state() / "runs" / "agent").write_text("kiro\n")
        self.addCleanup(setattr, dfs_chat, "CHAT_AGENTS", dfs_chat.CHAT_AGENTS)
        dfs_chat.CHAT_AGENTS = ("claude", "codex", "opencode")      # an agent a chat does not run under
        with self.assertRaises(ValueError) as cm:
            self.chats.send("W1", "hi")
        self.assertIn("claude, codex or opencode", str(cm.exception))


class TurnTimeout(unittest.TestCase):
    """A turn nobody is waiting on is asked to stop as ctrl-c would, and killed only if it won't."""

    def setUp(self):
        self.addCleanup(setattr, dfs_chat, "TURN_TIMEOUT", dfs_chat.TURN_TIMEOUT)
        self.addCleanup(setattr, dfs_chat, "TURN_GRACE", dfs_chat.TURN_GRACE)
        dfs_chat.TURN_TIMEOUT, dfs_chat.TURN_GRACE = 1, 1
        self.dir = Path(tempfile.mkdtemp())

    def run_turn(self, script):
        with self.assertRaises(subprocess.TimeoutExpired):
            dfs_chat.run_turn(["bash", "-c", script], "hello", None, dict(os.environ))

    def test_the_timeout_sends_sigterm_first(self):
        self.run_turn("trap 'echo term > %s/saw; exit' TERM; while :; do sleep 0.1; done" % self.dir)
        self.assertEqual((self.dir / "saw").read_text().strip(), "term")

    def test_an_agent_that_ignores_sigterm_is_killed_after_the_grace(self):
        t = time.time()
        self.run_turn("trap '' TERM; while :; do sleep 0.1; done")
        self.assertLess(time.time() - t, 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
