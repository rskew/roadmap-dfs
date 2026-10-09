#!/usr/bin/env python3
"""The roadmap on a phone: the author's half of the screen (tui.py) as a web page.

    web.py [--host 127.0.0.1] [--port 8765]   serve the roadmap in the current repo

It reads and writes exactly what the terminal screen does, through the same code:
`tree.py` for the task files, `state.py` for the rows, and `tui.py`'s own view
functions (`tree_entries`, `node_card`, `first_sentence`, `age`) for what a node
says, so the two cannot disagree about it. What it does is the review: read the
tree, answer a raise, correct a node, add one, accept a finished tree. It does not
start chains: that spends a subscription and belongs where the terminal is.

The page is kept current by server-sent events (`/api/events`), not by asking: a
watcher stats the task files once a second and a change, from here or from the
terminal or from a chain, is pushed to every open page, which then reads what it is
showing.

It also steers the walk (`walk.py`: one engine, the terminal's `w`, `n` and `K`): start it with a
session budget, stop it, choose where it goes next, interrupt a chain.

⚠️ THERE IS NO LOGIN, so it listens on this machine only (127.0.0.1) unless you say
otherwise. `--host 0.0.0.0` (or DFS_WEB_HOST) opens it to the network, and then anyone on
that network can read the roadmap, answer, correct and accept, and, unless `--no-walk`,
START AGENTS (the walk), which run with their permission prompts off: that is remote
code execution on this machine, for whoever can reach the port. Do it on a network you
trust. Bound to loopback, a request whose `Host` is not a loopback name is refused (a
page on another site can point its own name at 127.0.0.1: DNS rebinding), a write from
another website is refused (it must be JSON, which a page elsewhere cannot send without a
preflight this server never grants, and its `Origin` must be this server's own address).
A TLS proxy's hostname is admitted by name, `--allow-host` or DFS_WEB_ALLOW_HOST, and no other.

THE PROJECT'S COLOUR is `.dfs/theme` (`#rrggbb`), else one made from the name; it marks the
bar, the browser's own bar and the installed app, so two projects are told apart. It is chosen
in the same dialog as the name ("Rename project"), and `--project` in /design.css holds it.

THE PROJECT'S NAME is `.dfs/title`, made from git (the `origin` remote's name, else the
directory's) the first time it is asked for and the name from then on; it is edited on the
page ("Rename project"). One server per project, each on its own `--port`, installs as its
own app. AS AN APP, without the browser bar: iPhone's Share, Add to Home Screen works over
http; Android's Install needs https (or localhost), which means `--cert`/`--key` here, or a
TLS proxy such as `tailscale serve` (bind 127.0.0.1 and name the proxy's hostname with
`--allow-host`, or the Host check refuses it). The worker it installs caches nothing.
CHAT (`chat.py`, a button on a task and on the list) talks about a task or the project with claude,
READ-ONLY (Read, Grep, Glob) when the page runs the turn; when a screen serves the page the chats are
agents that screen keeps running in the background. The Chats tab lists them all. It starts agents, so
`--no-walk` turns it off.
ARTEFACTS (`.dfs/artefacts`) are served sandboxed at `/artefacts/<name>` and linked from the
text that names them.
"""
import argparse
import html
import subprocess
import json
import os
import queue
import re
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import paths as dfs_paths   # noqa: E402
import order as dfs_order   # noqa: E402
import state as dfs_state   # noqa: E402
import tree as dfs_tree     # noqa: E402
import tui as dfs_tui       # noqa: E402  (its view functions; nothing here starts curses)
import runs as dfs_runs     # noqa: E402
import walk as dfs_walk      # noqa: E402
import chat as dfs_chat      # noqa: E402

INDEX = HERE / "web" / "index.html"
STATIC = {"/sw.js": "text/javascript", "/design.css": "text/css", "/design": "text/html", "/icon-192.png": "image/png", "/icon-512.png": "image/png",
          "/icon-maskable-512.png": "image/png", "/apple-touch-icon.png": "image/png"}
MAX_BODY = 64 * 1024
LOCK = threading.Lock()       # one writer at a time: a task file is read, changed, written


class Refused(Exception):
    """A request that cannot be done, with the status to say so."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


# ── what the screen shows ─────────────────────────────────────────────────────

def project_title():
    """What this instance is called: the project's `.dfs/title`, made from git the first
    time it is asked for, and editable on the page."""
    return dfs_paths.project_title()


def index_html():
    name = project_title()
    return (INDEX.read_text().replace("{{project_json}}", json.dumps(name).replace("</", "<\\/"))
            .replace("{{colour_chosen}}", "true" if dfs_paths.read_theme() else "false")
            .replace("{{colour}}", dfs_paths.project_colour())
            .replace("{{project}}", html.escape(name))).encode()


def needs_you(it):
    return bool(it["status"] == "blocked" or it.get("open_raises")
                or it.get("standing") or it.get("corrections"))


def state_json():
    """The task list: the rows `state.py --json` gives the terminal, plus `rev`."""
    data = dfs_state.full()
    items = []
    for it in data["items"]:
        items.append(dict(
            id=it["id"], goal=it["goal"], depth=it.get("depth", 0), status=it["status"],
            why=humanize(it.get("why", "")), rev=dfs_tui.review_cell(it), needs_you=needs_you(it),
            raises=len(it.get("open_raises") or ()), standing=bool(it.get("standing")),
            assumptions=list(it.get("assumptions") or ()),
            corrections=list(it.get("corrections") or ()),
            waiting_on=it.get("waiting_on") or "", blocked_by=it.get("blocked_by") or "",
            segment=it.get("segment", 0), ahead=it.get("ahead", 0),
            uncommitted=bool(it.get("uncommitted"))))
    return dict(items=items, next_item=data.get("next_item"),
                needs_you=sum(1 for i in items if i["needs_you"]),
                colour=dfs_paths.project_colour(), colour_chosen=bool(dfs_paths.read_theme()))


_commit_cache = {}


def commits_for(task):
    """`git log` is too slow to run on every request: keyed by the task files' mtimes."""
    tags = dfs_tree.part_tags(task)
    key = []
    for g in tags:
        for p in (dfs_tree.part_path(task, g), dfs_tree.archive_part(task, g)):
            try:
                key.append(p.stat().st_mtime_ns)
            except OSError:
                key.append(0)
    hit = _commit_cache.get(task)
    if hit and hit[0] == key:
        return hit[1]
    got = dfs_tree.node_commits(task)
    _commit_cache[task] = (key, got)
    return got


def archive_text(task):
    tags = dfs_tree.part_tags(task)
    paths = [dfs_tree.archive_part(task, g) for g in tags]
    return "\n\n".join(p.read_text() for p in paths if p.exists())


ISO = __import__("re").compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")


def humanize(text):
    """A status line with its timestamps as ages: `open raise 2026-10-02T08:20:00Z on W9.3`
    is `open raise 4d ago on W9.3`. The timestamp is the raise's identity, not what to read."""
    return ISO.sub(lambda m: dfs_tui.age(m.group(0)), text or "")


def plain(card):
    """The card without the terminal's glyphs: the page says `confirmed`, not a tick."""
    out = []
    for role, text in card:
        if role == "meta":
            text = text.split(" ", 1)[1] if " " in text else text
        elif role == "label":
            text = text.replace("⚑ ", "")
        out.append([role, text])
    return out


def raise_json(r):
    return dict(ts=r["ts"], age=dfs_tui.age(r["ts"]),
                on=r["args"][0] if r["args"] else "", body=r["body"].strip())


def skim_for(t, nd):
    """The one line under a node: what the author overruled, else the verdict."""
    story = dfs_tree.story(t, nd["id"])
    if story["overruled"]:
        why = (story["overruled"]["body"].strip().splitlines() or [""])[0]
        return "overruled by you: " + dfs_tui.first_sentence(why)
    return dfs_tui.first_sentence(nd["fields"].get("Determination"))


ART_NAME = r"[0-9A-Za-z][0-9A-Za-z._-]*"
ART_TYPES = {".html": "text/html", ".png": "image/png", ".svg": "image/svg+xml",
             ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
             ".webp": "image/webp"}
# An artefact is a page an AGENT wrote, opened from the same address as the controls that
# start agents. So it is served sandboxed: an opaque origin, no network, no way to reach
# `/api` as this page (and a cross-origin write is refused, `same_origin`). The checker
# (artefact_check.sh) already holds them to self-contained, which is all they need.
ART_CSP = ("sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; "
           "style-src 'unsafe-inline'; img-src data: 'self'; font-src data:")


def strings(obj):
    """Every string inside a nested structure: the prose to scan for artefact names."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from strings(v)
    elif isinstance(obj, (list, tuple, set)):
        for v in obj:
            yield from strings(v)


def artefact_title(path):
    try:
        head = path.read_text(errors="replace")[:4096]
    except OSError:
        return ""
    m = re.search(r"<title[^>]*>(.*?)</title>", head, re.S | re.I)
    return html.unescape(" ".join(m.group(1).split())) if m else ""


def artefacts_for(text):
    """The artefacts a task's prose names (as the screen's `v` pane finds them: by scanning
    for `<name>.html`, and for a screenshot `<name>.png` and the other picture types) that
    exist in `.dfs/artefacts`. Each: file, title and kind ("page" or "image")."""
    root = dfs_paths.artefacts()
    on_disk = {}
    try:
        for p in sorted(root.iterdir()):
            if (p.suffix.lower() in ART_TYPES and not p.name.startswith(("_", "."))
                    and p.is_file()):
                on_disk[p.name] = p
    except OSError:
        pass
    named = []
    for name in re.findall(r"(%s\.(?:html|png|jpe?g|gif|webp|svg))" % ART_NAME, text, re.I):
        if name in on_disk and name not in named:
            named.append(name)

    def entry(n):
        page = on_disk[n].suffix.lower() == ".html"
        return dict(file=n, title=(artefact_title(on_disk[n]) if page else "") or n,
                    kind="page" if page else "image")
    return [entry(n) for n in named]


def task_json(task):
    if not dfs_tree.exists(task):
        raise Refused("no task %s" % task, 404)
    t = dfs_tree.load(task)
    archive, commits = archive_text(task), commits_for(task)
    status, why = dfs_tree.status_of(t)
    accepted = dfs_tree.accepted(t)
    rows = []
    for row in dfs_tui.tree_entries(t):
        if row["kind"] == "section":
            rows.append(dict(kind="section", key=row["key"], name=row["name"],
                             text=row["text"], open=row["open_default"]))
        elif row["kind"] == "raise":
            rows.append(dict(kind="raise", key=row["key"], **raise_json(row["raise_"])))
        else:
            nd, st = row["node"], row["status"]
            f = nd["fields"]
            story = dfs_tree.story(t, nd["id"])
            # Without the raises: the page draws those itself, with the Answer button.
            card = dfs_tui.node_card(t, dict(row, raises=[]), archive, commits.get(nd["id"], ()))
            rows.append(dict(
                kind="node", key=nd["id"], id=nd["id"], title=nd["title"], status=st,
                depth=row["depth"], kids=row["kids"], chain=row["chain"],
                parent=nd["parent"] or "", skim=skim_for(t, nd),
                editable=st in ("open", "parked") and nd["fields"].get("Approach") != dfs_tree.SHELF_STUB,
                approach=nd["fields"].get("Approach", ""),
                raises=[raise_json(r) for r in row["raises"]],
                raises_below=row["raises_below"],
                assumes=row["assumes"], ask=row["asks"],
                answered=bool(story["answered"]), overruled=bool(story["overruled"]),
                card=plain(card[1:])))      # [1:]: the heading is the row
    nodes = dfs_tree.by_id(t)
    assumed = []
    for nid in ([] if accepted else dfs_tree.assumed(t)):
        f = nodes[nid]["fields"]
        assumption, wrong = dfs_tree.split_falsifier(f.get("Hypothesis") or "")
        assumed.append(dict(task=task, id=nid, title=nodes[nid]["title"],
                            assumption=assumption, wrong=wrong, ask=f.get("Ask", "")))
    return dict(id=task, goal=t["name"] or (t["goal"].splitlines() or [""])[0],
                name=t["name"], goal_text=t["goal"],
                artefacts=artefacts_for("\n".join(strings(t))),
                status=status, why=humanize(why), accepted=accepted,
                acceptable=status == "done" and not accepted, rows=rows, assumed=assumed,
                notes=[dict(kind=k, verdict=v, body=b) for k, _ts, v, b in dfs_tree.notes(t)],
                sessions=dfs_tree.sessions_since_author(t))


def assumptions_json():
    """Every open assumption and ask in every task not yet accepted."""
    out = []
    for it in dfs_state.items():
        out += task_json(it["id"])["assumed"]
    return dict(assumed=out)


# ── what the author writes (the same log entries tui.py writes) ──────────────

def need(body, key, what):
    v = body.get(key)
    if not isinstance(v, str) or not v.strip():
        raise Refused("%s is needed" % what)
    return v.strip()


def set_title(body):
    try:
        name = dfs_paths.write_title(need(body, "title", "a name"))
    except ValueError as e:
        raise Refused(str(e))
    except OSError as e:
        raise Refused("could not write %s: %s" % (dfs_paths.rel(dfs_paths.title_file()), e), 500)
    HUB.notify()
    return dict(title=name)


def set_theme(body):
    """Choose the project's colour (`#rrggbb`), or clear the choice with an empty string
    so it follows the name again."""
    value = body.get("colour")
    if not isinstance(value, str):
        raise Refused("a colour is needed")
    try:
        colour = dfs_paths.write_theme(value)
    except ValueError as e:
        raise Refused(str(e))
    except OSError as e:
        raise Refused("could not write %s: %s" % (dfs_paths.rel(dfs_paths.theme_file()), e), 500)
    HUB.notify()
    return dict(colour=colour, chosen=bool(dfs_paths.read_theme()))


def new_task(body):
    """A new task, as `run --open` writes one: a name, the goal in the author's words, and
    todo lines that become a linear chain of open nodes."""
    name = need(body, "name", "a name")
    todos = body.get("todos") or []
    if isinstance(todos, str):
        todos = todos.splitlines()
    if not isinstance(todos, list) or not all(isinstance(x, str) for x in todos) or len(todos) > 100:
        raise Refused("todos are lines of text")
    goal = body.get("goal") or ""
    if not isinstance(goal, str) or len(goal) > 20000:
        raise Refused("the goal is text")
    with LOCK:
        try:
            task = dfs_tree.create_task(name, goal, todos, task=body.get("id") or None)
        except (ValueError, dfs_paths.NoBranch) as e:
            raise Refused(str(e), 409)
    HUB.notify()
    return dict(added=task)


MOVES = {"up", "down", "out", "in", "break"}

# Why a move that cannot be made cannot: the terminal's own words (tui.py reorder,
# reparent, toggle_break), since it is the same rule refusing.
MOVE_REFUSAL = {
    "up": "is already first among its siblings", "down": "is already last among its siblings",
    "out": "is already at the root",
    "in": "has no sibling above it in this section to go under",
}


def reorder(body):
    """Move one task in `.dfs/order.md`, as the terminal's `[` `]` `{` `}` `b` do.

    The whole tree is read from the derivation, moved by `order.py`, and written whole
    (`write_order`), so the fences survive. A move that cannot be made is a 409 saying
    why, and the file is not touched.
    """
    item = need(body, "item", "the task")
    move = body.get("move")
    if move not in MOVES:
        raise Refused("a move is one of %s" % ", ".join(sorted(MOVES)))
    with LOCK:
        items = dfs_state.full()["items"]
        nodes = dfs_order.tree_of(items)
        if dfs_order.index_of(nodes, item) is None:
            raise Refused("no task %s" % item, 404)
        if move == "break":
            had = dfs_order.has_break_above(nodes, item)
            after = dfs_order.break_toggled(nodes, item)
            refusal = ("is nested — a break separates whole branches, never cuts one"
                       if dict(nodes).get(item) else
                       "is the first task — a break above it would gate nothing")
        else:
            after = {"up": lambda: dfs_order.moved(nodes, item, -1),
                     "down": lambda: dfs_order.moved(nodes, item, 1),
                     "out": lambda: dfs_order.outdented(nodes, item),
                     "in": lambda: dfs_order.indented(nodes, item)}[move]()
            refusal = MOVE_REFUSAL[move]
        if after is None:
            raise Refused("%s %s" % (item, refusal), 409)
        try:
            dfs_order.write_order(after)
        except OSError as e:
            raise Refused("could not write %s: %s" % (dfs_paths.rel(dfs_paths.order()), e), 500)
    HUB.notify()
    return state_json()


def act(name, body):
    task = need(body, "task", "the task")
    if not dfs_tree.exists(task):
        raise Refused("no task %s" % task, 404)
    t = dfs_tree.load(task)
    nodes = dfs_tree.by_id(t)
    with LOCK:
        if name == "answer":
            ts = need(body, "ts", "the raise")
            if ts not in {r["ts"] for r in dfs_tree.open_raises(t)}:
                raise Refused("that raise is not open (already answered?)", 409)
            dfs_tree.append_log(task, "answer", [ts], need(body, "body", "an answer"))
        elif name == "correct":
            nid = need(body, "node", "the node")
            verdict = body.get("verdict")
            if nid not in nodes or verdict not in ("confirmed", "refuted"):
                raise Refused("a node, and confirmed or refuted, are needed")
            dfs_tree.append_log(task, "correct", [nid, verdict],
                                need(body, "body", "a directive"))
        elif name == "accept":
            if not (dfs_tree.status_of(t)[0] == "done" and not dfs_tree.accepted(t)):
                raise Refused("%s is not a finished tree waiting to be accepted" % task, 409)
            dfs_tree.append_log(task, "accept")
        elif name == "edit_task":
            try:
                dfs_tree.edit_task(task, need(body, "name", "a name"), need(body, "goal", "a goal"))
            except (ValueError, dfs_paths.NoBranch) as e:
                raise Refused(str(e), 409)
        elif name == "edit_node":
            nid = need(body, "node", "the node")
            if nid not in nodes:
                raise Refused("no node %s" % nid, 404)
            try:
                dfs_tree.edit_node(task, nid, need(body, "title", "a title"), body.get("approach") or "")
            except (ValueError, dfs_paths.NoBranch) as e:
                raise Refused(str(e), 409)
        elif name == "add":
            parent = body.get("parent") or None
            if parent and parent not in nodes:
                raise Refused("no node %s" % parent)
            try:
                nid = dfs_tree.add_node(task, need(body, "title", "a title"), parent,
                                        body.get("approach") or "")
            except (ValueError, dfs_paths.NoBranch) as e:
                raise Refused(str(e), 409)
            HUB.notify()
            return dict(added=nid, task=task_json(task))
        else:
            raise Refused("no such action", 404)
    HUB.notify()
    return dict(task=task_json(task))


# ── the walk ──────────────────────────────────────────────────────────────────

WALK = {"enabled": True, "walker": None}


def walker():
    return _the_walker()


def _the_walker():
    if WALK["walker"] is None:
        WALK["walker"] = dfs_walk.HeadlessWalk(on_change=lambda: HUB.notify())
    return WALK["walker"]


CHAT = {"chats": None}


def chats():
    if CHAT["chats"] is None:
        CHAT["chats"] = dfs_chat.Chats(on_change=lambda: HUB.notify(), walker=lambda: WALK["walker"])
    return CHAT["chats"]


def chat_act(name, body=None, scope=None):
    """A chat about a task or the project. It starts an agent, so it is off with the walk."""
    if not WALK["enabled"]:
        raise Refused("chat is off: this server was started with --no-walk", 403)
    try:
        if name == "chat/send":
            return chats().send(need(body, "scope", "a scope"), body.get("message"))
        if name == "chat/open":
            return chats().open(need(body, "scope", "a scope"))
        if name == "chat/clear":
            return chats().clear(need(body, "scope", "a scope"))
        if name == "chat":
            return chats().state(scope)
        if name == "chats":
            return dict(chats=chats().list())
    except ValueError as e:
        raise Refused(str(e), 409)
    raise Refused("no such action", 404)


def walk_json():
    snap = walker().snapshot() if WALK["enabled"] else {}
    return dict(snap, enabled=WALK["enabled"])


def walk_act(name, body):
    """Start, stop and steer the walk: what `w`, `n` and `K` do in the terminal."""
    if not WALK["enabled"]:
        raise Refused("the walk controls are off: this server was started with --no-walk", 403)
    w = walker()
    try:
        if name == "walk/start":
            w.start(body.get("budget"))
        elif name == "walk/stop":
            w.stop()
        elif name == "walk/pin":
            w.pin(body.get("item") or None)
        elif name == "walk/agent":
            w.set_agent(body.get("agent"))
        elif name == "chain/stop":
            w.stop_chain(need(body, "item", "the task"))
        else:
            raise Refused("no such action", 404)
    except ValueError as e:
        raise Refused(str(e), 409)
    HUB.notify()
    return walk_json()


# ── pushing changes ───────────────────────────────────────────────────────────

class Hub:
    """Who is listening on `/api/events`, and what to tell them. A change is a counter
    that goes up: the page does not need to know WHAT changed, only to read again."""

    def __init__(self):
        self.lock = threading.Lock()
        self.clients = set()
        self.version = 0

    def subscribe(self):
        q = queue.Queue()
        with self.lock:
            self.clients.add(q)
            q.put(self.version)             # a new listener is told where things stand
        return q

    def unsubscribe(self, q):
        with self.lock:
            self.clients.discard(q)

    def notify(self):
        with self.lock:
            self.version += 1
            for q in self.clients:
                q.put(self.version)


HUB = Hub()


def roadmap_signature():
    """What the pages show, as the mtimes of the files it is read from: the task parts,
    their archives and the order file. Stat only; nothing is read or parsed."""
    sig = []
    roots = [dfs_paths.items(), dfs_paths.state() / "archive" / "items"]
    for root in roots:
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                p = os.path.join(dirpath, f)
                try:
                    sig.append((p, os.stat(p).st_mtime_ns))
                except OSError:
                    pass
    try:
        sig.append(("order", os.stat(dfs_paths.order()).st_mtime_ns))
    except OSError:
        pass
    # What the walk did (and so who started, stopped or pinned it, from the screen or
    # the page): the activity log both write.
    try:
        sig.append(("activity", os.stat(os.path.join(dfs_runs.run_root(), "activity.log")).st_mtime_ns))
    except OSError:
        pass
    # A chain's progress (`run 2 of 5`) is its meta.json; its console is not watched, it
    # changes every line.
    for root in dfs_runs.run_roots():
        for dirpath, _dirs, files in os.walk(root):
            if "meta.json" in files:
                p = os.path.join(dirpath, "meta.json")
                try:
                    sig.append((p, os.stat(p).st_mtime_ns))
                except OSError:
                    pass
    return sorted(sig)


def watch(stop, interval=1.0):
    last = roadmap_signature()
    while not stop.wait(interval):
        now = roadmap_signature()
        if now != last:
            last = now
            HUB.notify()


# ── the server ────────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    server_version = "roadmap-web"
    KEEPALIVE = 15

    def log_message(self, fmt, *args):         # the terminal belongs to the person running it
        pass

    def send(self, status, body, ctype="application/json", extra=()):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if "text" in ctype or "json" in ctype else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def json(self, status, obj):
        self.send(status, json.dumps(obj, default=str))

    def artefact(self, name):
        """One file of `.dfs/artefacts`, by bare name: no path, no dotfile, a known type."""
        p = dfs_paths.artefacts() / name
        ctype = ART_TYPES.get(p.suffix.lower())
        if not re.fullmatch(ART_NAME, name) or ctype is None or not p.is_file():
            raise Refused("no such artefact", 404)
        return self.send(200, p.read_bytes(), ctype,
                         extra=[("Content-Security-Policy", ART_CSP)])

    def same_origin(self):
        """A write names where it came from: refused when that is another site."""
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return urlparse(origin).netloc == self.headers.get("Host", "")

    def events(self):
        """Server-sent events: `change` whenever the roadmap does, and a comment every
        few seconds so a proxy or a sleeping phone notices a dead connection."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        q = HUB.subscribe()
        try:
            self.wfile.write(b"retry: 2000\n\n")
            self.wfile.flush()
            while True:
                try:
                    v = q.get(timeout=self.KEEPALIVE)
                    # Coalesce a burst (a chain writing a task three times) into one.
                    while True:
                        try:
                            v = q.get_nowait()
                        except queue.Empty:
                            break
                    self.wfile.write(("event: change\ndata: %d\n\n" % v).encode())
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            HUB.unsubscribe(q)

    def host_ok(self):
        """Bound to loopback, only a loopback NAME is a request meant for this server: a
        page on another site can point its own name at 127.0.0.1 and talk to it as itself
        (DNS rebinding), and the `Host` it sends is its own."""
        if not self.server.loopback:
            return True
        host = self.headers.get("Host", "")
        name = host[1:host.index("]")] if host.startswith("[") and "]" in host else host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        return name in LOOPBACK or name.lower() in ALLOW_HOSTS

    def handle_any(self, method):
        url = urlparse(self.path)
        try:
            if not self.host_ok():
                raise Refused("this server answers to its own address only", 421)
            if method == "GET":
                return self.get(url.path)
            if not self.same_origin():
                raise Refused("that request came from another site", 403)
            if "application/json" not in self.headers.get("Content-Type", ""):
                raise Refused("JSON only", 415)
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise Refused("too large", 413)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                raise Refused("that is not JSON")
            if not isinstance(body, dict) or not url.path.startswith("/api/"):
                raise Refused("not found", 404)
            name = url.path[len("/api/"):]
            if name == "title":
                return self.json(200, set_title(body))
            if name == "theme":
                return self.json(200, set_theme(body))
            if name == "new":
                return self.json(200, new_task(body))
            if name == "order":
                return self.json(200, reorder(body))
            if name.startswith("chat/"):
                return self.json(200, chat_act(name, body))
            if name.startswith(("walk/", "chain/")):
                return self.json(200, walk_act(name, body))
            return self.json(200, act(name, body))
        except Refused as e:
            return self.json(e.status, dict(error=str(e)))
        except (OSError, FileNotFoundError, dfs_paths.NoBranch) as e:
            return self.json(500, dict(error=str(e)))

    def get(self, path):
        if path in ("/", "/index.html"):
            return self.send(200, index_html(), "text/html")
        if path == "/api/events":
            return self.events()
        if path == "/api/walk":
            return self.json(200, walk_json())
        if path == "/api/chats":
            return self.json(200, chat_act("chats"))
        if path.startswith("/api/chat/"):
            return self.json(200, chat_act("chat", scope=path[len("/api/chat/"):]))
        if path == "/api/state":
            return self.json(200, state_json())
        if path == "/api/assumptions":
            return self.json(200, assumptions_json())
        if path.startswith("/api/task/"):
            return self.json(200, task_json(path[len("/api/task/"):]))
        if path.startswith("/artefacts/"):
            return self.artefact(path[len("/artefacts/"):])
        if path in STATIC:
            return self.send(200, (HERE / "web" / (path[1:] + (".html" if path == "/design" else ""))).read_bytes(), STATIC[path])
        if path == "/manifest.json":
            icons = [dict(src="/icon-192.png", sizes="192x192", type="image/png"),
                     dict(src="/icon-512.png", sizes="512x512", type="image/png"),
                     dict(src="/icon-maskable-512.png", sizes="512x512", type="image/png",
                          purpose="maskable")]
            return self.json(200, dict(name=project_title(), short_name=project_title()[:12], id="/", start_url="/",
                                       scope="/", display="standalone", orientation="portrait",
                                       background_color="#f5f4ef", theme_color=dfs_paths.project_colour(),
                                       icons=icons))
        raise Refused("not found", 404)

    def do_GET(self):
        try:
            self.handle_any("GET")
        except (BrokenPipeError, ConnectionResetError):
            pass            # the page went away (navigated, closed): nobody to tell

    def do_POST(self):
        try:
            self.handle_any("POST")
        except (BrokenPipeError, ConnectionResetError):
            pass


LOOPBACK = ("127.0.0.1", "localhost", "::1")
ALLOW_HOSTS = set()      # names besides loopback a loopback-bound server answers to: a TLS proxy's


def default_host():
    """This machine only, unless DFS_WEB_HOST says otherwise: there is no login."""
    return os.environ.get("DFS_WEB_HOST") or "127.0.0.1"


class Server(ThreadingHTTPServer):
    daemon_threads = True          # an open event stream must not hold the process up

    @property
    def loopback(self):
        return self.server_address[0] in LOOPBACK


def make_server(host, port):
    return Server((host, port), Handler)


def lan_address():
    """The address a phone would use: the one this machine's default route leaves by."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))            # nothing is sent; this only picks a route
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--host", default=default_host(),
                    help="127.0.0.1 (this machine only, the default) or 0.0.0.0 (the network: "
                         "no login, and the walk starts agents)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("DFS_WEB_PORT", 8765)))
    ap.add_argument("--no-walk", action="store_true",
                    help="no walk controls: the page can read and write the roadmap but "
                         "cannot start an agent")
    ap.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                    help="a hostname a TLS proxy in front of this loopback server forwards "
                         "(e.g. the ts.net name `tailscale serve` gives); repeatable, or "
                         "DFS_WEB_ALLOW_HOST, comma-separated")
    ap.add_argument("--cert", help="a TLS certificate (PEM): serve https, which an installed "
                                   "app needs on any address but localhost")
    ap.add_argument("--key", help="the certificate's private key (PEM)")
    args = ap.parse_args(argv)
    if bool(args.cert) != bool(args.key):
        raise SystemExit("web: --cert and --key go together")
    if not dfs_paths.has_roadmap():
        raise SystemExit("web: " + dfs_paths.missing_message())
    handle = serve(args.host, args.port, walk_enabled=not args.no_walk,
                   cert=args.cert, key=args.key, allow_hosts=args.allow_host)
    print("roadmap web: " + handle.url)
    if not handle.public:
        print("  this machine only; --host 0.0.0.0 opens it to the network")
    if handle.url.startswith("http:") and handle.public:
        print("  to install it as an app, a browser wants https (or localhost): --cert/--key, "
              "or see the README")
    if handle.public:
        print("  no login: anyone on this network can answer, correct and accept"
              + (", and start agents (the walk), which run with their permission prompts "
                 "off. --no-walk removes that; --host 127.0.0.1 keeps it to this machine."
                 if WALK["enabled"] else ". --host 127.0.0.1 keeps it to this machine."))
    try:
        while handle.thread.is_alive():
            handle.thread.join(1)
    except KeyboardInterrupt:
        pass
    handle.stop()
    return 0


class Handle:
    """A running server: where it is, whether the network can reach it, and how to stop it."""

    def __init__(self, server, thread, stop, url, public):
        self.server, self.thread, self._stop, self.url, self.public = server, thread, stop, url, public

    def stop(self):
        self._stop.set()
        self.server.shutdown()
        self.server.server_close()


def serve(host, port, walker=None, walk_enabled=True, tries=1, cert=None, key=None, allow_hosts=()):
    """Start the page's server on threads of this process and return its `Handle`.

    `walker` is the walk the page controls: none, and it makes its own (`walker.Walker`);
    the terminal screen passes its own, so the page and the screen share ONE walk.
    `tries` > 1 takes the next free port when `port` is in use (one screen per project).
    `allow_hosts` adds to DFS_WEB_ALLOW_HOST (comma-separated), which the terminal screen's page
    takes by that alone."""
    WALK["enabled"] = walk_enabled
    ALLOW_HOSTS.clear()
    names = list(allow_hosts) + os.environ.get("DFS_WEB_ALLOW_HOST", "").split(",")
    ALLOW_HOSTS.update(n.strip().lower() for n in names if n.strip())
    if walker is not None:
        WALK["walker"] = walker
    server, last = None, None
    for p in range(port, port + max(1, tries)) if port else [0]:
        try:
            server = make_server(host, p)
            break
        except OSError as e:
            last = e
    if server is None:
        raise last
    if cert:
        import ssl
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
    stop = threading.Event()
    threading.Thread(target=watch, args=(stop,), daemon=True).start()
    if walk_enabled:
        threading.Thread(target=_the_walker().loop, args=(stop,), daemon=True).start()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    shown = lan_address() if host in ("0.0.0.0", "::") else host
    url = "%s://%s:%d/" % ("https" if cert else "http", shown, server.server_address[1])
    return Handle(server, thread, stop, url, host not in ("127.0.0.1", "localhost", "::1"))


if __name__ == "__main__":
    sys.exit(main())
