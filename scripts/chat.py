#!/usr/bin/env python3
"""A chat about a task, or about the project, that is THE chat session the screen's `c` opens.

One conversation per scope (a task, or `project`): an agent session (claude, codex, opencode
or kiro: the one it began under) whose id is kept in a
small file under the run root. The terminal's `c` (and `C`) starts or RESUMES that session
interactively; the web page resumes the same one a turn at a time, headless, with the same
permission-skipping flags as the screen's chat (`run.sh --chat-turn`): every tool works and
nothing is refused. What the page shows is read from the session's own transcript, so a message
typed at the screen shows on the page and one typed on the page shows when `c` is next opened.
It is the same conversation, not a copy.

With the terminal screen serving the page, a chat started on the page is an interactive agent that
screen runs in the BACKGROUND (ptyrelay.py): any number at once, one per task and one for the project,
none of them on the screen unless you ask. The page types into them and reads them from the
transcript; the screen's `c` attaches to the running one (ctrl-] leaves it running). They end with
the screen, with "New chat", or after DFS_CHAT_IDLE seconds (30 minutes; 0 never) with nobody using
them: the conversation stays, and the next message resumes it. With no screen (web.py alone) the page runs its own turns. An agent
running on the session in some OTHER terminal would tangle it, so then the page does not send.

Each scope keeps a log, `<run root>/chat/<scope>.log` (chatlog.py): what the page sent and what became of
it, each headless turn and why one failed, and, for a background chat, its start, its exit with the agent's
last screen, who closed it and a note when it went quiet after a message (DFS_CHAT_STALL).

It spends a subscription, as a chain does, so the page gates it behind the same switch as the
walk (`--no-walk`): a server that may not start agents does not chat either.
"""
import glob
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths as dfs_paths    # noqa: E402
import runs as dfs_runs      # noqa: E402
import tree as dfs_tree      # noqa: E402
import context as dfs_context   # noqa: E402
import chatlog   # noqa: E402
import uuid
from walk import AGENTS, agent_file, read_agent   # noqa: E402


def signal_turn(p, sig):
    """Signal a turn's whole process group: whatever the agent started holds its output open, and
    the turn would not end until that did too."""
    try:
        os.killpg(p.pid, sig)
    except OSError:
        pass


def run_turn(argv, message, cwd, env, on_start=None):
    """`subprocess.run` with the agent's end done as ctrl-c would: on a timeout it is sent
    SIGTERM (the whole process group, so the agent and what it started; claude is run.sh's own exec, the others
    sit in a pipeline into chat.py's reader) and
    SIGKILLed only if it has not gone after a grace. `run` itself would SIGKILL at once."""
    p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, cwd=cwd, env=env, start_new_session=True)
    if on_start:
        on_start(p)         # so a Stop from the page has the process to end
    try:
        out, err = p.communicate(message, timeout=TURN_TIMEOUT)
    except subprocess.TimeoutExpired as e:
        signal_turn(p, signal.SIGTERM)
        try:
            out, err = p.communicate(timeout=TURN_GRACE)
        except subprocess.TimeoutExpired:
            signal_turn(p, signal.SIGKILL)
            out, err = p.communicate()
        e.output, e.stderr = out, err      # what the agent had said, for the log of a hang
        raise
    return subprocess.CompletedProcess(argv, p.returncode, out, err)


SCOPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]*$")
MAX_MESSAGE = 4000
TURN_GRACE = 5              # seconds an agent asked to stop has before it is killed
TURN_TIMEOUT = 600          # seconds; a turn that takes longer is one nobody is waiting on


def transcript_messages(sid, agent="claude"):
    """The human turns and the agent's words in a session's transcript, in order:
    [{role: you|agent, text}]. Tool calls and results, thinking, and the harness's own
    notes are not part of a conversation, and the opening prompt the screen's chat starts
    with is the screen's, not something the author said. Each agent keeps its transcript
    in its own shape (`READERS`: a file of JSON lines, or opencode's sqlite store); one with
    no reader here reads as no conversation yet."""
    out, agent_at = [], {}
    for role, text, key in turns(sid, agent):
        text = text.strip()
        if not text:
            continue
        if role == "you":
            if text.startswith(("<command-", "<local-command", "<system-reminder")):
                continue
            if "The author writes: " in text:                 # the page's first turn carries framing
                text = text.rsplit("The author writes: ", 1)[1].strip()
            elif OPENING.match(text) or PAGE_OPENING.match(text):   # an opening prompt: the tool's words
                continue
            out.append(dict(role="you", text=text))
        elif key and key in agent_at:                        # one message, one line per block
            out[agent_at[key]]["text"] += "\n" + text
        else:
            agent_at[key] = len(out)
            out.append(dict(role="agent", text=text))
    return out


def authors(messages):
    """How many of a conversation's messages are the author's."""
    return sum(m["role"] == "you" for m in messages)


def turns(sid, agent):
    """(role, text, message key) for each piece of a session's transcript, as its agent's
    reader finds them: a line at a time from the transcript file, or from a store."""
    read = READERS.get(agent)
    if not sid or not read:
        return
    if agent in STORES:
        yield from read(sid)
        return
    path = transcript_file(sid, agent)
    if not path or not os.path.exists(path):
        return
    try:
        fh = open(path, errors="replace")
    except OSError:
        return
    with fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            yield from read(rec)


def claude_turns(rec):
    """(role, text, message id) for what one line of claude's jsonl transcript says."""
    msg = rec.get("message") or {}
    content = msg.get("content")
    if rec.get("type") == "user" and not rec.get("isMeta") and not rec.get("isSidechain"):
        text = content if isinstance(content, str) else "\n".join(
            b.get("text", "") for b in (content or []) if isinstance(b, dict) and b.get("type") == "text")
        return [("you", text, None)]
    if rec.get("type") == "assistant" and not rec.get("isSidechain") and isinstance(content, list):
        return [("agent", "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"),
                 msg.get("id"))]
    return []


def codex_turns(rec):
    """(role, text, item id) for one record of a codex rollout. The conversation is its
    `item_completed` events: a `UserMessage` is what was typed, without the environment and
    instructions codex sends beside it as user messages of its own, and an `AgentMessage`
    is what it said (a `commentary` phase between tool calls as well as the final answer).
    Read from codex-cli 0.162.0's rollout, `exec` and `exec resume` both appending to one file."""
    payload = rec.get("payload") if rec.get("type") == "event_msg" else None
    if not isinstance(payload, dict) or payload.get("type") != "item_completed":
        return []
    item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
    role = {"UserMessage": "you", "AgentMessage": "agent"}.get(item.get("type"))
    if not role:
        return []
    text = "\n".join(b.get("text", "") for b in item.get("content") or []
                     if isinstance(b, dict) and isinstance(b.get("text"), str))
    return [(role, text, item.get("id"))]


def opencode_db():
    """opencode's sqlite store (`opencode.db`), where all its sessions live. Under
    $XDG_DATA_HOME/opencode, or ~/.local/share/opencode, as opencode itself finds it."""
    data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(data, "opencode", "opencode.db")


def opencode_query(sql, args=()):
    """Rows from opencode's store, opened read-only (it is opencode's, and may be in use);
    none if there is no store or it cannot be read."""
    db = opencode_db()
    if not os.path.exists(db):
        return []
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=2)
        try:
            return con.execute(sql, args).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return []


def opencode_turns(sid):
    """(role, text, message id) for an opencode session, from its `message` and `part`
    tables (opencode 1.18.35): a message's `data` holds its role, and its text is its `text`
    parts in order. Tool, patch and step parts are not words, and a `synthetic` or `ignored`
    text is opencode's own note to the model."""
    rows = opencode_query(
        "select m.id, m.data, p.data from message m join part p on p.message_id = m.id "
        "where m.session_id = ? order by m.time_created, m.id, p.time_created, p.id", (sid,))
    out = []
    for mid, mdata, pdata in rows:
        try:
            role = json.loads(mdata).get("role")
            part = json.loads(pdata)
        except (ValueError, AttributeError):
            continue
        if role not in ("user", "assistant") or not isinstance(part, dict) or part.get("type") != "text":
            continue
        if part.get("synthetic") or part.get("ignored") or not isinstance(part.get("text"), str):
            continue
        out.append(("you" if role == "user" else "agent", part["text"], mid))
    return out


def kiro_home():
    """Where kiro-cli keeps its `.kiro` directory: $KIRO_HOME, else the home directory."""
    return os.environ.get("KIRO_HOME") or os.path.expanduser("~")


def kiro_messages(sid):
    """The `messages.jsonl` of a kiro session, or None. kiro-cli 2.27.1's `--v3` sessions are
    `sess_<uuid>` directories (`session.json`, `messages.jsonl`) under `.kiro/sessions/`, in a
    bucket per workspace (as a real run writes them); its sqlite database holds only auth and history."""
    if not sid or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}", sid) or ".." in sid:
        return None
    root = os.path.join(kiro_home(), ".kiro", "sessions")
    for pattern in (os.path.join(root, "*", sid, "messages.jsonl"), os.path.join(root, sid, "messages.jsonl")):
        found = sorted(glob.glob(pattern))
        if found:
            return found[0]
    return None


def kiro_turns(rec):
    """(role, text, message id) for one line of a kiro session's `messages.jsonl`, which a real
    kiro writes as `{"id", "timestamp", "payload": {"type", ...}}`. The words are a `user`
    payload's `content` and an `assistant` payload's `content` when it is something said
    (`operationType` Say). Tool calls and results, reasoning, summaries, kiro's notes and turn
    markers are not words, nor is a message kiro sent itself (a hook's, a notification, a
    synthetic one)."""
    body = rec.get("payload")
    if not isinstance(body, dict) or not isinstance(body.get("content"), str):
        return []
    meta = (body.get("_meta") or {}).get("kiro") or {}
    if body.get("type") == "user":
        if body.get("source") == "hook" or meta.get("syntheticUserMessageReason") or meta.get("notification"):
            return []
        role = "you"
    elif body.get("type") == "assistant" and body.get("operationType", "Say") == "Say":
        role = "agent"
    else:
        return []
    return [(role, body["content"], rec.get("id"))]


READERS = dict(claude=claude_turns, codex=codex_turns, opencode=opencode_turns, kiro=kiro_turns)
STORES = ("opencode",)       # the agents whose conversation is read from a store, not a transcript file


def transcript_file(sid, agent):
    """The file a session's transcript is in, or None (kiro's is its own `messages.jsonl`:
    `dfs_context.transcript_path` gives a chain's run log for kiro, which a chat has none of)."""
    if not sid:
        return None
    return kiro_messages(sid) if agent == "kiro" else dfs_context.transcript_path(sid, agent)


def has_transcript(sid, agent):
    """Whether this session has a conversation to resume yet (one the screen opened and the
    author left at once has none, and the agent would refuse to resume it)."""
    if not sid:
        return False
    if agent in STORES:
        return bool(opencode_query("select 1 from session where id = ?", (sid,)))
    p = transcript_file(sid, agent)
    return bool(p and os.path.exists(p))


def transcript_mtime(sid, agent):
    """When the conversation last changed, or None."""
    if agent in STORES:
        rows = opencode_query("select time_updated from session where id = ?", (sid,))
        return rows[0][0] / 1000 if rows else None
    p = transcript_file(sid, agent)
    try:
        return os.path.getmtime(p) if p else None
    except OSError:
        return None


CHAT_AGENTS = ("claude", "codex", "opencode", "kiro")      # the agents a chat can run under, here and on the screen


def codex_result(lines, sid=""):
    """One turn of `codex exec --json` as the single JSON line a turn is read from, the shape
    claude's `--output-format json` prints: {session_id, is_error, result}. The id is the
    stream's `thread.started` (codex mints it, on a resumed turn too), the result is the last
    `agent_message`, and a failure is an `error` or `turn.failed` event. With nothing said at
    all, `result` is empty and the caller has the agent's stderr."""
    answer, error, done = "", "", False
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        kind = rec.get("type")
        if kind == "thread.started":
            sid = rec.get("thread_id") or sid
        elif kind == "item.completed" and isinstance(rec.get("item"), dict) and rec["item"].get("type") == "agent_message":
            answer = rec["item"].get("text") or answer
        elif kind == "error":
            error = rec.get("message") or error
        elif kind == "turn.failed":
            error = (rec.get("error") or {}).get("message") or error or "the turn failed"
        elif kind == "turn.completed":
            done = True
    failed = bool(error) or not done
    return dict(type="result", session_id=sid, is_error=failed, result=(error or answer) if failed else answer)


def opencode_result(lines, sid=""):
    """One turn of `opencode run --format json` as the single JSON line a turn is read from
    ({session_id, is_error, result}, as codex_result). Every event carries the `sessionID`
    (opencode mints it, on a resumed turn too). The answer is the `text` of the last step,
    after its tool calls; a failure is an `error` event (its `error.data.message`), and a
    stream that never finished a step on `stop` is one too."""
    answer, error, done = [], "", False
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        sid = rec.get("sessionID") or sid
        kind, part = rec.get("type"), rec.get("part") if isinstance(rec.get("part"), dict) else {}
        if kind == "step_start":
            answer = []
        elif kind == "text" and isinstance(part.get("text"), str):
            answer.append(part["text"])
        elif kind == "step_finish":
            done = part.get("reason") == "stop"
        elif kind == "error":
            err = rec.get("error") if isinstance(rec.get("error"), dict) else {}
            error = (err.get("data") or {}).get("message") or err.get("message") or err.get("name") or error or "the turn failed"
    text = "".join(answer).strip()
    failed = bool(error) or not done
    return dict(type="result", session_id=sid, is_error=failed, result=(error or text) if failed else text)


KIRO_LOG = re.compile(r"^\[(INFO|DEBUG|WARN|ERROR)\]|^\(Use `node ")
KIRO_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|[▰▱]{7} [^▰▱]*?\(\^\) \+ C to cancel")


def kiro_result(lines, sid=""):
    """One turn of `kiro-cli chat --output-format stream-json` as the single JSON line a turn is
    read from ({session_id, is_error, result}, as codex_result). Each line is `{"type", "data"}`:
    `runStarted`, `sessionUpdate`s (ACP's, the session's id in `data.sessionId`), then
    `runFinished` (`data.finalText`, the turn's words) or `runError` (`data.message`). Without a
    final text the answer is what was said since the last tool call. Lines that are not JSON are
    kiro's own words (its log, a login it could not open) and the error when no answer came."""
    chunks, final, noise, error = [], None, [], ""
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            line = KIRO_ANSI.sub("", line).strip()
            if line and not KIRO_LOG.match(line):
                noise.append(line)
            continue
        if not isinstance(rec, dict):
            continue
        data = rec.get("data") if isinstance(rec.get("data"), dict) else {}
        sid = sid or dfs_context.kiro_session_id([rec])
        if rec.get("type") == "runFinished":
            if isinstance(data.get("finalText"), str):
                final = data["finalText"]
            if data.get("status") not in (None, "success"):
                error = "the turn ended: %s" % (data.get("stopReason") or data["status"])
        elif rec.get("type") == "runError":
            error = data.get("message") or "the turn failed"
        for u in dfs_context.acp_updates(rec):
            kind, content = u.get("sessionUpdate"), u.get("content")
            if kind == "tool_call":
                chunks = []
            elif kind == "agent_message_chunk" and isinstance(content, dict) and isinstance(content.get("text"), str):
                chunks.append(content["text"])
    answer = (final if final is not None else "".join(chunks)).strip()
    failed = bool(error) or not answer
    reason = error or "\n".join(noise[-3:]) or "the turn ended without an answer"
    return dict(type="result", session_id=sid, is_error=failed, result=reason if failed else answer)


PAGE_OPENING = re.compile(r"^You are talking with the author about ", re.S)
OPENING = re.compile(r"^Read \S*ROADMAP\.md\b.*(Discuss|discuss)", re.S)


def running_elsewhere(sid, own=(), agent="claude"):
    """Whether some process other than ours has the agent running on this session: the
    screen's chat, open now. Found by its command line (it names the session id)."""
    me = {os.getpid(), *own}
    try:
        pids = [int(p) for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return False
    for pid in pids:
        if pid in me:
            continue
        try:
            cmd = Path("/proc/%d/cmdline" % pid).read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        if sid in cmd and agent in cmd and "--chat-turn" not in cmd:
            return True
    return False


class Chats:
    def __init__(self, on_change=None, walker=lambda: None):
        self.walker = walker        # the screen's side of the chat, if there is a screen
        self.lock = threading.RLock()
        self.on_change = on_change or (lambda: None)
        self.busy = {}      # scope -> when its turn started
        self.pending = {}   # scope -> [(a message sent and not in the transcript yet, how many of the author's messages the transcript holds once it is, how many it held when the first of them was sent)]
        self.notes = {}     # scope -> [errors], until the conversation moves on
        self.procs = {}     # scope -> the process of its headless turn, while it runs
        self.stopped = set()    # scopes whose running turn the author has just stopped
        self.stops = {}     # scope -> the message whose turn was stopped, until the conversation moves on

    # ── the record: just the session id ────────────────────────────────────────────────
    def path(self, scope):
        return Path(dfs_runs.ensure_run_root()) / "chat" / (scope + ".json")

    def check(self, scope):
        if not SCOPE.fullmatch(scope or ""):
            raise ValueError("a chat is about a task, or the project")
        if scope != "project" and not dfs_tree.exists(scope):
            raise ValueError("no task %s" % scope)

    def record(self, scope):
        try:
            rec = json.loads(self.path(scope).read_text())
        except (OSError, ValueError):
            return {}
        return rec if isinstance(rec, dict) else {}

    def sid(self, scope):
        return self.record(scope).get("sid", "")

    def agent(self, scope):
        """The agent this scope's conversation runs under. A conversation keeps the agent it
        began with, since none can resume another's session; one not begun yet takes the
        screen's choice (`x`). A record from before the agent was kept is claude's."""
        rec = self.record(scope)
        if not rec.get("sid"):
            return read_agent(agent_file())
        return rec.get("agent") if rec.get("agent") in AGENTS else "claude"

    def save_sid(self, scope, sid, agent):
        p = self.path(scope)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(sid=sid, agent=agent)))
        os.replace(tmp, p)

    def state(self, scope):
        self.check(scope)
        with self.lock:
            sid, agent = self.sid(scope), self.agent(scope)
            messages = transcript_messages(sid, agent)
            w = self.walker()
            failed = w and getattr(w, "chat_failure", lambda s: None)(scope)
            if failed:      # the screen's chat ended on its own (its opening turn failed): say why, and the message is not sent
                chatlog.log(scope, "the screen's chat ended: %s" % failed)
                self.notes[scope] = [failed]
                self.pending.pop(scope, None)
            # A message is in once the transcript holds the author's messages it was sent after, and one more.
            # Not by its text alone: an agent may record it merged with one typed before it that was never
            # submitted; then its text is within a message recorded since.
            held, texts = authors(messages), [m["text"] for m in messages if m["role"] == "you"]
            unrecorded = [(text, need, base) for text, need, base in self.pending.get(scope, [])
                          if held < need and not any(text in t for t in texts[base:])]
            if unrecorded:
                self.pending[scope] = unrecorded
                messages += [dict(role="you", text=text) for text, _, _ in unrecorded]
            else:
                self.pending.pop(scope, None)
            messages += [dict(role="error", text=t) for t in self.notes.get(scope, [])]
            if scope in self.stops:
                messages.append(dict(role="note", text="Stopped before it answered."))
            started = self.busy.get(scope)
            live = bool(w and w.chat_live(scope))
            talking = bool(live and w.chat_active(scope))      # a background chat that is answering
            return dict(scope=scope, messages=messages, busy=bool(started) or talking,
                        busy_for=int(time.time() - started) if started else 0,
                        screen=bool(w and w.has_screen), live=live,
                        elsewhere=bool(sid) and not started and not live and running_elsewhere(sid, agent=agent),
                        agent=agent)

    def expect(self, scope, message, agent, sid=None):
        """Show `message` until the transcript records it. One sent before an earlier one is recorded
        waits for the author's message after that one."""
        held = authors(transcript_messages(sid or self.sid(scope), agent))
        waiting = self.pending.get(scope, [])
        self.pending[scope] = waiting + [(message, max(held, waiting[-1][1] if waiting else 0) + 1,
                                          waiting[0][2] if waiting else held)]

    # ── a turn ────────────────────────────────────────────────────────────────────────
    def send(self, scope, message):
        self.check(scope)
        message = (message or "").strip()
        if not message:
            raise ValueError("a message is needed")
        if len(message) > MAX_MESSAGE:
            raise ValueError("a message is %d characters or fewer" % MAX_MESSAGE)
        agent = self.agent(scope)
        if agent not in CHAT_AGENTS:
            raise ValueError("chat on the page runs under %s or %s; the agent is %s" % (", ".join(CHAT_AGENTS[:-1]), CHAT_AGENTS[-1], agent))
        w = self.walker()
        with self.lock:
            # A chat running on this screen has the session: type into it, as the keyboard does.
            if scope not in self.busy and w and w.chat_send(scope, message):
                chatlog.log(scope, "page: message of %d characters handed to the screen's chat" % len(message))
                self.expect(scope, message, agent)
                self.notes.pop(scope, None)
                self.stops.pop(scope, None)
                self.on_change()
                return self.state(scope)
            if scope in self.busy:
                chatlog.log(scope, "page: message refused, the last turn (%ds) is still answering"
                            % (time.time() - self.busy[scope]))
                raise ValueError("it is still answering the last message")
            sid = self.sid(scope)
            if sid and running_elsewhere(sid, agent=agent):
                chatlog.log(scope, "page: message refused, another terminal has session %s open" % sid)
                raise ValueError("another terminal has this conversation open: type there, or "
                                 "close it and send from here")
            self.expect(scope, message, agent, sid)
            self.notes.pop(scope, None)
            self.stops.pop(scope, None)
            self.busy[scope] = time.time()
        threading.Thread(target=self.turn, args=(scope, message, sid, False, agent), daemon=True).start()
        self.on_change()
        return self.state(scope)

    def interrupt(self, scope):
        """Stop pressed: end what the chat is doing and leave the conversation as it is. The screen's
        chat (a background agent) is sent that agent's own interrupt keys (ptyrelay's INTERRUPT); a headless turn's process is ended
        as ctrl-c would, and the turn then says it was interrupted rather than that it failed."""
        self.check(scope)
        w = self.walker()
        with self.lock:
            if scope in self.busy:
                p = self.procs.get(scope)
                if p is not None and p.poll() is not None:     # the agent has answered; the turn is only writing it down
                    chatlog.log(scope, "page: stop, the turn had already answered")
                else:
                    chatlog.log(scope, "page: stop, ending the turn (%ds in)" % (time.time() - self.busy[scope]))
                    self.stopped.add(scope)
                    self.end_turn(scope)
            elif w and (dropped := w.chat_interrupt(scope)) is not None:
                chatlog.log(scope, "page: stop, interrupt sent to the screen's chat")
                if dropped:     # the message was stopped before it was typed: it is not sent, and not shown as sent
                    chatlog.log(scope, "page: stop, %d message(s) were never typed" % dropped)
                    self.pending.pop(scope, None)
                    self.stops[scope] = True
            else:
                chatlog.log(scope, "page: stop, nothing was answering")
        self.on_change()
        return self.state(scope)

    def end_turn(self, scope):
        """Stop the headless turn's process if it has started (else `started` does as it does)."""
        p = self.procs.get(scope)
        if p is None or p.poll() is not None:
            return
        signal_turn(p, signal.SIGTERM)      # the group: the agent, which stops what it started, and the reader after it

        def reap():
            try:
                p.wait(TURN_GRACE)
            except subprocess.TimeoutExpired:
                signal_turn(p, signal.SIGKILL)
        threading.Thread(target=reap, daemon=True).start()

    def started(self, scope, p):
        with self.lock:
            self.procs[scope] = p
            if scope in self.stopped:       # Stop was pressed before the process existed
                self.end_turn(scope)

    def argv(self):
        return [str(dfs_paths.SCRIPTS / "run.sh")]

    def turn(self, scope, message, sid, opening=False, agent="claude"):
        error, new_sid, began, said = "", sid, time.time(), ""
        chatlog.log(scope, "turn: %s, %d characters, session %s" % (
            "opening" if opening else "message", len(message), sid or "(new)"))
        rc = None
        try:
            env = dict(os.environ)
            if opening:
                env["CHAT_OPENING"] = "1"      # nobody has spoken: the turn is the summary, unprompted
            env.pop("RUNDIR", None)
            env.pop("CHAT_SID", None)
            env.pop("CHAT_RESUME", None)
            if not new_sid and agent == "claude":
                # chosen here, not by run.sh, so a first turn that is stopped still has its session
                new_sid = str(uuid.uuid4())
            if new_sid:         # codex, opencode and kiro mint their own: a first turn has none to name, and is read from the stream
                env["CHAT_SID"] = new_sid
            if sid:
                # Resume only a session that has a transcript: one the screen made and the
                # author left at once has none, and `--resume` would refuse it.
                if has_transcript(sid, agent):
                    env["CHAT_RESUME"] = "1"
            r = run_turn(self.argv() + ["--" + agent] * (agent != "claude") + ["--chat-turn", scope], message, dfs_paths.work_root(), env,
                         on_start=lambda p: self.started(scope, p))
            rc = r.returncode
            out = {}
            try:
                out = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {}
            except ValueError:
                pass
            if r.returncode == 0 and isinstance(out, dict) and not out.get("is_error"):
                new_sid = out.get("session_id") or sid
            else:
                error = ((out.get("result") if isinstance(out, dict) else "") or r.stderr.strip()
                         or r.stdout.strip() or "the agent exited %d" % r.returncode)[-600:]
                if not new_sid and isinstance(out, dict):
                    new_sid = out.get("session_id") or ""      # it minted a session before it failed: the message is in it
        except subprocess.TimeoutExpired as e:
            error = "no answer in %d minutes" % (TURN_TIMEOUT // 60)
            said = "\n".join(x for x in (e.stderr, e.output) if isinstance(x, str) and x.strip())[-600:]
        except OSError as e:
            error = "could not start the agent: %s" % e
        except Exception as e:      # the turn's thread would die unseen, and the page wait forever
            error = "the turn failed: %s: %s" % (type(e).__name__, e)
        with self.lock:
            stopped = scope in self.stopped
        if stopped:
            error = ""      # ended on purpose: not a failure, whatever the killed agent left behind
        # a first turn that codex, opencode or kiro did not finish names no session to record, so
        # the message is in one of the agent's own that nothing here knows: say so
        lost = ("%s: %s had not recorded a session to continue" % ("the chat did not open" if opening else "the message was not kept", agent)
                if agent != "claude" and not new_sid and (error or stopped) else "")
        chatlog.log(scope, "turn: %s after %ds, rc=%s, session %s" % (
            "STOPPED" if stopped else "FAILED" if error else "answered", time.time() - began, rc, new_sid or "(none)"),
            (error + "\n" + said) if said else error)
        with self.lock:
            if new_sid and new_sid != sid:
                self.save_sid(scope, new_sid, agent)
            if error or lost:
                self.notes[scope] = [x for x in (error, lost) if x]
            if stopped:
                self.stops[scope] = True
            self.stopped.discard(scope)
            self.procs.pop(scope, None)
            self.busy.pop(scope, None)
        self.on_change()

    def open(self, scope):
        """The page opened this chat. A conversation that has begun is left as it is; a new one
        opens with the summary: the agent the screen runs for it does (that is where the page's
        chats live), and with no screen the page runs the opening turn itself."""
        self.check(scope)
        w = self.walker()
        note = ""
        agent = self.agent(scope)
        if agent in CHAT_AGENTS:
            sid = self.sid(scope)
            began = has_transcript(sid, agent) or bool(w and w.chat_live(scope))
            if not began:
                chatlog.log(scope, "page: opened a new chat%s" % (", on the screen" if w and w.has_screen else ""))
                if w and w.has_screen:
                    try:
                        w.chat_open(scope)
                    except ValueError as e:
                        note = str(e)
                        chatlog.log(scope, "page: the screen would not start the chat: %s" % e)
                else:
                    with self.lock:
                        if scope not in self.busy:
                            self.busy[scope] = time.time()
                            self.notes.pop(scope, None)
                            threading.Thread(target=self.turn, args=(scope, "", sid, True, agent), daemon=True).start()
            self.on_change()
        return dict(self.state(scope), note=note)

    def list(self):
        """Every conversation there is, newest first: what the page's Chats section shows."""
        out = []
        w = self.walker()
        d = Path(dfs_runs.ensure_run_root()) / "chat"
        for p in sorted(d.glob("*.json")) if d.is_dir() else []:
            scope = p.stem
            if not SCOPE.fullmatch(scope) or (scope != "project" and not dfs_tree.exists(scope)):
                continue
            sid, agent = self.sid(scope), self.agent(scope)
            messages = transcript_messages(sid, agent)
            live = bool(w and w.chat_live(scope))
            busy = scope in self.busy or bool(live and w.chat_active(scope))
            if not messages and not live and not busy:
                continue
            last = messages[-1] if messages else dict(role="", text="")
            updated = transcript_mtime(sid, agent) or p.stat().st_mtime
            out.append(dict(scope=scope, count=len(messages), role=last["role"], last=last["text"][:160],
                            updated=updated, live=live, busy=busy, agent=agent))
        return sorted(out, key=lambda c: -c["updated"])

    def clear(self, scope):
        """Start over: the next message is a new session. The old one stays in the agent's own history."""
        self.check(scope)
        with self.lock:
            if scope in self.busy:
                raise ValueError("it is still answering: wait for it")
            w = self.walker()
            chatlog.log(scope, "page: new chat, ending the old one (session %s)" % (self.sid(scope) or "none"))
            if w:
                w.chat_close(scope)                  # the agent running on it goes with it
            try:
                self.path(scope).unlink()
            except OSError:
                pass
            self.pending.pop(scope, None)
            self.notes.pop(scope, None)
            self.stops.pop(scope, None)
        self.on_change()
        return self.state(scope)


def claim(scope, agent="claude"):
    """For the screen's `c`: the session to open for this scope, as (sid, resume). The record
    is made now, before the chat starts, so the page can resume it, and `resume` is whether
    there is a transcript to resume (else it starts the session under that id). An agent that
    mints its own id (codex, opencode, kiro) has none to name for a new conversation: it comes back ("", False)
    and run.sh records the id once the opening turn has made it (`save`)."""
    chats = Chats()
    chats.check(scope)
    sid = chats.sid(scope)
    if sid and chats.agent(scope) != agent:
        raise ValueError("the %s conversation is %s's" % (scope, chats.agent(scope)))
    if agent != "claude":
        return (sid, True) if has_transcript(sid, agent) else ("", False)
    if not sid:
        sid = str(uuid.uuid4())
        chats.save_sid(scope, sid, "claude")
    p = dfs_context.transcript_path(sid, "claude")
    return sid, bool(p and os.path.exists(p))


def save(scope, sid, agent):
    """The conversation for this scope is session `sid` under `agent` (run.sh, once codex has
    minted the id of a chat the screen opened)."""
    chats = Chats()
    chats.check(scope)
    chats.save_sid(scope, sid, agent)


def main(argv):
    """`--codex-result [sid]`, `--opencode-result [sid]`, `--kiro-result [sid]`: that agent's JSON stream of a turn on
    stdin, the turn's one JSON line on stdout (exit 1 if it failed). `--save scope sid agent`:
    record a scope's conversation."""
    results = {"--codex-result": codex_result, "--opencode-result": opencode_result, "--kiro-result": kiro_result}
    if argv[:1] and argv[0] in results:
        out = results[argv[0]](sys.stdin, argv[1] if len(argv) > 1 else "")
        print(json.dumps(out))
        return 1 if out["is_error"] else 0
    if argv[:1] == ["--save"] and len(argv) == 4:
        save(*argv[1:])
        return 0
    print("usage: chat.py --codex-result [sid] | --opencode-result [sid] | --kiro-result [sid] | --save scope sid agent", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
