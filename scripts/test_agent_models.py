"""Stand-ins for the model services behind codex and opencode, so a REAL codex or opencode runs a
turn here with no login (kiro's is test_kiro_model.py). Only the model is faked: the sessions the
agent writes and the screens it draws are its own.

    with FakeCodexModel(say="It has one file.") as model:
        argv = ["codex", *model.args()]; env = dict(os.environ, **model.env())
    with FakeOpenAIModel(say="...") as model:
        env = dict(os.environ, **model.env())       # for opencode

A message that asks to "take your time" is answered in two parts, the second only when `release`
is set (or after `hold` seconds), so a turn can be interrupted while it is answering. If the
agent hangs up in between, `cancelled` is set: that is how a test sees an interrupt reach it.

`start_until` is for the tests that start a real opencode: one in about five starts hangs on a loaded machine, and the
test starts it again.
"""
import http.server
import json
import select
import signal
import socket
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

import ptyrelay

SLOW = "take your time"


def hung_up(sock):
    """Whether the other end has closed the connection (nothing to read, but readable)."""
    readable, _, _ = select.select([sock], [], [], 0)
    if not readable:
        return False
    try:
        return sock.recv(1, socket.MSG_PEEK) == b""
    except OSError:
        return True


def wait_or_hang_up(model, sock):
    """Hold the answer until released, the hold is over, or the agent hangs up (True then)."""
    for _ in range(int(model.hold / 0.1)):
        if model.release.wait(0.1):
            return False
        if hung_up(sock):
            model.cancelled.set()
            return True
    return False


class FakeModel:
    def __init__(self, say):
        self.say = say
        self.hold = 30              # seconds a slow answer waits between its parts
        self.release = threading.Event()
        self.cancelled = threading.Event()
        self.requests = []          # (path, body) of each call the agent made
        model = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("content-length") or 0))
                model.requests.append((self.path, body))
                if not self.path.endswith(model.endpoint):
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                try:
                    model.answer(self, json.loads(body), SLOW in body.decode(errors="replace"))
                except OSError:
                    model.cancelled.set()

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)

    @property
    def url(self):
        return "http://127.0.0.1:%d/v1" % self.server.server_address[1]

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()


def sse(data, kind=None):
    return (("event: %s\n" % kind if kind else "") + "data: %s\n\n" % json.dumps(data)).encode()


class FakeCodexModel(FakeModel):
    """codex's Responses API: `-c model_providers.fake=...` points codex at it."""
    endpoint = "/responses"

    def __init__(self, say="hello from the fake model"):
        super().__init__(say)

    def args(self):
        return ["-c", 'model_provider="fake"', "-c", 'model="fake-model"', "-c",
                'model_providers.fake={name="fake",base_url="%s",wire_api="responses",env_key="FAKE_KEY"}' % self.url]

    def env(self):
        return dict(FAKE_KEY="any")

    def answer(self, h, request, slow):
        n = len(self.requests)
        rid, mid, w = "resp_%d" % n, "msg_%d" % n, h.wfile

        def ev(kind, **data):
            w.write(sse(dict(data, type=kind), kind))

        ev("response.created", response=dict(id=rid, status="in_progress", output=[]))
        ev("response.output_item.added", output_index=0, item=dict(type="message", id=mid, role="assistant", status="in_progress", content=[]))
        ev("response.content_part.added", item_id=mid, output_index=0, content_index=0, part=dict(type="output_text", text=""))
        text = "Starting. " if slow else self.say
        ev("response.output_text.delta", item_id=mid, output_index=0, content_index=0, delta=text)
        w.flush()
        if slow:
            if wait_or_hang_up(self, h.connection):
                return
            text += self.say
            ev("response.output_text.delta", item_id=mid, output_index=0, content_index=0, delta=self.say)
        part = dict(type="output_text", text=text)
        ev("response.output_text.done", item_id=mid, output_index=0, content_index=0, text=text)
        ev("response.content_part.done", item_id=mid, output_index=0, content_index=0, part=part)
        item = dict(type="message", id=mid, role="assistant", status="completed", content=[dict(part, annotations=[])])
        ev("response.output_item.done", output_index=0, item=item)
        ev("response.completed", response=dict(id=rid, status="completed", output=[item], usage=dict(
            input_tokens=1, output_tokens=1, total_tokens=2, input_tokens_details=dict(cached_tokens=0),
            output_tokens_details=dict(reasoning_tokens=0))))
        w.flush()


class FakeOpenAIModel(FakeModel):
    """An OpenAI-compatible chat completions endpoint, which opencode takes as a provider."""
    endpoint = "/chat/completions"

    def __init__(self, say="hello from the fake model"):
        super().__init__(say)

    def env(self):
        return dict(OPENCODE_CONFIG_CONTENT=json.dumps({"model": "fake/fake-model", "autoupdate": False, "provider": {"fake": {
            "npm": "@ai-sdk/openai-compatible", "name": "Fake", "options": {"baseURL": self.url, "apiKey": "any"},
            "models": {"fake-model": {"name": "Fake", "limit": {"context": 100000, "output": 4000}}}}}}))

    def answer(self, h, request, slow):
        w = h.wfile

        def chunk(delta, finish=None, **extra):
            w.write(sse(dict(id="c1", object="chat.completion.chunk", created=1, model="fake-model",
                             choices=[dict(index=0, delta=delta, finish_reason=finish)] if delta is not None else [], **extra)))

        chunk(dict(role="assistant", content="Starting. " if slow else self.say))
        w.flush()
        if slow:
            if wait_or_hang_up(self, h.connection):
                return
            chunk(dict(content=self.say))
        chunk({}, "stop")
        chunk(None, usage=dict(prompt_tokens=1, completion_tokens=1, total_tokens=2))
        w.write(b"data: [DONE]\n\n")
        w.flush()


# ── opencode's start-up hang ────────────────────────────────────────────────────────────
# On a loaded machine about one real opencode start in five hangs, with or without ptyrelay in the way
# (a bare pty and no relay: 3 of 12 starts under 16 busy loops, 2 of 12 with the repo hidden from git
# by GIT_CEILING_DIRECTORIES): it draws its terminal queries, and its worker thread then never goes on,
# so the prompt is never drawn and the process sleeps in epoll_wait, its CPU stuck at about 4.4s for
# minutes. A start that is only slow is told from it by its CPU: every healthy start used at least 57
# ticks (0.57s) in each 5s, a hung one 4 to 13.
HUNG_SECONDS = 10           # how long a start with no prompt is watched
HUNG_TICKS = 25             # and it is hung when it used fewer ticks of CPU (1/100s) than this in that time


def cpu_ticks(pid):
    try:
        with open("/proc/%d/stat" % pid) as f:
            fields = f.read().rsplit(")", 1)[1].split()
        return int(fields[11]) + int(fields[12])
    except (OSError, IndexError, ValueError, TypeError):
        return None


def prompt_drawn(relay):
    """Whether relay's opencode has drawn its prompt screen."""
    return ptyrelay.READY["opencode"] in ptyrelay.chatlog.screen_text(relay.tail, keep=ptyrelay.TAIL)


def opencode_hung(relay, samples):
    """Whether relay's opencode has started and then made no progress for HUNG_SECONDS. `samples` is the list
    this call keeps its readings in: one per call of this function over the watch. Only once run.sh has said
    it is starting the interactive opencode (relay.handed, which is after the chat's headless opening turn,
    where nothing is expected to use CPU), or for a relay started on opencode itself, once it has written."""
    if not relay.live or prompt_drawn(relay) or not (relay.handed or (relay.first_out and relay.started and not relay.opening)):
        return False
    ticks, now = cpu_ticks(relay.pid), time.time()
    if ticks is None:
        return False
    if not samples or now - samples[-1][0] >= 1:
        samples.append((now, ticks))
    past = [s for s in samples if now - s[0] >= HUNG_SECONDS]
    return bool(past) and ticks - past[-1][1] < HUNG_TICKS


def has_server(pid):
    """Whether the opencode with this pid has its private server (`serve --stdio`) running, as a child of its own."""
    return subprocess.run(["pgrep", "-P", str(pid), "-f", "serve --stdio"], capture_output=True).returncode == 0


def opencode_server_gone(relay):
    """Whether relay's interactive opencode is alive but has lost its private server, which Bun crashes with a Bus
    error now and then (the same as `opencode_crashed`, but of the server): the screen then draws its busy
    animation for good and never asks the model. The server counts as lost once it was seen (or the prompt was
    drawn, which only a TUI with its server does) and is not there now; that is kept on `relay`, since the crash can come
    between one wait on the relay and the next. Only a child of the relay's own process counts: a headless turn's
    server under run.sh is a grandchild, and comes and goes as it should. Looked for about once a second."""
    if relay.agent != "opencode" or not relay.live:
        return False
    now = time.time()
    if now - getattr(relay, "server_looked", 0) < 1:
        return False
    relay.server_looked = now
    up = has_server(relay.pid)
    relay.server_seen = getattr(relay, "server_seen", False) or up or prompt_drawn(relay)
    return relay.server_seen and not up


def opencode_crashed(relay):
    """Whether relay's opencode was killed by SIGBUS, which a loaded machine does to it now and then (its
    executable is mapped from a store on zfs; seen at start-up, mid-turn on the screen and in `opencode run`)."""
    return relay.agent == "opencode" and not relay.live and relay.rc == -signal.SIGBUS


def start_until(relay, done, restart, timeout, tries=3):
    """Wait up to `timeout` for done(relay). A relay whose opencode hangs at start-up (`opencode_hung`) or is
    killed by SIGBUS (`opencode_crashed`) is closed and replaced by restart(), a started relay, at most `tries` starts in all. Returns the relay last started and
    whether done(relay) came true. `restart` registers the relay for closing, as the first one is."""
    for attempt in range(tries):
        samples, end, hung, why = [], time.time() + timeout, False, ""
        while time.time() < end and not hung:
            if done(relay):
                return relay, True
            why = "was killed by SIGBUS" if opencode_crashed(relay) else "lost its server" if opencode_server_gone(relay) else "hung at start-up" if opencode_hung(relay, samples) else ""
            hung = relay.agent == "opencode" and bool(why)
            time.sleep(0.1)
        if not hung:
            return relay, False
        relay.close()
        print("opencode %s (start %d of %d)" % (why, attempt + 1, tries), file=sys.stderr)
        if attempt + 1 < tries:
            relay = restart()
    return relay, False


class Stand:
    """Enough of a ptyrelay.Relay for `start_until`: a process that is the agent, and what it has drawn."""
    agent, live, first_out, started, opening, handed, rc = "opencode", True, 1.0, 1.0, False, 0.0, None

    def __init__(self, code, drawn=b""):
        self.proc = subprocess.Popen([sys.executable, "-c", code])
        self.pid, self.tail, self.closed = self.proc.pid, bytearray(drawn), False

    def close(self):
        self.closed = True
        self.proc.kill()
        self.proc.wait()


class StartUntil(unittest.TestCase):
    IDLE, BUSY = "import time; time.sleep(60)", "while True: pass"

    def setUp(self):
        for name, value in (("HUNG_SECONDS", 1), ("HUNG_TICKS", 25)):
            patcher = mock.patch.dict(globals(), {name: value})
            patcher.start()
            self.addCleanup(patcher.stop)
        self.stands = []

    def stand(self, code, drawn=b""):
        s = Stand(code, drawn)
        self.stands.append(s)
        self.addCleanup(s.close)
        return s

    def test_an_opencode_that_uses_no_cpu_and_has_drawn_no_prompt_is_started_again(self):
        first = self.stand(self.IDLE)
        second = self.stand(self.BUSY, b"ctrl+p commands")
        relay, ok = start_until(first, prompt_drawn, lambda: second, 20)
        self.assertEqual((relay, ok, first.closed), (second, True, True))

    def test_one_that_is_using_cpu_is_waited_for_not_restarted(self):
        busy = self.stand(self.BUSY)
        relay, ok = start_until(busy, prompt_drawn, lambda: self.fail("a slow start was restarted"), 3)
        self.assertEqual((relay, ok, busy.closed), (busy, False, False))

    def test_one_that_has_drawn_its_prompt_is_done_however_idle_it_is(self):
        idle = self.stand(self.IDLE, b"\x1b[1mctrl+p\x1b[0m commands")
        self.assertEqual(start_until(idle, prompt_drawn, lambda: self.fail("restarted"), 3), (idle, True))

    def test_the_last_start_is_closed_and_reported_not_done_when_every_one_hangs(self):
        starts = [self.stand(self.IDLE) for _ in range(3)]
        again = iter(starts[1:])
        relay, ok = start_until(starts[0], prompt_drawn, lambda: next(again), 30)
        self.assertEqual((relay, ok, [s.closed for s in starts]), (starts[2], False, [True, True, True]))

    def test_an_opencode_killed_by_sigbus_is_started_again_even_after_its_prompt_was_drawn(self):
        first = self.stand(self.IDLE, b"ctrl+p commands")
        first.live, first.rc = False, -signal.SIGBUS
        second = self.stand(self.IDLE, b"ctrl+p commands")
        relay, ok = start_until(first, lambda r: r is second, lambda: second, 20)
        self.assertEqual((relay, ok, first.closed), (second, True, True))

    SERVER = "import subprocess, sys, time; k = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(%d)', 'serve', '--stdio']); time.sleep(60)"

    def test_an_opencode_whose_server_has_gone_is_started_again_whatever_it_still_draws(self):
        for code, drawn in ((self.SERVER % 2, b""), (self.IDLE, b"ctrl+p commands")):     # seen and then gone; a prompt but no server
            first = self.stand(code, drawn)
            time.sleep(0.5)
            second = self.stand(self.IDLE, b"ctrl+p commands")
            relay, ok = start_until(first, lambda r: r is second, lambda: second, 12)
            self.assertEqual((relay, ok, first.closed), (second, True, True), code)

    def test_one_whose_server_is_up_or_not_yet_started_is_waited_for(self):
        up, starting = self.stand(self.SERVER % 60, b"ctrl+p commands"), self.stand(self.BUSY)
        end = time.time() + 10
        while not has_server(up.pid) and time.time() < end:     # the stand starts its server a moment after it starts
            time.sleep(0.05)
        for stand in (up, starting):
            relay, ok = start_until(stand, lambda r: False, lambda: self.fail("restarted"), 4)
            self.assertEqual((relay, ok, stand.closed), (stand, False, False))

    def test_one_that_ended_any_other_way_is_not_started_again(self):
        gone = self.stand(self.IDLE)
        gone.live, gone.rc = False, 0
        relay, ok = start_until(gone, lambda r: False, lambda: self.fail("restarted"), 2)
        self.assertEqual((relay, ok), (gone, False))

    def test_run_sh_s_opening_turn_is_not_a_hang_however_idle_it_waits(self):
        waiting = self.stand(self.IDLE)
        waiting.first_out = 1.0
        waiting.handed, waiting.opening = 0.0, True       # run.sh is running the headless opening turn
        relay, ok = start_until(waiting, prompt_drawn, lambda: self.fail("restarted in the opening turn"), 3)
        self.assertEqual((relay, ok, waiting.closed), (waiting, False, False))


if __name__ == "__main__":
    unittest.main()
