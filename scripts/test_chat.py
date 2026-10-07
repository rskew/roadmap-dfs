#!/usr/bin/env python3
"""chat.py and `run.sh --chat-turn`: a basic chat about a task or the project, read-only.
The agent is a stub that records how it was called and answers in claude's JSON.

Run:  python3 roadmap-dfs/scripts/test_chat.py
"""
import json
import os
import shutil
import stat
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

STUB = r"""#!/usr/bin/env bash
# a claude that records its arguments, keeps a transcript as claude does, and answers in -p JSON
echo "$@" >> "$CHAT_STUB_LOG"
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
        self._env = {k: os.environ.get(k) for k in ("CLAUDE_CMD", "CHAT_STUB_LOG", "CHAT_STUB_FAIL", "CHAT_STUB_SAY", "HOME")}
        os.environ.update(CLAUDE_CMD=str(self.stub), CHAT_STUB_LOG=str(self.log), HOME=str(self.root / "home"))
        (self.root / "home").mkdir()
        os.environ.pop("CHAT_STUB_FAIL", None)
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

    def test_a_turn_is_answered_and_kept(self):
        st = self.chats.send("W1", "Where is the work?")
        self.assertTrue(st["busy"])
        st = self.settle("W1")
        self.assertEqual([(m["role"], m["text"]) for m in st["messages"]],
                         [("you", "Where is the work?"), ("agent", "I read it. Fine.")])
        self.assertGreaterEqual(self.changes, 2, "the page is told when it starts and when it ends")
        self.assertTrue(self.chats.path("W1").exists(), "the session id is kept under the run root, not in the roadmap")
        self.assertEqual(list(self.chats.path("W1").parts[-3:-1]), ["runs", "chat"])

    def test_it_is_read_only_and_not_a_permission_skipper(self):
        self.chats.send("W1", "hello")
        self.settle("W1")
        first = self.calls()[0]
        self.assertIn("--allowedTools Read Grep Glob", first)
        self.assertIn("--disallowedTools Bash Edit Write NotebookEdit", first)
        self.assertNotIn("dangerously", first)
        self.assertIn("--output-format json", first)
        self.assertIn("task W1", first)                       # the framing, on the first turn
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

    def test_it_will_not_send_while_the_screens_chat_is_open_on_the_session(self):
        sid, _ = dfs_chat.claim("W1")
        screen = subprocess.Popen(["bash", "-c", 'exec -a "claude --session-id %s" sleep 30' % sid])
        try:
            time.sleep(0.3)
            self.assertTrue(self.chats.state("W1")["elsewhere"])
            with self.assertRaises(ValueError) as cm:
                self.chats.send("W1", "hi")
            self.assertIn("another terminal has this conversation open", str(cm.exception))
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
        (dfs_paths.state() / "runs" / "agent").write_text("codex\n")
        with self.assertRaises(ValueError) as cm:
            self.chats.send("W1", "hi")
        self.assertIn("claude", str(cm.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
