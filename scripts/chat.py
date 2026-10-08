#!/usr/bin/env python3
"""A chat about a task, or about the project, that is THE chat session the screen's `c` opens.

One conversation per scope (a task, or `project`): a claude session whose id is kept in a
small file under the run root. The terminal's `c` (and `C`) starts or RESUMES that session
interactively; the web page resumes the same one a turn at a time, headless and read-only
(`run.sh --chat-turn`: Read, Grep, Glob and nothing that changes anything). What the page shows
is read from the session's own transcript, so a message typed at the screen shows on the page
and one typed on the page shows when `c` is next opened. It is the same conversation, not a copy.

With the terminal screen serving the page, a chat started on the page is an interactive agent that
screen runs in the BACKGROUND (ptyrelay.py): any number at once, one per task and one for the project,
none of them on the screen unless you ask. The page types into them and reads them from the
transcript; the screen's `c` attaches to the running one (ctrl-] leaves it running). They end with
the screen (quit, SIGTERM or SIGHUP), with "New chat", or after DFS_CHAT_IDLE seconds (30 minutes) with
nobody using them; the conversation stays, and the next message resumes it. Ending one ends everything
the agent started, not only the agent. With no screen (web.py alone) the page runs its own turns. A claude
running on the session in some OTHER terminal would tangle it, so then the page does not send.

It spends a subscription, as a chain does, so the page gates it behind the same switch as the
walk (`--no-walk`): a server that may not start agents does not chat either.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths as dfs_paths    # noqa: E402
import runs as dfs_runs      # noqa: E402
import tree as dfs_tree      # noqa: E402
import ptyrelay             # noqa: E402
import context as dfs_context   # noqa: E402
import uuid
from walk import agent_file, read_agent   # noqa: E402

SCOPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
MAX_MESSAGE = 4000
TURN_TIMEOUT = 600          # seconds; a turn that takes longer is one nobody is waiting on


def transcript_messages(sid):
    """The human turns and the agent's words in a session's transcript, in order:
    [{role: you|agent, text}]. Tool calls and results, thinking, and the harness's own
    notes are not part of a conversation, and the opening prompt the screen's chat starts
    with is the screen's, not something the author said."""
    path = dfs_context.transcript_path(sid, "claude") if sid else None
    if not path or not os.path.exists(path):
        return []
    out, agent = [], {}
    try:
        fh = open(path, errors="replace")
    except OSError:
        return []
    with fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            msg = rec.get("message") or {}
            content = msg.get("content")
            if rec.get("type") == "user" and not rec.get("isMeta") and not rec.get("isSidechain"):
                text = content if isinstance(content, str) else "\n".join(
                    b.get("text", "") for b in (content or []) if isinstance(b, dict) and b.get("type") == "text")
                text = text.strip()
                if not text or text.startswith(("<command-", "<local-command", "<system-reminder")):
                    continue
                if "The author writes: " in text:                 # the page's first turn carries framing
                    text = text.rsplit("The author writes: ", 1)[1].strip()
                elif OPENING.match(text) or PAGE_OPENING.match(text):   # an opening prompt: the tool's words
                    continue
                out.append(dict(role="you", text=text))
            elif rec.get("type") == "assistant" and not rec.get("isSidechain") and isinstance(content, list):
                text = "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text").strip()
                if not text:
                    continue
                key = msg.get("id")
                if key and key in agent:                           # one message, one line per block
                    out[agent[key]]["text"] += "\n" + text
                else:
                    agent[key] = len(out)
                    out.append(dict(role="agent", text=text))
    return out


PAGE_OPENING = re.compile(r"^You are talking with the author about ", re.S)
OPENING = re.compile(r"^Read \S*ROADMAP\.md\b.*(Discuss|discuss)", re.S)


def running_elsewhere(sid, own=()):
    """Whether some process other than ours has a claude running on this session: the
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
        if sid in cmd and "claude" in cmd and "--chat-turn" not in cmd:
            return True
    return False


def run_turn(argv, message, cwd, env, timeout=None):
    """Run one turn and return its CompletedProcess. A turn that outlasts the timeout is ended
    with everything it started, not just the shell that began it (`subprocess.run` kills only
    that), and then TimeoutExpired is raised."""
    timeout = timeout or TURN_TIMEOUT
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, cwd=cwd, env=env, start_new_session=True)
    try:
        out, err = proc.communicate(message, timeout=timeout)
    except subprocess.TimeoutExpired:
        ptyrelay.stop_tree(proc.pid)
        proc.communicate()
        raise
    return subprocess.CompletedProcess(argv, proc.returncode, out, err)


class Chats:
    def __init__(self, on_change=None, walker=lambda: None):
        self.walker = walker        # the screen's side of the chat, if there is a screen
        self.lock = threading.RLock()
        self.on_change = on_change or (lambda: None)
        self.busy = {}      # scope -> when its turn started
        self.pending = {}   # scope -> what was sent and has not reached the transcript yet
        self.notes = {}     # scope -> [errors], until the conversation moves on

    # ── the record: just the session id ────────────────────────────────────────────────
    def path(self, scope):
        return Path(dfs_runs.ensure_run_root()) / "chat" / (scope + ".json")

    def check(self, scope):
        if not SCOPE.fullmatch(scope or ""):
            raise ValueError("a chat is about a task, or the project")
        if scope != "project" and not dfs_tree.exists(scope):
            raise ValueError("no task %s" % scope)

    def sid(self, scope):
        try:
            return json.loads(self.path(scope).read_text()).get("sid", "")
        except (OSError, ValueError):
            return ""

    def save_sid(self, scope, sid):
        p = self.path(scope)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(sid=sid)))
        os.replace(tmp, p)

    def state(self, scope):
        self.check(scope)
        with self.lock:
            sid = self.sid(scope)
            messages = transcript_messages(sid)
            pending = self.pending.get(scope)
            if pending and not any(m["role"] == "you" and m["text"] == pending for m in messages[-3:]):
                messages.append(dict(role="you", text=pending))
            elif pending:
                self.pending.pop(scope, None)
            messages += [dict(role="error", text=t) for t in self.notes.get(scope, [])]
            started = self.busy.get(scope)
            w = self.walker()
            live = bool(w and w.chat_live(scope))
            talking = bool(live and w.chat_active(scope))      # a background chat that is answering
            return dict(scope=scope, messages=messages, busy=bool(started) or talking,
                        busy_for=int(time.time() - started) if started else 0,
                        screen=bool(w and w.has_screen), live=live,
                        elsewhere=bool(sid) and not started and not live and running_elsewhere(sid),
                        agent=read_agent(agent_file()))

    # ── a turn ────────────────────────────────────────────────────────────────────────
    def send(self, scope, message):
        self.check(scope)
        message = (message or "").strip()
        if not message:
            raise ValueError("a message is needed")
        if len(message) > MAX_MESSAGE:
            raise ValueError("a message is %d characters or fewer" % MAX_MESSAGE)
        agent = read_agent(agent_file())
        if agent != "claude":
            raise ValueError("chat on the page runs under claude; the agent is %s (the screen's c "
                             "opens the others)" % agent)
        w = self.walker()
        with self.lock:
            # A chat running on this screen has the session: type into it, as the keyboard does.
            if scope not in self.busy and w and w.chat_send(scope, message):
                self.pending[scope] = message
                self.notes.pop(scope, None)
                self.on_change()
                return self.state(scope)
            if scope in self.busy:
                raise ValueError("it is still answering the last message")
            sid = self.sid(scope)
            if sid and running_elsewhere(sid):
                raise ValueError("another terminal has this conversation open: type there, or "
                                 "close it and send from here")
            self.pending[scope] = message
            self.notes.pop(scope, None)
            self.busy[scope] = time.time()
        threading.Thread(target=self.turn, args=(scope, message, sid), daemon=True).start()
        self.on_change()
        return self.state(scope)

    def argv(self):
        return [str(dfs_paths.SCRIPTS / "run.sh")]

    def turn(self, scope, message, sid, opening=False):
        error, new_sid = "", sid
        try:
            env = dict(os.environ)
            if opening:
                env["CHAT_OPENING"] = "1"      # nobody has spoken: the turn is the summary, unprompted
            env.pop("RUNDIR", None)
            env.pop("CHAT_SID", None)
            env.pop("CHAT_RESUME", None)
            if sid:
                env["CHAT_SID"] = sid
                # Resume only a session that has a transcript: one the screen made and the
                # author left at once has none, and `--resume` would refuse it.
                p = dfs_context.transcript_path(sid, "claude")
                if p and os.path.exists(p):
                    env["CHAT_RESUME"] = "1"
            r = run_turn(self.argv() + ["--chat-turn", scope], message, dfs_paths.work_root(), env)
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
        except subprocess.TimeoutExpired:
            error = "no answer in %d minutes" % (TURN_TIMEOUT // 60)
        except OSError as e:
            error = "could not start the agent: %s" % e
        with self.lock:
            if new_sid and new_sid != sid:
                self.save_sid(scope, new_sid)
            if error:
                self.notes[scope] = [error]
            self.busy.pop(scope, None)
        self.on_change()

    def open(self, scope):
        """The page opened this chat. A conversation that has begun is left as it is; a new one
        opens with the summary: the agent the screen runs for it does (that is where the page's
        chats live), and with no screen the page runs the opening turn itself."""
        self.check(scope)
        w = self.walker()
        note = ""
        if read_agent(agent_file()) == "claude":
            sid = self.sid(scope)
            p = dfs_context.transcript_path(sid, "claude") if sid else None
            began = bool(p and os.path.exists(p)) or bool(w and w.chat_live(scope))
            if not began:
                if w and w.has_screen:
                    try:
                        w.chat_open(scope)
                    except ValueError as e:
                        note = str(e)
                else:
                    with self.lock:
                        if scope not in self.busy:
                            self.busy[scope] = time.time()
                            self.notes.pop(scope, None)
                            threading.Thread(target=self.turn, args=(scope, "", sid, True), daemon=True).start()
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
            sid = self.sid(scope)
            messages = transcript_messages(sid)
            tp = dfs_context.transcript_path(sid, "claude") if sid else None
            live = bool(w and w.chat_live(scope))
            busy = scope in self.busy or bool(live and w.chat_active(scope))
            if not messages and not live and not busy:
                continue
            last = messages[-1] if messages else dict(role="", text="")
            try:
                updated = os.path.getmtime(tp) if tp else p.stat().st_mtime
            except OSError:
                updated = p.stat().st_mtime
            out.append(dict(scope=scope, count=len(messages), role=last["role"], last=last["text"][:160],
                            updated=updated, live=live, busy=busy))
        return sorted(out, key=lambda c: -c["updated"])

    def clear(self, scope):
        """Start over: the next message is a new session. The old one stays in claude's own history."""
        self.check(scope)
        with self.lock:
            if scope in self.busy:
                raise ValueError("it is still answering: wait for it")
            w = self.walker()
            if w:
                w.chat_close(scope)                  # the agent running on it goes with it
            try:
                self.path(scope).unlink()
            except OSError:
                pass
            self.pending.pop(scope, None)
            self.notes.pop(scope, None)
        self.on_change()
        return self.state(scope)


def claim(scope):
    """For the screen's `c`: the session to open for this scope, as (sid, resume). The record
    is made now, before the chat starts, so the page can resume it, and `resume` is whether
    there is a transcript to resume (else it starts the session under that id)."""
    chats = Chats()
    chats.check(scope)
    sid = chats.sid(scope)
    if not sid:
        sid = str(uuid.uuid4())
        chats.save_sid(scope, sid)
    p = dfs_context.transcript_path(sid, "claude")
    return sid, bool(p and os.path.exists(p))
