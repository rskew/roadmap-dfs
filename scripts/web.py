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

A task's page lists the chains that worked it (`runs.py`) and opens one's `console.log` (`/api/run/<name>`,
read by the directory's name and never by a path), so what the terminal's `l` shows is on the phone too.

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
THE PROJECT'S BACKGROUND is `.dfs/background` (a colour for light mode, one for dark) and, apart from
it, a picture for each mode, `.dfs/background-image-light` and `-dark` (`.dfs/background-image` serves a
mode that has none of its own): a png, jpeg, webp or gif of 8 MB or less, set at POST /api/background-image
(base64 in JSON, with a `mode`; the one write allowed a body past 64 KB), served at GET /background-image
(`?mode=light|dark`), and drawn fixed behind the page under a 90% veil of the paper so text keeps its
contrast on any picture.

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
import base64
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
from urllib.parse import parse_qs, urlparse

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
IMAGE_BODY = dfs_paths.IMAGE_MAX * 4 // 3 + MAX_BODY      # a picture in base64, and the JSON around it
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
            .replace("{{background_json}}", json.dumps(dfs_paths.read_background()))
            .replace("{{palettes_json}}", json.dumps(dfs_paths.background_palettes()))
            .replace("{{background_image_json}}", json.dumps(dfs_paths.background_image_versions()))
            .replace("{{project}}", html.escape(name))).encode()


def needs_you(it):
    return bool(it["status"] == "blocked" or it.get("open_raises")
                or it.get("standing") or it.get("corrections"))


def walk_marks(data):
    """Which task a work chain is running on now (item -> its progress), and which the walk
    takes next. What the rows say beside the terminal's running and next columns."""
    if WALK["enabled"]:
        snap = walker().snapshot()
        return {c["item"]: c["progress"] for c in snap["live"]}, snap["next"]
    return ({r["item"]: dfs_runs.progress(r) for r in dfs_runs.live_runs("work")},
            data.get("next_item"))


def state_json():
    """The task list: the rows `state.py --json` gives the terminal, plus `rev`."""
    data = dfs_state.full()
    running, nxt = walk_marks(data)
    items = []
    for it in data["items"]:
        items.append(dict(
            id=it["id"], goal=it["goal"], depth=it.get("depth", 0), status=it["status"],
            why=humanize(it.get("why", "")), rev=dfs_tui.review_cell(it), needs_you=needs_you(it),
            raises=len(it.get("open_raises") or ()), standing=bool(it.get("standing")),
            assumptions=list(it.get("assumptions") or ()),
            corrections=list(it.get("corrections") or ()),
            waiting_on=it.get("waiting_on") or "", blocked_by=it.get("blocked_by") or "",
            segment=it.get("segment", 0), ahead=it.get("ahead", 0), base=it.get("base") or "origin/main",
            uncommitted=it.get("uncommitted") or "",
            running=running.get(it["id"], ""), next=it["id"] == nxt))
    return dict(items=items, next_item=data.get("next_item"),
                needs_you=sum(1 for i in items if i["needs_you"]),
                colour=dfs_paths.project_colour(), colour_chosen=bool(dfs_paths.read_theme()),
                background=dfs_paths.read_background(), palettes=dfs_paths.background_palettes(),
                background_image=dfs_paths.background_image_versions())


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
# One thing a page may do: `connect-src 'self'` lets it reach `/artefact-state/<itself>`, which
# keeps what the reader left on it and, from the page the raise names, records the answer. Every
# `/api` write still needs JSON from this server's own origin, and a sandboxed page has neither
# (JSON asks a preflight this server never grants; its origin is `null`).
ART_CSP = ("sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; "
           "style-src 'unsafe-inline'; img-src data: 'self'; font-src data:; connect-src 'self'")


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
                depth=row["depth"], indent=row["indent"], kids=row["kids"], chain=row["chain"],
                parent=nd["parent"] or "",
                progress=row["progress"],
                skim=skim_for(t, nd),
                editable=st in ("open", "parked") and nd["fields"].get("Approach") != dfs_tree.SHELF_STUB,
                approach=nd["fields"].get("Approach", ""),
                raises=[raise_json(r) for r in row["raises"]],
                raises_below=row["raises_below"],
                assumes=row["assumes"], ask=row["asks"],
                parks=dfs_tree.actioned_siblings(t, nd["id"]),
                page=next(iter(dfs_tree.ARTEFACT_REF.findall(f.get("Hypothesis") or "")), ""),
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
                name=t["name"], goal_text=t["goal"], runs=runs_for(task),
                artefacts=artefacts_for("\n".join(strings(t))),
                status=status, why=humanize(why), accepted=accepted,
                acceptable=status == "done" and not accepted, rows=rows, assumed=assumed,
                notes=[dict(kind=k, verdict=v, body=b) for k, _ts, v, b in dfs_tree.notes(t)],
                sessions=dfs_tree.sessions_since_author(t))


RUN_LISTED = 12             # the newest chains a task page lists
RUN_CHUNK = 128 * 1024     # the most of a run's log one answer carries


def run_entry(r):
    """What the page needs of one chain: no path, only the directory's name, which `run_log`
    finds again among the run directories rather than joining onto a root."""
    return dict(name=os.path.basename(r["dir"]), item=r["item"], state=r["state"],
                progress=dfs_runs.progress(r), mode=r["mode"], agent=r["agent"],
                started=r["started"], finished=r["finished"], stop=r["stop"], rc=r["rc"],
                peak=r["peak"], turns=r["turns"], has_log=bool(r["console"]))


def runs_for(task):
    """The newest chains that worked this task, newest first."""
    return [run_entry(r) for r in dfs_runs.run_dirs() if r["item"] == task][:RUN_LISTED]


def run_log(name, start=None):
    """A chain's console log. With no `start` the last `RUN_CHUNK` bytes, from a line start;
    with one, what has been written since that byte, so a live log is followed by asking
    again from the `end` the last answer gave. `name` is matched against the run directories
    that exist, never joined onto a path."""
    run = next((r for r in dfs_runs.run_dirs() if os.path.basename(r["dir"]) == name), None)
    if run is None:
        raise Refused("no such run", 404)
    if not run["console"]:
        return dict(run_entry(run), text="", start=0, end=0)
    try:
        size = os.stat(run["console"]).st_size
        first = start is None or start > size   # first look, or the file is not the one we saw
        if first:
            start = max(0, size - RUN_CHUNK)
        with open(run["console"], "rb") as fh:
            fh.seek(start)
            data = fh.read(RUN_CHUNK)
    except OSError as e:
        raise Refused("cannot read that log: %s" % e, 500)
    if first and start and b"\n" in data:
        cut = data.index(b"\n") + 1            # a cut tail begins at a line, not mid-line
        start, data = start + cut, data[cut:]
    if run["live"] and not data.endswith(b"\n") and b"\n" in data:
        data = data[:data.rindex(b"\n") + 1]   # a half-written line waits for the next ask
    return dict(run_entry(run), text=data.decode("utf-8", "replace"), start=start, end=start + len(data))


def assumptions_json():
    """Every open assumption (an old Ask counts) in every task not yet accepted. The page no longer shows this
    list (each task has its own Flagged view); the endpoint stays for API clients."""
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


def set_background(body):
    """Choose the page background for light mode (`light`) and for dark mode (`dark`), each
    `#rrggbb`, or clear one with an empty string so that mode keeps the design's paper; a
    mode left out is unchanged. The project's colour, which marks the top, is not touched."""
    given = {m: body[m] for m in dfs_paths.MODES if m in body}
    if not given or not all(isinstance(v, str) for v in given.values()):
        raise Refused("a light or a dark background is needed")
    try:
        have = dfs_paths.write_background(**given)
    except ValueError as e:
        raise Refused(str(e))
    except OSError as e:
        raise Refused("could not write %s: %s" % (dfs_paths.rel(dfs_paths.background_file()), e), 500)
    HUB.notify()
    return dict(background=have, palettes=dfs_paths.background_palettes())


def set_background_image(body):
    """Set the picture behind the page (`image`, a png, jpeg, webp or gif in base64) or clear it
    with an empty string, for the `mode` "light" or "dark", or for both when there is none. The
    background colours are not touched."""
    value, mode = body.get("image"), body.get("mode", "")
    if not isinstance(value, str):
        raise Refused("an image is needed")
    if not isinstance(mode, str):
        raise Refused("a mode is light or dark")
    try:
        data = base64.b64decode(value, validate=True) if value else b""
        dfs_paths.write_background_image(data, mode)
    except ValueError as e:
        raise Refused(str(e) if "background image" in str(e) or "a mode" in str(e) else "that is not base64")
    except OSError as e:
        raise Refused("could not write %s: %s" % (dfs_paths.rel(dfs_paths.background_image_file()), e), 500)
    HUB.notify()
    return dict(background_image=dfs_paths.background_image_versions())


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


MOVES = {"up", "down", "out", "in", "break", "place"}

# Why a move that cannot be made cannot: the terminal's own words (tui.py reorder,
# reparent, toggle_break), since it is the same rule refusing.
MOVE_REFUSAL = {
    "up": "is already first among its siblings", "down": "is already last among its siblings",
    "out": "is already at the root",
    "in": "has no sibling above it in this section to go under",
    "place": "cannot go there: a drag stays among its own siblings",
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
            if move == "place":
                target, where = need(body, "target", "the task to go beside"), body.get("where")
                if where not in ("before", "after"):
                    raise Refused("where is before or after")
                if dfs_order.index_of(nodes, target) is None:
                    raise Refused("no task %s" % target, 404)
            after = {"up": lambda: dfs_order.moved(nodes, item, -1),
                     "down": lambda: dfs_order.moved(nodes, item, 1),
                     "out": lambda: dfs_order.outdented(nodes, item),
                     "in": lambda: dfs_order.indented(nodes, item),
                     "place": lambda: dfs_order.placed(nodes, item, target, where == "after")}[move]()
            refusal = MOVE_REFUSAL[move]
        if after is None:
            raise Refused("%s %s" % (item, refusal), 409)
        try:
            dfs_order.write_order(after)
        except OSError as e:
            raise Refused("could not write %s: %s" % (dfs_paths.rel(dfs_paths.order()), e), 500)
    HUB.notify()
    return state_json()


def answering_raise(page):
    """The open raise that names `page` as its answer (`Answer with:`), as (task, entry), else None."""
    for task in dfs_tree.task_ids():
        for r in dfs_tree.open_raises(dfs_tree.load(task)):
            if dfs_tree.answer_page(r["body"]) == page:
                return task, r
    return None


def record_correction(task, nid, verdict, why, park=False):
    """The author's ruling on a node, as the web form and an assumption's page both give it.
    Confirming prunes the later siblings; those that hold work are pruned only once the author
    has agreed to park them (`park`)."""
    t = dfs_tree.load(task)
    if nid not in dfs_tree.by_id(t) or verdict not in ("confirmed", "refuted"):
        raise Refused("a node, and confirmed or refuted, are needed")
    if not isinstance(why, str) or not why.strip():
        raise Refused("a directive is needed")
    parks = dfs_tree.actioned_siblings(t, nid, verdict)
    if parks and park is not True:
        raise Refused("confirming %s prunes %s, which hold work: agree to park them first"
                      % (nid, ", ".join(parks)), 409)
    dfs_tree.append_log(task, "correct", [nid, verdict], why.strip())


def artefact_state(page, body=None):
    """What `/artefact-state/<page>` says and does. Without a body it tells the page what the reader
    left on it (`state`), the raise it answers (`raise`, while that is open) and the node whose
    Hypothesis names it while that waits on the author (`node`). A body `{state, answer}` keeps the
    state, and records `answer` as the author's answer to the raise the page is named for; with
    `verdict` ("confirmed" or "refuted") the answer is instead the author's ruling on that node."""
    root = dfs_paths.artefacts()
    if not re.fullmatch(ART_NAME, page) or not page.lower().endswith(".html") or not (root / page).is_file():
        raise Refused("no such page", 404)
    f = dfs_paths.artefact_state() / (page + ".json")
    answered = None
    if body is not None:
        with LOCK:
            found = ruling = None
            if "verdict" in body:
                ruling = dfs_tree.deciding_node(page)
                if not ruling:
                    raise Refused("no node waiting on the author names this page in its Hypothesis", 409)
                if body["verdict"] not in ("confirmed", "refuted"):
                    raise Refused("the verdict is confirmed or refuted")
                if not isinstance(body.get("answer"), str) or not body["answer"].strip():
                    raise Refused("a directive is needed")
            elif "answer" in body:
                text = body["answer"]
                if not isinstance(text, str) or not text.strip():
                    raise Refused("an answer is needed")
                found = answering_raise(page)
                if not found:
                    raise Refused("no open raise names this page to answer with", 409)
            if "state" in body:
                f.parent.mkdir(parents=True, exist_ok=True)
                tmp = f.with_suffix(".tmp")
                tmp.write_text(json.dumps(body["state"]))
                tmp.replace(f)
            if found:
                task, r = found
                dfs_tree.append_log(task, "answer", [r["ts"]], text.strip())
                answered = dict(task=task, ts=r["ts"])
            if ruling:
                record_correction(*ruling, body["verdict"], body["answer"], body.get("park"))
                answered = dict(task=ruling[0], node=ruling[1], verdict=body["verdict"])
        if answered:
            HUB.notify()
    found = answering_raise(page)
    try:
        saved = json.loads(f.read_text())
    except (OSError, ValueError):
        saved = None
    node = dfs_tree.deciding_node(page)
    return {"state": saved, "answered": answered,
            "raise": dict(task=found[0], ts=found[1]["ts"]) if found else None,
            "node": dict(task=node[0], id=node[1],
                         parks=dfs_tree.actioned_siblings(dfs_tree.load(node[0]), node[1])) if node else None}


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
            record_correction(task, nid, body.get("verdict"), need(body, "body", "a directive"),
                              body.get("park"))
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
        elif name == "delete_task":
            try:
                dfs_tree.delete_task(task)
            except (ValueError, dfs_paths.NoBranch) as e:
                raise Refused(str(e), 409)
            HUB.notify()
            return dict(deleted=task)
        elif name == "delete_node":
            nid = need(body, "node", "the node")
            if nid not in nodes:
                raise Refused("no node %s" % nid, 404)
            try:
                gone = dfs_tree.delete_node(task, nid)
            except (ValueError, dfs_paths.NoBranch) as e:
                raise Refused(str(e), 409)
            HUB.notify()
            return dict(deleted=gone, task=task_json(task))
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
        if name == "chat/interrupt":
            return chats().interrupt(need(body, "scope", "a scope"))
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
    if WALK["enabled"]:
        return dict(walker().snapshot(), enabled=True)
    # Off, the page still shows which task a chain is on and which is next, from the files.
    running, nxt = walk_marks(dfs_state.full())
    return dict(enabled=False, next=nxt, live=[dict(item=i, progress=p) for i, p in running.items()])


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
        if not any(k == "Cache-Control" for k, _ in extra):
            self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def json(self, status, obj, extra=()):
        self.send(status, json.dumps(obj, default=str), extra=extra)

    def artefact_api(self, method, page):
        """`/artefact-state/<page>`: the one thing a sandboxed page may reach. It answers anyone who
        asks (`Access-Control-Allow-Origin: *`, since the page's origin is opaque) and reads a plain
        text body, which needs no preflight; a write from another website is still refused."""
        cors = [("Access-Control-Allow-Origin", "*")]
        try:
            body = None
            if method != "GET":
                if self.headers.get("Origin") not in (None, "", "null") and not self.same_origin():
                    raise Refused("that request came from another site", 403)
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    raise Refused("Content-Length is not a number")
                if n > MAX_BODY:
                    raise Refused("too large", 413)
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                except ValueError:
                    raise Refused("that is not JSON")
                if not isinstance(body, dict):
                    raise Refused("that is not JSON")
            return self.json(200, artefact_state(page, body), cors)
        except (ValueError, dfs_paths.NoBranch) as e:
            return self.json(409, dict(error=str(e)), cors)
        except Refused as e:
            return self.json(e.status, dict(error=str(e)), cors)

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
            if url.path.startswith("/artefact-state/"):
                return self.artefact_api(method, url.path[len("/artefact-state/"):])
            if method == "GET":
                return self.get(url.path)
            if not self.same_origin():
                raise Refused("that request came from another site", 403)
            if "application/json" not in self.headers.get("Content-Type", ""):
                raise Refused("JSON only", 415)
            n = int(self.headers.get("Content-Length") or 0)
            if n > (IMAGE_BODY if url.path == "/api/background-image" else MAX_BODY):
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
            if name == "background":
                return self.json(200, set_background(body))
            if name == "background-image":
                return self.json(200, set_background_image(body))
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
        if path.startswith("/api/run/"):
            from_ = parse_qs(urlparse(self.path).query).get("from", [""])[0]
            return self.json(200, run_log(path[len("/api/run/"):], int(from_) if from_.isdigit() else None))
        if path == "/background-image":
            mode = parse_qs(urlparse(self.path).query).get("mode", [""])[0]
            image = dfs_paths.read_background_image(mode if mode in dfs_paths.MODES else "")
            if image is None:
                raise Refused("no background image", 404)
            return self.send(200, image[0], image[1], extra=[("Cache-Control", "no-cache")])
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
