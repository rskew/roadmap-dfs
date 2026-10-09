#!/usr/bin/env python3
"""A screen for driving the roadmap: what is open, and the one key that acts on it.

The roadmap is already the state and `run.sh` is already the
actions. What was missing is the LOOK — "which items are blocked, what is the raise,
how many raises per pick" is four greps and a scroll through a 500-line file, done
before every decision about where to spend a session. So this holds no state of its
own and decides nothing about the WORK: it reads `state.py` (the same derivation the
runner stops on, so the screen and the loop cannot disagree about what "blocked"
means) and shells out to the runner for everything that writes. The one file it writes
itself is `.dfs/order.md`, which holds no decision and no work state: it is
the order the author means to work the items in, and `[`/`]` move the selected item in
it. See `order.py` for why that is a file rather than §2.

⚠️ It never appends to the roadmap itself. `ANSWER`, `REVIEW` and `OPEN` are
transcription-only (§0.10) and the runner's `--review`/`--open` passes are what may
write them, in front of the author. A TUI that composed entries would be a second writer of the one file
that is supposed to have a single audited one.

Four panes over the same selection, all scrollable:
  item      the selected item's raises, proposals and unchecked `## Next`
  3  log    the task's Log — sessions, raises, answers, critic verdicts, corrections
  l  agent  the runs on disk — a live chain's console AND the turns the agent is
            taking inside it, then any finished run's captured result
  v  art    the artefacts this item's raises name, opened in a real browser
  h  act    the activity log: what this screen and its walk did (from 150 columns,
            a column beside the tree, under the live chain's console tail)
and `?`, every key (`KEYS`), where Enter presses the one under the cursor; `:`, any of
them by name; `/`, search as you type.

⚠️ A WORK CHAIN RUNS IN THE BACKGROUND, NOT INSTEAD OF THIS SCREEN. It is minutes to
hours of non-interactive work whose output already lands in a file, so handing it the
terminal bought nothing and cost the roadmap being visible for the whole of it — and
with the screen gone there was nowhere for "which items are working" to be shown, so
the question could not even be asked. The chain is detached, the `l` pane tails both what
the SCRIPT says and what the AGENT is doing, and the item list marks who is working. The passes that take the
author's WORDS — `--review`, `--open`, `--chat` — still hand the terminal over,
because they read `/dev/tty` and §0.10 says a script rather than an agent is what
appends them; a curses form standing between the author and that script is the one
thing that guarantee does not survive.

Several sessions share this tree and append to the roadmap while you are looking at
it, so the screen WATCHES the files rather than waiting to be told: there is no
refresh key because there is nothing to refresh by hand.

Run:  python3 <scripts>/tui.py
"""
import calendar
import collections
import colorsys
import curses
import glob
import importlib.util
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import queue
import tempfile
import sys
import threading
import textwrap
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import runs as dfs_runs               # noqa: E402  (needs HERE on the path first)
import order as dfs_order              # noqa: E402  (the same)
import paths as dfs_paths              # noqa: E402  (the same)
import limit as dfs_limit              # noqa: E402  (the same)
from walk import (AGENTS, WALK_CONTINUE, WALK_HOLD, WALK_RAISE, WALK_START_GRACE,   # noqa: E402,F401
                  WALK_STOP, WalkMixin, agent_file, read_agent, walk_decide, walk_held_for)
import tree as dfs_tree               # noqa: E402  (the same)

# ⚠️ TWO ROOTS. `ROOT` is the repo being worked on — the CWD, and where everything is
# launched — and `STATE` is that repo's own `.dfs`. `paths.py` is the one
# place either is derived; read it before spelling any path here.
ROOT = dfs_paths.work_root()
STATE = dfs_paths.state()
EPIC = dfs_paths.EPIC
RUNNER = dfs_paths.rel(dfs_paths.SCRIPTS / "run.sh")
# What `x` cycles through; each but the first is a run.sh flag of its own name.
ART_DIR = dfs_paths.artefacts()
ART_PORT = int(os.environ.get("DFS_ARTEFACT_PORT") or 3016)  # docs/artefacts.md

# ── the review tree ───────────────────────────────────────────────────────────
#
# `--review` printed the whole tree as one line per node WITH its determination, then
# asked for a node id to be typed. On a big tree (W13: 17 nodes, most determinations a
# paragraph) that is a wall to scroll back through, and correcting a node meant
# copying its id out of it. The pane lays the tree out one node per line, opens the
# one under the cursor, and does the review's three writes where the node is.
# The rows and a node's detail are functions of the tree, out here where they can be
# tested without a terminal.

TREE_MARK = {"confirmed": "✓", "refuted": "✗", "open": "○", "parked": "‖",
             "dormant": "‖", "pruned": "·"}
# Marks a blank line in the tree whose indent guides are settled once both its
# neighbours are known; compared by identity, never mutated.
BLANK_GUIDES = []
# The farthest a row is drawn in, in half steps: a chain past it reads level, but
# keeps its `↳` mark.
TREE_INDENT_CAP = 16
TREE_QUIET = ("refuted", "parked", "dormant", "pruned")
# The task file's own prose, above the tree: what the nodes are FOR. Goal starts
# open because a review judges nodes against it; Background starts folded because
# it can run to pages (W8's is 5,500 characters) and is reference, not the question.
TREE_SECTIONS = (("Goal", True), ("Background", False))
SUMMARY_MISSING = ("(no summary written — the session that finished the tree should have "
                   "added one; [c]hat about this item to ask for it)")


def prose_blocks(text):
    """A section's markdown as display lines: hard-wrapped paragraphs joined back
    into one line each, so the pane can re-wrap them to its own width, with list
    items, headings and blank lines kept where they were."""
    out = []
    for line in (text or "").strip("\n").splitlines():
        line = line.rstrip()
        starts = re.match(r"^\s*([-*+]\s|\d+[.)]\s|#|>|\|)", line)
        if not line or not out or not out[-1] or starts:
            out.append(line)
        else:
            out[-1] = out[-1] + " " + line.strip()
    return out


def tree_entries(t, folded=(), flagged=False):
    """The pane's rows, in order: the task's own raises, then the nodes depth-first.

    A node in `folded` keeps its row and hides its descendants. A raise on a node
    rides on the node's row, since that is where the author reads the node; a raise
    naming no node, or a node the tree does not have, is its own row at the top, so
    none is ever unreachable. Above them all, the task's Goal and Background, as
    rows that fold like any other.

    ⚠️ A fold must not hide a raise. The first tree this pane was tried on had its
    one open raise on W13.26, four levels under W13.8, and folding W13.8 took the
    only thing the author was there to answer off the screen. So a folded row
    carries `raises_below`, the count of open raises it is hiding.

    `flagged` keeps only the nodes that want the author (an assumption, an old Ask, an
    open raise on the node) and drops the rest, ancestors included. Folds are off
    then (a fold on a dropped row would hide a flagged node below it) and a kept
    node's `kids` is 0, so no row shows a fold mark. Sections and the task's own
    raises stay."""
    if flagged:
        folded = ()
    status, pruned = dfs_tree.effective(t)
    asleep = dfs_tree.dormant(t)
    nodes = dfs_tree.by_id(t)
    wants = set(dfs_tree.assumed(t))        # not refuted, pruned, or ruled on by the author
    on_node, out = {}, []
    sections = dict(t["sections"])
    order = TREE_SECTIONS
    if dfs_tree.status_of(t)[0] == "done":
        # ⚠️ ONLY WHILE IT IS DONE: a summary of finished work, above the Goal it
        # answers. Reopened, the task is not what the summary says, and a stale
        # "achieved" over open work is worse than nothing. Done without one, the row
        # says how to get one rather than leaving the author to wonder where it is.
        sections["Summary"] = (sections.get("Summary") or "").strip() or SUMMARY_MISSING
        order = (("Summary", True),) + order
    for name, shown in order:
        if (sections.get(name) or "").strip():
            out.append(dict(key="section:%s:%s" % (t["task"], name), kind="section",
                            depth=0, name=name, text=sections[name],
                            open_default=shown))
    held = [] if dfs_tree.accepted(t) else dfs_tree.notes(t)
    if held:
        # ⚠️ WHAT THE REVIEW SAID ON ITS WAY TO `ok`. A reviewer's minor findings and a
        # critic's remarks live in the log body and the skill tells the author to read
        # them when they accept, but nothing on the screen did. Listed folded: they can
        # run long, and the accept prompt counts them for the author who wants to read.
        out.append(dict(key="section:%s:Notes" % t["task"], kind="section", depth=0,
                        name="Reviewer and critic notes", open_default=False,
                        text="\n".join("%s %s:\n%s\n" % (k, v, body)
                                       for k, _ts, v, body in held)))
    for r in dfs_tree.open_raises(t):
        nid = r["args"][0] if r["args"] else ""
        if nid in nodes:
            on_node.setdefault(nid, []).append(r)
        else:
            out.append(dict(key="raise:" + r["ts"], kind="raise", depth=0, raise_=r))

    def kids_of(parent):
        return sorted((n for n in t["nodes"]
                       if (n["parent"] if n["parent"] in nodes else None) == parent),
                      key=lambda n: n["n"])

    def walk(parent, d, ind):
        sibs = kids_of(parent)
        for nd in sibs:
            nid = nd["id"]
            st = ("pruned" if nid in pruned else
                  status[nid] if status[nid] != "open" or nid not in asleep
                  else "dormant")
            kids = kids_of(nid)
            shut = nid in folded and bool(kids)
            below = (sum(len(on_node.get(k["id"], ()))
                         for k in dfs_tree.descendants(t, nid)) if shut else 0)
            # ⚠️ `chain`: drawn at its parent's column, which reads as the parent's
            # sibling, so "add under" looked like it had added beside. The row says it.
            out.append(dict(key=nid, kind="node", depth=d, indent=min(ind, TREE_INDENT_CAP),
                            node=nd, status=st,
                            chain=parent is not None and len(sibs) == 1,
                            raises=on_node.get(nid, []), kids=len(kids),
                            folded=shut, raises_below=below,
                            assumes=nid in wants and bool(nd["fields"].get("Hypothesis")),
                            asks=nid in wants and bool(nd["fields"].get("Ask"))))
            if flagged:
                if on_node.get(nid) or out[-1]["assumes"] or out[-1]["asks"]:
                    out[-1]["kids"] = 0
                else:
                    out.pop()
            if nid not in folded:
                # ⚠️ INDENT ONLY AT A FORK. `--open` makes every todo the child of the
                # one before, so a twenty-step task was a staircase forty columns
                # deep and a real alternative (two siblings) looked like one more
                # step. A node that is the only child of its parent continues at its
                # parent's column; its siblings, when there are any, step in.
                #
                # ⚠️ BUT NEVER LEVEL WITH ITS PARENT. A chain step at its parent's
                # column read as a sibling of the parent's siblings, so `indent` (in
                # half steps) puts a fork's child two past its parent and an only
                # child one past, capped so a long chain stays narrow. `depth` is
                # the fork count and keeps its meaning; a surface draws from `indent`.
                walk(nid, d + (1 if len(kids) > 1 else 0), ind + (2 if len(kids) > 1 else 1))
    walk(None, 0, 0)
    return out


def archived_fields(text, nid):
    """A node's Approach and Hypothesis as `.dfs/archive/items/<task>.md` holds them.

    A session over the task file's budget moves a determined node's Approach
    and Hypothesis there (`tree.py shelve`) and leaves `archived.`; the review should
    read the words the work was judged against, not the stub. `dfs_tree.load` already
    puts them back, so this is for a node parsed from the file alone."""
    return dfs_tree.archived_fields(text).get(nid, {})


def node_detail(nd, archived=None, commits=()):
    """A node opened: `(label, text)` pairs in the order a reviewer reads them."""
    f, archived = nd["fields"], archived or {}
    out = []
    for k in ("Approach", "Hypothesis"):
        if k in nd.get("from_archive", ()):
            out.append(("%s (archive)" % k, f[k]))
        elif k in archived and ("archive" in f.get(k, "") or k not in f):
            out.append(("%s (archive)" % k, archived[k]))
        elif k in f:
            out.append((k, f[k]))
    out += [("Evidence", ev) for ev in nd["evidence"]]
    if "Determination" in f:
        out.append(("Determination", f["Determination"]))
    skip = ("Status", "Parent", "Approach", "Hypothesis", "Determination", "Ask", "Corrects")
    out += [(k, v) for k, v in f.items() if k not in skip]
    # A commit HEAD has and the default branch on origin lacks says so on its line.
    off = (dfs_tree.unpushed() or (set(), {}))[0]
    out += [("Commit", "%s %s%s" % (sha[:7], subj, " · not on %s" % dfs_tree.default_ref() if sha in off else ""))
            for sha, subj, _at in commits]
    return out


def age(ts, now=None):
    """`2d ago` for an ISO timestamp: a raise's identity is its timestamp, and the
    screen showed it as one, where how long it has waited is what the author reads."""
    try:
        secs = (now or time.time()) - calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, TypeError):
        return ts
    for size, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if secs >= size:
            return "%d%s ago" % (secs // size, unit)
    return "just now"


def clip(text, width):
    """`text` in `width` columns, with an ellipsis INSIDE them when it is cut: the
    one that said there was more used to be written past the edge, and clipped off."""
    return text if len(text) <= width else text[:max(0, width - 1)].rstrip() + "…"


def first_sentence(text):
    """The first sentence of a Determination, without the verdict word the row's mark
    already says: what the author skims under each node."""
    text = re.sub(r"^\s*(confirmed|refuted)\b[:.,—-]*\s*", "", text or "", flags=re.I)
    m = re.search(r"(?<=[.!?])\s", text)
    return (text[:m.start()] if m else text).strip()


def node_card(t, row, archive="", commits=()):
    """One node as the author reads it: `(role, text)` blocks, in the order a review
    goes. The panel at the right and a node opened in place both draw this, so they
    cannot disagree about what a node says.

    Roles: head, meta, label, text, amber, plus, minus, red, dim. The author's own
    words are kept WITH the node: an answered raise, a correction and its reason, and
    a determination the author has since overruled."""
    nd, st = row["node"], row["status"]
    f = nd["fields"]
    story = dfs_tree.story(t, nd["id"])
    out = [("head", "%s · %s" % (nd["id"], nd["title"]))]
    meta = "%s %s" % (TREE_MARK.get(st, "?"), st)
    if nd["parent"]:
        meta += " · under %s" % nd["parent"]
    out.append(("meta", meta))
    if story["overruled"]:
        out.append(("red", "you overruled this: the agent said %s, you said %s"
                    % (nd["status"], story["overruled"]["disp"])))
    for r in row.get("raises", ()):
        out += [("label", "Raise · %s — needs you" % age(r["ts"])), ("red", r["body"].strip())]
    if f.get("Ask"):
        out += [("label", "Ask — a question for you"), ("red", f["Ask"])]
    for label, text in node_detail(nd, archived_fields(archive, nd["id"]), commits):
        base = label.split(" ")[0]
        if base == "Hypothesis":
            assumption, wrong = dfs_tree.split_falsifier(text)
            out += [("label", "⚑ Hypothesis"), ("amber", assumption)]
            if wrong:
                out += [("label", "Wrong if"), ("amber", wrong)]
        elif base == "Evidence":
            if not any(r == "label" and x == "Evidence" for r, x in out):
                out.append(("label", "Evidence"))
            kind, _, rest = text.partition(":")
            if kind.strip().lower() == "for":
                out.append(("plus", "+ " + rest.strip()))
            elif kind.strip().lower() == "against":
                out.append(("minus", "− " + rest.strip()))
            else:
                out.append(("text", text))
        elif base == "Determination":
            out.append(("label", "Determination"))
            out.append(("dim" if story["overruled"] else "text", text))
        elif base == "Commit":
            out.append(("dim", "commit " + text))
        else:
            out += [("label", label), ("text", text)]
    for c in story["corrections"]:
        out += [("label", "You corrected this to %s" % c["disp"]), ("text", c["body"].strip())]
    c = story["carries"]
    if c:
        out += [("label", "Carries out %s of %s" % (
            "your correction" if c["disp"] != "backtrack" else "the critic's backtrack", c["node"])),
                ("text", c["body"].strip())]
    for raised, answer in story["answered"]:
        out += [("label", "You answered a raise on this node"),
                ("dim", (raised["body"].strip().splitlines() or [""])[0]),
                ("text", answer["body"].strip())]
    return out


def assumed_nodes(t):
    """The live nodes the author should read before accepting: those that state a
    Hypothesis (an assumption) or, in an old tree, an Ask (a question). See `dfs_tree.assumed`."""
    return dfs_tree.assumed(t)


def strip_comments(text):
    """What the author wrote in an editor, without the `#` lines that framed it."""
    return "\n".join(l for l in text.splitlines() if not l.startswith("#")).strip()


POLL_MS = 1000


# ── reading ────────────────────────────────────────────────────────────────────

def load():
    out = subprocess.run([sys.executable, str(HERE / "state.py"), "--json"],
                         cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit("state.py failed:\n" + out.stderr)
    return json.loads(out.stdout)


def roadmap_dirty():
    """Uncommitted changes under `.dfs` — a warning, not a state.

    The epic records the loss this prevents: a blocked commit leaves your paths
    STAGED, and the next commit from any other session in the same tree takes
    them. Both roadmap files once landed inside a commit about worker
    throttling. Several sessions share this tree, so uncommitted roadmap work is
    worth seeing before starting another one.
    """
    out = subprocess.run(["git", "status", "--porcelain", "--", "."],
                         cwd=STATE, capture_output=True, text=True)
    lines = [l for l in out.stdout.splitlines() if l.strip()]
    staged = sum(1 for l in lines if l[:1] not in (" ", "?"))
    return len(lines), staged


def epic_todo():
    """The epic's own unchecked todos — the outer program this roadmap serves."""
    if not EPIC.exists():
        return []
    todo = []
    for m in re.finditer(r"^- \[( |x)\] (.+(?:\n      .+)*)", EPIC.read_text(), re.M):
        todo.append((m.group(1) == "x", re.sub(r"\s+", " ", m.group(2)).strip()))
    return todo


def watch_signature():
    """What the screen is watching, as one comparable value.

    Mtimes rather than a filesystem-watch API: three globs cost microseconds at a
    one-second poll, and a watcher would need a thread whose failure mode is a
    screen that has quietly stopped updating — the same silent-staleness shape the
    app's own live path is written against.
    """
    paths = [dfs_paths.roadmap(), dfs_paths.order()]
    paths += sorted(dfs_paths.items().glob("*.md")) + sorted(dfs_paths.items().glob("*/*.md"))
    sig = []
    for p in paths:
        try:
            sig.append((str(p), p.stat().st_mtime_ns))
        except OSError:
            sig.append((str(p), 0))
    logs, consoles = [], []
    for root in dfs_runs.run_roots():
        logs += glob.glob(os.path.join(root, "dfs_run_*", "*.json"))
        consoles += glob.glob(os.path.join(root, "dfs_run_*", "console.log"))
    for f in sorted(logs):
        try:
            sig.append((f, os.stat(f).st_mtime_ns))
        except OSError:
            pass
    # A live chain's console is APPENDED to, and on a filesystem with coarse mtime a
    # second of output can land inside one tick — so the size is in the signature
    # too, or the tail stops moving while the run is still talking.
    for f in sorted(consoles):
        try:
            st = os.stat(f)
            sig.append((f, st.st_mtime_ns, st.st_size))
        except OSError:
            pass
    return tuple(sig)


def art_title(path):
    """The human name, which the filename cannot carry because it is a uuid."""
    try:
        head = path.read_text(errors="replace")[:8192]
    except OSError:
        return ""
    m = re.search(r"<title>(.*?)</title>", head, re.S | re.I)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def art_server_up():
    """Is something answering on 3016? The difference between a link and a dead one.

    Asked only while the artefact pane is drawn, and with a short timeout: the screen
    redraws on a one-second poll and must not spend it waiting on a socket.
    """
    try:
        with socket.create_connection(("127.0.0.1", ART_PORT), 0.15):
            return True
    except OSError:
        return False


def artefacts_named_by(entries, item):
    """Artefact filenames named in §3 entries about this item, in entry order.

    ⚠️ Found by SCANNING the prose, which is the point: README.md puts the link inside
    §0.9's existing fields precisely so this experiment can be abandoned without
    withdrawing a law. There is no artefact field to read and there must not be one.
    A name that no longer resolves is still listed — a dead link inside an entry that
    can never be edited is worth seeing, not hiding.
    """
    seen, out = set(), []
    for e in entries:
        if e["target"] != item:
            continue
        for name in re.findall(r"([0-9A-Za-z][0-9A-Za-z._-]*\.html)", e["body"]):
            if name not in seen:
                seen.add(name)
                out.append((name, e["id"]))
    return out


def discover_runs():
    """Every run log on disk: the chains, then the sessions inside them.

    Two kinds, because a backgrounded chain is readable long before it has captured
    anything. A `console` is the chain's own output as it is being written — the only
    thing there is to look at for the first several minutes, and the only place a
    launch failure is ever said. A `result` is one session's captured JSON, which is
    what carries the usage and the transcript id.

    ⚠️ A LIVE CHAIN SORTS FIRST regardless of mtime. Newest-first is right for
    history and wrong the moment something is running: a chain that has been going
    an hour is older than the run log of the one before it, and the thing you opened
    this pane for would be below the fold.
    """
    runs = []
    for r in dfs_runs.run_dirs():
        d = r["dir"]
        if r["console"]:
            try:
                st = os.stat(r["console"])
            except OSError:
                st = None
            if st:
                runs.append(dict(path=r["console"], dir=d, name="console.log",
                                 kind="console", mtime=st.st_mtime, size=st.st_size,
                                 item=r["item"], agent=r["agent"], mode=r["mode"],
                                 run=r))
        for f in sorted(glob.glob(os.path.join(d, "*.json"))):
            if os.path.basename(f) in ("meta.json", "meta.json.tmp"):
                continue
            try:
                st = os.stat(f)
            except OSError:
                continue
            if st.st_size == 0:
                continue
            runs.append(dict(path=f, dir=d, name=os.path.basename(f),
                             kind="result", mtime=st.st_mtime, size=st.st_size,
                             item=r["item"], agent=r["agent"], mode=r["mode"],
                             run=r))
    runs.sort(key=lambda e: (0 if e["run"]["live"] else 1, -e["mtime"]))
    return runs


def is_growing(run):
    """Is this log still being APPENDED to, i.e. is it worth sticking to its end?

    ⚠️ ASKED OF THE FILE, NOT OF THE CHAIN, and that is the whole of the fix. Follow
    was armed for a `console` alone, which is right under Claude — `-p --output-format
    json` writes ONE object at exit, so the run log is empty for the whole session and
    the console is the only thing moving. Under `--codex` it is the opposite: `exec
    --json` streams, so the run log is the live one (788 KB and climbing while its
    console held four lines of the runner's own output), and opening it got a pane
    pinned to the top of a file whose interesting end kept receding.

    Only the run the chain says it is IN, never a finished sibling: a chain on run 2
    still has run-1.json beside it, and following the end of a file nothing writes to
    is a footer saying `● following` about a pane that will never move again.
    """
    chain = run["run"]
    if not chain["live"]:
        return False
    if run["kind"] == "console":
        return True
    return run["name"] == "run-%s.json" % chain["run"]


def keep_in_view(scroll, top, end, body_h):
    """The scroll that shows the cursor's row, and as much of what is under it as fits.

    ⚠️ THE ROW'S HEADER IS NOT THE WHOLE OF IT. An expanded node is its header and then
    its detail, and the cursor was kept in view by the header alone, so opening the
    bottom node put everything it had to say below the fold, with nothing to scroll to
    it but the author's own d. `end` is the row's last line: it is brought up into
    view, but never by pushing `top` off the screen, so a block taller than the pane
    opens at its header and reads down from there.
    """
    if top < scroll:
        return top
    if end >= scroll + body_h:
        return min(top, end - body_h + 1)
    return scroll


def follows(growing, following, scroll, bottom):
    """Whether the log pane sticks to the end of its file on THIS draw.

    ⚠️ **A LATCH SET WHEN THE PANE OPENED IS THE BUG**, and `is_growing` beside it is
    only half the answer: it says which FILE is moving, and this says whether the
    READER is at the end of it. Following was decided once, on Enter, from whatever
    `is_growing` said in that instant, and then dropped by any scroll at all — so a
    pane stopped auto-scrolling for two ordinary reasons and could not be talked back
    into it except through `G`:

      · a run opened before its chain wrote `meta.json` has no item and no liveness
        yet (`dfs_runs`), so `is_growing` was false for a chain that then ran for an
        hour; and
      · reading DOWN to the end turned following OFF — `d`, space and PageDown all
        unfollowed — so the one gesture that means "show me the latest" was the one
        that stopped the latest arriving.

    So it is DERIVED every draw from three facts, the way a tail does it: the file is
    growing, and either we were already following or the reader is at the bottom.
    Arriving at the end by any means sticks; scrolling up unsticks; a chain that ends
    unsticks, so the footer cannot say `● following` about a pane that will never
    move again.
    """
    return bool(growing) and (bool(following) or scroll >= bottom)


def read_text_or_none(path):
    try:
        return Path(path).read_text()
    except OSError:
        return None


def reselect(keys, key, fallback):
    """Where a cursor lands after its list is re-derived: on the SAME THING.

    ⚠️ A CURSOR IS AN IDENTITY, NOT A ROW NUMBER. `reload` kept `sel` as an index and
    only clamped it, so when another session added an item or moved one in
    `order.md` — which the file watch reloads on, a second later — the cursor stayed
    on row 3 while a different item slid under it, and the next `w`, `n` or `r` acted
    on that one. The runs list is the same (a new live chain sorts first) and so is
    the tree (a session adds nodes). Only a thing that is GONE falls back to the row,
    clamped, since there is nothing left to follow.
    """
    if key is not None and key in keys:
        return keys.index(key)
    return max(0, min(fallback, len(keys) - 1))


def find_next(texts, needle, cur):
    """`/`: the first of `texts` after `cur` holding `needle`, wrapping; `(i, wrapped)`.

    Smart case, as a pager does it: all lower case matches either, and a capital
    means the author meant it — so `w13` finds W13, which is how ids get typed.
    """
    if not needle or not texts:
        return None, False
    fold = needle == needle.lower()
    want = needle.lower() if fold else needle
    n = len(texts)
    for step in range(1, n + 1):
        i = (cur + step) % n
        if want in (texts[i].lower() if fold else texts[i]):
            return i, i <= cur
    return None, False


def fuzzy(needle, hay):
    """A match score for `:` (lower is better), or None: the letters of `needle` in
    order in `hay` — fzf's rule — scored by where the first lands and how far apart
    the rest are, so a prefix beats a scatter and `rv` still finds `review`."""
    if not needle:
        return 0
    at, gaps, start = -1, 0, None
    for ch in needle:
        nxt = hay.find(ch, at + 1)
        if nxt < 0:
            return None
        if start is None:
            start = nxt
        else:
            gaps += nxt - at - 1
        at = nxt
    return start * 10 + gaps


# ⚠️ EVERY KEY THE SCREEN ANSWERS TO, for `?` (`lines_keys`) and `:` (`command`). The
# footer holds a dozen at most and drops them as the terminal narrows, and its own
# rule is that a key not on it does not exist — which, on a narrow terminal, was most
# of them. This is the list that does not narrow. `test_screen.py` checks it
# against `act`, so a key added there and not here fails a test instead of vanishing.
#
# (where it applies, key as shown, the code `act` is handed, NAME, what it does). The
# name is tig's idea: one word per action, shown beside its key in `?` and typed at
# `:` — so the list of keys and the command language are the same list. Where a key
# is used decides the pane `?`'s Enter and `:` put back before pressing it (`key_run`).
KEY_PLACES = [("items", "on the item list"), ("tree", "in the tree — tab"),
              ("runs", "in agent runs — L"), ("agentlog", "reading a run"),
              ("artefact", "in artefacts — v"), ("panes", "panes"),
              ("any", "anywhere")]
KEYS = [
    ("items", "⏎", 10, "work", "work it in the background, or review it — whichever it needs"),
    ("items", "tab", 9, "tree", "into the item's tree, and back out"),
    ("items", "w", ord("w"), "walk", "walk the roadmap depth-first, or stop the walk"),
    ("items", "n", ord("n"), "pin", "pin this item as where the walk goes next (again: unpin)"),
    ("items", "K", ord("K"), "stop", "stop this item's running chain (asks first)"),
    ("items", "r", ord("r"), "review", "review: open the tree to answer and correct"),
    ("items", "R", ord("R"), "review-terminal", "review in the terminal pass (run.sh --review)"),
    ("items", "c", ord("c"), "chat", "chat about this item (run.sh --chat)"),
    ("items", "C", ord("C"), "chat-project", "chat about the project as a whole (run.sh --chat project)"),
    ("items", "o", ord("o"), "open", "open a new task (run.sh --open)"),
    ("items", "e", ord("e"), "edit", "edit the item's file in $EDITOR"),
    ("items", "m", ord("m"), "needs-you", "show only what is waiting on you, or everything"),
    ("items", "[  -", ord("["), "up", "move up among its siblings"),
    ("items", "]  = +", ord("]"), "down", "move down among its siblings"),
    ("items", "{  <", ord("{"), "out", "move out of its branch"),
    ("items", "}  >", ord("}"), "in", "move into the branch above"),
    ("items", "b", ord("b"), "fence", "put a fence above it (asks first), or take the fence away"),
    ("items", "z", ord("z"), "undo", "undo the last move made from here"),
    ("items", "x", ord("x"), "agent", "switch the agent: claude, codex, kiro or opencode (remembered)"),
    ("tree", "⏎", 10, "node", "open or close the node under the cursor"),
    ("tree", "space", ord(" "), "fold", "fold or unfold what is below it"),
    ("tree", "p", ord("p"), "flagged", "show only the ⚑ assumptions and raises, or every node"),
    ("tree", "a", ord("a"), "answer", "answer the raise here"),
    ("tree", "n  N", ord("n"), "next-flag", "next, previous raise or ⚑ in the tree"),
    ("tree", "^d  ^u", 4, "card-scroll", "scroll the node panel at the right"),
    ("tree", "i", ord("i"), "add", "add a node under the one at the cursor (none: under the task)"),
    ("tree", "f", ord("f"), "correct", "correct this node: it was really confirmed or refuted"),
    ("tree", "A", ord("A"), "accept", "accept a finished tree (asks first)"),
    ("tree", "tab", 9, "list", "back to the item list"),
    ("runs", "⏎", 10, "read", "read this run"),
    ("agentlog", "G", ord("G"), "follow", "to the end, and follow it while it grows"),
    ("artefact", "⏎", 10, "browse", "open this artefact in a browser"),
    ("panes", "3", ord("3"), "log", "the task's log"),
    ("panes", "l", ord("l"), "lastlog", "the log of the selected item's most recent run"),
    ("panes", "L", ord("L"), "runs", "agent runs: the live chains and the finished ones, newest first"),
    ("panes", "v", ord("v"), "art", "artefacts: the pictures its raises name"),
    ("panes", "t", ord("t"), "todo", "the epic's todo"),
    ("panes", "H", ord("H"), "assumptions", "every open assumption, across the tasks"),
    ("panes", "h", ord("h"), "activity", "what this screen and its walk did"),
    ("any", "↑↓ j k", ord("j"), "move", "move the cursor, or the text where there is none"),
    ("any", "d  u", ord("d"), "half-page", "half a page down, up"),
    ("any", "space pgdn", curses.KEY_NPAGE, "page", "a page down (pgup: up)"),
    ("any", "g  G", ord("g"), "top", "top, bottom"),
    ("any", "/", ord("/"), "search", "search this pane as you type; ⏎ on an empty one finds the next"),
    ("any", ":", ord(":"), "command", "run any of these by name, with an item: `:work W7 5`"),
    ("any", "esc", 27, "back", "back one level (never quits)"),
    ("any", "?", ord("?"), "keys", "these keys; ⏎ on one presses it"),
    ("any", "q", ord("q"), "quit", "quit"),
]
# Second spellings `act` takes for a key above, for keyboards where the first is a
# chord: `[ ] { }` are AltGr on German and French layouts, and `- = + < >` are keys of
# their own there. Kept apart so the table above lists each action once.
KEY_ALIASES = {ord("-"): ord("["), ord("="): ord("]"), ord("+"): ord("]"),
               ord("<"): ord("{"), ord(">"): ord("}")}


def tail_lines(path, limit=600):
    """The end of a file that is still being written to.

    Read whole and cut: a chain's console is tens of kilobytes, a seek-and-scan would
    be the same work with an off-by-one in it, and this is called on a one-second
    poll only while the pane is open.
    """
    try:
        text = Path(path).read_text(errors="replace")
    except OSError as e:
        return ["cannot read %s: %s" % (path, e)]
    lines = text.splitlines()
    return lines[-limit:]


def read_result(path):
    """The runner's captured output for one run.

    Claude's `-p --output-format json` is one object; codex's `exec --json` is a
    stream of them. Both are read here because a chain run under `--codex` is the
    one whose logs you most need and least expect to be shaped differently.

    The two are returned as different things on purpose — `(result, [])` against
    `(None, events)` — because they ARE different things, and the pane condenses each
    with its own renderer (`codex_result_lines` for the second). A line that is not
    JSON is kept as `_raw` rather than dropped: on a run that died before it started,
    codex's own stderr is the whole of what happened.
    """
    raw = Path(path).read_text(errors="replace")
    try:
        return json.loads(raw), []
    except ValueError:
        pass
    events = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except ValueError:
            events.append({"_raw": line})
    return None, events


ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b[@-_]?")
CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def strip_ansi(text):
    """Text without its terminal colour codes: what the command's reader SAW."""
    return ANSI.sub("", text or "")


def printable(text):
    """Text curses draws one cell per character, so clipping by length is honest.

    ⚠️ A log carries the terminal output of whatever the agent ran, and Playwright
    colours its: `ESC[2mRunning ESC[22m1132ESC[2m tests`. curses does not interpret
    an ESC handed to `addstr`; it draws it as the two cells `^[`, so the row was
    wider than the length it had been clipped to, and a `\r` moved the cursor back
    over what the row had already drawn. Colour codes are dropped, since the words
    between them are the content; a tab becomes the four spaces the console pane
    already used; any other control character becomes one space, which keeps a row
    its length and says something was there.
    """
    return CONTROL.sub(" ", strip_ansi(text).replace("\t", "    "))


# ── inline markdown in a card ──────────────────────────────────────────────────
#
# A card's prose is markdown as the task files write it: `code` and **bold**. The
# lines are wrapped as text (`para(..., rich=True)`) and each carries ONE attribute, so
# the spans travel as four private-use characters that `put` turns into runs: wrapping then sees them as
# characters (a line may break a few columns early, never late) and the markers the
# author typed are never drawn.

BOLD_ON, BOLD_OFF, CODE_ON, CODE_OFF = "\ue000", "\ue001", "\ue002", "\ue003"
MARKS = re.compile("[\ue000-\ue003]")
SPAN = re.compile(r"`([^`\n]+)`|\*\*([^*\n]+)\*\*")


def mark_up(text):
    """`text` with `code` and **bold** as span marks. Code is cut out first, so a
    `**` inside it stays a literal; nothing nests."""
    return SPAN.sub(lambda m: (CODE_ON + m.group(1) + CODE_OFF) if m.group(1) is not None
                    else BOLD_ON + m.group(2) + BOLD_OFF, str(text))


def unmark(text):
    """The cells `text` takes on the screen: its span marks taken out."""
    return MARKS.sub("", text)


def carry_marks(lines):
    """Wrapped `(text, attr)` lines whose spans each close on their own line: a span
    the wrap split is closed at the end of one line and reopened on the next."""
    out, open_ = [], ""
    for text, attr, *rest in lines:
        text = open_ + text
        open_ = ""
        for m in MARKS.findall(text):
            open_ = m if m in (BOLD_ON, CODE_ON) else ""
        if open_:
            text += BOLD_OFF if open_ == BOLD_ON else CODE_OFF
        out.append((text, attr, *rest))
    return out


def clip_marked(text, width):
    """`text` as span marks, in `width` cells with an ellipsis when it is cut; a span
    the cut leaves open is closed before the ellipsis."""
    marked = mark_up(text)
    if len(unmark(marked)) <= width:
        return marked
    keep, cells = "", 0
    for ch in marked:
        if cells >= max(0, width - 1) and not MARKS.match(ch):
            break
        keep += ch
        cells += not MARKS.match(ch)
    (closed, _), = carry_marks([(keep.rstrip(), 0)])
    return closed + "…"


def code_attr():
    accent = look("accent")
    return accent if accent & curses.A_COLOR else curses.A_UNDERLINE


# ── codex's two log shapes ─────────────────────────────────────────────────────
#
# ⚠️ **CLAUDE WRITES ONE OBJECT AND CODEX WRITES JSONL**, and until 2026-09-17 this
# screen condensed only the first. A codex run's result pane printed
# `json.dumps(event)[:400]` per line — a command's whole aggregated output cut
# mid-token, the base64 of an encrypted reasoning blob, forty rows of it — and its
# transcript pane printed NOTHING AT ALL, because `transcript_lines` keeps records
# whose `type` is `assistant` or `user` and every record in a codex rollout is a
# `response_item` or an `event_msg`. Neither pane was shaped wrongly FOR codex; both
# were shaped for Claude, and codex fell through them.
#
# Both codex shapes are condensed here to the SAME rows the Claude pane shows — what
# the agent said, what it ran, one row per result plus its size — because the point
# of these panes is counting what a session did, and that should not need a second
# layout learnt to do it under `--codex`. The rows are named by ROLE rather than
# carrying curses attributes, so they can be tested without a terminal:
# `curses.color_pair` raises until `initscr`, which is the whole reason the Claude
# renderer above has never had a test.
WRAPPED_ROLES = frozenset(("say", "you", "run", "err"))


def codex_text(content):
    """The text of a codex content field, whether it is a string or blocks."""
    if isinstance(content, str):
        return content
    parts = []
    for b in content or []:
        if isinstance(b, dict):
            parts.append(b.get("text") or "")
        elif isinstance(b, str):
            parts.append(b)
    return "\n".join(p for p in parts if p)


def codex_command(command):
    """The command a codex exec item actually ran.

    Codex wraps every one as `/bin/bash -lc "<the command>"`, so showing the item
    verbatim spends the first fourteen columns of every row on the same wrapper and
    pushes the part that differs off the right edge. shlex unquotes it exactly as
    bash would; anything that is not a plain `-lc` string is shown as written, which
    is the honest answer rather than a guess at what a longer invocation meant.
    """
    raw = command if isinstance(command, str) else " ".join(
        str(c) for c in (command or []))
    try:
        parts = shlex.split(raw)
    except ValueError:
        return raw
    if (len(parts) == 3 and parts[1] in ("-lc", "-c", "-lic")
            and os.path.basename(parts[0]) in ("bash", "sh", "zsh")):
        return parts[2]
    return raw


CALL_KEYS = ("cmd", "command", "file_path", "path", "query", "pattern")


def codex_call_argument(text):
    """The argument inside a codex tool call, as a person would read it.

    An `exec` call arrives as a line of JavaScript —
    `const r = await tools.exec_command({"cmd": "<the command>", "workdir": "<dir>"});
    text(r.output);` — so the command, the only part of it worth a row, sits in an
    object inside a program. Printed as it arrives it fills three wrapped rows with
    the same wrapper on every one of them, and the `sed` range that differs lands
    mid-row.

    Two passes, and ⚠️ **THE SECOND IS NOT A BELT-AND-BRACES DUPLICATE OF THE
    FIRST**: codex writes that object as JSON in some runs (`{"cmd": "<the command>"}`) and as a
    JavaScript literal with a BARE KEY in others (`{cmd: "<the command>"}`), and the second does
    not parse as JSON at all. Two rollouts an hour apart on this box differ that way,
    so a reader that only decoded the object silently fell back to printing the
    snippet for a whole session at a time. Either way the VALUE is decoded with
    `raw_decode` rather than matched with a pattern, so a command carrying a quote, a
    brace or a newline does not take its row with it.
    """
    s = text if isinstance(text, str) else json.dumps(text or "")
    at = s.find("{")
    while at != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(s, at)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            for key in CALL_KEYS:
                if obj.get(key):
                    return codex_command(obj[key])
            return re.sub(r"\s+", " ", json.dumps(obj))
        at = s.find("{", at + 1)
    for key in CALL_KEYS:
        for m in re.finditer(r'["\']?\b%s["\']?\s*:\s*' % key, s):
            if m.end() >= len(s) or s[m.end()] not in '"[':
                continue
            try:
                val, _ = json.JSONDecoder().raw_decode(s, m.end())
            except ValueError:
                continue
            if isinstance(val, (str, list)) and val:
                return codex_command(val)
    return re.sub(r"\s+", " ", s).strip()


TOOL_CALL = re.compile(r"\btools\.(\w+)\s*\(")
PATCH_FILE = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.+)$", re.M)
# Codex's tool names where a shorter one is what the rest of the pane already says:
# the run log calls every command `exec`, so the transcript does too.
TOOL_LABEL = {"exec_command": "exec"}


def _call_object(s):
    """The first JSON object inside a call's argument text, or None."""
    at = s.find("{")
    while at != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(s, at)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            return obj
        at = s.find("{", at + 1)
    return None


def codex_call_rows(name, text):
    """A codex tool call, as rows of (text, role).

    The call's own `name` is `exec` for EVERY code-mode call, because each one is a
    script, so the tool that script actually called is read out of the script
    (`tools.<tool>(<args>)`) and named instead. Three are drawn as what they did
    rather than as what they were passed:

    ⚠️ `apply_patch` arrives as `const patch = "<the whole patch>"`, one JavaScript
    string literal with its newlines escaped, so the generic path wrapped an entire
    patch into a single row that ran to dozens of screen rows. It is drawn as the
    files it touched, the way the run log draws a `file_change`.

    `write_stdin` with nothing to write and `wait` are the agent POLLING a command it
    started, which on a whole-suite run is most of its calls. Their arguments are
    bookkeeping (`yield_time_ms`, `max_output_tokens`), so they are named by what is
    being waited on and nothing else.
    """
    s = text if isinstance(text, str) else json.dumps(text or "")
    m = TOOL_CALL.search(s)
    tool = m.group(1) if m else (name or "tool")
    if tool == "apply_patch" or (not m and "*** Begin Patch" in s):
        # The literal's escapes are undone only as far as finding the file lines
        # needs; the file names themselves are plain paths.
        files = PATCH_FILE.findall(s.replace("\\n", "\n"))
        if files:
            return [("  ✎ %s %s" % (kind.lower(), path.strip()), "run")
                    for kind, path in files]
        return [("  ✎ patch   [%d chars]" % len(s), "run")]
    obj = _call_object(s[m.end() - 1:] if m else s) or {}
    if tool == "write_stdin" and "session_id" in obj:
        chars = obj.get("chars") or ""
        if not chars:
            return [("  → poll  session %s" % obj["session_id"], "run")]
        return [("  → write_stdin  session %s  %s" % (obj["session_id"], json.dumps(chars)),
                 "run")]
    if tool == "wait" and "cell_id" in obj:
        return [("  → wait  cell %s" % obj["cell_id"], "run")]
    return [("  → %s  %s" % (TOOL_LABEL.get(tool, tool), codex_call_argument(s)), "run")]


def codex_output_text(body):
    """What a command PRINTED, out of the envelope a polling tool wraps it in.

    `write_stdin` and `wait` answer with an object — `{"chunk_id": <id>,
    "wall_time_seconds": <n>, "output": "<what was printed>"}` — so the result row
    showed the envelope's first forty characters, which are the same on every poll.
    The output is the part that differs; `None` when the body is not such an object.
    """
    t = (body or "").strip()
    if not t.startswith("{"):
        return None
    try:
        obj = json.loads(t)
    except ValueError:
        return None
    if isinstance(obj, dict) and isinstance(obj.get("output"), str):
        return obj["output"]
    return None


def codex_session_id(events):
    """The thread a codex run belongs to, or "".

    ⚠️ **NOT `event["id"]`**, which is what this read until 2026-09-17 and is an
    event ORDINAL: the older stream numbers its events, so the pane took `"0"` for a
    session id and then reported a transcript missing at `<sessions>/0.jsonl` — a
    wrong answer shaped exactly like a right one. The id is `thread_id` in the
    current stream and `session_id` in the one before it, and nothing else in these
    events is a session.
    """
    for e in events:
        if not isinstance(e, dict):
            continue
        msg = e.get("msg") if isinstance(e.get("msg"), dict) else {}
        for cand in (e.get("thread_id"), e.get("session_id"),
                     msg.get("thread_id"), msg.get("session_id")):
            if cand:
                return str(cand)
    return ""


def _result_row(text, marker="", indent="    "):
    """One row for a tool result: its first line, then how much was left out.

    The same cut the Claude renderer makes, for the same reason — wrapping results is
    what made a transcript unreadable in the first place — and the size marker is
    what keeps it a condensation rather than a silent truncation.
    """
    s = text or ""
    # Sized as it ARRIVED, colour codes included: the size stands for what the
    # agent was handed, and the colours were part of that.
    first = strip_ansi(s).strip().splitlines()
    return "%s← %s%s%s" % (indent, marker, first[0][:200] if first else "",
                           "   [%d chars]" % len(s) if s else "")


RESULT_SEPARATOR = "<RESULT_END>"


def codex_tool_output(output):
    """A codex tool result and whether it failed, without its runner's wrapper.

    ⚠️ **THE FIRST LINE OF THE RECORD IS NOT THE FIRST LINE OF THE OUTPUT.** Codex's
    exec tool returns blocks: a preamble (`Script completed` or `Script failed`, the
    wall time, `Output:`), then what the command printed, then `<RESULT_END>` between
    one command's output and the next. A one-row condensation that took the record's
    first line therefore said `← Script completed` on every row of the pane — true,
    useless, and identical for a session that read ten files and one that read none.
    The preamble is read for the one thing it carries that the output does not, which
    is whether the script failed, and then dropped.
    """
    blocks = [output] if isinstance(output, str) else [
        (b.get("text") or "") if isinstance(b, dict) else str(b)
        for b in output or []]
    first = blocks[0].strip() if blocks else ""
    marker = "failed · " if first.startswith("Script failed") else ""
    if first.startswith(("Script completed", "Script failed")):
        blocks = blocks[1:]
    body = [b for b in blocks if b.strip() and b.strip() != RESULT_SEPARATOR]
    return marker, "\n".join(body)


def codex_item_lines(item, live=False):
    """One item of a codex `exec --json` stream, as rows of (text, role)."""
    t = item.get("type")
    if t in ("agent_message", "assistant_message"):
        return [("  " + ln, "say")
                for ln in (item.get("text") or "").splitlines() if ln.strip()]
    if t == "reasoning":
        text = item.get("text") or ""
        return [("  ~ reasoning, %d chars" % len(text), "think")] if text.strip() else []
    if t == "command_execution":
        rows = [("  → exec  %s" % codex_command(item.get("command")), "run")]
        if live and item.get("status") in (None, "in_progress"):
            rows.append(("    ← running", "out"))
            return rows
        code = item.get("exit_code")
        bad = code not in (0, None)
        rows.append((_result_row(item.get("aggregated_output"),
                                 "exit %s · " % code if bad else ""),
                     "err" if bad else "out"))
        return rows
    if t == "file_change":
        rows = [("  ✎ %s %s" % (c.get("kind") or "change", c.get("path") or ""), "run")
                for c in item.get("changes") or [] if isinstance(c, dict)]
        return rows or [("  ✎ file change", "run")]
    if t == "mcp_tool_call":
        return [("  → %s.%s  %s" % (item.get("server") or "mcp", item.get("tool") or "",
                                    item.get("status") or ""), "run")]
    if t == "web_search":
        return [("  → search  %s" % (item.get("query") or ""), "run")]
    if t == "todo_list":
        return [("  ☰ todo list, %d items" % len(item.get("items") or []), "dim")]
    if t == "error":
        return [("  ⨯ %s" % (item.get("message") or "no message"), "err")]
    # An item type nobody has taught this screen still gets a row NAMING itself,
    # rather than being dropped: a codex release that adds one should read as one
    # unfamiliar row, not as a session that did less than it did.
    return [("  · %s — %s" % (t, re.sub(r"\s+", " ", json.dumps(item))[:160]), "dim")]


def codex_result_lines(events):
    """A codex run's captured `exec --json` stream, as rows of (text, role).

    ⚠️ **AN ITEM IS RENDERED ONCE.** `item.started` and `item.completed` carry the
    same item, so a pane that drew both showed every command twice — once with an
    empty output and once with its real one. The started copy is drawn only when no
    completed one follows it, which is exactly the case worth seeing: the command a
    LIVE run is sitting in right now.
    """
    done = set()
    for e in events:
        if isinstance(e, dict) and e.get("type") == "item.completed":
            done.add((e.get("item") or {}).get("id"))
    lines, counts, usage = [], collections.Counter(), {}
    for e in events:
        if not isinstance(e, dict):
            continue
        if "_raw" in e:
            # A line that is not JSON at all: codex's own stderr, or a nix warning
            # that landed in the capture. Kept, because on a run that produced
            # nothing else it is the only thing that says why.
            lines.append(("  " + e["_raw"][:200], "dim"))
            continue
        t = e.get("type") or ""
        if t == "item.updated":
            continue
        if t.startswith("item."):
            item = e.get("item") or {}
            if t != "item.completed" and item.get("id") in done:
                continue
            counts[item.get("type") or "item"] += 1
            lines.extend(codex_item_lines(item, live=(t != "item.completed")))
        elif t in ("turn.failed", "thread.error", "error"):
            err = e.get("error") if isinstance(e.get("error"), dict) else e
            lines.append(("  ⨯ %s" % str(err.get("message")
                                         or re.sub(r"\s+", " ", json.dumps(e)))[:400],
                          "err"))
        elif t == "turn.completed":
            usage = e.get("usage") or {}
    head = ["%d items" % sum(counts.values())]
    head += ["%s %d" % (k, v) for k, v in counts.most_common()]
    out = [(" · ".join(head), "head")]
    if usage:
        # ⚠️ SPELT OUT AS A SUM, because `input 10,028k` under a screen that also
        # says `peak context 176k` reads as a second answer to the same question and
        # is 57x the first. Codex's turn usage adds up every request the turn made,
        # so it is what the turn SPENT; what it was CARRYING is peak context, one
        # definition, measured in context.py and printed below this.
        out.append(("%s input tokens summed over the turn's requests · %s cached · "
                    "%s output" % (fmt_tokens(usage.get("input_tokens")),
                                   fmt_tokens(usage.get("cached_input_tokens")),
                                   fmt_tokens(usage.get("output_tokens"))), "dim"))
    return out + [("", "dim")] + lines


# ── kiro's log shape ───────────────────────────────────────────────────────────
#
# `kiro-cli chat --output-format stream-json` writes the run's ACP events, one per
# line, with kiro's own stderr captured between them (run.sh). That one file is
# both the run's result and its transcript (context.py, `_kiro_log`), so it is
# condensed once, to the same rows the other panes draw. The updates are found by
# ACP's own field names at any depth (`dfs_context.acp_updates`), because no captured
# run pinned the envelope around them when this was written.

def kiro_session_id(events):
    return _sc.kiro_session_id(e for e in events if isinstance(e, dict))


def _acp_text(content):
    """The text of an ACP content block, or of a list of them."""
    if isinstance(content, list):
        return "".join(_acp_text(c) for c in content)
    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return content["text"]
        if "content" in content:
            return _acp_text(content["content"])
    return content if isinstance(content, str) else ""


def kiro_result_lines(events):
    """A kiro run's captured stream, as rows of (text, role).

    Message and thought chunks arrive a few words at a time, so consecutive chunks of
    one kind are joined into one row. A tool call is drawn when it starts, under the
    title kiro gave it, and again only if it FAILS — a completed call's output is in
    the file, and drawing every one is what made transcripts unreadable.
    """
    rows, counts, usage = [], collections.Counter(), {}
    pending = [None, []]                  # the chunk kind being joined, its pieces

    def flush():
        kind, parts = pending
        text = "".join(parts).strip()
        if text:
            if kind == "user_message_chunk":
                rows.append((you_row(text), "you"))
            elif kind == "agent_thought_chunk":
                flat = re.sub(r"\s+", " ", text)
                rows.append(("  … " + flat[:200] + ("   [%d chars]" % len(text)
                                                     if len(flat) > 200 else ""), "think"))
            else:
                rows.append((text, "say"))
        pending[0], pending[1] = None, []

    titles = {}
    for e in events:
        if not isinstance(e, dict):
            continue
        if "_raw" in e:
            # kiro's own stderr: on a run that died before it started (no login, a
            # limit), the only thing that says why.
            line = strip_ansi(e["_raw"]).strip()
            if line:
                flush()
                rows.append(("  " + line[:200], "dim"))
            continue
        updates = _sc.acp_updates(e)
        if not updates:
            err = e.get("error")
            if err:
                flush()
                msg = err.get("message") if isinstance(err, dict) else err
                rows.append(("  ⨯ %s" % str(msg)[:400], "err"))
            continue
        for u in updates:
            kind = u["sessionUpdate"]
            if kind in ("user_message_chunk", "agent_message_chunk", "agent_thought_chunk"):
                if pending[0] != kind:
                    flush()
                    pending[0] = kind
                pending[1].append(_acp_text(u.get("content")))
                continue
            if kind == "usage_update":
                usage = u
                continue
            if kind not in ("tool_call", "tool_call_update"):
                continue
            flush()
            tid = u.get("toolCallId")
            if kind == "tool_call" or tid not in titles:
                titles[tid] = u.get("title") or titles.get(tid) or ""
                if kind == "tool_call":
                    counts["tool calls"] += 1
                    rows.append(("  → %s  %s" % (u.get("kind") or "tool", titles[tid]), "run"))
            if u.get("status") == "failed":
                rows.append((_result_row(_acp_text(u.get("content")) or titles[tid],
                                         marker="⨯ "), "err"))
    flush()
    counts["messages"] = sum(1 for _, role in rows if role == "say")
    head = " · ".join("%d %s" % (v, k) for k, v in counts.items())
    out = [(head, "head")]
    if isinstance(usage.get("used"), int):
        out.append(("context %s of %s" % (fmt_tokens(usage["used"]),
                                          fmt_tokens(usage.get("size"))), "dim"))
    return out + [("", "dim")] + rows


# ── opencode's log shape ───────────────────────────────────────────────────────
#
# `opencode run --format json` writes one event per line — `step_start`, `text`,
# `reasoning`, `tool_use`, `step_finish`, `error` — with opencode's own stderr
# captured between them (run.sh). As kiro's, that one file is the run's result and
# its transcript (context.py), condensed once to the rows the other panes draw.
# A `tool_use` arrives once, finished, so each call is one `→` row and a result row
# only when it errored.

def opencode_session_id(events):
    return _sc.opencode_session_id(e for e in events if isinstance(e, dict))


def opencode_call_argument(tool, state):
    """What an opencode tool call was ABOUT, as a person would read it."""
    inp = state.get("input") if isinstance(state.get("input"), dict) else {}
    for key in ("command", "filePath", "file_path", "path", "pattern", "url", "query"):
        if inp.get(key):
            return str(inp[key]).strip().splitlines()[0]
    return state.get("title") or ""


def opencode_result_lines(events):
    """An opencode run's captured `--format json` stream, as rows of (text, role)."""
    rows, counts, last = [], collections.Counter(), None
    for e in events:
        if not isinstance(e, dict):
            continue
        if "_raw" in e:
            # opencode's own stderr: on a run that died before it started (a config
            # that does not parse, no credentials), the only thing that says why.
            line = strip_ansi(e["_raw"]).strip()
            if line:
                rows.append(("  " + line[:200], "dim"))
            continue
        kind, part = e.get("type"), e.get("part") if isinstance(e.get("part"), dict) else {}
        if kind == "text" and str(part.get("text") or "").strip():
            rows.append((str(part["text"]).strip(), "say"))
        elif kind == "reasoning" and str(part.get("text") or "").strip():
            flat = re.sub(r"\s+", " ", part["text"]).strip()
            rows.append(("  … " + flat[:200] + ("   [%d chars]" % len(flat)
                                                 if len(flat) > 200 else ""), "think"))
        elif kind == "tool_use":
            counts["tool calls"] += 1
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            tool = part.get("tool") or "tool"
            rows.append(("  → %s  %s" % (TOOL_LABEL.get(tool, tool),
                                         opencode_call_argument(tool, state)), "run"))
            if state.get("status") == "error":
                rows.append((_result_row(str(state.get("error") or state.get("output") or ""),
                                         marker="⨯ "), "err"))
        elif kind == "step_finish":
            last = _sc._opencode_peak(e)
        elif kind == "error":
            err = e.get("error") if isinstance(e.get("error"), dict) else {}
            data = err.get("data") if isinstance(err.get("data"), dict) else {}
            msg = data.get("message") or err.get("message") or err.get("name") or "error"
            rows.append(("  ⨯ %s%s" % (str(msg)[:400],
                                       " (HTTP %s)" % data["statusCode"]
                                       if data.get("statusCode") else ""), "err"))
    counts["messages"] = sum(1 for _, role in rows if role == "say")
    out = [(" · ".join("%d %s" % (v, k) for k, v in counts.items()), "head")]
    if last is not None:
        out.append(("context %s" % fmt_tokens(last), "dim"))
    return out + [("", "dim")] + rows


YOU_CHARS = 600


def you_row(text):
    """What the author said, whole unless it is the runner's briefing.

    ⚠️ A chain's first user message is not something anybody typed: it is the
    briefing `run.sh` builds, §0-§2 of the roadmap verbatim, around 40,000
    characters. Wrapped in full it was three hundred rows ahead of the session's
    first action, so the pane opened on the rules rather than the work. A message
    longer than a person types is cut to its opening and its size, the same cut a
    tool result gets; one the author actually wrote is shorter than that and is
    still shown whole.
    """
    flat = re.sub(r"\s+", " ", text).strip()
    if len(flat) <= YOU_CHARS:
        return "you: " + flat
    return "you: %s   [%d chars]" % (flat[:YOU_CHARS // 2], len(text))


def codex_payload_lines(p):
    """One record of a codex rollout, as rows of (text, role)."""
    t = p.get("type")
    if t in ("message", "agent_message"):
        role = "assistant" if t == "agent_message" else (p.get("role") or "assistant")
        text = codex_text(p.get("content"))
        if role == "assistant":
            return [("  " + ln, "say") for ln in text.splitlines() if ln.strip()]
        # `developer` is the harness talking and a `user` message opening with `<` is
        # its machinery, neither of which the author typed.
        if role == "user" and not text.lstrip().startswith("<"):
            return [(you_row(text), "you")]
        # ⚠️ ONE ROW PLUS ITS SIZE, not wrapped — the same cut a tool result gets,
        # and for a stronger reason. Codex's developer preamble is the skills table,
        # the plugin catalogue and the collaboration rules: four messages and about
        # forty rows of dim text, ahead of the first thing the session actually did.
        # Wrapping them put the work below the fold on every codex transcript, which
        # is the Claude renderer's own rule applied to a message a hundred times the
        # size of the ones it was written for.
        flat = re.sub(r"\s+", " ", text).strip()
        return [("     %s   [%d chars]" % (flat[:200], len(text)), "meta")]
    if t == "reasoning":
        summary = "\n".join(s.get("text") or "" for s in p.get("summary") or []
                            if isinstance(s, dict))
        if summary.strip():
            return [("  ~ " + ln, "think") for ln in summary.splitlines() if ln.strip()]
        # ⚠️ An empty summary beside an `encrypted_content` blob is NOT a thinking
        # block this pane can size. The blob is ciphertext plus base64, so a
        # `~ thinking, 4312 chars` row would be measuring the envelope and would be
        # read as a measurement of the thought. Claude's side can give the number
        # because the text is there; here there is nothing true to say, and a
        # session's worth of rows saying it is worse than silence.
        return []
    if t in ("custom_tool_call", "function_call", "local_shell_call"):
        return codex_call_rows(p.get("name"), p.get("input") or p.get("arguments")
                               or p.get("action") or "")
    if t in ("custom_tool_call_output", "function_call_output",
             "local_shell_call_output"):
        marker, body = codex_tool_output(p.get("output"))
        printed = codex_output_text(body)
        if printed is not None:
            if not strip_ansi(printed).strip():
                return [("    ← %sno new output" % marker, "err" if marker else "out")]
            body = printed
        return [(_result_row(body, marker), "err" if marker else "out")]
    return [("  · %s record" % t, "dim")]


def codex_transcript_lines(records):
    """A codex rollout, as rows of (text, role).

    ⚠️ **`event_msg` IS SKIPPED, AND THAT IS NOT AN OMISSION.** A rollout records the
    same turn twice — once as the `response_item` sent to the model and once as an
    `item_completed` event for whoever is watching — so a renderer that took both
    drew every message and every command twice over. What is read from `event_msg` is
    the one thing with no `response_item` to carry it: a turn that was ABORTED, which
    is the record that says why a chain stopped where it did. Token counts are the
    other resident and are measured in context.py, in one place, deliberately.
    """
    made = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        p = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
        if rec.get("type") == "event_msg":
            if p.get("type") == "turn_aborted":
                made.append(("  ⨯ turn aborted: %s"
                             % (p.get("reason") or "no reason given"), "err"))
            continue
        if rec.get("type") != "response_item":
            continue
        made.extend(codex_payload_lines(p))
    return made

def _dfs_context():
    """The peak-context definition the CHECKPOINT is written in, not a third copy.

    §0.5b's 125,000 is measured by context.py and the runner's chain
    ceiling reads the same quantity, so a screen computing its own would put a third
    number under the same word. The first draft here did exactly that, adding
    `input_tokens` and reading user records too — and on a real 238-response
    transcript it came out 431,911 against the canonical 431,909. ⚠️ That near-miss
    is the argument, not a counter-argument: a duplicate definition that AGREES on
    the inputs you happen to try is one nobody removes, and it drifts the first time
    a provider fills a field differently. Peak is the max over DEDUPED assistant
    responses of cache_read + cache_creation, defined in one file.

    ⚠️ THE THRESHOLDS COME WITH IT, and until 2026-09-11 they did not. This screen
    imported the measurement and then spelled the number it is measured against —
    `125_000` four times, `195_000` once and the word "125k" in a string — so the
    file whose docstring argues against a second copy of the definition carried five
    copies of the budget. It went unnoticed because a constant cannot drift the way a
    computation can: it just stays behind, silently, the first time §0.5b moves.
    """
    spec = importlib.util.spec_from_file_location("dfs_context",
                                                  HERE / "context.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_sc = _dfs_context()
peak_and_turns, CHECKPOINT, FLAT_TO = _sc.peak_and_turns, _sc.CHECKPOINT, _sc.FLAT_TO


def transcript_path(session_id, agent):
    """One definition, in context.py; this keeps the Path return the panes use."""
    found = _sc.transcript_path(session_id, agent, root=ROOT)
    return Path(found) if found else None


def fmt_tokens(n):
    return "%.0fk" % (n / 1000.0) if n and n >= 1000 else str(n or 0)


# ── the screen ─────────────────────────────────────────────────────────────────

def row_state(it):
    """What one row's status cell says — three questions, three answers.

    `↳W10` is "answer something above this in your own branch"; `»W12` is "finish
    something on the far side of a fence"; anything else is the item's own status.
    The ancestor comes first when both hold, because answering it is the move that
    can be made now.

    ⚠️ **A marker only ever replaces `open`**, which is the whole argument for having
    them: "open" is true of a held item and useless. A `done` item is finished
    wherever it sits and a `blocked` one has a question of its own to answer — so
    saying either of those is waiting on something else is a sentence that is not
    true, and a fence would otherwise have said it about whole segments at a time.
    """
    held = it.get("blocked_by") or it.get("waiting_on")
    if it["status"] != "open" or not held:
        return it["status"]
    return ("↳" if it.get("blocked_by") else "»") + held


def review_cell(it):
    """What the author's pass over this row would step through, or "".

    ⚠️ **It counts BOTH kinds** — the open raises and the standing assumptions — because
    `r` is one pass over both (§0.10) and a number that promised one of them would be
    wrong about what pressing it does. The raises are also in the status cell, as
    `blocked`, and that is not a duplicate: `blocked` says the branch cannot move,
    while this says how much is waiting for the author, which on a DONE row is the
    whole of what there is to say.

    Blank at zero, deliberately. A column of `rev 0` down a roadmap teaches the eye to
    skip the column, and the rows that matter here are the few that are not zero.
    """
    n = len(it.get("open_raises") or ()) + len(it.get("assumptions") or ())
    if n:
        return "rev %d" % n
    # A finished tree with nothing in it to read still waits for a tick: said as that,
    # so it is not the same `rev 1` as a task with a decision in it.
    return "accept" if it.get("standing") else ""


def ahead_cell(it):
    """`ahead 3` when 3 of this task's node commits are not on origin/main, `edits` for
    task-file changes not committed yet, `log` when those are log entries alone
    (`ahead 3 +edits` for both), else "". Blank at zero for the reason `review_cell` is."""
    n, edits = it.get("ahead") or 0, it.get("uncommitted")
    word = "log" if edits == "log" else "edits"
    return " ".join(p for p in ("ahead %d" % n if n else "", ("+" + word if n else word) if edits else "") if p)


# ---- the look -----------------------------------------------------------------
#
# ⚠️ QUIET CHROME, PLAIN CONTENT, AND NEVER THE TERMINAL'S BACKGROUND. What made the
# calm tools calm (docs/tui-research.md §0) was not how few colours they used — btop
# uses 82 — but that every rule, label and hint sits a step below the data, and that
# no large area is painted. So: rules and labels grey, text the terminal's own colour,
# ONE accent for keys and the cursor bar, the three status colours softened, and the
# selection a faint band with a one-cell bar (fzf's) instead of a slab of reverse
# video. The background is left alone, because this screen lives in somebody's tmux
# next to their editor; which is why it has to ask whether that background is light.
# Pair numbers 1–3 are red, green and cyan everywhere in this file, as before.
PAIRS = {"red": 1, "green": 2, "cyan": 3, "chrome": 4, "accent": 5, "sel": 6,
         "selbar": 7, "amber": 8, "selred": 9, "selgreen": 10, "selcyan": 11, "project": 12}
LOOK = {}     # name -> attr, filled by `init_look`; empty (all 0) until then
LIGHT = False  # the terminal's background, asked once before curses starts
SIDE_AT = 150  # columns from which the activity column sits beside the tree
NODE_AT = 110  # columns from which the selected node is shown beside the tree
BAR = "▌"      # the cursor's one-cell accent, where the band is drawn


def terminal_is_light():
    """Whether the terminal's background is light: DFS_THEME, then COLORFGBG, then
    the terminal itself (OSC 11), and dark when none of them says.

    Asked BEFORE curses starts, on /dev/tty, with a tenth of a second to answer: a
    terminal that ignores the query costs that and nothing else."""
    theme = os.environ.get("DFS_THEME", "").lower()
    if theme in ("light", "dark"):
        return theme == "light"
    fgbg = os.environ.get("COLORFGBG", "")
    if fgbg:
        bg = fgbg.split(";")[-1]
        if bg.isdigit():
            return int(bg) in (7, 15)
    try:
        import termios
        import tty as _tty
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except (OSError, ImportError):
        return False
    try:
        old = termios.tcgetattr(fd)
        _tty.setraw(fd)
        os.write(fd, b"\x1b]11;?\x1b\\")
        got, end = b"", time.time() + 0.1
        while time.time() < end and not (got.endswith(b"\x07") or got.endswith(b"\x1b\\")):
            import select
            r, _, _ = select.select([fd], [], [], max(0, end - time.time()))
            if not r:
                break
            got += os.read(fd, 64)
    except OSError:
        return False
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except (OSError, UnboundLocalError):
            pass
        os.close(fd)
    m = re.search(rb"rgb:([0-9a-fA-F]+)/([0-9a-fA-F]+)/([0-9a-fA-F]+)", got)
    if not m:
        return False
    r, g, b = (int(x, 16) / float(16 ** len(x) - 1) for x in m.groups())
    return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.5


def init_look(light=False, coloured=True):
    """The palette, from what the terminal can do. `NO_COLOR` gets attributes only:
    dim chrome, reverse selection, bold keys — ncdu's three, which read fine."""
    plain = (bool(os.environ.get("NO_COLOR")) or not coloured
             or not curses.has_colors())
    n = 0 if plain else curses.COLORS
    if n >= 256:
        if light:
            c = dict(red=124, green=28, cyan=24, chrome=245, accent=25, amber=130,
                     selbg=254)
        else:
            c = dict(red=174, green=108, cyan=110, chrome=243, accent=110, amber=179,
                     selbg=236)
        fg = {"red": c["red"], "green": c["green"], "cyan": c["cyan"],
              "chrome": c["chrome"], "accent": c["accent"], "amber": c["amber"]}
        for name, col in fg.items():
            curses.init_pair(PAIRS[name], col, -1)
        curses.init_pair(PAIRS["sel"], -1, c["selbg"])
        curses.init_pair(PAIRS["selbar"], c["accent"], c["selbg"])
        for name in ("red", "green", "cyan"):
            curses.init_pair(PAIRS["sel" + name], c[name], c["selbg"])
        LOOK.update(chrome=curses.color_pair(PAIRS["chrome"]),
                    accent=curses.color_pair(PAIRS["accent"]),
                    amber=curses.color_pair(PAIRS["amber"]),
                    sel=curses.color_pair(PAIRS["sel"]),
                    selbar=curses.color_pair(PAIRS["selbar"]), band=True)
    elif n >= 8:
        for name, col in (("red", curses.COLOR_RED), ("green", curses.COLOR_GREEN),
                          ("cyan", curses.COLOR_CYAN), ("accent", curses.COLOR_CYAN),
                          ("amber", curses.COLOR_YELLOW)):
            curses.init_pair(PAIRS[name], col, -1)
        LOOK.update(chrome=curses.A_DIM, accent=curses.color_pair(PAIRS["accent"]),
                    amber=curses.color_pair(PAIRS["amber"]), sel=curses.A_REVERSE,
                    selbar=curses.A_REVERSE, band=False)
    else:
        LOOK.update(chrome=curses.A_DIM, accent=curses.A_BOLD, amber=curses.A_BOLD,
                    sel=curses.A_REVERSE, selbar=curses.A_REVERSE, band=False)


def look(name):
    return LOOK.get(name, 0)


XTERM_LEVELS = (0, 95, 135, 175, 215, 255)     # what the cube's six steps really are


def xterm256(rgb):
    """The nearest colour in the 6x6x6 cube of the terminal's 256 (16 + 36r + 6g + b),
    by the cube's real levels, which are not evenly spaced."""
    r, g, b = (min(range(6), key=lambda i: abs(XTERM_LEVELS[i] - c)) for c in rgb)
    return 16 + 36 * r + 6 * g + b


def project_attr(colour, light, bold=curses.A_BOLD):
    """The project's colour (`#rrggbb`) as a text attribute: its HUE at a fixed lightness
    that reads on this terminal (dark on a light one, light on a dark one), snapped to
    the cube; bold alone when the terminal has fewer than 256 colours.

    ⚠️ Not the colour itself, lightened: a name's colour is dark so white text reads on
    it, and blending that toward white (or snapping it to the cube) left six muted
    shades for every hue. Fixed lightness and full saturation keep 12 (light) and 24
    (dark) apart. The pair is set again only when the colour changes."""
    if LOOK.get("band") is not True:          # init_look's 256-colour branch
        return bold
    h = colorsys.rgb_to_hls(*(int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)))[0]
    rgb = tuple(round(c * 255) for c in colorsys.hls_to_rgb(h, .3 if light else .65, 1))
    if LOOK.get("project_rgb") != (rgb, light):
        curses.init_pair(PAIRS["project"], xterm256(rgb), -1)
        LOOK["project_rgb"] = (rgb, light)
    return curses.color_pair(PAIRS["project"]) | bold


def selected(attr):
    """A row's attr as the SELECTION draws it. Panes still mark their cursor row with
    A_REVERSE, which is the one flag every line producer already sets and the tests
    already read; this is where that mark becomes the band, keeping the row's own
    colour and weight on it."""
    if not LOOK.get("band"):
        return attr
    base = attr & ~curses.A_REVERSE & ~curses.A_COLOR
    pair = curses.pair_number(attr & curses.A_COLOR) if attr & curses.A_COLOR else 0
    name = {PAIRS["red"]: "selred", PAIRS["green"]: "selgreen",
            PAIRS["cyan"]: "selcyan"}.get(pair, "sel")
    return base | curses.color_pair(PAIRS[name])


def start_agent_update():
    """Move the agent pin on to today's nixpkgs, in the background (agent.py).

    ⚠️ Background because it is a download, and `c` must not wait for it: the pin
    moves only once the new claude-code is built, so a key pressed meanwhile starts
    the old one at once. Its own session, so a Ctrl-C in a chat started from here
    does not kill it halfway.
    """
    try:
        return subprocess.Popen(
            [sys.executable, str(dfs_paths.SCRIPTS / "agent.py"), "--update"],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, start_new_session=True)
    except OSError:
        return None


ACTIVITY_KEEP = 400   # activity lines held, and read back from the file at start


def activity_file():
    """Where the activity log is kept: the run root, `.dfs/runs`, which is gitignored
    and outside what the file watch reads — so writing it cannot wake the poll."""
    return os.path.join(dfs_runs.run_root(), "activity.log")


def read_activity(path, keep=ACTIVITY_KEEP):
    """The last `keep` events from the file: `(epoch, kind, text)`."""
    out = []
    try:
        with open(path, errors="replace") as fh:
            lines = fh.read().splitlines()[-keep:]
    except OSError:
        return out
    for ln in lines:
        parts = ln.split("\t", 2)
        if len(parts) == 3:
            try:
                out.append((float(parts[0]), parts[1], parts[2]))
            except ValueError:
                continue
    return out


class UI(WalkMixin):
    WATCH_HINT = ", [l] to watch"      # what a started chain's message adds for a person at the screen
    # State a screen built without __init__ (the tests' `object.__new__`) still
    # needs to draw: nothing being typed, no activity yet, the whole list shown.
    input = None
    clip = None
    events = ()
    only_mine = False
    order_undo = ()
    answers = ()
    real = False     # only a screen made by __init__ writes the log or rings
    card_panel = False   # the selected node is drawn at the right (set by `draw`)
    card_scroll = 0      # how far down that panel is read (^d ^u)
    asm_sel = 0          # the assumptions pane's cursor

    def __init__(self, stdscr):
        self.scr = stdscr
        self.sel = 0
        self.run_sel = 0
        self.art_sel = 0
        self.list_scroll = 0
        self.scroll = 0
        self.pane = "item"          # item | reglog | runs | agentlog | todo | artefact | keys
        self.key_sel = 0            # the `?` pane's cursor, into `key_rows`
        self.keys_from = ("item", "list")   # (pane, focus) `?` was pressed from
        self.last_search = ""       # `/`, repeated by an empty one
        self.input = None           # what is being typed, while it is — `read_line`
        self.only_mine = False      # `m`: only the items waiting on the author
        self.order_undo = []        # `z`: (order.md before, order.md we wrote)
        self.answers = []           # `:`'s arguments, handed to the prompts it meets
        # The activity log: what this screen and its walk DID, newest last. Read back
        # from the file, so a restart does not forget why the walk stopped.
        self.events = collections.deque(read_activity(activity_file()),
                                        maxlen=ACTIVITY_KEEP)
        self.real = True
        self.commands = queue.Queue()   # what the web page asks of this screen: `call_soon`
        # Which half of the item pane the keys drive: the item list at the top, or
        # the item's tree below it. Tab swaps them; see `tree_focused`.
        self.focus = "list"
        self.agent = read_agent(agent_file())   # which run.sh launches: one of AGENTS
        self.msg = ""
        self.agent_update = None    # the process start_agent_update began, from main
        self.data = load()
        self.dirty = roadmap_dirty()
        self.runs = discover_runs()
        self.chains = dfs_runs.run_dirs()
        self.sig = watch_signature()
        self.open_run = None
        self.follow = False         # derived every draw from now on — `follows`
        self._cache = {}            # (path, mtime) -> rendered lines
        self._measure_cache = {}    # transcript -> (when, peak, responses)
        self._sel_row = self._sel_end = None
        self.body_h = 1             # rows the detail pane showed at the last draw
        self.walk_init()
        # The review tree. Folds and opened nodes are keyed by node id, which
        # carries its task, so a fold survives a trip to another item and back.
        self.tree_sel = 0
        self.tree_item = None       # the item the cursor was placed on
        self.tree_folded = set()
        self.tree_flagged = False   # `p`: only the nodes that want the author
        self.tree_open = set()
        self._tree_cache = {}       # task -> (mtimes, tree, archive text, commits)

    # ---- data ----------------------------------------------------------------

    @property
    def items(self):
        return self.data["items"]

    def current(self):
        return self.items[self.sel] if self.items else None

    def chain_for(self, item, mode="work"):
        """The newest work chain for an item, live or not — what marks its row.

        A DEAD chain is shown as well as a live one, and that is the point: a chain
        that died on its feet leaves a silence indistinguishable from an item nobody
        has picked up, which is the exact shape this repo keeps writing down about
        workers that stop without saying so. `unknown` is not shown — see
        runs.py, those are the directories from before a pid was recorded.
        """
        for r in self.chains:
            if r["item"] == item and r["mode"] == mode and r["state"] != "unknown":
                return r
        return None

    def live(self, mode="work"):
        return [r for r in self.chains if r["live"] and r["mode"] == mode]

    # ---- the web page beside this screen ---------------------------------------
    # The web server runs in this process, on its own threads, and shares this screen's
    # walk: one state, one set of decisions, controllable from either. Curses and the
    # walk's state belong to THIS thread, so the server does not touch them: it hands
    # a function to `call_soon`, which `poll` runs here, and waits for what it returns.

    commands = None
    web = None          # the running server's handle, from `start_web`

    def call_soon(self, fn, timeout=5.0):
        """Run `fn` on the screen's own thread and return its result (or raise its
        error). A screen with a prompt open does not poll, so a request can wait: it
        gives up after `timeout` seconds and says so."""
        if self.commands is None:
            raise ValueError("this screen is not serving the web page")
        call = _Call(fn)
        self.commands.put(call)
        if not call.done.wait(timeout):
            call.cancelled = True
            raise ValueError("the terminal screen is busy (a prompt is open): try again in a moment")
        if call.error is not None:
            raise call.error
        return call.result

    def drain_commands(self):
        while self.commands is not None:
            try:
                call = self.commands.get_nowait()
            except queue.Empty:
                return
            if call.cancelled:
                continue
            try:
                call.result = call.fn()
            except Exception as e:      # the caller's to report, not the screen's to die of
                call.error = e
            call.done.set()

    def refresh_chains(self):
        """Re-derive which chains are on the box, NOW rather than at the last poll.

        A handful of `/proc` reads, against a whole roadmap re-parse — cheap enough
        that anything about to make a decision on `self.chains` can simply ask again
        rather than reason about how old the list is.
        """
        self.chains = dfs_runs.run_dirs()

    def run_tag(self, item):
        """`● run 2/5` or `⨯ died`, plus the attribute to draw it in."""
        r = self.chain_for(item)
        if r is None or r["state"] == "finished":
            return "", 0
        if r["live"]:
            return "● %s" % self.chain_progress(r), curses.color_pair(2) | curses.A_BOLD
        return "⨯ died %s" % self.chain_progress(r), curses.color_pair(1)

    def move(self, step):
        """↑↓ and j/k both move the PANE'S CURSOR, and the pane says what that is.

        They were split — j/k moved the item, the arrows scrolled the detail — and on
        the item pane that made the arrows look dead: the detail is usually shorter
        than its half of the screen, so the scroll clamps back to 0 in `draw` and
        pressing down moves nothing at all. `scripts/model-explorer.py`, the other
        screen in this family, binds them together, and a cursor key that selects in
        one of two sibling TUIs and not the other is one you stop trusting in both.
        The agent log is the one pane with no cursor, so there the text is what moves.
        """
        if self.pane == "agentlog":
            self.scroll = max(0, self.scroll + step)
        elif self.pane == "artefact":
            self.art_sel = max(0, min(self.art_sel + step, len(self.artefacts()) - 1))
        elif self.pane == "runs":
            self.run_sel = max(0, min(self.run_sel + step, len(self.runs) - 1))
        elif self.pane == "keys":
            self.key_sel = max(0, min(self.key_sel + step, len(self.key_rows()) - 1))
        elif self.pane == "assumptions":
            self.asm_sel = max(0, min(self.asm_sel + step, len(self.asm_rows()) - 1))
        elif self.tree_focused():
            self.tree_sel = max(0, min(self.tree_sel + step, len(self.tree_rows()) - 1))
        else:
            shown = self.visible()
            if shown:
                at = shown.index(self.sel) if self.sel in shown else 0
                self.sel = shown[max(0, min(at + step, len(shown) - 1))]
            self.scroll = 0         # a new item is read from its top

    def nodes(self):
        """The tree as the screen is showing it, for `dfs_order` to move a node in."""
        return dfs_order.tree_of(self.items)

    def list_rows(self):
        """The list as ROWS: every item, with a rule wherever a fence sits.

        A fence is DRAWN and not selectable. Everything this screen does to a row —
        run it, answer it, move it, stop its chain — needs an item, so a cursor that
        could land on a rule would make every one of those keys begin by asking
        whether it had. `b` toggles the fence above the selected item instead, which
        is the whole of what a fence has to do from here.
        """
        rows, seg = [], 0
        shown = set(self.visible())
        for idx, it in enumerate(self.items):
            while seg < it.get("segment", 0):
                if not self.only_mine:
                    rows.append(("break", it.get("waiting_on")))
                seg += 1
            if idx in shown:
                rows.append(("item", idx))
        return rows

    def reorder(self, step):
        """Move the selected item among ITS SIBLINGS, carrying its subtree.

        Ids sort by when work was SCOPED, which is rarely the order to do it in, so
        the list's own sequence carried no information and the real one was in the
        author's head. This writes `.dfs/order.md` and nothing else — no entry, no
        item file, nothing above §3 (`order.py` says why).

        ⚠️ Up and down stay INSIDE the branch. Moving through the flattened list
        would re-parent an item as a side effect of a key that says nothing about
        parents; `{`/`}` is the only place a parent changes.
        """
        it = self.current()
        if it is None:
            return
        if self.retree(dfs_order.moved(self.nodes(), it["id"], step),
                       it, "is already %s among its siblings"
                       % ("first" if step < 0 else "last")):
            self.said(it, "moved %s" % ("up" if step < 0 else "down"))

    def reparent(self, step):
        """Move the selected item sideways: out of its branch, or into the one above.

        This is the other half of the tree, and the half the walk turns on — a child
        is work that only makes sense under its parent, so putting an item under
        another is a claim that a question the parent cannot answer is one this item
        cannot either (`dfs_state.walk`).
        """
        it = self.current()
        if it is None:
            return
        after = (dfs_order.outdented(self.nodes(), it["id"]) if step < 0
                 else dfs_order.indented(self.nodes(), it["id"]))
        # ⚠️ "in this section": with no sibling above, and with a BREAK above, the
        # refusal is the same key doing nothing for two reasons — nesting under a
        # fence is not a shape, and reaching past one would move this work across it.
        refusal = ("is already at the root" if step < 0
                   else "has no sibling above it in this section to go under")
        if self.retree(after, it, refusal):
            now = next((i for i in self.items if i["id"] == it["id"]), None)
            parent = now["parent"] if now else None
            self.said(it, ("moved under %s" % parent) if parent
                          else "moved to the root")

    def toggle_break(self):
        """Put a fence above the selected item, or take away the one that is there.

        The third thing an author knows about their own plan, after "this comes
        before that" and "this only makes sense under that": **nothing after here is
        worth starting until all of that is finished**. Nesting cannot say it — it is
        a claim about every branch above at once, and forcing the roadmap into one
        spine to express it would make every RAISE stall the whole tree, which is the
        stall the depth-first move exists to avoid.
        """
        it = self.current()
        if it is None:
            return
        nodes = self.nodes()
        had = dfs_order.has_break_above(nodes, it["id"])
        refusal = ("is nested — a break separates whole branches, never cuts one"
                   if it["depth"] else
                   "is the first item — a break above it would gate nothing")
        after = dfs_order.break_toggled(nodes, it["id"])
        # ⚠️ ADDING ONE ASKS; taking one away does not. A fence gates everything after
        # it on everything before, and `b` sits among the keys a hand finds by
        # accident. A refusal (nested, first) says so without asking first.
        if after is not None and not had:
            if self.prompt("put a break above %s — everything after waits on "
                           "everything above? [y/N]:" % it["id"])[:1].lower() != "y":
                self.msg = "no break added"
                return
        if self.retree(after, it, refusal):
            self.said(it, "is no longer behind a break" if had
                          else "now waits on everything above the break")

    def said(self, it, did):
        self.event("order", "%s %s" % (it["id"], did))
        self.msg = "%s %s — %s is uncommitted · z undoes" % (
            it["id"], did, dfs_paths.rel(dfs_paths.order()))

    def retree(self, after, it, refusal):
        """Write a moved tree, keep the cursor on the ITEM, and say what happened.

        ⚠️ THE CURSOR FOLLOWS THE ITEM, NOT THE ROW. Leaving `sel` where it was would
        select whichever item slid into that row, so pressing the key twice would
        move two different items and look like one that would not stay put — the same
        row-versus-identity confusion the app's own rows are keyed against. A move
        that carries a subtree can shift the cursor by more than one row, which is
        what makes this load-bearing rather than tidy.
        """
        if after is None:
            self.msg = "%s %s" % (it["id"], refusal)
            return False
        if self.only_mine:
            # The rows around it are hidden, so "up one" would be up past items
            # nobody can see: a move is made on the whole tree or not at all.
            self.msg = "the list is filtered to what needs you — m shows it all to move"
            return False
        before = read_text_or_none(dfs_paths.order())
        dfs_order.write_order(after)
        if not isinstance(self.order_undo, list):
            self.order_undo = []
        self.order_undo.append((before, read_text_or_none(dfs_paths.order()), it["id"]))
        self.reload()
        self.sel = next((n for n, i in enumerate(self.items) if i["id"] == it["id"]),
                        self.sel)
        self.scroll = 0
        return True

    def undo_order(self):
        """`z`: put `order.md` back as it was before the last move made from here.

        ⚠️ ONLY IF NOBODY ELSE HAS WRITTEN IT SINCE. The file is the author's plan
        and several sessions read it; restoring an old copy over somebody's newer
        edit would be a move nobody made. So the file must still be exactly what
        this screen wrote, or the undo refuses and says why."""
        if not self.order_undo:
            self.msg = "nothing to undo — z undoes the moves made from this screen"
            return
        before, wrote, iid = self.order_undo[-1]
        path = dfs_paths.order()
        if read_text_or_none(path) != wrote:
            self.order_undo = []
            self.msg = "%s has changed since that move — not undone" % dfs_paths.rel(path)
            return
        self.order_undo.pop()
        if before is None:
            path.unlink()
        else:
            path.write_text(before)
        self.event("order", "undid the last move of %s" % iid)
        self.reload()
        self.msg = "undid the last move of %s" % iid

    def reload(self, note=""):
        # What each cursor is ON, asked before the lists move under it — `reselect`.
        was = self.current()
        was_node = self.tree_row() if self.tree_focused() else None
        was_run = (self.runs[self.run_sel]["path"]
                   if 0 <= self.run_sel < len(self.runs) else None)
        self.data = load()
        self.dirty = roadmap_dirty()
        self.chains = dfs_runs.run_dirs()
        self.runs = discover_runs()
        self.sig = watch_signature()
        self.sel = reselect([i["id"] for i in self.items],
                            was["id"] if was else None, self.sel)
        self.run_sel = reselect([r["path"] for r in self.runs], was_run, self.run_sel)
        if was_node is not None and was and self.current() is not None \
                and self.current()["id"] == was["id"]:
            self.tree_sel = reselect([r["key"] for r in self.tree_rows()],
                                     was_node["key"], self.tree_sel)
        # The open run holds its chain's state, so it is re-bound rather than kept:
        # an entry captured while the chain was live goes on saying `live` for the
        # rest of the session, and a pane that is watching a finished chain must say
        # so — it is the one place the end is visible if the message was missed.
        if self.open_run:
            self.open_run = next((r for r in self.runs
                                  if r["path"] == self.open_run["path"]),
                                 self.open_run)
        if note:
            self.msg = note

    # ---- activity ------------------------------------------------------------

    def event(self, kind, text):
        """Record something this screen or its walk DID, and return the text.

        ⚠️ The message line is not a record: the next poll writes "refreshed" over
        it, and the walk makes its decisions a tick at a time with nobody looking.
        Every runner command started, every order written, every walk decision and
        every chain that ended lands here too, and in `activity.log` beside the run
        directories, so the answer to "what happened while I was away" is on the
        screen rather than reconstructed from consoles."""
        now = time.time()
        if not isinstance(self.events, collections.deque):
            self.events = collections.deque(maxlen=ACTIVITY_KEEP)
        self.events.append((now, kind, text))
        if self.real:
            try:
                path = os.path.join(dfs_runs.ensure_run_root(), "activity.log")
                with open(path, "a") as fh:
                    fh.write("%.0f\t%s\t%s\n" % (now, kind, text.replace("\n", " ")))
            except OSError:
                pass
        return text

    def notify(self, text):
        """Say something happened to somebody not looking: the terminal's bell, which
        tmux and most terminals turn into a mark on the tab rather than a sound, and
        with DFS_NOTIFY=desktop a desktop notification too. DFS_NOTIFY=off for none.
        For the ends the walk exists to bring back — a chain ending, a walk stopping —
        and nothing else."""
        how = os.environ.get("DFS_NOTIFY", "bell").lower()
        if not self.real or how == "off":
            return
        try:
            curses.beep()
        except curses.error:
            pass
        if how == "desktop":
            msg = printable(text).replace(";", ",")[:200]
            try:
                with open("/dev/tty", "w") as tty:
                    tty.write("\x1b]777;notify;dfs;%s\x07\x1b]9;%s\x07" % (msg, msg))
            except OSError:
                pass

    GLYPH = {"run": ("$", "accent"), "walk": ("●", "red"), "stop": ("○", "chrome"),
             "order": ("↕", "chrome"), "review": ("✎", "accent"),
             "done": ("■", "green"), "died": ("■", "red"), "raise": ("●", "red"),
             "trees": ("+", "chrome"), "hold": ("◔", "amber")}

    def activity_lines(self, width):
        """The log as rows, oldest first: time in grey, a glyph for the kind, the text
        wrapped under itself. A day boundary gets its date, so `09:14` is not read as
        this morning when it was yesterday's."""
        out, day = [], None
        for when, kind, text in self.events:
            t = time.localtime(when)
            d = time.strftime("%a %d %b", t)
            if d != day:
                day = d
                out.append((d, look("chrome") | curses.A_BOLD))
            glyph, colour = self.GLYPH.get(kind, ("·", "chrome"))
            if kind == "run" and text.startswith("$ "):
                text = text[2:]             # the glyph IS the prompt
            attr = {"red": curses.color_pair(1), "green": curses.color_pair(2)}.get(
                colour) or look(colour)
            head = "%s %s " % (time.strftime("%H:%M", t), glyph)
            lines = textwrap.wrap(text, max(10, width - len(head))) or [""]
            out.append((head + lines[0], attr if kind in ("raise", "died") else 0))
            for more in lines[1:]:
                out.append((" " * len(head) + more, 0))
        if not out:
            out.append(("nothing yet — runs, walk decisions and order moves land here",
                        look("chrome")))
        return out

    def lines_activity(self, width):
        out, para = self.wrapper(width)
        m = self.data.get("metrics") or {}
        if m:
            out.append(("counts, over every tree", curses.A_BOLD))
            para("   ".join((
                "open raises %d" % m.get("open_raises", 0),
                "nodes %d" % m.get("nodes", 0),
                "confirmed %d" % m.get("confirmed", 0),
                "refuted %d" % m.get("refuted", 0),
                "parked %d" % m.get("parked", 0), "pruned %d" % m.get("pruned", 0),
                "critic %d ok %d issues" % (m.get("critic_ok", 0),
                                            m.get("critic_issues", 0)),
                "review %d ok %d issues" % (m.get("review_ok", 0),
                                            m.get("review_issues", 0)),
                "corrected %d" % m.get("corrections", 0),
                "sessions %d" % m.get("work_sessions", 0),
                "~%.0fk tokens" % (m.get("tokens", 0) / 1000.0))), look("chrome"))
            out.append(("", 0))
        out.append(("activity — what this screen and its walk did, newest last",
                    curses.A_BOLD))
        out.append(("", 0))
        return out + self.activity_lines(width - 2)

    def chain_ended(self, r):
        """A chain stopped: say so, record it, and ring for it."""
        note = self.ended_note(r)
        self.event("done" if str(r.get("rc")) == "0" else "died", note)
        self.notify(note)
        return note

    def ended_note(self, r):
        """What to say when a chain you were not watching stops.

        The whole point of backgrounding is not sitting in front of it, so the end
        has to come and find you. `rc` is the runner's own exit code: 0 is a chain
        that stopped on a FACT about the roadmap, anything else stopped on a
        problem, and no rc at all means it never reached its own last line.
        """
        item = next((i for i in self.items if i["id"] == r["item"]), None)
        return "%s's chain ended (%s) — %s" % (
            r["item"], ("rc %s" % r["rc"]) if r["rc"] else "died without finishing",
            item["why"] if item else "read it with [l]")

    def agent_update_tick(self):
        """Report the background claude-code update once, when it ends. Silent when
        nothing moved: the screen is for news, and "still the same" is not."""
        proc = self.agent_update
        if proc is None or proc.poll() is None:
            return
        self.agent_update = None
        out, err = proc.communicate()
        if proc.returncode:
            self.msg = (err.strip().splitlines() or ["claude-code update failed"])[-1]
        elif "unchanged" not in out:
            self.msg = "agent updated: %s (from the next c)" % out.strip()

    def poll(self):
        """Auto-refresh. Cheap enough to do every second, so there is no refresh key."""
        self.agent_update_tick()
        self.drain_commands()
        self.walk_tick()
        if self.chat_host is not None and CHAT_IDLE > 0:
            self.chat_host.reap_idle(CHAT_IDLE)
        sig = watch_signature()
        was_live = {r["dir"] for r in self.chains if r["live"]}
        if sig == self.sig:
            if not was_live:
                return
            # ⚠️ A CHAIN KILLED OUTRIGHT WRITES NOTHING, so the file signature says
            # the same as it did a second ago and a screen that trusted it would
            # show `● live` for ever — the silent-staleness shape this repo keeps
            # writing down. While anything is running, liveness is re-derived every
            # tick: a handful of /proc reads, against a whole roadmap re-parse,
            # which is why this branch does not simply call reload().
            self.refresh_chains()
            ended = [r for r in self.chains if r["dir"] in was_live and not r["live"]]
            if ended:
                self.reload()
                self.msg = self.chain_ended(ended[0])
            return
        before = self.data["metrics"]["entries"]
        self.reload()
        after = self.data["metrics"]["entries"]
        ended = [r for r in self.chains
                 if r["dir"] in was_live and not r["live"]]
        if ended:
            self.msg = self.chain_ended(ended[0])
        elif after > before:
            # Worth saying out loud: in this tree it is usually a peer session, and
            # an entry appearing under you changes what is safe to start.
            self.msg = "the trees moved: +%d" % (after - before)
            self.event("trees", "the trees moved: +%d entr%s" % (
                after - before, "y" if after - before == 1 else "ies"))
        else:
            self.msg = "refreshed"

    # ---- drawing -------------------------------------------------------------

    def put(self, y, x, text, attr=0):
        """addstr that clips rather than raising at the last cell of the screen.

        Everything drawn passes through `printable` first, here, because this is the
        one place every pane's text meets the terminal: see that function for what a
        stray ESC did to a row.
        """
        h, w = self.scr.getmaxyx()
        # The right edge is the COLUMN's while one is being drawn (`clip`): the
        # main column must not write into the activity column beside it.
        edge = min(w - 1, getattr(self, "clip", None) or w - 1)
        if y < 0 or y >= h or x >= edge:
            return
        text = printable(text)
        if MARKS.search(text):
            return self.put_marked(y, x, text, attr, edge)
        text = text[: max(0, edge - x)]
        try:
            self.scr.addstr(y, x, text, attr)
        except curses.error:
            pass

    def put_marked(self, y, x, text, attr, edge):
        """A line with span marks, drawn as runs: bold adds A_BOLD, code takes the accent."""
        run = attr
        for part in re.split("([\ue000-\ue003])", text):
            if part == BOLD_ON:
                run = attr | curses.A_BOLD
            elif part == CODE_ON:
                run = (attr & ~curses.A_COLOR) | code_attr()
            elif part in (BOLD_OFF, CODE_OFF):
                run = attr
            elif part and x < edge:
                part = part[: edge - x]
                try:
                    self.scr.addstr(y, x, part, run)
                except curses.error:
                    pass
                x += len(part)

    def puts(self, y, x, parts):
        """Several (text, attr) runs on one row, left to right; returns where it ended."""
        for text, attr in parts:
            self.put(y, x, text, attr)
            x += len(text)
        return x

    def main_w(self):
        """The main column's width: the screen, less the activity column when wide."""
        return getattr(self, "clip", None) or self.scr.getmaxyx()[1]

    def status_attr(self, status):
        # `open` is most of the list, so it is the plain one: colour is for the
        # states that ask something of you (blocked) or are over (done).
        return {"blocked": curses.color_pair(1),
                "done": curses.color_pair(2)}.get(status, 0)

    def header(self):
        h, w = self.scr.getmaxyx()
        agent = self.agent
        live = len(self.live())
        # What a depth-first walk would take next, which is the whole point of the
        # tree and is otherwise only derivable by reading every row's blocked_by.
        nxt = self.walk_target()
        # ⚠️ The walk goes in the HEADER, not the message line: a message is the last
        # thing that happened and scrolls away, while "something is spending money
        # unattended right now" is a state, and one nobody should have to remember
        # they left on.
        walk = self.walk_badge()
        dirty, staged = self.dirty
        warn = ""
        if staged:
            warn = "%d staged " % staged
        elif dirty:
            warn = "%d uncommitted " % dirty
        # The page this screen serves, as its port: enough to find it (the full address
        # is the first line of `h`), short enough that it is the first thing to yield.
        web = ("web :%s  " % self.web.url.rsplit(":", 1)[1].strip("/")) if self.web else ""
        right = warn + web + ("%s " % agent)
        # ⚠️ Still ONE LINE THAT CHANGES SHAPE when the walk is on: the badge goes
        # first, in capitals, and red only while it is spending (see `walk_badge`).
        # The bar under it is gone — a slab of reverse video was the loudest thing
        # on the screen and said nothing — so the words carry it: the name in bold,
        # labels grey, the values in the terminal's own colour.
        x = 1
        if walk:
            x = self.puts(0, x, [(walk, (curses.color_pair(1) | curses.A_BOLD)
                                  if self.walk_on else look("chrome")),
                                 ("   ", 0)])
        grey = look("chrome")
        # ⚠️ ONE LINE, AND ONLY WHAT ASKS SOMETHING OF YOU OR SAYS WHERE THINGS ARE.
        # There was a second line of cumulative counts — nodes, confirmed, refuted,
        # parked, pruned, critic, review, sessions, tokens — kept for an experiment
        # (the old roadmap's §0.6) that ended with it on 2026-09-25. Totals that only
        # grow are read by nobody at a glance; the one that asked for action (open
        # raises, standing assumptions) is `need you` here, the same number `m`
        # filters to. The rest are at the top of `h`, and in metrics.py.
        mine = sum(1 for it in self.items if self.needs_you(it))
        # ⚠️ The header leads with the PROJECT'S name, in its colour (`.dfs/theme`), not
        # the word "roadmap": with two or three terminals open on different projects,
        # that word was the same in all of them.
        name = dfs_paths.project_title()
        if len(name) > 24:
            name = name[:23] + "…"
        groups = {"tasks": [(str(len(self.items)), 0), (" tasks", grey)],
                  "next": [("next ", grey), (nxt or "—", look("accent") if nxt else grey)],
                  "running": [(str(live), curses.color_pair(2)), (" running", grey)],
                  "mine": [(str(mine), look("amber")), (" need you", grey)]}
        order = (["tasks", "next"] + (["running"] if live else [])
                 + (["mine"] if mine else []))
        # The left yields to the right, a WHOLE segment at a time — `next W` is worse
        # than no `next` — and the least useful first: "uncommitted" and the agent
        # change what a key will do, the task count does not.
        room = w - len(right) - 2 - x
        for drop in ("tasks", "running", "next", "mine"):
            width = len(name) + sum(3 + sum(len(t) for t, _ in groups[g])
                                         for g in order)
            if width <= room or drop not in order:
                continue
            order.remove(drop)
        parts = [(name, project_attr(dfs_paths.project_colour(), LIGHT))]
        for g in order:
            parts += [("   ", 0)] + groups[g]
        self.clip = max(x + 8, w - len(right) - 2)
        self.puts(0, x, parts)
        self.clip = None
        if warn:
            self.put(0, max(0, w - len(right) - 1), warn, look("amber"))
        if web:
            self.put(0, max(0, w - len(agent) - 2 - len(web)), web, look("chrome"))
        self.put(0, max(0, w - len(agent) - 2), agent, look("chrome"))

    def draw_list(self, top, height):
        """The item list, windowed so the selection is always on it.

        The list was drawn from item 0 and cut at `height`, which on a short terminal
        means the cursor walks off the bottom and every further press moves something
        invisible — the same complaint as a key that does nothing, and the reason the
        run selector already carries this (see `draw`).
        """
        # ⚠️ The window is over ROWS, and a rule is a row. Scrolling by item index
        # while drawing rules between them puts the cursor off the bottom by one row
        # per fence above it — the same complaint the windowing was added for.
        rows = self.list_rows()
        sel_row = next((n for n, (kind, val) in enumerate(rows)
                        if kind == "item" and val == self.sel), 0)
        if sel_row < self.list_scroll:
            self.list_scroll = sel_row
        elif sel_row >= self.list_scroll + height:
            self.list_scroll = sel_row - height + 1
        self.list_scroll = max(0, min(self.list_scroll, max(0, len(rows) - height)))
        # ⚠️ The run column is only there when something is running. Reserving it
        # always would cost every item's goal 14 columns for a state that is empty
        # most of the time; a column that appears is also its own signal.
        tags = {it["id"]: self.run_tag(it["id"]) for it in self.items}
        tagw = max([len(t) for t, _ in tags.values()] + [0])
        tagw = tagw + 2 if tagw else 0
        # The id column carries the TREE, so it is as wide as the deepest branch and
        # no wider — a fixed indent would spend the goal's columns on nesting that
        # may not exist. Every later column is measured off it rather than spelled,
        # which is what a hand-written offset got wrong the moment depth appeared.
        idw = max([len(it["id"]) + 2 * it["depth"] for it in self.items] + [4])
        statw = 8
        # Sized to the widest count and absent when there are none, exactly as the run
        # column above is: a roadmap with nothing outstanding should not pay columns
        # for saying so.
        revs = {it["id"]: review_cell(it) for it in self.items}
        revw = max([len(r) for r in revs.values()] + [0])
        revw = revw + 2 if revw else 0
        aheads = {it["id"]: ahead_cell(it) for it in self.items}
        aheadw = max([len(a) for a in aheads.values()] + [0])
        aheadw = aheadw + 2 if aheadw else 0
        for i in range(min(height, len(rows) - self.list_scroll)):
            kind, val = rows[self.list_scroll + i]
            y = top + i
            if kind == "break":
                # ⚠️ The rule SAYS what it is holding. A bare line reads as decoration
                # and a decoration that silently stops the walk is the worst of both:
                # `waiting on W12` is the item to go and finish, and `above is done`
                # is a fence that is no longer in anybody's way.
                label = ("── break · waiting on %s " % val) if val \
                        else "── break · above is done "
                w = self.main_w() - 1
                self.put(y, 2, (label + "─" * max(0, w - 2 - len(label)))[:max(0, w - 2)],
                         look("chrome"))
                continue
            idx = val
            it = self.items[idx]
            marker = ">" if idx == self.sel else " "
            if idx == self.sel:
                self._list_y = y
            attr = ((curses.A_REVERSE if self.focus != "tree" or self.pane != "item"
                     else curses.A_BOLD) if idx == self.sel else 0)
            tag, tag_attr = tags[it["id"]]
            # ⚠️ The STATUS cell is the item's own fact; where it SITS is a second
            # one. An item whose branch an ancestor is holding reads "↳W10" — the
            # question to go and answer — because "open" would be true and useless.
            state = row_state(it)
            if state == "done":
                state = "✓ done"
            rev = revs[it["id"]]
            cell = ("  " * it["depth"]) + it["id"]
            ahead = aheads[it["id"]]
            line = "%s %s %s %s%s%s%s" % (marker, cell.ljust(idw), state.ljust(statw),
                                          rev.ljust(revw), ahead.ljust(aheadw),
                                          tag.ljust(tagw), it["goal"])
            band = attr & curses.A_REVERSE
            # ⚠️ A DONE row recedes whole (id, goal and all), not just its status
            # word: that one word's colour was the only thing telling a finished
            # task from an open one, and it is lost in a list of forty. The cursor
            # row keeps its band so it is still the one that is found.
            if it["status"] == "done" and not band:
                attr = look("chrome")
            self.put(y, 0, line.ljust(self.main_w() - 1), selected(attr) if band else attr)
            if band and LOOK.get("band"):
                self.put(y, 0, BAR, look("selbar"))
            if idx != self.sel:
                # ⚠️ `3 + idw`, not `2 + idw`: the line is marker, space, cell, SPACE,
                # state, so the colour re-put has to clear the separator too or it
                # lands a column left of where the format put it — which showed as
                # the selected row's status sitting one column right of every other
                # row's, the hand-written offset the id column above is measured to
                # avoid. The tag is the same distance further on.
                st_at = (look("chrome") if state not in (it["status"], "✓ done")
                         else self.status_attr(it["status"]))
                self.put(y, 3 + idw, state.ljust(statw), st_at)
                # ⚠️ Every later column is measured off the one before it, never
                # spelled: the review cell is between the status and the run tag, so
                # the tag's offset moves with it. A hand-written offset here is what
                # put the selected row's status one column right of every other row's.
                if rev:
                    self.put(y, 3 + idw + statw, rev.ljust(revw), look("amber"))
                if ahead:
                    self.put(y, 4 + idw + statw + revw, ahead.ljust(aheadw), look("chrome"))
                if tag:
                    self.put(y, 4 + idw + statw + revw + aheadw, tag, tag_attr)

    def wrapper(self, width):
        w = max(20, width - 2)
        out = []

        def para(text, attr=0, indent="", hang=None, rich=False):
            """Wrap, with the continuation lines visibly continuations.

            A wrapped `Options:` field whose second line starts at the same column
            as the first reads as a second field, which is the one thing these
            entries must not do — §0.9's field names are what makes the log
            greppable and what the eye uses to find `Against that` before
            approving anything.
            """
            body = indent if hang is None else indent + hang
            first = len(out)
            for line in textwrap.wrap(mark_up(text) if rich else text, w - len(indent),
                                      initial_indent=indent,
                                      subsequent_indent=body) or [indent]:
                out.append((line, attr))
            if rich:
                out[first:] = carry_marks(out[first:])
        return out, para

    def card_lines(self, width, blocks, indent=""):
        """`node_card`'s blocks as wrapped, styled lines: the node as the panel at the
        right draws it and as a node opened in place draws it."""
        out, para = self.wrapper(width)
        chrome, red, green = look("chrome"), curses.color_pair(1), curses.color_pair(2)
        attrs = dict(head=curses.A_BOLD, meta=chrome, label=chrome, text=0,
                     amber=look("amber"), plus=green, minus=red,
                     red=red | curses.A_BOLD, dim=chrome)
        for role, text in blocks:
            if role == "label":
                if out and out[-1][0].strip():
                    out.append(("", 0))
                out.append((indent + text, chrome | curses.A_BOLD))
                continue
            for line in (text or "").splitlines() or [""]:
                para(line, attrs.get(role, 0),
                     indent=indent + ("" if role in ("head", "meta") else "  "),
                     hang="  " if role in ("plus", "minus") else "", rich=True)
        return out

    def log_wrap(self, width, text, attr, hang="      "):
        """Wrap one log line, keeping its marker column and hanging its continuations.

        ⚠️ Long lines were CLIPPED here, not wrapped — a tool_use command, an agent
        sentence and a runner line all ran off the right edge and the rest was gone
        with nothing to say so. Clipping is the worst of the three options: wrapping
        costs rows, a visible size marker costs a few columns, and a silent cut reads
        as a complete short line, which is how a reader is misled rather than merely
        inconvenienced.

        Continuations are INDENTED PAST THE MARKER for the same reason `para` hangs
        §0.9's fields: a wrapped `  → Bash  <command>` whose second row starts in the
        marker column reads as a second tool call, and the point of this pane is to
        count what the agent did.
        """
        w = max(20, width - 2)
        lead = text[:len(text) - len(text.lstrip())]
        body = text.strip()
        if not body:
            return [("", attr)]
        return [(ln, attr) for ln in textwrap.wrap(
            body, w, initial_indent=lead, subsequent_indent=lead + hang,
            break_long_words=True, break_on_hyphens=False,
            drop_whitespace=True, replace_whitespace=True) or [text[:w]]]

    def lines_todo(self, width):
        out, para = self.wrapper(width)
        out.append(("epic todo — %s" % dfs_paths.rel(EPIC), curses.A_BOLD))
        out.append(("", 0))
        for done, text in epic_todo():
            para(("[x] " if done else "[ ] ") + text,
                 look("chrome") if done else 0, hang="    ")
        return out

    def lines_reglog(self, width, it):
        """The task's Log: every session, raise, answer, verdict and correction."""
        out, para = self.wrapper(width)
        out.append(("log · %s" % it["id"], curses.A_BOLD))
        out.append(("", 0))
        mine = [e for e in self.data["entries"]
                if e["target"] == it["id"] and e["type"] != "NODE"]
        answered = {e["disp"].split(" · ")[0] for e in mine if e["type"] == "ANSWER"}
        for e in mine:
            head = "%s · %s%s" % (e["id"], e["type"].lower(),
                                  (" · " + e["disp"]) if e["disp"] else "")
            attr = curses.A_BOLD
            if e["type"] == "RAISE" and e["id"] not in answered:
                head += "   ← open, needs you"
                attr = curses.color_pair(1) | curses.A_BOLD
            elif e["type"] in ("SESSION", "END"):
                attr = look("chrome")
            out.append((head, attr))
            for line in e["body"].splitlines():
                para(line, look("chrome"), indent="  ", hang="    ")
        if len(out) == 2:
            out.append(("  nothing in this task's log yet", look("chrome")))
        return out

    def artefacts(self):
        """What the artefact pane offers, for the item you are on.

        Two groups, and the second is why the pane is worth opening on an item whose
        raises name nothing: the ones this item's entries point at, then everything
        else in the directory — which is where the two standing maps live, read
        whenever one of their epic's items is picked rather than once and then dead.
        """
        it = self.current()
        named = artefacts_named_by(self.data["entries"], it["id"]) if it else []
        on_disk = sorted(p.name for p in ART_DIR.glob("*.html")
                         if not p.name.startswith("_"))
        taken = {n for n, _ in named}
        out = [dict(name=n, entry=eid, exists=(ART_DIR / n).exists(), mine=True)
               for n, eid in named]
        out += [dict(name=n, entry=None, exists=True, mine=False)
                for n in on_disk if n not in taken]
        return out

    def open_artefact(self, art):
        """Hand it to a real browser, detached — a browser is not a terminal program.

        ⚠️ It does NOT pretend. With no display reachable there is no window to put
        this in, so rather than forking something that fails where nobody sees it, the
        URL goes on the status line: README.md already leans on a terminal making it
        clickable, which is the same thing `--review` relies on when it prints a raise.
        """
        if not art["exists"]:
            self.msg = "%s is named by entry %s but is not in %s" % (
                art["name"], art["entry"], dfs_paths.rel(ART_DIR))
            return
        # This screen's own web server serves them too (sandboxed), so when it is up that
        # is the address: no second `http.server` to start.
        if self.web:
            url = "http://localhost:%s/artefacts/%s" % (
                self.web.url.rsplit(":", 1)[1].strip("/"), art["name"])
        else:
            url = ("http://localhost:%d/%s" % (ART_PORT, art["name"]) if art_server_up()
                   else (ART_DIR / art["name"]).as_uri())
        browser = os.environ.get("BROWSER")
        argv = ([browser, url] if browser else
                next(([b, url] for b in ("xdg-open", "firefox", "chromium")
                      if shutil.which(b)), None))
        headless = not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
        if argv is None or (headless and not browser):
            self.msg = "%s — %s" % (url, "no display from here, so open it yourself"
                                    if argv else "no browser on PATH")
            return
        try:
            subprocess.Popen(argv, cwd=ROOT, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as e:
            self.msg = "%s — could not launch %s: %s" % (url, argv[0], e)
            return
        self.msg = "opened %s in %s" % (art["name"], os.path.basename(argv[0]))

    def asm_rows(self):
        """Every assumption the author has not accepted (an old Ask counts as one), across the tasks:
        `(item index, node)`. The pre-accept job is "show me every open assumption I
        have not looked at", and the only way was to enter each task."""
        out = []
        for idx, it in enumerate(self.items):
            if not it.get("assumptions"):
                continue
            t, _, _ = self.tree_of(it["id"])
            nodes = dfs_tree.by_id(t)
            out += [(idx, nodes[nid]) for nid in it["assumptions"] if nid in nodes]
        return out

    def lines_assumptions(self, width):
        out, para = self.wrapper(width)
        rows = self.asm_rows()
        self.asm_sel = max(0, min(self.asm_sel, len(rows) - 1))
        out.append(("assumptions and asks · what rests on a decision of yours, in every task "
                    "you have not accepted", curses.A_BOLD))
        out.append(("  ⏎ goes to it in its tree · ↑↓/jk moves · esc back", look("chrome")))
        out.append(("", 0))
        self._sel_row = None
        if not rows:
            out.append(("  none: every task is accepted, or none states a hypothesis",
                        look("chrome")))
            return out
        for i, (idx, nd) in enumerate(rows):
            sel = i == self.asm_sel
            if sel:
                self._sel_row = len(out)
            f = nd["fields"]
            out.append(("%s %s  %s" % (">" if sel else " ", nd["id"], nd["title"]),
                        look("amber") | curses.A_BOLD | (curses.A_REVERSE if sel else 0)))
            if f.get("Hypothesis"):
                assumption, wrong = dfs_tree.split_falsifier(f["Hypothesis"])
                para(assumption, look("amber"), indent="    ")
                if wrong:
                    para("wrong if " + wrong, look("amber"), indent="    ")
            if f.get("Ask"):
                para("ask: " + f["Ask"], curses.color_pair(1), indent="    ")
            out.append(("", 0))
        return out

    def lines_artefacts(self, width):
        out, para = self.wrapper(width)
        it = self.current()
        arts = self.artefacts()
        self.art_sel = max(0, min(self.art_sel, len(arts) - 1))
        up = art_server_up()
        out.append(("artefacts · a picture for one raise, opened in a browser",
                    curses.A_BOLD))
        out.append(("  enter opens one · ↑↓/jk moves · esc back", look("chrome")))
        out.append((("  served on %d" % ART_PORT) if up else
                    ("  nothing on %d — python3 -m http.server %d --directory %s"
                     % (ART_PORT, ART_PORT, dfs_paths.rel(ART_DIR))),
                    look("chrome") if up else curses.color_pair(3)))
        out.append(("", 0))
        if not arts:
            out.append(("  no artefact in %s" % dfs_paths.rel(ART_DIR), look("chrome")))
            return out
        self._sel_row = None
        group = None
        for i, a in enumerate(arts):
            head = ("named by %s" % (it["id"] if it else "this item")) if a["mine"] \
                else "everything else on disk"
            if head != group:
                group = head
                out.append(("", 0))
                out.append(("  " + head, look("chrome")))
            title = art_title(ART_DIR / a["name"]) if a["exists"] else "missing"
            # A uuid plus `.html` is 41, and a name cut mid-extension reads as a
            # different file: it is the one column here that must not be truncated.
            label = "%s %-41s %s" % (">" if i == self.art_sel else " ",
                                     a["name"], title)
            if i == self.art_sel:
                self._sel_row = len(out)
            attr = curses.A_REVERSE if i == self.art_sel else 0
            if not a["exists"]:
                attr = attr | curses.color_pair(1)
            out.append((label, attr))
            if a["entry"] is not None:
                out.append(("    named in %s" % a["entry"], look("chrome")))
        return out

    def lines_runs(self, width):
        """The run selector: which agent run to look at. Live chains at the top."""
        out, para = self.wrapper(width)
        live = len(self.live())
        out.append(("agent runs — %d log%s on disk%s" % (
            len(self.runs), "" if len(self.runs) == 1 else "s",
            (", %d chain%s running" % (live, "" if live == 1 else "s")) if live
            else ", newest first"), curses.A_BOLD))
        out.append(("  enter opens one · ↑↓/jk moves · esc back", look("chrome")))
        out.append(("", 0))
        if not self.runs:
            out.append(("  nothing in " + ", ".join(
                "%s/dfs_run_*" % r for r in dfs_runs.run_roots()),
                look("chrome")))
            out.append(("  a console appears here the moment a chain starts", look("chrome")))
            return out
        self._sel_row = None
        for i, r in enumerate(self.runs):
            chain = r["run"]
            when = time.strftime("%d %b %H:%M", time.localtime(r["mtime"]))
            # ⚠️ The marker is on the file that is STILL BEING WRITTEN, not on every
            # file of a live chain. A chain on run 2 has run-1.json beside it, and
            # marking that `● run 2/10` too says the finished session is the one to
            # open — which under `--codex`, where the run log is the live stream, is
            # exactly the choice this list exists to make.
            if is_growing(r):
                state = "● %s" % self.chain_progress(chain)
                attr = curses.color_pair(2)
            elif chain["state"] == "stopped":
                state = "⨯ died"
                attr = curses.color_pair(1)
            else:
                state = ""
                attr = 0
            label = "%s %-6s %-11s %-12s %6s %-11s %s" % (
                ">" if i == self.run_sel else " ",
                r["item"] or "—", when, r["name"],
                "%.0fk" % (r["size"] / 1000.0) if r["size"] >= 1000 else str(r["size"]),
                state, os.path.basename(r["dir"]))
            if i == self.run_sel:
                self._sel_row = len(out)
            out.append((label, curses.A_REVERSE if i == self.run_sel else attr))
        return out

    def lines_console(self, width, run):
        """A chain's own output, as it is being written.

        ⚠️ NOT cached: it is re-read on every draw, because that is what makes it a
        tail rather than a snapshot.

        ⚠️ Wrapped WITH A HANGING INDENT, which is what answers the reason this pane
        used to clip instead. The worry was real — these lines are the runner's own
        columns, and a wrapped `exit 0 · 84 turns · peak context 131072` starting
        again in column zero reads as a second run — but clipping did not avoid that
        failure so much as trade it for a quieter one: the launch line naming the
        agent and its flags is longer than any pane, so the flags a run was actually
        started with were the part that fell off. An indent the runner never emits
        cannot be mistaken for the start of a run, so both readings are safe.
        """
        chain = run["run"]
        out = []
        head = "%s · chain %s" % (run["item"] or "run", chain["state"])
        if chain["live"]:
            head += " · %s · %s" % (self.chain_progress(chain),
                                    dfs_runs.age(chain))
        elif chain["rc"]:
            head += " · rc %s" % chain["rc"]
        out.append((head, curses.A_BOLD))
        out.append((run["path"], look("chrome")))
        if chain["live"]:
            out.append(("the script above, the agent's own turns below · "
                        + ("following · [K] stops the chain" if self.follow
                           else "not following · [G] jumps back to the last line"),
                        look("chrome")))
        out.append(("", 0))
        for line in tail_lines(run["path"]):
            out.extend(self.log_wrap(width, line.replace("\t", "    "), 0))
        if len(out) == 4:
            out.append(("  nothing written yet — the agent is starting", look("chrome")))
        out.extend(self.lines_live_agent(width, chain))
        return out

    def lines_live_agent(self, width, chain):
        """The agent's OWN turns, while it is still taking them.

        ⚠️ The console above this is the SCRIPT talking — `run 2/5`, then the exit
        line — and under Claude the run's captured JSON is one object written at
        EXIT, so without this a live chain showed that a session had started and
        nothing whatever about what it was doing. The transcript is written by the
        provider as the session goes; the id is the one the RUNNER minted, recorded
        in meta.json before launch, which is what makes it readable now rather than
        at the end (`run.sh`, run_session).
        """
        agent = chain["agent"] or "claude"
        if not chain["sid"]:
            if chain["live"]:
                return [("", 0), ("the running session has not named itself yet — a "
                                  "chain started before this was recorded shows its "
                                  "turns only when it ends", look("chrome"))]
            return []
        tp = transcript_path(chain["sid"], agent)
        out = [("", 0)]
        if tp is None or not tp.exists():
            out.append(("no transcript at %s yet" % tp, look("chrome")))
            return out
        peak, responses = self.measured(tp, agent)
        note = ""
        if peak > CHECKPOINT:
            note = "  ← past the %s checkpoint" % fmt_tokens(CHECKPOINT)
        out.append(("agent · session %s · %d responses · peak context %s%s"
                    % (chain["sid"][:8], responses, fmt_tokens(peak), note),
                    curses.color_pair(1) if peak > CHECKPOINT else curses.A_BOLD))
        out.append((str(tp), look("chrome")))
        out.append(("", 0))
        # Bounded: this is re-read on every draw while the session grows.
        out.extend(self.transcript_lines(tp, agent, tail_bytes=256 * 1024,
                                              width=width)[-200:])
        return out

    def lines_agentlog(self, width, run):
        """One run: what the runner captured, then the agent's own turns.

        The captured JSON is the RESULT — the last message plus the usage — and on a
        failed run it is the only thing that says why (a usage limit reads as a
        one-line result and an exit 0). The turns underneath come from the
        provider's transcript, which is where the actual work is.
        """
        key = (run["path"], os.stat(run["path"]).st_mtime_ns, width)
        if key in self._cache:
            return self._cache[key]
        out, para = self.wrapper(width)
        out.append(("%s · %s" % (run["item"] or "run", run["name"]), curses.A_BOLD))
        out.append((run["path"], look("chrome")))
        out.append(("", 0))

        result, events = read_result(run["path"])
        sid, agent = "", run["agent"] or "claude"
        # A stream that is ONE line (a run that died on its first event) parses as a
        # single JSON object, which `read_result` takes for claude's result.
        if result is not None and agent in ("kiro", "opencode"):
            result, events = None, [result]
        if result is not None:
            sid = result.get("session_id", "")
            usage = result.get("usage") or {}
            fields = [
                ("turns", result.get("num_turns")),
                ("cost", ("$%.2f" % result["total_cost_usd"])
                 if isinstance(result.get("total_cost_usd"), (int, float)) else None),
                ("stop", result.get("stop_reason") or result.get("subtype")),
                ("error", result.get("is_error")),
                ("duration", ("%.0fs" % (result["duration_ms"] / 1000.0))
                 if result.get("duration_ms") else None),
                ("cache read", fmt_tokens(usage.get("cache_read_input_tokens"))),
                ("output", fmt_tokens(usage.get("output_tokens"))),
            ]
            out.append((" · ".join("%s %s" % (k, v) for k, v in fields
                                   if v not in (None, "", "0")), 0))
            out.append(("session %s" % (sid or "unknown"), look("chrome")))
            out.append(("", 0))
            out.append(("result", curses.A_BOLD))
            for line in str(result.get("result", "")).splitlines() or [""]:
                para(line, indent="  ", hang="  ")
        elif agent == "opencode":
            # As kiro, below: the log IS the transcript.
            sid = opencode_session_id(events)
            peak, responses = peak_and_turns(run["path"], agent)
            out.append(("session %s · %d responses · peak context %s"
                        % (sid or "unknown", responses, fmt_tokens(peak)), look("chrome")))
            out.append(("", 0))
            out.extend(self.emit_roles(opencode_result_lines(events), width))
            self._cache = {k: v for k, v in self._cache.items() if k[0] != run["path"]}
            self._cache[key] = out
            return out
        elif agent == "kiro":
            # The log IS the transcript, so it is drawn once, here, and measured
            # rather than opened a second time below.
            sid = kiro_session_id(events)
            peak, responses = peak_and_turns(run["path"], agent)
            out.append(("session %s · %d responses · peak context %s"
                        % (sid or "unknown", responses, fmt_tokens(peak)), look("chrome")))
            out.append(("", 0))
            out.extend(self.emit_roles(kiro_result_lines(events), width))
            self._cache = {k: v for k, v in self._cache.items() if k[0] != run["path"]}
            self._cache[key] = out
            return out
        else:
            # codex: a stream of events rather than one object, condensed to the same
            # rows the transcript pane draws instead of dumped back as the JSON it
            # arrived in.
            sid = codex_session_id(events)
            out.append(("session %s" % (sid or "unknown"), look("chrome")))
            out.append(("", 0))
            out.extend(self.emit_roles(codex_result_lines(events), width))

        tp = transcript_path(sid, agent)
        out.append(("", 0))
        if not sid:
            out.append(("no session id in this result, so there is no transcript to "
                        "open — which is itself the finding on a run that died before "
                        "it started", look("chrome")))
        elif tp is None or not tp.exists():
            out.append(("transcript not on this box: %s" % tp, look("chrome")))
        else:
            peak, responses = peak_and_turns(str(tp), agent)
            out.append(("transcript · %s" % tp, curses.A_BOLD))
            out.append(("%d responses · peak context %s%s"
                        % (responses, fmt_tokens(peak),
                           ("  ← past the %s checkpoint" % fmt_tokens(CHECKPOINT))
                           if peak > CHECKPOINT else ""),
                        curses.color_pair(1) if peak > FLAT_TO else 0))
            out.append(("", 0))
            out.extend(self.transcript_lines(tp, agent, width=width))
        # ⚠️ ONE ENTRY PER FILE, not one per (file, mtime, width). The mtime is in the
        # key so a GROWING log re-renders as it grows — which under `--codex` is every
        # draw for the life of a session, the run log being the live stream — so
        # keeping the superseded renders retains a condensed copy of the whole
        # transcript for every second somebody watched it.
        self._cache = {k: v for k, v in self._cache.items() if k[0] != run["path"]}
        self._cache[key] = out
        return out

    @staticmethod
    def jsonl_lines(path, tail_bytes=None):
        """A transcript's records, optionally only its last stretch.

        A LIVE transcript grows for the whole run and is re-read on every draw, so
        the tail is bounded rather than the file being parsed again each second. The
        first line of a byte-offset read is a fragment of a record — dropped, because
        half a JSON object parses as nothing anyway and the alternative is a line of
        garbage at the top of the pane every time.
        """
        try:
            if tail_bytes is None:
                return path.read_text(errors="replace").splitlines()
            size = path.stat().st_size
            with open(path, "rb") as fh:
                if size > tail_bytes:
                    fh.seek(size - tail_bytes)
                raw = fh.read()
            lines = raw.decode("utf8", "replace").splitlines()
            return lines[1:] if size > tail_bytes else lines
        except OSError:
            return []

    def measured(self, path, agent):
        """Peak context and responses, re-measured at most every few seconds.

        ⚠️ It parses the WHOLE transcript — that is what makes it the same number the
        budget is written in (context.py, one definition) — so on a live
        session it is throttled rather than run at the draw rate. A figure a few
        seconds old is what a checkpoint means anyway; a screen that stutters is not.
        """
        now = time.time()
        was = self._measure_cache.get(str(path))
        if was and now - was[0] < 5:
            return was[1], was[2]
        peak, responses = peak_and_turns(str(path), agent)
        self._measure_cache[str(path)] = (now, peak, responses)
        return peak, responses

    def role_attr(self, role):
        """What a row's ROLE looks like, in one place.

        The codex renderers name their rows rather than carrying curses attributes,
        because `curses.color_pair` raises until `initscr` and a module that cannot
        be imported outside a terminal cannot be tested — which is the whole reason
        the Claude renderer beside them has no test. The mapping is the Claude
        pane's, unchanged: what the agent RAN is green, what it SAID is plain, a
        result is dim, and the author's own words are cyan.
        """
        return {"say": 0,
                "you": curses.color_pair(3),
                "meta": look("chrome"),
                "think": look("chrome"),
                "run": curses.color_pair(2),
                "out": look("chrome"),
                "err": curses.color_pair(1),
                "head": curses.A_BOLD,
                "dim": look("chrome")}.get(role, 0)

    def emit_roles(self, rows, width):
        """Named rows to drawn ones, wrapping the ones that carry content.

        The same two cuts the Claude renderer makes: what the agent said and what it
        ran are wrapped in full, and a RESULT stays one row plus its `[N chars]`.
        """
        made = []
        for text, role in rows:
            attr = self.role_attr(role)
            if role in WRAPPED_ROLES and width:
                made.extend(self.log_wrap(width, text, attr))
            else:
                made.append((text, attr))
        return made

    def transcript_lines(self, path, agent="claude", tail_bytes=None, width=None):
        """The agent's turns, condensed to what a reader scans for.

        Full tool results are what make a transcript unreadable — they are the same
        hundreds of modest outputs the epic measured as the real context cost — so a
        result is one line plus its size, and the file is there when the size is the
        interesting part.

        ⚠️ TWO DIFFERENT CUTS, and only one of them is a truncation to regret. What
        the agent SAID and what it RAN are wrapped in full, because they are the
        content this pane exists to show. A tool RESULT stays one row plus `[N
        chars]`, which is the condensation above and not an oversight — wrapping
        results is what made a transcript unreadable in the first place, and the size
        marker already says there is more.
        """
        if agent in ("kiro", "opencode"):
            events = []
            for line in self.jsonl_lines(path, tail_bytes):
                try:
                    events.append(json.loads(line))
                except ValueError:
                    events.append({"_raw": line})
            render = kiro_result_lines if agent == "kiro" else opencode_result_lines
            return self.emit_roles(render(events), width)
        if agent == "codex":
            # A rollout's records are `response_item` and `event_msg`, so the Claude
            # filter below keeps NONE of them — this pane was empty under `--codex`
            # until 2026-09-17, which read as a session that had taken no turns.
            recs = []
            for line in self.jsonl_lines(path, tail_bytes):
                try:
                    recs.append(json.loads(line))
                except ValueError:
                    continue
            return self.emit_roles(codex_transcript_lines(recs), width)
        made = []
        def emit(text, attr):
            made.extend(self.log_wrap(width, text, attr) if width else [(text, attr)])
        for line in self.jsonl_lines(path, tail_bytes):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") not in ("assistant", "user"):
                continue
            msg = d.get("message") or {}
            content = msg.get("content")
            if isinstance(content, str):
                # The harness wraps its own machinery in the user role; it is not
                # something the author typed, so it is dimmed rather than dropped.
                meta = content.lstrip().startswith("<")
                emit(you_row(content) if not meta
                     else "     " + re.sub(r"\s+", " ", content),
                     look("chrome") if meta else curses.color_pair(3))
                continue
            for b in content or []:
                t = b.get("type")
                if t == "text":
                    for ln in (b.get("text") or "").splitlines():
                        if ln.strip():
                            emit("  " + ln, 0)
                elif t == "thinking":
                    n = len(b.get("thinking") or "")
                    if n:
                        made.append(("  ~ thinking, %d chars" % n, look("chrome")))
                elif t == "tool_use":
                    inp = b.get("input") or {}
                    arg = inp.get("command") or inp.get("file_path") or inp.get("pattern") or ""
                    arg = re.sub(r"\s+", " ", str(arg))
                    emit("  → %s  %s" % (b.get("name"), arg),
                         curses.color_pair(2))
                elif t == "tool_result":
                    c = b.get("content")
                    s = c if isinstance(c, str) else json.dumps(c)
                    first = (s or "").strip().splitlines()
                    made.append(("    ← %s%s" % (
                        (first[0][:200] if first else ""),
                        "   [%d chars]" % len(s or "")), look("chrome")))
        return made

    def detail_lines(self, width):
        if self.pane == "todo":
            return self.lines_todo(width)
        if self.pane == "runs":
            return self.lines_runs(width)
        if self.pane == "artefact":
            return self.lines_artefacts(width)
        if self.pane == "keys":
            return self.lines_keys(width)
        if self.pane == "activity":
            return self.lines_activity(width)
        if self.pane == "plan":
            return self.lines_plan(width)
        if self.pane == "assumptions":
            return self.lines_assumptions(width)
        if self.pane == "agentlog" and self.open_run:
            try:
                if self.open_run.get("kind") == "console":
                    return self.lines_console(width, self.open_run)
                return self.lines_agentlog(width, self.open_run)
            except OSError as e:
                return [("that run log is gone: %s" % e, curses.color_pair(1))]
        it = self.current()
        if it is None:
            return [("no items — press [o] to open one", 0)]
        if self.pane == "reglog":
            return self.lines_reglog(width, it)
        # ⚠️ The item pane IS the tree, laid out as the review reads it. It printed an
        # outline with every determination inline, which on a big tree was a wall;
        # the tree rows fold, open one node at a time, and take the review's writes.
        return self.lines_tree(width)

    def tree_of(self, task):
        """`(tree, archive text, commits)` for a task, re-read when either file moves.

        Commits come from `git log`, which is too slow for every draw; a node's commit
        lands with an edit to its task file, so the file's mtime is a fair key."""
        tags = dfs_tree.part_tags(task)
        paths = [dfs_tree.part_path(task, g) for g in tags] + \
                [dfs_tree.archive_part(task, g) for g in tags]
        key = []
        for p in paths:
            try:
                key.append(p.stat().st_mtime_ns)
            except OSError:
                key.append(0)
        hit = self._tree_cache.get(task)
        if hit and hit[0] == key:
            return hit[1:]
        t = dfs_tree.load(task)
        archive = "\n\n".join(p.read_text() for p in paths[len(tags):] if p.exists())
        got = (key, t, archive, dfs_tree.node_commits(task))
        self._tree_cache[task] = got
        return got[1:]

    def tree_rows(self):
        it = self.current()
        if it is None:
            return []
        t, _, _ = self.tree_of(it["id"])
        return tree_entries(t, self.tree_folded, self.tree_flagged)

    def toggle_flagged(self):
        """`p`: the tree as only the nodes that want the author, or all of it. The
        cursor stays on its row when the row survives, else it goes to the top."""
        row = self.tree_row()
        self.tree_flagged = not self.tree_flagged
        keys = [r["key"] for r in self.tree_rows()]
        self.tree_sel = keys.index(row["key"]) if row and row["key"] in keys else 0
        self.msg = ("only ⚑ assumptions and raises — p shows every node"
                    if self.tree_flagged else "every node")

    def tree_jump_flag(self, step):
        """Move the cursor to the next (or previous) row that wants the author: an open
        raise or an assumption (⚑; an old Ask counts). A tree is read for those, and
        with nothing to jump by the only way to find them was to open every node."""
        rows = self.tree_rows()

        def wants(r):
            if r["kind"] == "raise":
                return True
            if r["kind"] != "node":
                return False
            return bool(r["raises"] or r["assumes"] or r["asks"])
        for k in range(1, len(rows) + 1):
            i = (self.tree_sel + step * k) % len(rows)
            if wants(rows[i]):
                self.tree_sel = i
                return
        self.msg = "no raise or ⚑ in this tree"

    def choose(self, label, keys):
        """One KEY out of `keys`, with no ⏎: `""` for any other, which cancels. The
        `[c]onfirmed or [r]efuted:` question looked like a single key and took a line."""
        if self.answers:
            return self.answers.pop(0)[:1].lower()
        self.msg = label
        self.draw()
        self.scr.timeout(-1)
        try:
            ch = self.scr.getch()
        finally:
            self.scr.timeout(POLL_MS)
        self.msg = ""
        got = chr(ch).lower() if 32 <= ch < 127 else ""
        return got if got in keys else ""

    def tree_row(self):
        rows = self.tree_rows()
        return rows[self.tree_sel] if rows and self.tree_sel < len(rows) else None

    # ---- ? / : and the filter ---------------------------------------------------

    def key_place(self):
        """Which part of `KEYS` the screen `?` was pressed from is in."""
        pane, focus = self.keys_from
        if pane == "item":
            return "tree" if focus == "tree" else "items"
        return pane if pane in ("runs", "agentlog", "artefact") else "panes"

    def key_rows(self):
        """`KEYS`, the part for where you were FIRST: the rest is still there below
        it, since most keys work from anywhere and a list of only this pane's would
        hide them."""
        here = self.key_place()
        order = [here] + [p for p, _ in KEY_PLACES if p != here]
        return [k for p in order for k in KEYS if k[0] == p]

    def lines_keys(self, width):
        out, para = self.wrapper(width)
        para("keys — ⏎ presses the one under the cursor, esc goes back; each name "
             "also works at :", curses.A_BOLD)
        titles = dict(KEY_PLACES)
        here = self.key_place()
        rows = self.key_rows()
        self.key_sel = max(0, min(self.key_sel, len(rows) - 1))
        self._sel_row = None
        place = None
        for i, (p, label, _, name, what) in enumerate(rows):
            if p != place:
                place = p
                out.append(("", 0))
                out.append(("%s%s" % (titles[p], "  · where you were" if p == here
                                      else ""), look("chrome")))
            sel = i == self.key_sel
            if sel:
                self._sel_row = len(out)
            # The description WRAPS under itself: on a narrow screen it is the part
            # that would be cut, and it is what this pane is for.
            lead = "%s %-10s %-16s " % (">" if sel else " ", label, name)
            lines = textwrap.wrap(what, max(16, width - len(lead) - 2)) or [""]
            out.append((lead + lines[0], curses.A_REVERSE if sel else 0))
            for more in lines[1:]:
                out.append((" " * len(lead) + more, 0))
        return out

    def key_run(self, row=None):
        """⏎ on `?`, and `:`: put back the pane the key belongs to, then press it.

        The pane is the KEY'S, not simply the one `?` came from: a tree key read off
        the item list is pressed in the tree, which is the only place it means
        anything, rather than landing on the list and saying "tab to the tree"."""
        if row is None:
            rows = self.key_rows()
            if not rows:
                return True
            row = rows[self.key_sel]
        place, code = row[0], row[2]
        pane, focus = self.keys_from
        self.pane, self.focus, self.scroll = pane, focus, 0
        if place == "items":
            self.pane, self.focus = "item", "list"
        elif place == "tree":
            self.open_tree()
        elif place in ("runs", "artefact"):
            self.pane = place
        elif place == "agentlog" and self.open_run is not None:
            self.pane = "agentlog"
        if code in (ord("?"), ord(":")):
            return True
        # Drawn first: a key that prompts would otherwise ask its question over the
        # keys pane, about a screen that is no longer the one it acts on.
        self.draw()
        return self.act(code)

    # :

    def command(self):
        """`:` — any key by its name, and an item to do it to: `:work W7 5`,
        `:review W12`, `:walk 20`, `:pin W9`, `:go W3`.

        ⚠️ NAMES SCALE WHERE LETTERS DO NOT. k9s ran out of letters and grew a `:`;
        this screen was already one key short (`n` could not be "next match"). A
        command is a row of `KEYS` pressed through `key_run`, so there is no second
        implementation of anything: an item id selects that item first, and what is
        left over answers the prompts the key meets (`answers`) — `:walk 20` is `w`
        answered 20, and nothing is ever confirmed on your behalf, since a y/N is
        only answered by a `y` you typed. As you type, the matches are listed above
        the line with what each does, helix's way, and Tab takes the picked one."""
        self.keys_from = (self.pane, self.focus)
        got = self.read_line(":", menu=self.command_menu,
                             hint="tab complete   ⏎ run   esc cancel",
                             complete=self.command_complete)
        if not got:
            return True
        return self.command_run(got)

    def command_run(self, line):
        words = line.split()
        name, args = words[0].lower(), words[1:]
        if name == "go":
            if not args:
                self.msg = ":go wants an item — :go W7"
                return True
            return self.go(args[0]) or True
        row = next((k for k in KEYS if k[3] == name), None)
        if row is None:
            # ⚠️ ONLY A NAME, OR THE START OF EXACTLY ONE, RUNS. `activ` is
            # `activity`. A fuzzy match is for the list above the line, where Tab
            # takes it on sight — run blind, `:reviwe` started `review-terminal`,
            # whose letters happen to hold r-e-v-i-w-e in order.
            hits = self.command_matches(name)
            named = [k for k in KEYS if k[3].startswith(name)]
            if len(named) == 1:
                row = named[0]
            else:
                self.msg = ":%s — no such command%s" % (
                    name, "; did you mean %s?" % ", ".join(k[3] for k in hits[:3])
                    if hits else "; ? lists them")
                return True
        if args and self.item_index(args[0]) is not None:
            self.go(args.pop(0))
        self.answers = list(args)
        try:
            return self.key_run(row)
        finally:
            self.answers = []

    def item_index(self, iid):
        want = iid.upper()
        return next((n for n, i in enumerate(self.items) if i["id"].upper() == want),
                    None)

    def go(self, iid):
        n = self.item_index(iid)
        if n is None:
            self.msg = "no item %s" % iid
            return False
        if self.only_mine and not self.needs_you(self.items[n]):
            self.only_mine = False
        self.sel, self.scroll = n, 0
        self.pane, self.focus = "item", "list"
        return True

    def command_matches(self, typed):
        typed = typed.lower()
        scored = []
        for k in KEYS:
            sc = fuzzy(typed, k[3])
            if sc is None:
                sc = fuzzy(typed, k[4].lower())
                sc = None if sc is None else sc + 1000
            if sc is not None:
                scored.append((sc, k))
        return [k for _, k in sorted(scored, key=lambda e: e[0])]

    def command_menu(self, text):
        """What `:` shows above the line: commands while the name is being typed,
        then items once it has one."""
        grey = look("chrome")
        words = text.split(" ")
        if len(words) == 1:
            hits = [k for k in self.command_matches(words[0])
                    if k[3] not in ("move", "half-page", "page", "top", "back",
                                    "command")] if words[0] else \
                [k for k in KEYS if k[0] == self.key_place()]
            rows = [("%-16s %-8s %s" % (k[3], k[1].split()[0], k[4]), 0) for k in hits[:8]]
            return rows, "commands"
        scored = []
        for it in self.items:
            sc = fuzzy(words[1].lower(), ("%s %s" % (it["id"], it["goal"])).lower())
            if sc is not None or not words[1]:
                scored.append((sc or 0, it))
        rows = [("%-6s %-8s %s" % (it["id"], row_state(it), it["goal"]), 0)
                for _, it in sorted(scored, key=lambda e: e[0])[:8]]
        return rows or [("no item matches", grey)], "items"

    def command_complete(self, text, row):
        if row is None:
            return text
        head = row[0].split()[0]
        words = text.split(" ")
        if len(words) == 1:
            return head + " "
        return " ".join(words[:1] + [head] + words[2:]).rstrip() + " "

    # /

    def cursors(self):
        return dict(sel=self.sel, tree_sel=self.tree_sel, run_sel=self.run_sel,
                    key_sel=self.key_sel, art_sel=self.art_sel, scroll=self.scroll)

    def search_space(self):
        """What `/` looks through here, where the cursor is in it, and how to land on
        a match: the rows a pane's cursor walks, or the text of a pane without one."""
        if self.pane == "keys":
            rows = self.key_rows()
            return ([" ".join((k[1], k[3], k[4])) for k in rows], self.key_sel,
                    lambda i: setattr(self, "key_sel", i))
        if self.pane == "runs":
            return (["%s %s %s" % (r["item"], r["name"], os.path.basename(r["dir"]))
                     for r in self.runs], self.run_sel,
                    lambda i: setattr(self, "run_sel", i))
        if self.pane == "artefact":
            return ([a["name"] for a in self.artefacts()], self.art_sel,
                    lambda i: setattr(self, "art_sel", i))
        if self.tree_focused():
            def text(r):
                if r["kind"] == "node":
                    return "%s %s" % (r["node"]["id"], r["node"]["title"])
                if r["kind"] == "raise":
                    return "raise " + r["raise_"]["body"]
                return "%s %s" % (r["name"], r.get("text", ""))
            return ([text(r) for r in self.tree_rows()], self.tree_sel,
                    lambda i: setattr(self, "tree_sel", i))
        if self.pane == "item":
            shown = self.visible()

            def land(i):
                self.sel, self.scroll = shown[i], 0
            return (["%s %s %s" % (self.items[n]["id"], self.items[n]["goal"],
                                   self.items[n].get("why", "")) for n in shown],
                    shown.index(self.sel) if self.sel in shown else 0, land)
        # A pane with no cursor: the match's line goes to the top, and reading
        # somewhere is not following the end.
        lines = self.detail_lines(self.main_w())

        def scroll_to(i):
            self.follow = False
            self.scroll = i
        return [ln[0] for ln in lines], self.scroll, scroll_to

    def search(self, needle, here=False):
        """Land on the next match; with `here`, the current row may be it — which is
        what typing does, since the row you are on can already match."""
        texts, cur, land = self.search_space()
        i, wrapped = find_next(texts, needle, cur - 1 if here else cur)
        n = sum(1 for t in texts if find_next([t], needle, -1)[0] is not None)
        if i is None:
            self.msg = "/%s — not here" % needle
            return 0
        land(i)
        self.msg = "/%s — %d match%s%s" % (needle, n, "" if n == 1 else "es",
                                           ", from the top again" if wrapped else "")
        return n

    def search_typed(self):
        """`/`: the cursor moves to the first match AS YOU TYPE, the count is on the
        line, ⏎ keeps it and Esc puts the cursor back where it was. An empty ⏎ is
        the next match of the last search."""
        origin = self.cursors()

        def put_back():
            for k, v in origin.items():
                setattr(self, k, v)

        def change(text):
            put_back()
            if self.input is None:
                return
            if not text:
                self.input["hint"] = None
                return
            n = self.search(text, here=True)
            self.input["hint"] = ("%d match%s   ⏎ keep   esc back" % (
                n, "" if n == 1 else "es")) if n else "no match   esc back"
        hint = ("⏎ next %s   esc back" % self.last_search) if self.last_search else None
        got = self.read_line("/", on_change=change, hint=hint)
        if got is None:
            put_back()
            self.msg = ""
        elif got:
            self.last_search = got
        elif self.last_search:
            self.search(self.last_search)

    # m

    def needs_you(self, it):
        """What `m` keeps: an item with a question for the author — its own raise, a
        standing assumption to keep or redirect, a correction not yet carried out."""
        return bool(it["status"] == "blocked" or it.get("open_raises")
                    or it.get("standing") or it.get("corrections"))

    def visible(self):
        """Indices of the items the list shows. The selected one always, so turning
        the filter on, or an answer landing, never leaves the cursor on nothing."""
        if not self.only_mine:
            return list(range(len(self.items)))
        return [n for n, it in enumerate(self.items)
                if self.needs_you(it) or n == self.sel]

    def toggle_mine(self):
        self.only_mine = not self.only_mine
        if self.only_mine:
            mine = [n for n, it in enumerate(self.items) if self.needs_you(it)]
            if not mine:
                self.only_mine = False
                self.msg = "nothing is waiting on you"
                return
            if self.sel not in mine:
                self.sel = mine[0]
            self.msg = "%d of %d need you — m shows everything" % (len(mine),
                                                                    len(self.items))
        else:
            self.msg = "all %d items" % len(self.items)
        self.scroll = 0

    # the walk's plan, shown while `w` asks for its budget

    def lines_plan(self, width):
        """What a walk would do, before it is asked for money: the order it would
        take the items in if every chain finishes its item, what is held back and
        by what, and what the budget means. aptitude shows its pending actions
        before `g`; a walk is the same kind of commitment.

        ⚠️ A FORECAST, and it says so: a chain that raises hands the walk the next
        branch instead, and the order can change under it. What is certain is the
        FIRST item — `walk_target`, the pin else `next_item` — and that is the line in the accent."""
        out, para = self.wrapper(width)
        grey = look("chrome")
        first = self.walk_target()
        take, held, blocked, done = [], [], [], 0
        for it in self.items:
            state = row_state(it)
            if it["status"] == "done":
                done += 1
            elif it["status"] == "blocked":
                blocked.append(it)
            elif state != "open":
                held.append((it, state))
            else:
                take.append(it)
        take.sort(key=lambda it: it["id"] != first)
        out.append(("the walk, if each chain finishes its item", curses.A_BOLD))
        para("a chain that raises hands it the next branch instead, so only the first "
             "is certain", grey)
        out.append(("", 0))
        if not take:
            out.append(("  nothing it can take — every branch is blocked or finished",
                        curses.color_pair(1)))
        for n, it in enumerate(take, 1):
            pin = "  pinned" if it["id"] == self.walk_next else ""
            out.append(("  %2d  %-6s %s%s" % (n, it["id"], it["goal"], pin),
                        look("accent") if n == 1 else 0))
        if held:
            out.append(("", 0))
            out.append(("held back", grey))
            for it, state in held:
                why = ("a raise above it, on %s" % state[1:] if state.startswith("↳")
                       else "a fence, until %s is done" % state[1:])
                out.append(("      %-6s %s — %s" % (it["id"], it["goal"], why), grey))
        if blocked:
            out.append(("", 0))
            out.append(("waiting on your answer: %s" % ", ".join(i["id"] for i in blocked),
                        curses.color_pair(1)))
        out.append(("", 0))
        para("%d finished. The budget is sessions over the whole walk; each chain is "
             "given what is left, so it cannot overshoot." % done, grey)
        return out

    def lines_tree(self, width):
        out, para = self.wrapper(width)
        it = self.current()
        if it is None:
            return [("no items — press [o] to open one", 0)]
        if self.tree_item != it["id"]:
            self.tree_item, self.tree_sel = it["id"], 0
        t, archive, commits = self.tree_of(it["id"])
        rows = tree_entries(t, self.tree_folded, self.tree_flagged)
        self.tree_sel = max(0, min(self.tree_sel, len(rows) - 1))
        para("%s — %s" % (it["id"], it["goal"]), curses.A_BOLD)
        out.append((it["why"], self.status_attr(it["status"])))
        if it.get("corrections"):
            out.append(("corrected, not yet carried out: %s"
                        % ", ".join(it["corrections"]), curses.A_BOLD))
        out.append(("sessions %d of %d since the author spoke · critic in %d"
                    % (it.get("sessions", 0), 10, max(5 - it.get("since_critic", 0), 0)),
                    look("chrome")))
        if self.tree_flagged:
            out.append(("only ⚑ assumptions and raises · p shows every node",
                        curses.A_BOLD))
        out.append(("", 0))
        if not rows:
            out.append(("no nodes yet", look("chrome")))
            return out
        self._sel_row = self._sel_end = None
        red = curses.color_pair(1)
        # The cursor is drawn only where the keys go: two highlighted rows on one
        # screen is a question about which one j moves.
        focused = self.tree_focused()
        for i, row in enumerate(rows):
            # The row before this one is finished, and if it was the cursor's, its
            # block ends here (see `keep_in_view`).
            if self._sel_row is not None and self._sel_end is None:
                self._sel_end = len(out) - 1
            sel = focused and i == self.tree_sel
            cursor = ">" if sel else " "
            pad = "  " * row["depth"]
            if sel:
                self._sel_row = len(out)
            if row["kind"] == "section":
                shown = (row["key"] in self.tree_open) != row["open_default"]
                out.append(("%s %s %s" % (cursor, "▾" if shown else "▸", row["name"]),
                            curses.A_BOLD | (curses.A_REVERSE if sel else 0)))
                if shown:
                    for line in prose_blocks(row["text"]):
                        para(line, indent="    ", hang="  " if re.match(
                            r"^\s*([-*+]|\d+[.)])\s", line) else "", rich=True)
                    out.append(("", 0))
                continue
            if row["kind"] == "raise":
                r = row["raise_"]
                on = (" on " + r["args"][0]) if r["args"] else " on the task"
                out.append(("%s ● raise%s · %s — needs you" % (cursor, on, age(r["ts"])),
                            red | curses.A_BOLD | (curses.A_REVERSE if sel else 0)))
                for line in r["body"].splitlines():
                    para(line, indent="    ", hang="  ", rich=True)
                continue
            nd, st = row["node"], row["status"]
            fold = ("▸" if row["folded"] else "▾") if row["kids"] else " "
            flag = "  ● raise" if row["raises"] else ""
            assumes, asks = row["assumes"], row["asks"]
            if assumes:
                flag += "  ⚑ assumption"
            if asks:
                flag += "  ? ask"
            if dfs_tree.story(t, nd["id"])["answered"]:
                flag += "  ✎ answered"
            if row["raises_below"]:
                flag += "  ● %d raise%s below" % (row["raises_below"],
                                                  "" if row["raises_below"] == 1 else "s")
            attr = (look("chrome") if st in TREE_QUIET else 0)
            if assumes or asks:
                # ⚠️ AMBER AND BOLD, because most nodes are "something was done" and
                # these are the ones the author is there to read. A raise is red and
                # wins: it blocks the task, an assumption only asks to be looked at.
                attr = look("amber") | curses.A_BOLD
            if row["raises"] or row["raises_below"]:
                attr = red | curses.A_BOLD
            if sel:
                attr |= curses.A_REVERSE
            # Indent guides, as an editor draws them: one faint rule per ancestor,
            # at the column its fold glyph sits in, so two nodes a screen apart can
            # be read as siblings or not without counting spaces. Below the header
            # an unfolded parent's own column joins in, running its glyph down to
            # the children.
            above = [2 + 2 * k for k in range(row["depth"])]
            below = above + ([2 + 2 * row["depth"]]
                             if row["kids"] and not row["folded"] else [])
            first = len(out)
            head = "%s %s%s %s%s %s %s%s" % (cursor, pad, fold, "↳" if row["chain"] else "",
                                             nd["id"], TREE_MARK.get(st, "?"), mark_up(nd["title"]), flag)
            lead = len(cursor) + 1 + len(pad) + len(fold) + 1
            for line in (textwrap.wrap(
                    head, max(20, width - 2), subsequent_indent=" " * (lead + 2))):
                out.append((line, attr, below if len(out) > first else above))
            out[first:] = carry_marks(out[first:])
            inner = " " * (lead + 2)
            room = max(20, width - len(inner) - 2)
            story = dfs_tree.story(t, nd["id"])
            if row["key"] not in self.tree_open:
                # ⚠️ SKIM: the verdict, one line, under each node. The tree was a list
                # of titles and the story a keypress away per node, so nothing could
                # be skimmed. What the AUTHOR overruled is said instead of the
                # determination they overruled.
                if story["overruled"]:
                    why = (story["overruled"]["body"].strip().splitlines() or [""])[0]
                    out.append((inner + clip_marked("overruled by you: " + why, room),
                                red | curses.A_BOLD))
                elif first_sentence(nd["fields"].get("Determination")):
                    out.append((inner + clip_marked(first_sentence(nd["fields"]["Determination"]),
                                                  room), look("chrome")))
                if not getattr(self, "card_panel", False):
                    # With the node panel at the right the whole of it is there; without
                    # one the assumption and what would show it wrong are kept under the
                    # row, each on a line of its own and clipped with a visible mark.
                    if assumes:
                        assumption, wrong = dfs_tree.split_falsifier(nd["fields"]["Hypothesis"])
                        out.append((inner + clip_marked(assumption, room), look("amber")))
                        if wrong:
                            out.append((inner + clip_marked("wrong if " + wrong, room), look("amber")))
                    if asks:
                        out.append((inner + clip_marked("ask: " + nd["fields"]["Ask"], room), red))
            if row["key"] not in self.tree_open and not row["raises"]:
                continue
            for r in row["raises"]:
                out.append((inner + "raise · %s — needs you" % age(r["ts"]), red | curses.A_BOLD))
                for line in r["body"].splitlines():
                    para(line, indent=inner + "  ", hang="  ", rich=True)
            if row["key"] in self.tree_open:
                # The same card the panel draws (minus its heading, which is the row).
                card = node_card(t, dict(row, raises=[]), archive, commits.get(nd["id"], ()))
                out += self.card_lines(width - len(inner), card[1:], inner)
                out.append(("", 0, BLANK_GUIDES))
            out[first:] = [ln if len(ln) > 2 else ln + (below,) for ln in out[first:]]
        if self._sel_row is not None and self._sel_end is None:
            self._sel_end = len(out) - 1
        # A blank line carries only the guides both its neighbours do, so a rule
        # stops where its branch ends rather than hanging into the gap after it.
        def guides(n):
            g = out[n][2] if 0 <= n < len(out) and len(out[n]) > 2 else []
            return [] if g is BLANK_GUIDES else g
        for n, ln in enumerate(out):
            if len(ln) > 2 and ln[2] is BLANK_GUIDES:
                out[n] = ("", 0, [c for c in guides(n - 1) if c in guides(n + 1)])
        return out

    def footer(self, w):
        """Keys, narrowing by dropping the hints before the keys.

        A key that is not on the footer does not exist for the person using this, so
        what goes first when the terminal is narrow is the navigation — j/k and the
        arrows are discoverable by pressing them, while `r` for review is not.
        """
        it = self.current()
        enter = {"review": "⏎ review", "work": "⏎ run in background",
                 "none": "⏎ —"}[self.next_action(it)]
        # From the poll's cache, not a fresh scan: this runs on every draw, and a
        # footer is not worth walking /proc for.
        if it is not None and any(r["item"] == it["id"] for r in self.live()):
            enter = "⏎ (running)"
        actions = [enter, "w walk" if not self.walk_on else "w stop walk", "n walk next",
                   "r review", "c chat", "o open", "e edit", "[/] order",
                   "{/} branch", "b break", "3 log", "l last log", "L runs", "v art", "t todo",
                   "x agent"]
        if self.live():
            # A key that stops a background chain has to be on the footer of the
            # screen that started it, or the only way to end one is `kill`.
            actions.insert(1, "K stop")
        nav = ["↑↓/jk item", "d/u scroll"]
        if self.pane == "artefact":
            actions, nav = ["⏎ open in browser", "esc back"], ["↑↓/jk artefact"]
        elif self.pane == "runs":
            actions, nav = ["⏎ open", "esc back"], ["↑↓/jk run"]
        elif self.tree_focused():
            actions = ["⏎ open", "n next ⚑", "p flagged", "space fold", "i add", "a answer", "f correct"]
            if it is not None and it["status"] == "done" and self.tree_acceptable(it):
                actions.append("A accept")
            actions += ["R terminal review", "c chat", "tab items"]
            nav = ["↑↓/jk node", "d/u scroll"]
        elif self.pane == "item":
            actions.insert(1, "tab tree")
        elif self.pane == "agentlog":
            actions, nav = ["L run list", "esc back"], ["↑↓ scroll", "d/u half"]
            if self.open_run and is_growing(self.open_run):
                actions.insert(0, "G follow" if not self.follow else "● following")
        elif self.pane == "keys":
            actions, nav = ["⏎ press it", "esc back"], ["↑↓/jk key", "/ search"]
        # `?` is the last thing to go: it is where everything the footer dropped is.
        segs = actions + nav + [": command", "? keys", "q quit"]
        while len(segs) > 3 and len(" · ".join(segs)) > w - 2:
            # The pane's own keys go first, then `: command`; `? keys` never.
            segs.pop(-4 if len(segs) > 4 else -3)
        return " · ".join(segs)

    def side_w(self, w):
        """The activity column's width: none below `SIDE_AT`, where the main column
        needs every cell it has; then about a third, and never so wide that the tree
        is the one squeezed."""
        if self.tree_focused():
            # ⚠️ WHILE THE TREE HAS THE KEYS the right column is the selected node,
            # not the activity log: reading a tree is moving through it, and a node
            # was one ⏎ and most of a small screen each. It starts earlier (NODE_AT)
            # than the activity column does, because it is what the pane is for.
            return 0 if w < NODE_AT else min(84, max(50, w * 42 // 100))
        return 0 if w < SIDE_AT else min(72, max(44, w * 36 // 100))

    def rule(self, y, x0, x1, title="", tag=""):
        """A section's rule: grey line, its name in it, a position tag at the right."""
        self.put(y, x0, "─" * max(0, x1 - x0), look("chrome"))
        if title:
            self.put(y, x0 + 2, " %s " % title, look("chrome"))
        if tag:
            self.put(y, max(x0, x1 - len(tag) - 2), tag, look("chrome"))

    def draw(self):
        self.scr.erase()
        h, w = self.scr.getmaxyx()
        self._list_y = None
        self.clip = None
        self.header()
        side = self.side_w(w)
        main = w - side - (1 if side else 0)
        self.clip = main
        self.card_panel = bool(side) and self.tree_focused()
        list_h = min(len(self.list_rows()), max(3, (h - 5) // 3))
        if self.tree_focused():
            # ⚠️ THE LIST IS ONE ROW, the selected item, while the tree has the keys:
            # at 80×24 it kept eight rows and left the tree twelve, so one opened node
            # filled the pane and the task's own name scrolled out of it.
            list_h = 1
        self.draw_list(2, list_h)
        sep = 2 + list_h
        name = {"item": "item", "reglog": "task log", "runs": "agent runs",
                "agentlog": "agent log", "todo": "epic todo",
                "artefact": "artefacts", "keys": "keys", "activity": "activity",
                "plan": "walk plan", "assumptions": "assumptions"}[self.pane]
        if self.pane == "item":
            name = ("tree · %s" % self.current()["id"]
                    if self.tree_focused() and self.current() else
                    "tree" if self.tree_focused() else "item")

        body_top = sep + 1
        body_h = max(1, h - body_top - 2)
        self.body_h = body_h
        self._sel_row = self._sel_end = None
        lines = self.detail_lines(main)
        # ⚠️ Following is DERIVED here, not remembered from the keypress that opened
        # the pane — see `follows`. One place, so the footer, the pane's own header
        # and the scroll cannot disagree about it.
        if self.pane == "agentlog" and self.open_run is not None:
            bottom = max(0, len(lines) - body_h)
            self.follow = follows(is_growing(self.open_run), self.follow,
                                  self.scroll, bottom)
            if self.follow:
                self.scroll = bottom
        else:
            self.follow = False
        # A pane with a cursor owns its scroll: moving onto a row below the fold has
        # to bring the row with it, or j/k walks an invisible cursor.
        if self._sel_row is not None:
            end = self._sel_end if self._sel_end is not None else self._sel_row
            self.scroll = keep_in_view(self.scroll, self._sel_row, end, body_h)
        self.scroll = max(0, min(self.scroll, max(0, len(lines) - body_h)))
        tag = ""
        if len(lines) > body_h:
            tag = "%d–%d of %d" % (self.scroll + 1,
                                   min(self.scroll + body_h, len(lines)), len(lines))
        self.rule(sep, 0, main - 1, name, tag)
        for i, line in enumerate(lines[self.scroll:self.scroll + body_h]):
            text, attr = line[0], line[1]
            y = body_top + i
            # ⚠️ A_REVERSE is how every pane MARKS its cursor row; `selected` is how
            # that mark is drawn — a band across the column and the accent bar where
            # the `>` was, so the row keeps its own colour instead of inverting.
            if attr & curses.A_REVERSE and LOOK.get("band"):
                self.put(y, 1, text + " " * (main - 2 - len(unmark(text))), selected(attr))
                if text.startswith(">"):
                    self.put(y, 1, BAR, look("selbar"))
                continue
            self.put(y, 1, text, attr)
            # A third element is indent-guide columns (see `lines_tree`), drawn only
            # into blank cells so a guide never overwrites text, and never across the
            # selected row, whose band is the cursor.
            if len(line) > 2 and not attr & curses.A_REVERSE:
                for c in line[2]:
                    if c >= len(unmark(text)) or unmark(text)[c] == " ":
                        self.put(y, 1 + c, "│", look("chrome"))
        self.clip = None
        if side:
            (self.draw_card if self.card_panel else self.draw_side)(main, 2, h - 3, w)

        self.put(h - 2, 1, self.msg, 0)
        if self.input is not None:
            cur = self.draw_input(h, w)
        else:
            self.draw_footer(h - 1, w)
            cur = None
        # ⚠️ The hardware cursor is PARKED where the keys go, although it is hidden.
        # A screen reader follows it, not the band, and curses leaves it wherever the
        # last write ended — the end of the footer, on every draw — so a reader was
        # told the footer once a second and never the row you were on. While typing
        # it is where the typing goes, and shown.
        y = cur if cur is not None else self.cursor_y(body_top, body_h)
        if y is not None:
            try:
                self.scr.move(*(y if isinstance(y, tuple) else (y, 0)))
            except curses.error:
                pass
        self.scr.refresh()

    def draw_footer(self, y, w):
        """The footer's keys: the key in the accent, what it does in grey.

        `footer` still decides WHICH keys fit, as text; this only draws them, so the
        narrowing rule has one home. A label that says a state is on wants seeing
        (`w stop walk`, while a walk runs) and is drawn in red."""
        x = 1
        for seg in self.footer(w).split(" · "):
            key, _, label = seg.partition(" ")
            if seg.startswith("●"):
                x = self.puts(y, x, [(seg, curses.color_pair(2)), ("   ", 0)])
                continue
            loud = self.walk_on and label == "stop walk"
            x = self.puts(y, x, [(key, look("accent") | curses.A_BOLD), (" ", 0),
                                 (label, (curses.color_pair(1) | curses.A_BOLD) if loud
                                  else look("chrome")), ("   ", 0)])

    def draw_input(self, h, w):
        """The input line, where the footer was (htop's, fzf's): the prompt in the
        accent, what is typed, and its own keys at the right. A `menu` — `:`'s
        matches — is drawn above it, over the pane, bottom up. Returns the cursor."""
        inp = self.input
        hint = inp.get("hint") or "⏎ ok   esc cancel"
        self.put(h - 1, max(0, w - len(hint) - 2), hint, look("chrome"))
        x = self.puts(h - 1, 1, [(inp["label"], look("accent") | curses.A_BOLD),
                                 (" " if inp["label"] else "", 0)])
        room = max(1, w - x - len(hint) - 4)
        start = max(0, inp.get("pos", len(inp["text"])) - room)
        text = inp["text"][start:start + room]
        self.put(h - 1, x, text, 0)
        menu = inp.get("menu") or []
        for i, (line, attr) in enumerate(reversed(menu[: max(0, h - 6)])):
            y = h - 2 - i
            self.put(y, 0, " " * (w - 1), 0)
            if attr & curses.A_REVERSE and LOOK.get("band"):
                self.put(y, 0, (" " + line).ljust(w - 2), selected(attr))
                self.put(y, 0, BAR, look("selbar"))
            else:
                self.put(y, 1, line, attr)
        if menu:
            self.rule(h - 2 - min(len(menu), h - 6), 0, w - 1, inp.get("title", ""))
        return (h - 1, x + inp.get("pos", len(inp["text"])) - start)

    def card_for_row(self, width):
        """The lines of the panel at the right: whatever the tree's cursor is on."""
        row = self.tree_row()
        it = self.current()
        if row is None or it is None:
            return [("nothing selected", look("chrome"))]
        t, archive, commits = self.tree_of(it["id"])
        if row["kind"] == "node":
            return self.card_lines(width, node_card(t, row, archive, commits.get(row["key"], ())))
        out, para = self.wrapper(width)
        if row["kind"] == "section":
            out.append((row["name"], curses.A_BOLD))
            out.append(("", 0))
            for line in prose_blocks(row["text"]):
                para(line, 0, indent="", hang="  " if re.match(r"^\s*([-*+]|\d+[.)])\s", line) else "", rich=True)
        else:
            r = row["raise_"]
            out.append(("raise%s · %s — needs you" % (
                (" on " + r["args"][0]) if r["args"] else "", age(r["ts"])),
                        curses.color_pair(1) | curses.A_BOLD))
            out.append(("", 0))
            for line in r["body"].splitlines():
                para(line, 0, indent="", hang="  ", rich=True)
        return out

    def draw_card(self, x0, top, bottom, w):
        """The selected node, formatted, at the right of the tree (see `side_w`)."""
        for y in range(top, bottom):
            self.put(y, x0, "│", look("chrome"))
        x = x0 + 2
        width = w - x - 1
        row = self.tree_row()
        self.rule(top, x, w - 1, row["key"] if row and row["kind"] == "node" else
                  (row["name"] if row and row["kind"] == "section" else "raise") if row else "")
        lines = self.card_for_row(width)
        room = max(1, bottom - top - 1)
        key = row["key"] if row else None
        if getattr(self, "_card_key", None) != key:
            self._card_key, self.card_scroll = key, 0
        start = max(0, min(getattr(self, "card_scroll", 0), max(0, len(lines) - room)))
        self.card_scroll = start
        shown = lines[start:start + room]
        for i, line in enumerate(shown):
            self.put(top + 1 + i, x, line[0][:width], line[1])
        if start + room < len(lines):
            # Over the last line, padded so what it replaces does not show through.
            self.put(top + room, x, ("… %d more — ^d" % (len(lines) - start - room))
                     .ljust(width)[:width], look("chrome"))

    def draw_side(self, x0, top, bottom, w):
        """The activity column: what is running now, then what has happened.

        ⚠️ WATCHING THE WALK WAS THREE KEYS AWAY from the tree you would be watching
        it from (`L`, pick the run, ⏎), and the tree was gone while you did. From
        `SIDE_AT` columns both are on the screen: the live chain's console tail at
        the top, and the activity log under it — the record of what this screen and
        its walk did, which is the part the message line kept losing."""
        for y in range(top, bottom):
            self.put(y, x0, "│", look("chrome"))
        x = x0 + 2
        width = w - x - 1
        live = sorted(self.live(), key=lambda r: r["dir"] != self.walk_dir)
        y = top
        if live:
            r = live[0]
            self.rule(y, x, w - 1, "live · %s · %s" % (r["item"], self.chain_progress(r)))
            y += 1
            share = max(4, (bottom - top) * 55 // 100)
            tail = []
            if r.get("console"):
                try:
                    tail = [ln for ln in tail_lines(r["console"], 200)
                            if ln.strip()]
                except OSError:
                    tail = []
            # As tall as the console is, up to a little over half: a chain that has
            # said four lines should not hold the activity log off half the column.
            for ln in tail[-(share - 1):]:
                self.put(y, x, strip_ansi(ln)[:width], look("chrome")
                         if ln.startswith("dfs_run:") else 0)
                y += 1
            y += 1
        self.rule(y, x, w - 1, "activity")
        y += 1
        rows = self.activity_lines(width)
        for text, attr in rows[-max(0, bottom - y):] if bottom > y else []:
            if re.match(r"\d\d:\d\d ", text):
                self.puts(y, x, [(text[:5], look("chrome")), (text[5:], attr)])
            else:
                self.put(y, x, text, attr)
            y += 1

    def cursor_y(self, body_top, body_h):
        """The screen row of whatever the keys drive: a pane's cursor row if it has
        one on screen, else the selected item's row in the list."""
        row = self._sel_row
        if row is not None and self.scroll <= row < self.scroll + body_h:
            return body_top + row - self.scroll
        if self.pane == "item" and not self.tree_focused():
            return self._list_y
        return None

    # ---- acting --------------------------------------------------------------

    def chat_env(self, scope):
        """The session this scope's chat shares with the web page (chat.py): CHAT_SID, and
        CHAT_RESUME when it has already got a transcript. Only claude's can be shared."""
        if self.agent != "claude":
            return None
        try:
            import chat as dfs_chat
            sid, resume = dfs_chat.claim(scope)
        except (OSError, ValueError, ImportError):
            return None
        return dict(os.environ, CHAT_SID=sid, **({"CHAT_RESUME": "1"} if resume else {}))

    chat_host = None        # the chats running in the background, by scope (ptyrelay.ChatHost)

    def chats_running(self):
        if self.chat_host is None:
            import ptyrelay
            self.chat_host = ptyrelay.ChatHost()
        return self.chat_host

    def start_chat(self, scope):
        """The chat for this scope, running in the BACKGROUND (started if it is not). Any
        number can be going: the page types into them, `c` looks at one. A new one opens with
        the summary (run.sh); one with a conversation already is resumed."""
        argv = self.runner("--chat", scope)
        return self.chats_running().ensure(scope, lambda: (argv, self.chat_env(scope) or dict(os.environ), ROOT))

    def open_chat(self, scope):
        """What `c` and `C` do: look at this scope's chat, running it first if it is not.
        ctrl-] comes back to the roadmap and leaves it running."""
        relay = self.start_chat(scope)
        self.event("run", "chat %s" % scope)
        curses.def_prog_mode()
        curses.endwin()
        print("\n[chat %s: ctrl-] returns to the roadmap and leaves it running]\n" % scope, flush=True)
        try:
            kept = relay.attach()
        except KeyboardInterrupt:
            kept = True
        if not kept:
            print("\n(the chat ended)")
            try:
                input("\n[enter] back to the roadmap ")
            except (EOFError, KeyboardInterrupt):
                pass
        self.scr.clear()
        curses.reset_prog_mode()
        self.scr.refresh()
        self.reload()

    def shell(self, argv, pause=True, env=None):
        """Hand the real terminal over. Sessions are interactive and stream output."""
        self.event("run", "$ " + shlex.join(argv))
        curses.def_prog_mode()
        curses.endwin()
        print("\n$ " + " ".join(argv) + "\n", flush=True)
        try:
            rc = subprocess.call(argv, cwd=ROOT, env=env)
        except KeyboardInterrupt:
            rc = 130
            print("\n(interrupted)")
        except OSError as e:
            # A command that is not there (an $EDITOR this box lacks) is said, here,
            # rather than ending the screen with a traceback.
            rc = 127
            print("\ncannot run %s: %s" % (argv[0], e.strerror or e))
        if pause:
            try:
                input("\n[enter] back to the roadmap ")
            except (EOFError, KeyboardInterrupt):
                pass
        self.scr.clear()
        curses.reset_prog_mode()
        self.scr.refresh()
        self.reload()
        return rc

    def read_run(self, run, back, at_end=False):
        """Open one run's log. `back` is the pane esc returns to: the list it was
        chosen from (`L`) or the item it was asked of directly (`l`). Both ask for the
        END of it: the line worth reading is the last one, live or finished."""
        self.open_run = run
        self.run_back = back
        self.pane = "agentlog"
        # A live log opens at its END and stays there: the line worth
        # reading on a running chain is always the last one. Where it
        # OPENS is all this decides; `follows` in `draw` is what keeps it
        # there, and arms a moment later if liveness lands a moment later
        # — a run directory whose `meta.json` is not written yet has none
        # to read, and that chain then runs for an hour.
        self.scroll = 10 ** 6 if at_end or is_growing(self.open_run) else 0

    # ---- the walk ------------------------------------------------------------

    def set_agent(self, name):
        """`x`'s choice, kept for the next screen (and the web page) in `agent_file`."""
        WalkMixin.set_agent(self, name)
        if self.real:
            try:
                with open(os.path.join(dfs_runs.ensure_run_root(), "agent"), "w") as fh:
                    fh.write(self.agent + "\n")
            except OSError:
                pass

    def walk_toggle(self):
        """Turn the depth-first walk on or off.

        ⚠️ The budget is in SESSIONS, over the WHOLE walk, and it is the only bound
        there is. A per-item cap was the obvious thing to ask for and was worse than
        useless: a chain that used one up left the item still open, so `next_item`
        named it again and the walk started another chain on it immediately — "cap 5"
        meant five sessions, then five more, for as long as the item stayed open. A
        number that reads like a limit and is not one is worse than no number.

        ⚠️ The prompt is also the confirmation. `K` already asks before stopping ONE
        chain because that is an hour of somebody's subscription; this starts chains
        until the budget or the tree runs out, so a bare keypress is not enough of a
        decision. Escaping it leaves the walk off.

        ⚠️ WHICH IS WHY THE NUMBER IS NOT OFFERED IN BRACKETS. It read
        `session budget [20], empty to cancel:`, and `[n]` means the opposite two
        keys away: `cap [5]:` is the one other prompt here and Enter on it takes the
        5. So Enter on this one — the natural way to accept a number the screen is
        already showing — cancelled, and answered "walk: not started", which is a
        sentence about a keypress nobody made. The last budget is still worth
        showing, since it is what you would usually type again; it is shown as the
        last one rather than as an offer.
        """
        if self.walk_on:
            self.walk_end_chain()
            return
        was = (self.pane, self.scroll)
        self.pane, self.scroll = "plan", 0
        try:
            raw = self.prompt("walk: how many sessions? (last walk: %d, nothing cancels)"
                              % self.walk_budget, "")
        finally:
            self.pane, self.scroll = was
        if not raw:
            self.msg = "walk: not started — it wants a number of sessions"
            return
        try:
            budget = int(raw)
        except ValueError:
            self.msg = "walk: %r is not a number of sessions" % raw
            return
        try:
            self.walk_begin(budget)
        except ValueError as e:
            self.msg = "walk: %s" % e

    def chain_progress(self, chain):
        """Where a chain IS — placed in the walk when the walk started it.

        ⚠️ A WALK-STARTED CHAIN HAS NO CAP OF ITS OWN. `walk_tick` passes it the
        budget REMAINING (`left`), deliberately, so a chain cannot overshoot the
        walk by running one more session than it has left to spend. That makes
        `run/cap` a fraction of two different things: the numerator counts inside
        this chain, the denominator belongs to the walk. The fourth session of a
        100-session walk read `run 1/97` — two true numbers, one false fraction,
        and neither half the number anybody is spending. So the walk's own chain
        is placed in the walk, which is what `walk_badge` already says in the
        header; a chain started by hand off `s` keeps `run/cap`, where the cap IS
        the bound that was typed.
        """
        if self.walk_on and chain["dir"] == self.walk_dir:
            return "run %d/%d" % (self.walk_used(), self.walk_budget)
        return dfs_runs.progress(chain)

    def walk_badge(self):
        """The walk as a BADGE: what it is doing, or why it stopped.

        ⚠️ THE REASON IT STOPPED IS A STATE, AND IT LIVES HERE. `walk_off` also puts
        it on the message line, and the message line cannot hold it: `poll` writes
        "refreshed" (or a chain's ended note) from the SAME tick, so a walk that
        stopped because of a bug was a badge quietly disappearing and nothing said.
        `walk_note` was written and read by nothing at all until 2026-09-16 — a
        recorded reason nobody could see. It stays up until the next walk starts.

        ⚠️ Shape first, colour second. Written as one dot-separated segment in the
        middle of the header it read exactly like `18 items` — same weight, same
        place, nothing to catch the eye of somebody who left it on an hour ago. So
        it goes at the FRONT, in capitals, behind a dot: the header LINE changes
        shape when the mode is on, which is legible on a monochrome terminal, over
        a screenshot, and out of the corner of an eye. The red overlay in `header`
        is reinforcement, never the signal.
        """
        pin = (" → %s" % self.walk_next) if self.walk_next else ""
        if not self.walk_on:
            if self.walk_note:
                return "○ walk off%s · %s" % (pin, self.walk_note)
            return ("○ walk off%s" % pin) if pin else ""
        spent = "%d/%d" % (self.walk_used(), self.walk_budget)
        held = self.walk_until - time.time()
        if held > 0:
            return "● walk held %s%s %s" % (walk_held_for(held), pin, spent)
        run = self.walk_run()
        if run and run["live"]:
            return "● walk %s%s %s" % (run["item"], pin, spent)
        return "● walk%s %s" % (pin, spent)

    def stop_chain(self, item):
        """Ctrl-C for a chain nobody has a terminal on.

        SIGINT rather than SIGTERM, and to the process GROUP rather than the pid,
        because that is exactly what a Ctrl-C in the foreground was: the runner's own
        `on_interrupt` trap is what ends the CHAIN rather than just the run in it, and
        it only fires for INT or TERM reaching the whole group. The agent under it
        gets the same signal in the same instant, as it would have.
        """
        r = dfs_runs.live_for(item)
        if r is None:
            self.msg = "no chain is running on %s" % item
            return
        if self.prompt("stop %s's chain, pid %s? [y/N]: " % (item, r["pid"]))[:1].lower() != "y":
            self.msg = "left it running"
            return
        try:
            self.interrupt_chain(item)
        except ValueError as e:
            self.msg = str(e)

    def compose(self, frame):
        """The author's words, written in `$EDITOR` under `frame` as `#` lines.

        The review is transcription, so what is kept is exactly what they typed; the
        frame is there to answer against and is stripped. Empty means cancel."""
        fd, path = tempfile.mkstemp(prefix="dfs-review-", suffix=".md")
        try:
            with os.fdopen(fd, "w") as f:
                f.write("\n\n" + "".join("# %s\n" % l for l in frame))
            self.shell([os.environ.get("EDITOR", "vi"), path], pause=False)
            with open(path) as f:
                return strip_comments(f.read())
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def tree_acceptable(self, it):
        t, _, _ = self.tree_of(it["id"])
        return dfs_tree.status_of(t)[0] == "done" and not dfs_tree.accepted(t)

    def tree_answer(self, item):
        row = self.tree_row()
        raises = ([row["raise_"]] if row and row["kind"] == "raise" else
                  row.get("raises", []) if row else [])
        if not raises:
            self.msg = "no open raise here — the red rows carry one"
            return
        r = raises[0]
        on = (" on " + r["args"][0]) if r["args"] else ""
        frame = (["Answer to the raise logged at %s%s. Lines starting # are dropped;"
                  % (r["ts"], on), "an empty answer leaves the raise open.", ""]
                 + r["body"].splitlines())
        body = self.compose(frame)
        if not body:
            self.msg = "not answered — the raise stays open"
            return
        dfs_tree.append_log(item, "answer", [r["ts"]], body)
        self.reload()
        self.msg = self.event("review", "%s: answered %s%s" % (item, r["ts"], on))

    def tree_correct(self, item):
        row = self.tree_row()
        if not row or row["kind"] != "node":
            self.msg = "put the cursor on the node to correct"
            return
        nid = row["key"]
        v = self.choose("%s was really [c]onfirmed or [r]efuted? (any other key cancels)" % nid,
                        "cr")
        if v not in ("c", "r"):
            self.msg = "not corrected"
            return
        verdict = "confirmed" if v == "c" else "refuted"
        frame = (["Directive for %s, corrected to %s: what the work should do from here."
                  % (nid, verdict),
                  "%s, and %s reopens."
                  % ("Nothing is pruned: it already stands confirmed"
                     if dfs_tree.keeps(verdict, row["status"]) else
                     "Everything below it is pruned%s"
                     % (" and so are the siblings made after it" if v == "c" else ""), item),
                  "Lines starting # are dropped; an empty directive cancels.", ""]
                 + row["node"]["raw"])
        body = self.compose(frame)
        if not body:
            self.msg = "empty directive — not corrected"
            return
        dfs_tree.append_log(item, "correct", [nid, verdict], body)
        self.reload()
        self.msg = self.event("review", "corrected %s to %s; %s reopens" % (nid, verdict, item))

    def tree_add(self, item):
        """Put an open node in the tree: a child of the node under the cursor, or of
        the task when the cursor is on a row that is not a node. The title is asked on
        the footer, the approach only if there is one to give."""
        row = self.tree_row()
        parent = row["key"] if row and row["kind"] == "node" else None
        if parent and row["status"] in ("refuted", "pruned"):
            # ⚠️ Born pruned: a node under a refuted or pruned one is cut off with it,
            # and the new node would be drawn as dead the moment it was made.
            if self.prompt("%s is %s, so a node under it is %s too. Add one anyway? [y/N]:"
                           % (parent, row["status"], "pruned")).strip()[:1].lower() != "y":
                self.msg = "no node added"
                return
        title = self.prompt("new node under %s — title (empty cancels):" % (parent or item))
        if not title.strip():
            self.msg = "no node added"
            return
        approach = self.prompt("approach (optional, ⏎ for none):")
        try:
            nid = dfs_tree.add_node(item, title, parent, approach)
        except (ValueError, OSError, dfs_paths.NoBranch) as e:
            self.msg = "not added: %s" % e
            return
        self.reload()
        self.msg = self.event("review", "added %s under %s" % (nid, parent or item))

    def tree_accept(self, item):
        it = self.current()
        if it is None or not self.tree_acceptable(it):
            self.msg = "%s is not a finished tree waiting to be accepted" % item
            return
        t, _, _ = self.tree_of(item)
        assumed = assumed_nodes(t)
        # ⚠️ NAMED IN THE QUESTION, with what would show each one wrong, because accepting
        # is the moment they stop being looked at; and the reviewer's notes are counted.
        bits = []
        nodes = dfs_tree.by_id(t)
        for nid in assumed:
            f = nodes[nid]["fields"]
            wrong = dfs_tree.split_falsifier(f.get("Hypothesis") or "")[1] or f.get("Ask", "")
            bits.append("%s (%s)" % (nid, clip(wrong, 36)) if wrong else nid)
        note = (" Assumptions: %s." % "; ".join(bits)) if bits else ""
        kept = len(dfs_tree.notes(t))
        if kept:
            note += " %d reviewer note%s above." % (kept, "" if kept == 1 else "s")
        if self.prompt("accept %s's tree as finished?%s [y/N]:" % (item, note))[:1].lower() != "y":
            self.msg = "not accepted"
            return
        dfs_tree.append_log(item, "accept")
        self.reload()
        self.msg = self.event("review", "accepted %s's tree" % item)

    def prompt(self, label, default=""):
        """A line typed on the footer: what was typed, `default` for an empty ⏎, and
        "" for Esc — which cancels past the default, since Esc then ⏎ at `cap [5]:`
        used to start a chain. A `:` command's arguments answer these first
        (`answers`), so `:walk 20` is `w` and 20 without the question."""
        if self.answers:
            return self.answers.pop(0)
        got = self.read_line(label)
        if got is None:
            return ""
        return got or default

    def read_line(self, label, on_change=None, menu=None, hint=None, complete=None):
        """Read a line where the footer is, a key at a time. None means Esc.

        ⚠️ `getstr` IS GONE, for the three things it could not do: Esc was typed as a
        character rather than cancelling; nothing could happen WHILE typing, which is
        what search-as-you-type is (htop's, fzf's); and there was no Tab. This is the
        one line editor — ←→, ⌫, ^U, ^W, Tab, ↑↓ in a menu — and every prompt,
        `/` and `:` go through it. The poll waits while you type, as it did before.

        `on_change(text)` runs after each edit; `menu(text)` gives the rows to show
        above the line and their title; `complete(text, row)` is what Tab makes of
        the text given the picked row.
        """
        inp = self.input = dict(label=label, text="", pos=0, hint=hint, menu=[],
                                pick=0)
        self.scr.timeout(-1)
        try:
            curses.curs_set(1)
        except curses.error:
            pass
        try:
            while True:
                if menu is not None:
                    rows, inp["title"] = menu(inp["text"])
                    inp["pick"] = max(0, min(inp["pick"], len(rows) - 1))
                    inp["menu"] = [(t, a | (curses.A_REVERSE if i == inp["pick"] else 0))
                                   for i, (t, a) in enumerate(rows)]
                    inp["rows"] = rows
                self.draw()
                try:
                    ch = self.scr.get_wch()
                except curses.error:
                    continue
                except KeyboardInterrupt:
                    return None
                text, pos = inp["text"], inp["pos"]
                if ch in ("\n", "\r", curses.KEY_ENTER):
                    return text.strip()
                if ch == "\x1b":
                    return None
                if ch == curses.KEY_RESIZE:
                    continue
                if ch in (curses.KEY_UP, curses.KEY_DOWN):
                    inp["pick"] += -1 if ch == curses.KEY_UP else 1
                    continue
                if ch == "\t" and complete is not None:
                    rows = inp.get("rows") or []
                    text = complete(text, rows[inp["pick"]] if rows else None)
                    pos = len(text)
                elif ch in (curses.KEY_BACKSPACE, "\x7f", "\x08"):
                    if pos:
                        text, pos = text[:pos - 1] + text[pos:], pos - 1
                elif ch == curses.KEY_DC:
                    text = text[:pos] + text[pos + 1:]
                elif ch == curses.KEY_LEFT:
                    pos = max(0, pos - 1)
                elif ch == curses.KEY_RIGHT:
                    pos = min(len(text), pos + 1)
                elif ch in (curses.KEY_HOME, "\x01"):
                    pos = 0
                elif ch in (curses.KEY_END, "\x05"):
                    pos = len(text)
                elif ch == "\x15":                      # ^U: the whole line
                    text, pos = "", 0
                elif ch == "\x17":                      # ^W: the word before
                    cut = len(text[:pos].rstrip().rpartition(" ")[0])
                    cut = cut + 1 if cut else 0
                    text, pos = text[:cut] + text[pos:], cut
                elif isinstance(ch, str) and ch.isprintable():
                    text, pos = text[:pos] + ch + text[pos:], pos + 1
                else:
                    continue
                if text != inp["text"]:
                    inp["pick"] = 0
                inp["text"], inp["pos"] = text, pos
                if on_change is not None:
                    on_change(text)
        finally:
            self.input = None
            try:
                curses.curs_set(0)
            except curses.error:
                pass
            self.scr.timeout(POLL_MS)

    def next_action(self, it):
        """What enter does on this item: the state decides, not the author.

        A roadmap item is only ever in one of three postures, and each has exactly
        one next move — an open RAISE wants answering (§0.10: answering is the cheap
        half and the only input this system has), an open item wants working, and a
        closed one wants nothing. Making the author remember which key matches which
        posture is asking them to re-derive what the screen already knows. ⚠️ The
        footer LABEL comes from here too: a key whose advertised meaning is computed
        separately from what it does is the drift this repo keeps writing rules
        about.
        """
        if it is None:
            return "none"
        if it["status"] == "blocked":
            return "review"
        if it["status"] == "done":
            # A finished item is not finished for the AUTHOR while it still holds
            # assumptions nobody has kept or redirected — and a closed item is where
            # they accumulate, 39 of the first 50 having been left on one.
            return "review" if it.get("standing") else "none"
        return "work"

    def tree_focused(self):
        return self.pane == "item" and self.focus == "tree"

    def open_tree(self):
        self.pane = "item"
        self.focus = "tree"
        self.scroll = 0
        it = self.current()
        if it is not None and self.tree_item != it["id"]:
            self.tree_item, self.tree_sel = it["id"], 0

    def unfollow(self):
        """A deliberate scroll means the author is reading, not watching — until they
        read back down to the end, where `follows` arms it again. Which is why this
        being on the downward keys too costs nothing: they land at the bottom and the
        next draw sticks there."""
        self.follow = False

    def act(self, ch):
        it = self.current()
        item = it["id"] if it else ""
        # ⚠️ A PAGE IS WHAT THE PANE SHOWED, read off the last draw. It was `h - 10`,
        # a guess at the chrome that ignored the item list above the pane: at 50
        # rows the pane showed 30 and a page moved 40, so every space bar stepped
        # over ten rows nobody saw. One row is kept as context across the turn.
        page = max(1, self.body_h - 1)
        ch = KEY_ALIASES.get(ch, ch)

        if ch in (ord("q"),):
            return False
        if self.pane == "keys" and ch in (curses.KEY_ENTER, 10, 13):
            return self.key_run()
        if ch == ord("?") or (ch == 27 and self.pane == "keys"):
            if self.pane == "keys":
                self.pane, self.focus = self.keys_from
            else:
                self.keys_from = (self.pane, self.focus)
                self.pane, self.key_sel = "keys", 0
            self.scroll = 0
        elif ch == ord("/"):
            self.search_typed()
        elif ch == ord(":"):
            return self.command()
        elif ch == ord("H"):
            self.pane = "item" if self.pane == "assumptions" else "assumptions"
            self.asm_sel = 0
            self.scroll = 0
        elif ch == ord("h"):
            self.pane = "item" if self.pane == "activity" else "activity"
            self.scroll = 10 ** 6            # newest last, so it opens at the end
        elif ch == ord("m"):
            self.toggle_mine()
        elif ch == ord("z"):
            self.undo_order()
        elif ch == 27:
            # Esc walks BACK one level and never quits. A key that sometimes exits
            # the program and sometimes closes a pane is one you stop pressing;
            # `q` is the only way out, from anywhere.
            self.follow = False
            if self.pane == "agentlog":
                self.pane = getattr(self, "run_back", "runs")   # where it was opened from
            elif self.tree_focused():
                self.focus = "list"      # out of the tree, back to the items
            elif self.pane in ("runs", "reglog", "todo", "artefact", "activity", "assumptions"):
                self.pane = "item"
            self.scroll = 0
        elif ch in (ord("j"), curses.KEY_DOWN):
            self.unfollow()
            self.move(1)
        elif ch in (ord("k"), curses.KEY_UP):
            self.unfollow()
            self.move(-1)
        elif ch == 9 and self.pane == "item":
            # Tab: the item list or the item's tree. Two cursors on one screen, one
            # set of keys, so which one they drive has to be a key of its own.
            self.focus = "list" if self.focus == "tree" else "tree"
            if self.focus == "tree":
                self.open_tree()
            else:
                self.scroll = 0
        elif ch == ord("p") and self.tree_focused():
            self.toggle_flagged()
        elif ch == ord(" ") and self.tree_focused():
            row = self.tree_row()
            if row and row["kind"] == "section":
                self.tree_open ^= {row["key"]}
            elif row and row["kind"] == "node" and row["kids"]:
                self.tree_folded ^= {row["key"]}
            else:
                self.msg = "nothing below this row to fold"
        elif ch in (curses.KEY_NPAGE, ord(" ")):
            self.unfollow()
            self.scroll += page
        elif ch == curses.KEY_PPAGE:
            self.unfollow()
            self.scroll = max(0, self.scroll - page)
        elif ch in (ord("d"), ord("u")):
            self.unfollow()
            # The fine-grained read the arrows used to give, now that they select:
            # half a screen, so what you were reading is still on it.
            self.scroll = max(0, self.scroll + (page // 2 if ch == ord("d")
                                                else -(page // 2)))
        elif self.card_panel and ch in (4, 21):
            # ^d ^u: the panel at the right, which `d` `u` (the tree's own text) leave be.
            self.card_scroll = max(0, getattr(self, "card_scroll", 0) + (6 if ch == 4 else -6))
        elif self.tree_focused() and ch in (curses.KEY_HOME, ord("g")):
            # ⚠️ THE CURSOR, not the text. In the tree `g` scrolled to the top and left
            # the cursor on a row nobody could see, and the next ⏎ acted on it.
            self.tree_sel = 0
        elif self.tree_focused() and ch in (curses.KEY_END, ord("G")):
            self.tree_sel = max(0, len(self.tree_rows()) - 1)
        elif ch in (curses.KEY_HOME, ord("g")):
            self.unfollow()
            self.scroll = 0
        elif ch in (curses.KEY_END, ord("G")):
            # On a live console this is "follow again", which is the same key doing
            # the same thing: go to the last line and keep going to it.
            # Past the end of any pane; the clamp in `draw` lands it on the last
            # line, and `follows` arms following from there if the file is growing.
            self.scroll = 10 ** 6
        elif ch == ord("x"):
            self.set_agent(AGENTS[(AGENTS.index(self.agent) + 1) % len(AGENTS)])
        elif ch == ord("3"):
            # ⚠️ It was `r`, which now runs the review. The pane is called §3 and this
            # is its digit, so the key still spells what it opens — and moving a
            # READ-ONLY pane is the right way round: the key that took the terminal and
            # wrote to the roadmap kept its own letter, rather than a pane quietly
            # becoming a write.
            self.pane = "item" if self.pane == "reglog" else "reglog"
            self.scroll = 0
        elif ch == ord("t"):
            self.pane = "item" if self.pane == "todo" else "todo"
            self.scroll = 0
        elif ch == ord("l"):
            # The log of the SELECTED item's most recent run. `discover_runs` sorts a
            # live chain first and the rest newest first, so the first run that names
            # the item is the one to read: a chain still going, if there is one.
            self.runs = discover_runs()
            run = next((r for r in self.runs if r["item"] == item), None)
            if run is not None:
                self.read_run(run, "item", at_end=True)
            else:
                self.msg = "no run of %s yet — [L] lists every run" % (item or "anything")
        elif ch == ord("L"):
            self.runs = discover_runs()
            self.pane = "runs"
            self.scroll = 0
        elif ch == ord("v"):
            # The raise says to look at a picture, and until now the screen showing
            # the raise was the one place you could not get to it.
            self.pane = "item" if self.pane == "artefact" else "artefact"
            self.art_sel = 0
            self.scroll = 0
        elif ch in (curses.KEY_ENTER, 10, 13):
            if self.tree_focused():
                row = self.tree_row()
                if row:
                    self.tree_open ^= {row["key"]}
            elif self.pane == "assumptions":
                rows = self.asm_rows()
                if rows:
                    idx, nd = rows[max(0, min(self.asm_sel, len(rows) - 1))]
                    self.sel = idx
                    self.open_tree()
                    keys = [r["key"] for r in self.tree_rows()]
                    self.tree_sel = keys.index(nd["id"]) if nd["id"] in keys else 0
            elif self.pane == "artefact":
                arts = self.artefacts()
                if arts:
                    self.open_artefact(arts[self.art_sel])
                else:
                    self.msg = "no artefact in %s" % dfs_paths.rel(ART_DIR)
            elif self.pane == "runs":
                if self.runs:
                    self.read_run(self.runs[self.run_sel], "runs", at_end=True)
                else:
                    self.msg = "no run logs on this box yet"
            else:
                action = self.next_action(it)
                if action == "review":
                    self.open_tree()
                elif action == "work":
                    cap = self.prompt("cap [5], esc cancels:", "5")
                    if cap.isdigit() and int(cap) > 0:
                        self.start_chain(item, cap)
                    else:
                        self.msg = "not started%s" % (
                            " — %r is not a number of sessions" % cap if cap else "")
                elif it is None:
                    self.msg = "no items — press [o] to open one"
                else:
                    self.msg = ("%s is closed — %s. [c]hat to reopen the thinking, "
                                "or [o]pen new work." % (item, it["why"]))
        elif ch == ord("r"):
            # ⚠️ ONE KEY FOR BOTH KINDS OF DECISION, and it is the SELECTED item's.
            # `r` for review, the §3 pane having moved to `3`: this is the key that
            # does something, and the letter belongs to the verb rather than to a pane.
            # `a` and `A` are gone with the split: answering was a pass you ran
            # because an item was blocked and reviewing a pass nobody ran at all,
            # while the raise and the assumption that came of it are usually the same
            # decision twice — so they are one pass, and its subject is the row the
            # cursor is on, which is the thing the author is already thinking about.
            # It opens the TREE: the terminal pass printed a big tree as a wall and
            # made you type node ids out of it. `R` is still that pass, which is also
            # where a raise can be talked through in a chat before it is answered.
            self.open_tree()
        elif ch == ord("R"):
            self.shell(self.runner("--review", item))
        elif ch == ord("a") and self.tree_focused():
            self.tree_answer(item)
        elif ch == ord("f"):
            # ⚠️ ONE KEY, ONE MEANING, WHICHEVER HALF HAS THE KEYS. Correcting was `c`
            # in the tree while `c` was chat in the list, so the same keypress wrote a
            # correction to the task's Log or opened a chat depending on where the
            # focus happened to be. `c` is chat everywhere; `f` fixes a node, and
            # outside the tree it says where to go rather than doing something else.
            if self.tree_focused():
                self.tree_correct(item)
            else:
                self.msg = "f corrects the node under the tree's cursor — tab to the tree"
        elif ch == ord("i") and self.tree_focused():
            self.tree_add(item)
        elif ch == ord("A") and self.tree_focused():
            self.tree_accept(item)
        elif ch == ord("c"):
            self.open_chat(item)
        elif ch == ord("C"):
            self.open_chat("project")
        elif ch == ord("K"):
            # Capital, and it asks: a chain is an hour of somebody's subscription and
            # there is no undo for stopping one halfway.
            self.stop_chain(item)
        elif ch in (ord("["), ord("]")):
            # Paired keys for a paired move, and free in the sibling explorer too —
            # `,`/`.` are its slice stepping, and a key that means one thing in one of
            # two screens and something else in the other is one you stop trusting in
            # both (see `move`).
            self.reorder(-1 if ch == ord("[") else 1)
        elif ch == ord("b"):
            # Unpaired, because a fence has two states and not two directions: the
            # screen shows which one the row is in, so one key is the whole of it.
            self.toggle_break()
        elif ch in (ord("{"), ord("}")):
            # The SHIFTED pair, for the other axis of the same move: same fingers,
            # same pairing, and nothing else to learn once `[`/`]` is in the hand.
            self.reparent(-1 if ch == ord("{") else 1)
        elif ch == ord("w"):
            self.walk_toggle()
        elif ch in (ord("n"), ord("N")) and self.tree_focused():
            self.tree_jump_flag(1 if ch == ord("n") else -1)
        elif ch == ord("n"):
            self.walk_pin(it)
        elif ch == ord("o"):
            self.shell([RUNNER, "--open"])
        elif ch == ord("e"):
            # This branch's part if it has written one, else the part holding the Goal.
            try:
                path = dfs_tree.path_for(item)
            except dfs_paths.NoBranch:
                path = dfs_tree.home_path(item)
            if not path.exists():
                path = dfs_tree.home_path(item)
            if path.exists():
                self.shell([os.environ.get("EDITOR", "vi"), str(path)], pause=False)
            else:
                self.msg = "no item file for %s yet" % item
        return True

    def loop(self):
        try:
            curses.curs_set(0)
        except curses.error:
            pass                    # a terminal that cannot hide it (vt100) shows it
        self.scr.timeout(POLL_MS)
        while True:
            self.draw()
            try:
                ch = self.scr.getch()
            except KeyboardInterrupt:
                break
            if ch == -1:                # the poll tick: nothing pressed
                self.poll()
                continue
            self.msg = ""
            if ch == curses.KEY_RESIZE:
                continue
            if not self.act(ch):
                break


class _Call:
    """One thing the web server asked of the screen, and where its answer goes."""

    def __init__(self, fn):
        self.fn, self.done = fn, threading.Event()
        self.result = self.error = None
        self.cancelled = False


class UiWalker:
    """The screen's own walk, as the web page sees it: the interface `HeadlessWalk` has,
    but nothing here is state. Controls are run on the screen's thread (`UI.call_soon`);
    the snapshot is only read."""

    def __init__(self, ui):
        self.ui = ui

    def start(self, budget):
        self.ui.call_soon(lambda: self.ui.walk_start(budget))

    def stop(self):
        self.ui.call_soon(lambda: self.ui.walk_end_chain() if self.ui.walk_on else None)

    def pin(self, item):
        self.ui.call_soon(lambda: self.ui.walk_pin_id(item))

    def stop_chain(self, item):
        self.ui.call_soon(lambda: self.ui.interrupt_chain(item))

    def set_agent(self, name):
        self.ui.call_soon(lambda: self.ui.set_agent(name))

    def agent(self):
        return self.ui.agent

    # the chats: background agents this process runs, any number, that the page types into
    has_screen = True

    def _relay(self, scope):
        return self.ui.chat_host.get(scope) if self.ui.chat_host else None

    def chat_live(self, scope):
        return self._relay(scope) is not None

    def chat_active(self, scope):
        r = self._relay(scope)
        return bool(r and r.active)

    def chat_open(self, scope):
        """A new chat was opened on the page: start it, and it opens with the summary."""
        self.ui.start_chat(scope)
        return True

    def chat_send(self, scope, text):
        if self.ui.agent != "claude":
            return False
        return self.ui.start_chat(scope).send(text)

    def chat_close(self, scope):
        if self.ui.chat_host:
            self.ui.chat_host.close(scope, why="new chat")

    def chat_interrupt(self, scope):
        r = self._relay(scope)
        return r.interrupt() if r else None

    def loop(self, stop, interval=1.0):
        stop.wait()             # the screen's own poll steps the walk

    def snapshot(self, data=None):
        return self.ui.walk_snapshot()


# Seconds a background chat may sit without a word before the screen ends its agent (0: never).
# The conversation is kept; the next message to it, from the page or `c`, resumes it.
CHAT_IDLE = float(os.environ.get("DFS_CHAT_IDLE", 30 * 60))


def start_web(ui):
    """Serve the web page from this process, beside the screen, unless DFS_WEB says not
    to. It shares the screen's walk. The first free port from DFS_WEB_PORT (8765) up:
    another project's screen may have the first. Returns the server's handle or None."""
    if os.environ.get("DFS_WEB", "1").lower() in ("0", "off", "no", "false"):
        return None
    try:
        import web as dfs_web
        handle = dfs_web.serve(dfs_web.default_host(),
                               int(os.environ.get("DFS_WEB_PORT", "8765")), walker=UiWalker(ui),
                               tries=20)
    except (OSError, ValueError, ImportError) as e:
        ui.event("stop", "web: not serving (%s)" % e)
        return None
    ui.event("run", "web: %s%s" % (handle.url, "  (no login: anyone on this network can "
                                    "answer, correct, accept and start the walk)"
                                    if handle.public else ""))
    ui.msg = "web: " + handle.url
    return handle


def main(stdscr):
    # ncurses defaults ESCDELAY to 1000ms: having read an ESC byte it waits that
    # long for the rest of a possible escape sequence (an arrow key is ESC [ A)
    # before handing the bare ESC over. Measured here: a printable key arrives in
    # 0.2ms, ESC in 1001ms. That is the whole of the "pause after esc" — the pane
    # you left sits on the screen for a second, most visibly on the log panes,
    # where esc is the only way back (the others also toggle off their own key).
    # 25ms is longer than any local terminal takes to deliver the rest of a
    # sequence and short enough to read as instant.
    curses.set_escdelay(25)
    # ⚠️ A terminal without colour (vt100, a serial console) has no default colours
    # to use, and this call RAISING took the whole screen down before it drew. It
    # gets the attribute-only look instead, which is what NO_COLOR gets.
    try:
        curses.use_default_colors()
        coloured = True
    except curses.error:
        coloured = False
    init_look(LIGHT, coloured)
    ui = UI(stdscr)
    # From here and not UI(): the tests build a UI of their own, and one that went to
    # the network and rewrote a pin every time it was constructed would be a test of nix.
    ui.agent_update = start_agent_update()
    ui.web = start_web(ui)
    try:
        ui.loop()
    finally:
        if ui.web is not None:
            ui.web.stop()
        if ui.chat_host is not None:
            ui.chat_host.close_all()        # the chats run for as long as this screen does


if __name__ == "__main__":
    if not dfs_paths.has_roadmap():
        raise SystemExit("dfs_tui: " + dfs_paths.missing_message())
    LIGHT = terminal_is_light()
    curses.wrapper(main)
