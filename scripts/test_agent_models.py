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
"""
import http.server
import json
import select
import socket
import threading

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
