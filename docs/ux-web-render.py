#!/usr/bin/env python3
"""Read the web page's content for the old TUI review's findings, without seeing it drawn (W131.17).

    python3 docs/ux-web-render.py [SCRIPTS_DIR] [--text]     (run from the repository root)

Copies the fixture in `docs/tui-ux-review/items/` (the one the old review used) into a temp
git repo, serves it with `SCRIPTS_DIR/web.py` (default: this repository's `scripts/`), and for
W3, W7 and W9 does two things:

1. `chromium --headless --dump-dom` of `#/t/<task>` at a phone window (390x844): what the
   page's script builds, as text. A finding is "carried" when its words are in that DOM.
2. `GET /api/task/<task>`: the JSON the page draws from, to read `assumed` and `notes`,
   which the accept form lists. The accept form itself opens on a click, which a DOM dump
   cannot make (no playwright here), so those are read from the JSON and the form's template
   in `scripts/web/index.html` (`acceptForm`), not from a rendered dialog.

It cannot show layout, colour or whether a line is clipped: the browser here draws no text.
"""
import json, os, re, shutil, socket, subprocess, sys, tempfile, time, urllib.request

here = os.path.dirname(os.path.abspath(__file__))
args = [a for a in sys.argv[1:] if not a.startswith("--")]
scripts = os.path.abspath(args[0] if args else os.path.join(here, "..", "scripts"))
fixture = os.path.join(here, "tui-ux-review", "items")
chrome = shutil.which("chromium") or shutil.which("chromium-browser")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def text_of(dom):
    """The visible words of a dumped DOM: scripts and styles out, tags out, entities undone."""
    import html
    d = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", dom)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"(?s)<[^>]+>", " ", d))).strip()


class Ws:
    """Just enough of a websocket client to send DevTools commands to chromium."""
    def __init__(self, url):
        import base64, urllib.parse
        u = urllib.parse.urlparse(url)
        self.s = socket.create_connection((u.hostname, u.port), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall(("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                        "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (u.path, u.hostname, u.port, key)).encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.s.recv(1)
        self.n = 0

    def send(self, obj):
        data = json.dumps(obj).encode()
        mask = os.urandom(4)
        n = len(data)
        head = b"\x81" + (bytes([0x80 | n]) if n < 126 else b"\xfe" + n.to_bytes(2, "big") if n < 65536 else b"\xff" + n.to_bytes(8, "big"))
        self.s.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recvn(self, n):
        out = b""
        while len(out) < n:
            chunk = self.s.recv(n - len(out))
            if not chunk:
                raise EOFError
            out += chunk
        return out

    def recv(self):
        msg = b""
        while True:
            b0, b1 = self.recvn(2)
            n = b1 & 0x7F
            if n == 126:
                n = int.from_bytes(self.recvn(2), "big")
            elif n == 127:
                n = int.from_bytes(self.recvn(8), "big")
            msg += self.recvn(n)
            if b0 & 0x80:
                return json.loads(msg) if msg[:1] == b"{" else {}

    def call(self, method, **params):
        self.n += 1
        self.send({"id": self.n, "method": method, "params": params})
        while True:
            r = self.recv()
            if r.get("id") == self.n:
                return r

    def close(self):
        self.s.close()


work = tempfile.mkdtemp(prefix="web-render-")
server = None
try:
    os.makedirs(os.path.join(work, ".dfs"))
    shutil.copytree(fixture, os.path.join(work, ".dfs", "items"))
    open(os.path.join(work, ".dfs", "ROADMAP.md"), "w").write("# Roadmap\n")   # web.py wants a roadmap in the work root
    subprocess.run(["git", "init", "-q", work], check=True)
    port = free_port()
    server = subprocess.Popen([sys.executable, os.path.join(scripts, "web.py"), "--port", str(port), "--no-walk"],
                              cwd=work, stdout=subprocess.DEVNULL, stderr=open(os.path.join(work, "server.err"), "w"))
    base = "http://127.0.0.1:%d" % port
    for _ in range(50):
        try:
            urllib.request.urlopen(base + "/api/state", timeout=1).read()
            break
        except Exception:
            time.sleep(0.2)
    else:
        sys.exit("web.py did not start: " + open(os.path.join(work, "server.err")).read()[-800:])

    def dom(task, node=None):
        """The page after its script has run, and what the accept dialog says after a click on
        Accept. `--dump-dom` dumps before the page's fetches finish (it printed "Loading..."), so
        this drives chromium over its DevTools websocket instead (a few lines of RFC 6455, since
        there is no playwright here), waits for the rows, reads the DOM, then clicks Accept."""
        port_d = free_port()
        prof = tempfile.mkdtemp(prefix="chrome-prof-")
        proc = subprocess.Popen([chrome, "--headless", "--no-sandbox", "--disable-gpu", "--window-size=390,844",
                                 "--user-data-dir=" + prof, "--remote-debugging-port=%d" % port_d, "about:blank"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(100):
                try:
                    tabs = json.loads(urllib.request.urlopen("http://127.0.0.1:%d/json" % port_d, timeout=1).read())
                    tab = next(t for t in tabs if t["type"] == "page")
                    break
                except Exception:
                    time.sleep(0.2)
            ws = Ws(tab["webSocketDebuggerUrl"])
            ws.call("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=1, mobile=True)
            ws.call("Page.navigate", url="%s/#/t/%s%s" % (base, task, "/" + node if node else ""))
            ev = lambda js: ws.call("Runtime.evaluate", expression=js, returnByValue=True, awaitPromise=True)["result"]["result"].get("value")
            for _ in range(60):
                if ev("document.querySelectorAll('#view .row').length") or ev("document.querySelector('#view .wait') === null"):
                    break
                time.sleep(0.25)
            time.sleep(1)
            page = ev("document.querySelector('#view').outerHTML") + "\n<!--SHEET-->" + (ev("document.querySelector('#sheet').textContent") or "")
            has_btn = ev("!!document.querySelector('[data-act=accept]')")
            ev("document.querySelector('[data-act=accept]') && document.querySelector('[data-act=accept]').click()")
            time.sleep(1)
            dlg = ev("(document.querySelector('#form[open]') || {}).textContent || ''")
            if "--debug" in sys.argv:
                print("DEBUG", has_btn, repr(ev("document.querySelector('#form').outerHTML.slice(0, 400)")), file=sys.stderr)
            width = ev("[document.documentElement.scrollWidth, innerWidth]")
            ws.close()
            return page, ((dlg or "") if has_btn else "(no Accept button on this page)"), width
        finally:
            proc.terminate()
            proc.wait(timeout=20)
            for _ in range(5):          # chromium's helpers may still be writing as it exits
                shutil.rmtree(prof, ignore_errors=True)
                if not os.path.exists(prof):
                    break
                time.sleep(0.5)

    def api(task):
        return json.loads(urllib.request.urlopen("%s/api/task/%s" % (base, task), timeout=5).read())

    for task in ("W2", "W3", "W7", "W9"):
        d, dialog, width = dom(task)
        t = text_of(d.split("<!--SHEET-->")[0])
        j = api(task)
        nodes = [r for r in j["rows"] if r["kind"] == "node"]
        print("=" * 8, task, "status=%s, accepted=%s, #view %d chars, %d visible words" % (
            j["status"], j["accepted"], len(d), len(t.split())))
        print("rows (JSON) / node rows in DOM:", len(nodes), "/", len(re.findall(r'class="row ', d)))
        print("skim lines in DOM:", len(re.findall(r'class="skim"', d)), "of", sum(1 for r in nodes if r["skim"]), "nodes with a skim")
        print("answered tags in DOM:", len(re.findall(r'class="tag">answered', d)), "; overruled rows:",
              len(re.findall(r'class="row [^"]*overruled', d)), "(JSON: %d, %d)" % (
                  sum(1 for r in nodes if r["answered"]), sum(1 for r in nodes if r["overruled"])))
        print("flagged (assumes) rows in DOM:", len(re.findall(r'class="row [^"]*assumes', d)), "(JSON: %d)" % sum(
            1 for r in nodes if r["assumes"] or r["ask"]))
        print("'Wrong if' words in the DOM text:", len(re.findall(r"wrong if", t, re.I)))
        for a in j["assumed"]:
            dn, _, _ = dom(task, a["id"])
            tn = re.sub(r"\s+", " ", dn.split("<!--SHEET-->")[1])      # the node sheet (#sheet), opened by the URL
            i = tn.lower().find("wrong if")
            print("   opened %s: 'Wrong if' in the opened node sheet: %s%s" % (a["id"], i >= 0,
                  (" -> " + tn[i:i + 90]) if i >= 0 else ""))
            if "--text" in sys.argv:
                k = tn.find(a["id"], tn.find("Summary") + 1)
                print("      ", tn[:1800])
        for r in nodes:
            if r["answered"] or r["overruled"]:
                dn, _, _ = dom(task, r["id"])
                sheet = re.sub(r"\s+", " ", dn.split("<!--SHEET-->")[1])
                print("   %s sheet carries the answer's words ('Go with (a)', 'Sales will give'): %s, the raise's ('three customers still run'): %s, 'overruled'/'you said': %s" % (
                    r["id"], "Sales will give" in sheet or "Go with (a)" in sheet, "three customers still run" in sheet, "overruled" in sheet))
                print("   opened %s (answered=%s, overruled=%s): sheet says: %s" % (r["id"], r["answered"], r["overruled"], sheet[:520]))
        for n in j["notes"]:
            print("   note %r on the task page: %s" % (n["heading"], n["heading"] in t))
        print("'Next flagged' and 'Flagged nodes only' buttons on the page:", "Next flagged" in t, "Flagged nodes only" in t)
        print("page scrollWidth / viewport width:", width)
        print("accept dialog after a click on Accept (text):", repr(re.sub(r"\s+", " ", dialog)[:1200]) if dialog else "none opened")
        print("assumed[] (the accept form's list):")
        for a in j["assumed"]:
            print("   %s | wrong=%r" % (a["id"], (a["wrong"] or "")[:110]))
        print("notes[] (the accept form's reviewer notes):", [(n["kind"], n["heading"], len(n["body"])) for n in j["notes"]])
        for r in nodes:
            if r["raises"]:
                print("   %s has %d raise(s) in JSON; answered=%s" % (r["id"], len(r["raises"]), r["answered"]))
        if "--text" in sys.argv:
            print(t[:3000])
            print(re.search(r"(?s)<main id=.view.*?</main>", d).group(0)[:1500] if "<main" in d else "no main")
finally:
    if server:
        server.terminate()
    shutil.rmtree(work, ignore_errors=True)
