#!/usr/bin/env python3
"""web.py: who may talk to it, what it shows, and that what it writes is what the
terminal screen writes (the same log entries, through the same code).

Run:  python3 roadmap-dfs/scripts/test_web.py
"""
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import paths as dfs_paths   # noqa: E402
import tree as dfs_tree     # noqa: E402
import web as dfs_web       # noqa: E402
import test_walker          # noqa: E402  (its in-memory world, for the walk endpoints)

TASK = """# W1 · Bound the limiter

## Goal

Keep the limiter's memory bounded.

## Tree

### W1.1 · Measure the table
Status: confirmed
Approach: Count entries after a day of one-off ids in src/limiter.py.
Determination: Confirmed: it grows without bound. Next, evict.

### W1.2 · Evict idle buckets
Parent: W1.1
Status: confirmed
Approach: In src/limiter.py drop buckets that have fully refilled, on each refill pass.
Hypothesis: Idle buckets are safe to drop. Wrong if a client returns inside one window.
Ask: Should we log each eviction?
Determination: Confirmed: memory is flat over 10k ids.

### W1.3 · Check under churn
Parent: W1.2
Status: open
Approach: Run test_churn.py with 10k ids and confirm the table stays under 1k entries.

## Log

- 2026-09-25T09:00:00Z · session · work · run-1
- 2026-09-25T09:05:00Z · raise · W1.3
  Is churn from one client or many?
"""


class Web(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / ".dfs" / "items").mkdir(parents=True)
        (self.root / ".dfs" / "ROADMAP.md").write_text("# Roadmap\n")
        (self.root / ".dfs" / "items" / "W1.md").write_text(TASK)
        self._wr = dfs_paths.work_root
        dfs_paths.work_root = lambda: self.root
        dfs_paths._TAG_CACHE.clear()
        subprocess.run("git init -q -b master && git add -A && git -c user.name=t "
                       "-c user.email=t@t commit -qm x", shell=True, cwd=self.root, check=True)
        self.server = dfs_web.make_server("127.0.0.1", 0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        dfs_web._commit_cache.clear()
        self.fake = test_walker.FakeWalker()
        dfs_web.WALK.update(enabled=True, walker=self.fake)

    def tearDown(self):
        dfs_web.ALLOW_HOSTS.clear()
        dfs_web.WALK.update(enabled=True, walker=None)
        self.fake.restore()
        self.server.shutdown()
        self.server.server_close()
        dfs_paths.work_root = self._wr
        dfs_paths._TAG_CACHE.clear()
        shutil.rmtree(self.root, ignore_errors=True)

    def call(self, method, path, body=None, ctype="application/json", headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = dict(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body) if ctype == "application/json" else body
            headers["Content-Type"] = ctype
        conn.request(method, path, data, headers)
        r = conn.getresponse()
        raw = r.read()
        try:
            return r.status, json.loads(raw), r
        except ValueError:
            return r.status, raw, r

    def test_it_listens_on_this_machine_only_unless_told_otherwise(self):
        old = os.environ.pop("DFS_WEB_HOST", None)
        try:
            self.assertEqual(dfs_web.default_host(), "127.0.0.1")
            os.environ["DFS_WEB_HOST"] = "0.0.0.0"
            self.assertEqual(dfs_web.default_host(), "0.0.0.0")       # an explicit opt-in
        finally:
            os.environ.pop("DFS_WEB_HOST", None)
            if old is not None:
                os.environ["DFS_WEB_HOST"] = old

    def test_a_foreign_host_name_is_refused_on_loopback_dns_rebinding(self):
        for host, ok in (("127.0.0.1:%d" % self.port, True), ("localhost:%d" % self.port, True),
                         ("[::1]:%d" % self.port, True), ("evil.example:%d" % self.port, False),
                         ("evil.example", False), ("127.0.0.1.evil.example:1", False)):
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            conn.putrequest("GET", "/api/state", skip_host=True)
            conn.putheader("Host", host)
            conn.endheaders()
            status = conn.getresponse().status
            self.assertEqual(status == 200, ok, host)
            if not ok:
                self.assertEqual(status, 421)

    def test_a_tls_proxys_name_is_answered_on_loopback_only_when_allowed(self):
        def ask(host, method="GET", path="/manifest.json", origin=None):
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            conn.putrequest(method, path, skip_host=True)
            conn.putheader("Host", host)
            if method == "POST":
                conn.putheader("Content-Type", "application/json")
                conn.putheader("Content-Length", "2")
                conn.putheader("Origin", origin)
            conn.endheaders(b"{}" if method == "POST" else None)
            return conn.getresponse().status
        name = "phone.tail1234.ts.net"
        self.assertEqual(ask(name), 421)
        dfs_web.ALLOW_HOSTS.update(["phone.tail1234.ts.net"])
        self.assertEqual(ask(name), 200)
        self.assertEqual(ask(name.upper()), 200, "host names are not case-sensitive")
        self.assertEqual(ask("evil.example"), 421, "only the named host is added")
        self.assertEqual(ask(name, "POST", "/api/nope", "https://" + name), 400, "same origin passes the write guard")
        self.assertEqual(ask(name, "POST", "/api/nope", "https://evil.example"), 403)

    def test_chat_goes_through_the_page_and_is_off_with_the_walk(self):
        import stat
        import time as _t
        import test_chat
        stub = self.root / "claude-stub"
        stub.write_text(test_chat.STUB)
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        saved = {k: os.environ.get(k) for k in ("CLAUDE_CMD", "CHAT_STUB_LOG", "HOME", "CHAT_STUB_SAY")}
        os.environ.update(CLAUDE_CMD=str(stub), CHAT_STUB_LOG=str(self.root / "stub.log"), HOME=str(self.root / "home"),
                          CHAT_STUB_SAY="Hello from the agent.")
        (self.root / "home").mkdir()
        dfs_web.CHAT["chats"] = None
        try:
            status, st, _ = self.call("GET", "/api/chat/W1")
            self.assertEqual((status, st["messages"], st["busy"]), (200, [], False))
            self.assertEqual(self.call("GET", "/api/chat/W9")[0], 409)
            self.assertEqual(self.call("POST", "/api/chat/send", dict(scope="W1", message=""))[0], 409)
            status, st, _ = self.call("POST", "/api/chat/send", dict(scope="W1", message="Where are we?"))
            self.assertEqual(status, 200)
            for _ in range(100):
                st = self.call("GET", "/api/chat/W1")[1]
                if not st["busy"]:
                    break
                _t.sleep(0.1)
            self.assertEqual([(m["role"], m["text"]) for m in st["messages"]],
                             [("you", "Where are we?"), ("agent", "I read it. Hello from the agent.")])
            self.assertEqual(self.call("POST", "/api/chat/clear", dict(scope="W1"))[1]["messages"], [])
            dfs_web.WALK["enabled"] = False
            self.assertEqual(self.call("POST", "/api/chat/send", dict(scope="W1", message="hi"))[0], 403)
            self.assertEqual(self.call("GET", "/api/chat/W1")[0], 403)
        finally:
            dfs_web.WALK["enabled"] = True
            dfs_web.CHAT["chats"] = None
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def put_artefact(self, name="0a1b2c.html", title="How the limiter is wired"):
        d = self.root / ".dfs" / "artefacts"
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text("<!doctype html><title>%s</title><p>hello</p>" % title)
        (d / "_TEMPLATE.html").write_text("<title>template</title>")
        (self.root / ".dfs" / "secret.html").write_text("<title>not an artefact</title>")

    def test_artefacts_are_served_sandboxed_and_only_by_bare_name(self):
        self.put_artefact()
        status, raw, r = self.call("GET", "/artefacts/0a1b2c.html")
        self.assertEqual((status, r.getheader("Content-Type")), (200, "text/html; charset=utf-8"))
        csp = r.getheader("Content-Security-Policy")
        self.assertIn("sandbox", csp)
        self.assertIn("default-src 'none'", csp)
        for bad in ("/artefacts/../secret.html", "/artefacts/%2e%2e/secret.html", "/artefacts/missing.html",
                    "/artefacts/.hidden.html", "/artefacts/0a1b2c.py", "/artefacts/sub/x.html", "/artefacts/"):
            self.assertEqual(self.call("GET", bad)[0], 404, bad)

    def test_a_task_lists_the_artefacts_it_names_then_the_rest(self):
        self.put_artefact()
        self.put_artefact("standing-map.html", "The standing map")
        dfs_tree.append_log("W1", "raise", (), "Which design? See .dfs/artefacts/0a1b2c.html, "
                            "or http://localhost:3016/0a1b2c.html, or gone.html.")
        arts = self.call("GET", "/api/task/W1")[1]["artefacts"]
        self.assertEqual([(a["file"], a["title"], a["named"]) for a in arts],
                         [("0a1b2c.html", "How the limiter is wired", True),
                          ("standing-map.html", "The standing map", False)])

    def test_the_name_starts_from_git_then_the_file_is_the_name(self):
        subprocess.run(["git", "remote", "add", "origin", "git@github.com:acme/payments-api.git"],
                       cwd=self.root, check=True)
        self.assertFalse((self.root / ".dfs" / "title").exists())
        self.assertEqual(dfs_paths.project_title(), "payments-api")          # from the remote, once
        self.assertEqual((self.root / ".dfs" / "title").read_text(), "payments-api\n")
        subprocess.run(["git", "remote", "set-url", "origin", "git@github.com:acme/renamed.git"],
                       cwd=self.root, check=True)
        self.assertEqual(dfs_paths.project_title(), "payments-api")          # the file wins from here

    def test_the_page_and_the_app_carry_the_project_name_and_it_is_editable(self):
        (self.root / ".dfs" / "title").write_text('My <proj> "x"\n')
        status, raw, _ = self.call("GET", "/")
        page = raw.decode()
        self.assertIn("<title>My &lt;proj&gt; &quot;x&quot;</title>", page)
        self.assertIn('let PROJECT = "My <proj> \\"x\\"";', page)
        self.assertNotIn("{{", page)
        self.assertEqual(self.call("GET", "/manifest.json")[1]["name"], 'My <proj> "x"')
        status, body, _ = self.call("POST", "/api/title", dict(title="  Gateway   core  "))
        self.assertEqual((status, body["title"]), (200, "Gateway core"))
        self.assertEqual((self.root / ".dfs" / "title").read_text(), "Gateway core\n")
        self.assertEqual(self.call("GET", "/manifest.json")[1]["name"], "Gateway core")
        for bad in ("", "   ", "x" * 81):
            self.assertEqual(self.call("POST", "/api/title", dict(title=bad))[0], 400, bad)

    def test_the_design_system_is_served_and_the_page_uses_it(self):
        status, css, r = self.call("GET", "/design.css")
        self.assertEqual((status, r.getheader("Content-Type")), (200, "text/css; charset=utf-8"))
        for token in (b"--attention", b"--settled", b"--assumption", b"--hit", b"--fs-body", b"--s4"):
            self.assertIn(token, css)
        status, page, r = self.call("GET", "/design")
        self.assertEqual((status, r.getheader("Content-Type")), (200, "text/html; charset=utf-8"))
        self.assertIn(b"Design system", page)
        index = self.call("GET", "/")[1]
        self.assertIn(b'href="/design.css"', index)
        # one set of names: the page and the system agree on the meanings' tokens
        for old in (b"var(--red", b"var(--yellow", b"var(--blue"):
            self.assertNotIn(old, index)
            self.assertNotIn(old, css)

    def test_it_is_installable_a_manifest_icons_and_a_worker(self):
        status, m, _ = self.call("GET", "/manifest.json")
        self.assertEqual((status, m["display"], m["start_url"], m["scope"]), (200, "standalone", "/", "/"))
        sizes = {i["sizes"]: i for i in m["icons"]}
        self.assertIn("192x192", sizes)
        self.assertIn("512x512", sizes)
        self.assertTrue(any(i.get("purpose") == "maskable" for i in m["icons"]))
        for i in m["icons"]:
            status, raw, r = self.call("GET", i["src"])
            self.assertEqual((status, r.getheader("Content-Type")), (200, "image/png"), i["src"])
            self.assertTrue(raw.startswith(b"\x89PNG"))
        status, raw, r = self.call("GET", "/sw.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", r.getheader("Content-Type"))
        self.assertEqual(self.call("GET", "/apple-touch-icon.png")[0], 200)
        self.assertNotIn("caches.open", raw.decode(), "the worker caches nothing")
        self.assertEqual(self.call("GET", "/web/index.html")[0], 404, "only the listed files are served")
        page = self.call("GET", "/")[1].decode()
        self.assertIn("!isSecureContext && /Android/", page, "an Android page on plain http says why Install is missing")
        self.assertIn("--allow-host", (HERE.parent / "README.md").read_text(), "the README names the https route")

    def task(self):
        return self.call("GET", "/api/task/W1")[1]

    def log(self):
        return dfs_tree.load("W1")["log"]

    # ── who may write ───────────────────────────────────────────────────────────
    def test_a_write_from_another_site_is_refused_and_one_from_this_one_is_not(self):
        ts = self.task()["rows"][-1]["raises"][0]["ts"]
        body = dict(task="W1", ts=ts, body="Many.")
        status, data, _ = self.call("POST", "/api/answer", body,
                                    headers={"Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        self.assertEqual([e for e in self.log() if e["kind"] == "answer"], [])
        own = "http://127.0.0.1:%d" % self.port
        self.assertEqual(self.call("POST", "/api/answer", body, headers={"Origin": own})[0], 200)

    def test_a_write_must_be_json_and_a_read_changes_nothing(self):
        before = (self.root / ".dfs" / "items" / "W1.md").read_text()
        self.assertEqual(self.call("POST", "/api/accept", "task=W1",
                                   ctype="application/x-www-form-urlencoded")[0], 415)
        for path in ("/api/state", "/api/task/W1", "/api/assumptions"):
            self.assertEqual(self.call("GET", path)[0], 200)
        self.assertEqual((self.root / ".dfs" / "items" / "W1.md").read_text(), before)

    # ── being told, not asking ──────────────────────────────────────────────────
    def events(self):
        """An open `/api/events`: a function that returns the next `event:` name."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/api/events")
        r = conn.getresponse()
        self.assertEqual((r.status, r.getheader("Content-Type")), (200, "text/event-stream"))
        self.addCleanup(conn.close)

        def next_event():
            while True:
                line = r.fp.readline().decode()
                if line.startswith("event:"):
                    return line.split(":", 1)[1].strip()
                if not line:
                    return None
        return next_event

    def test_a_listener_is_told_where_things_stand_and_again_when_a_write_lands(self):
        nxt = self.events()
        self.assertEqual(nxt(), "change")             # on connecting
        ts = self.task()["rows"][-1]["raises"][0]["ts"]
        self.assertEqual(self.call("POST", "/api/answer", dict(task="W1", ts=ts, body="Many."))[0], 200)
        self.assertEqual(nxt(), "change")             # the write

    def test_a_change_made_elsewhere_reaches_the_listener(self):
        """A chain or the terminal screen writing the task file: the watcher notices."""
        stop = threading.Event()
        self.addCleanup(stop.set)
        threading.Thread(target=dfs_web.watch, args=(stop, 0.05), daemon=True).start()
        nxt = self.events()
        self.assertEqual(nxt(), "change")
        dfs_tree.append_log("W1", "answer", [dfs_tree.open_raises(dfs_tree.load("W1"))[0]["ts"]], "Many.")
        self.assertEqual(nxt(), "change")

    # ── what it shows ───────────────────────────────────────────────────────────
    def test_the_list_says_what_waits_on_the_author(self):
        status, data, _ = self.call("GET", "/api/state")
        self.assertEqual(status, 200)
        w1 = data["items"][0]
        self.assertEqual((w1["id"], w1["status"]), ("W1", "blocked"))
        self.assertTrue(w1["needs_you"])
        self.assertEqual(w1["assumptions"], ["W1.2"])
        self.assertEqual(w1["rev"], "rev 2")          # the raise and the assumption
        self.assertNotIn("2026-09-25T", w1["why"], "a timestamp is shown as an age")

    def test_a_node_carries_its_skim_flags_and_card(self):
        rows = {r["key"]: r for r in self.task()["rows"]}
        n = rows["W1.2"]
        self.assertEqual(n["skim"], "memory is flat over 10k ids.")
        self.assertTrue(n["assumes"] and n["ask"])
        roles = [r for r, _ in n["card"]]
        self.assertNotIn("head", roles, "the heading is the row")
        self.assertIn(["amber", "Idle buckets are safe to drop."], n["card"])
        self.assertIn(["amber", "a client returns inside one window."], n["card"])
        raised = rows["W1.3"]
        self.assertEqual(len(raised["raises"]), 1)
        self.assertEqual(raised["raises"][0]["body"], "Is churn from one client or many?")
        self.assertNotIn("Raise", [t for _, t in raised["card"]], "the page draws raises itself")

    def test_the_assumptions_across_tasks(self):
        status, data, _ = self.call("GET", "/api/assumptions")
        self.assertEqual([a["id"] for a in data["assumed"]], ["W1.2"])
        self.assertEqual(data["assumed"][0]["wrong"], "a client returns inside one window.")

    # ── what it writes ──────────────────────────────────────────────────────────
    def test_an_answer_is_the_log_entry_the_terminal_writes_and_cannot_be_given_twice(self):
        ts = self.task()["rows"][-1]["raises"][0]["ts"]
        self.assertEqual(self.call("POST", "/api/answer", dict(task="W1", ts=ts, body=" "))[0], 400)
        status, data, _ = self.call("POST", "/api/answer", dict(task="W1", ts=ts, body="Many."))
        self.assertEqual(status, 200)
        e = self.log()[-1]
        self.assertEqual((e["kind"], e["args"], e["body"].strip()), ("answer", [ts], "Many."))
        self.assertEqual(self.call("POST", "/api/answer", dict(task="W1", ts=ts, body="Again"))[0], 409)

    def test_a_correction_overrules_a_node_and_says_so(self):
        self.assertEqual(self.call("POST", "/api/correct", dict(
            task="W1", node="W1.2", verdict="maybe", body="x"))[0], 400)
        status, data, _ = self.call("POST", "/api/correct", dict(
            task="W1", node="W1.2", verdict="refuted", body="It leaks under churn."))
        self.assertEqual(status, 200)
        e = self.log()[-1]
        self.assertEqual((e["kind"], e["args"]), ("correct", ["W1.2", "refuted"]))
        row = {r["key"]: r for r in data["task"]["rows"]}["W1.2"]
        self.assertTrue(row["overruled"])
        self.assertTrue(row["skim"].startswith("overruled by you:"))
        self.assertFalse(row["assumes"], "a refuted node's assumption is moot")

    def test_a_tree_is_accepted_only_when_it_is_finished(self):
        self.assertEqual(self.call("POST", "/api/accept", dict(task="W1"))[0], 409)
        self.assertFalse(self.task()["acceptable"])

    def test_a_node_is_added_open_under_its_parent_and_a_missing_parent_is_refused(self):
        status, data, _ = self.call("POST", "/api/add", dict(
            task="W1", parent="W1.3", title="  Add a   metric ", approach="In src/metrics.py."))
        self.assertEqual(status, 200)
        self.assertEqual(data["added"], "W1.4")
        nd = dfs_tree.by_id(dfs_tree.load("W1"))["W1.4"]
        self.assertEqual((nd["status"], nd["parent"], nd["title"]), ("open", "W1.3", "Add a metric"))
        self.assertEqual(self.call("POST", "/api/add", dict(task="W1", parent="W1.9", title="x"))[0], 400)
        self.assertEqual(self.call("POST", "/api/add", dict(task="W1", title=""))[0], 400)

    def test_a_new_task_is_written_as_run_open_writes_it(self):
        status, data, _ = self.call("POST", "/api/new", dict(name="Cache the lookups", goal="Hits above 90%.", todos="profile it\nadd the cache\n"))
        self.assertEqual(status, 200)
        new = data["added"]
        t = dfs_tree.by_id(dfs_tree.load(new))
        self.assertEqual([(n["title"], n["status"]) for n in t.values()], [("profile it", "open"), ("add the cache", "open")])
        self.assertIn(new, [i["id"] for i in self.call("GET", "/api/state")[1]["items"]])
        self.assertEqual(self.call("POST", "/api/new", dict(name=""))[0], 400)
        self.assertEqual(self.call("POST", "/api/new", dict(name="x", id="W1"))[0], 409)     # taken
        self.assertEqual(self.call("POST", "/api/new", dict(name="x", todos=[1]))[0], 400)

    def test_an_unknown_task_or_action_is_not_found(self):
        self.assertEqual(self.call("GET", "/api/task/W99")[0], 404)
        self.assertEqual(self.call("POST", "/api/accept", dict(task="W99"))[0], 404)
        self.assertEqual(self.call("POST", "/api/nonsense", dict(task="W1"))[0], 404)

    # ── the walk ────────────────────────────────────────────────────────────────
    def test_the_walk_starts_with_a_budget_and_says_where_it_stands(self):
        status, snap, _ = self.call("GET", "/api/walk")
        self.assertEqual((status, snap["on"], snap["enabled"]), (200, False, True))
        self.assertEqual([c["id"] for c in snap["candidates"]], ["W1", "W2"])
        status, snap, _ = self.call("POST", "/api/walk/start", dict(budget=6))
        self.assertEqual((status, snap["on"], snap["budget"]), (200, True, 6))
        self.assertEqual(self.fake.started_chains, [("W1", "6")])

    def test_a_budget_that_is_not_a_number_of_sessions_is_refused(self):
        for bad in (0, "many", None, -1):
            self.assertEqual(self.call("POST", "/api/walk/start", dict(budget=bad))[0], 409)
        self.assertFalse(self.fake.walk_on)

    def test_next_is_chosen_from_the_open_tasks_and_a_task_that_cannot_be_worked_is_refused(self):
        status, snap, _ = self.call("POST", "/api/walk/pin", dict(item="W2"))
        self.assertEqual((status, snap["pinned"]), (200, "W2"))
        self.assertEqual(self.call("POST", "/api/walk/pin", dict(item="W3"))[0], 409)
        status, snap, _ = self.call("POST", "/api/walk/pin", dict(item=None))
        self.assertIsNone(snap["pinned"])

    def test_the_agent_is_chosen_for_the_next_chain(self):
        status, snap, _ = self.call("POST", "/api/walk/agent", dict(agent="codex"))
        self.assertEqual((status, snap["agent"]), (200, "codex"))
        self.assertEqual(self.call("POST", "/api/walk/agent", dict(agent="bash"))[0], 409)

    def test_stopping_the_walk_ends_its_chain(self):
        self.call("POST", "/api/walk/start", dict(budget=6))
        self.fake.run_dirs = [test_walker.run("d1", "W1", live=True, run_no="2")]
        status, snap, _ = self.call("POST", "/api/walk/stop", {})
        self.assertEqual((status, snap["on"]), (200, False))
        self.assertEqual(self.fake.lowered, [("d1", 2)])

    def test_a_chain_is_interrupted_by_item_and_one_that_is_not_running_is_refused(self):
        self.assertEqual(self.call("POST", "/api/chain/stop", dict(item="W1"))[0], 409)
        self.assertEqual(self.call("POST", "/api/chain/stop", {})[0], 400)

    def test_with_the_walk_off_the_page_can_still_read_and_write_but_not_start_agents(self):
        dfs_web.WALK["enabled"] = False
        status, snap, _ = self.call("GET", "/api/walk")
        self.assertEqual((status, snap), (200, dict(enabled=False)))
        self.assertEqual(self.call("POST", "/api/walk/start", dict(budget=3))[0], 403)
        self.assertEqual(self.fake.started_chains, [])
        self.assertEqual(self.call("GET", "/api/state")[0], 200)

    def test_the_page_is_served_with_no_login(self):
        status, body, r = self.call("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(("<title>%s</title>" % dfs_web.project_title()).encode(), body)
        self.assertIn("text/html", r.getheader("Content-Type"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
