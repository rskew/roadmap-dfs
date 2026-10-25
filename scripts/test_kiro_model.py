"""A stand-in for the model service behind kiro-cli, so a REAL kiro-cli can run a turn here
with no login. kiro-cli 2.27.1 (`--v3`) talks to its model over HTTP and takes the service's
address from KIRO_KAS_ENDPOINT and KIRO_KAS_CONTROL_PLANE_ENDPOINT (loopback only), with any
string as KIRO_API_KEY. Only the model is faked: the session files kiro writes and the events
it prints are its own, which is what the chat reads.

    with FakeKiroModel(say="It has one file.") as model:
        env = model.env()          # the variables to run kiro-cli under

A message that asks to "take your time" is answered in two parts with `hold` seconds between them
(or until `release` is set), so a turn can be interrupted while it is still answering.

A message that asks to "use a tool" is answered with a `list_directory` call first, and the
tool's result with `say`, as a model that uses a tool does. If the result says the call was
"held back", the model asks for it again, and `lag` seconds pass before the first call is asked for.
"""
import http.server
import json
import struct
import threading
import time
import zlib

from test_agent_models import wait_or_hang_up

MODEL = {"modelId": "fake-model", "modelName": "Fake", "description": "a stand-in", "rateMultiplier": 1.0,
         "rateUnit": "Credit", "tokenLimits": {"maxInputTokens": 200000, "maxOutputTokens": 8000},
         "supportedInputTypes": ["TEXT"]}


def _header(name, value):
    n, v = name.encode(), value.encode()
    return bytes([len(n)]) + n + bytes([7]) + struct.pack(">H", len(v)) + v


def event(kind, payload):
    """One event of an `application/vnd.amazon.eventstream` body."""
    headers = _header(":message-type", "event") + _header(":event-type", kind) + _header(":content-type", "application/json")
    body = json.dumps(payload).encode()
    prelude = struct.pack(">II", 12 + len(headers) + len(body) + 4, len(headers))
    prelude += struct.pack(">I", zlib.crc32(prelude))
    message = prelude + headers + body
    return message + struct.pack(">I", zlib.crc32(message))


METERING = event("meteringEvent", {"unit": "credit", "unitPlural": "credits", "usage": 0.01})


class FakeKiroModel:
    def __init__(self, say="hello from the fake model"):
        self.say = say
        self.lag = 0                # seconds the first answer waits before it asks for the tool
        self.hold = 30              # seconds a "take your time" answer waits between its parts
        self.release = threading.Event()
        self.cancelled = threading.Event()      # kiro hung up on a slow answer: it was interrupted
        self.requests_seen = 0
        self.requests = []          # (x-amz-target, body) of each call kiro made
        model = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("content-length") or 0))
                target = self.headers.get("x-amz-target") or ""
                model.requests.append((target, body))
                if target.endswith("ListAvailableModels"):
                    self.reply("application/x-amz-json-1.0", json.dumps({"models": [MODEL], "defaultModel": MODEL}).encode())
                elif target.endswith("GenerateAssistantResponse"):
                    request = json.loads(body)
                    if "take your time" in request["conversationState"]["currentMessage"]["userInputMessage"].get("content", ""):
                        return self.slowly(request)
                    if model.lag and not model.requests_seen:
                        model.requests_seen += 1
                        time.sleep(model.lag)
                    self.reply("application/vnd.amazon.eventstream", model.generate(request))
                else:
                    self.reply("text/plain", b"", 404)

            def slowly(self, request):
                self.send_response(200)
                self.send_header("content-type", "application/vnd.amazon.eventstream")
                self.end_headers()                  # no length: the body ends when the connection does
                try:
                    self.wfile.write(event("assistantResponseEvent", {"content": "Starting. "}))
                    self.wfile.flush()
                    if wait_or_hang_up(model, self.connection):
                        return
                    self.wfile.write(event("assistantResponseEvent", {"content": model.say}) + METERING
                                     + event("metadataEvent", {"stopReason": "end_turn"}))
                except OSError:
                    model.cancelled.set()           # kiro hung up: it was interrupted

            def reply(self, kind, data, status=200):
                self.send_response(status)
                self.send_header("content-type", kind)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)

    def generate(self, request):
        current = request["conversationState"]["currentMessage"]["userInputMessage"]
        results = current.get("userInputMessageContext", {}).get("toolResults")
        held = bool(results) and "held back" in json.dumps(results)
        if (held or not results) and ("use a tool" in current.get("content", "") or held):
            return (event("assistantResponseEvent", {"content": "Let me look. "})
                    + event("toolUseEvent", {"toolUseId": "tu%d" % (1 + held), "name": "list_directory",
                                             "input": json.dumps({"path": "."}), "stop": True})
                    + METERING + event("metadataEvent", {"stopReason": "tool_use"}))
        # a stream with no stop reason is read as cut off, and asked again
        return event("assistantResponseEvent", {"content": self.say}) + METERING + event("metadataEvent", {"stopReason": "end_turn"})

    def env(self):
        url = "http://127.0.0.1:%d" % self.server.server_address[1]
        return dict(KIRO_API_KEY="any", KIRO_KAS_ENDPOINT=url, KIRO_KAS_CONTROL_PLANE_ENDPOINT=url)

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
