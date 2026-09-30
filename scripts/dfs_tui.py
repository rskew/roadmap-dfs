#!/usr/bin/env python3
"""A screen for driving the roadmap: what is open, and the one key that acts on it.

The roadmap is already the state and `dfs_run.sh` is already the
actions. What was missing is the LOOK — "which items are blocked, what is the raise,
how many raises per pick" is four greps and a scroll through a 500-line file, done
before every decision about where to spend a session. So this holds no state of its
own and decides nothing about the WORK: it reads `dfs_state.py` (the same derivation the
runner stops on, so the screen and the loop cannot disagree about what "blocked"
means) and shells out to the runner for everything that writes. The one file it writes
itself is `.dfs/order.md`, which holds no decision and no work state: it is
the order the author means to work the items in, and `[`/`]` move the selected item in
it. See `dfs_order.py` for why that is a file rather than §2.

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

Run:  python3 <scripts>/dfs_tui.py
"""
import collections
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
import tempfile
import sys
import textwrap
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import dfs_runs               # noqa: E402  (needs HERE on the path first)
import dfs_order              # noqa: E402  (the same)
import dfs_paths              # noqa: E402  (the same)
import dfs_limit              # noqa: E402  (the same)
import dfs_tree               # noqa: E402  (the same)

# ⚠️ TWO ROOTS. `ROOT` is the repo being worked on — the CWD, and where everything is
# launched — and `STATE` is that repo's own `.dfs`. `dfs_paths.py` is the one
# place either is derived; read it before spelling any path here.
ROOT = dfs_paths.work_root()
STATE = dfs_paths.state()
EPIC = dfs_paths.EPIC
RUNNER = dfs_paths.rel(dfs_paths.SCRIPTS / "dfs_run.sh")
ART_DIR = dfs_paths.artefacts()
ART_PORT = int(os.environ.get("DFS_ARTEFACT_PORT") or 3016)  # docs/artefacts.md

# ── the walk ──────────────────────────────────────────────────────────────────
#
# A chain ends; something has to decide what happens next. That decision is a
# FUNCTION OF THREE FACTS and nothing else, so it lives out here where it can be
# read and tested without a terminal: what the chain recorded about why it stopped
# (`dfs_run.sh` writes it, `dfs_runs.py` gives it meaning), whether §3 actually
# grew while it ran, and what a depth-first walk would take now (`dfs_state`).
#
# ⚠️ ONLY `limit` MEANS "WAIT AND TRY THE SAME THING AGAIN". Every other unhappy
# ending — a crash, a session that appended nothing, a blown context ceiling —
# means something about the machinery is wrong, and an autonomous spender that
# treats those as "probably temporary" is one that burns a subscription overnight
# on a loop. A usage limit and a crash are both `rc 1`, which is exactly why the
# reason is recorded rather than inferred.
#
# ⚠️ A SUSPECT ITEM IS A RAISE, NOT A STOP. A chain that moved nothing in its tree,
# or a session the runner found `idle`, says something about THAT item, and the
# author's instrument for "look at this item" is a raise: it blocks the item, and
# `next_item` then takes the next branch. Stopping the whole walk instead held every
# other branch hostage to one item, overnight, for a check that can be wrong (see
# `dfs_state.content`). It is still bounded: each raise blocks one item for good, so
# a broken machine raises once per item and ends at "every branch is blocked".
WALK_CONTINUE = {"blocked", "done", "cap"}
# Endings that say something about THE ITEM, so the author hears about it on the item
# and the walk takes the next branch.
WALK_RAISE = {
    "idle": "a session on it changed nothing in the repo",
}
# Endings that STOP the walk, because the next item would fare no better or because
# they are significant enough that a person should look before more is spent.
# ⚠️ `overbudget` and `overturns` stay here: the context ceiling (CHAIN_CEILING,
# 400,000) was set far past any normal overrun precisely so that reaching it means
# the budget is not holding (the highest peak on record is ~237,000). And `failed`
# stays here because it is two things in one word: a session that exited non-zero
# (on 2026-09-25 that was nix failing to fetch the agent, which every item would hit
# next), and a critic or reviewer that changed code or gave no verdict. Raising on
# the item for the first would raise on every item in turn.
WALK_STOP = {
    "dirty": "the working tree has uncommitted work outside .dfs/, which every next "
             "chain would refuse too; commit it, then w",
    "interrupt": "interrupted",
    "overbudget": "a session went past the context ceiling; the budget is not holding",
    "overturns": "a session went past the turn ceiling with no context reading",
    "failed": "a session failed; its console says whether the agent exited non-zero "
              "or a critic or reviewer changed code or gave no verdict",
}
WALK_HOLD = 3600                # the fallback when the reset time is unparseable

# ⚠️ A CHAIN IS NOT ON THE BOX UNTIL IT HAS SAID SO, AND IT SAYS SO SECOND.
# `start_chain` MAKES the run directory and `dfs_run.sh` IDENTIFIES it, by writing
# `meta.json` — and `dfs_runs.run_dirs` skips a directory that has none, because a
# directory without one cannot be asked which item it is or whether it is alive. The
# gap between those two moments is real and was measured on 2026-09-16: the chain
# writes its meta about 110 ms after exec (three python starts before it), while
# `start_chain`'s own `reload()` reaches `run_dirs()` at about 55 ms. So the walk's
# own chain was absent from the list the walk then judged it by. This is how long it
# may stay absent before that is read as a chain that DIED rather than one that has
# not spoken yet.
WALK_START_GRACE = 15           # seconds — see `walk_tick`


def walk_decide(stop, moved, next_item):
    """What the walk does after a chain ends, as `(action, why)`.

    `run` carries the item, `hold` and `stop` carry a reason to show a person.

    `raise` carries the question to put to the author on the item; the walk then
    goes on to the next item, which the raise has just made the item stop being.

    ⚠️ The `moved` cross-check is deliberately redundant with the runner's own
    `idle` stop. The runner refuses to repeat a session that appended nothing
    WITHIN a chain; this refuses to start another chain on an item that produced
    nothing, which is the same guarantee one level up and the only thing standing
    between a broken item and an unattended loop. Both end in a raise, which blocks
    the item, rather than in a stop, which would block every other item too.
    """
    if stop == "limit":
        return ("hold", "a usage limit")
    if stop in WALK_RAISE:
        return ("raise", WALK_RAISE[stop])
    if stop not in WALK_CONTINUE:
        return ("stop", WALK_STOP.get(stop) or stop
                or "the chain ended without recording why")
    if not moved:
        return ("raise", "the chain moved nothing in the task tree")
    if next_item is None:
        return ("stop", "every branch is blocked or finished")
    return ("run", next_item)


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
TREE_QUIET = ("refuted", "parked", "dormant", "pruned")
# The task file's own prose, above the tree: what the nodes are FOR. Goal starts
# open because a review judges nodes against it; Background starts folded because
# it can run to pages (W8's is 5,500 characters) and is reference, not the question.
TREE_SECTIONS = (("Goal", True), ("Background", False))


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


def tree_entries(t, folded=()):
    """The pane's rows, in order: the task's own raises, then the nodes depth-first.

    A node in `folded` keeps its row and hides its descendants. A raise on a node
    rides on the node's row, since that is where the author reads the node; a raise
    naming no node, or a node the tree does not have, is its own row at the top, so
    none is ever unreachable. Above them all, the task's Goal and Background, as
    rows that fold like any other.

    ⚠️ A fold must not hide a raise. The first tree this pane was tried on had its
    one open raise on W13.26, four levels under W13.8, and folding W13.8 took the
    only thing the author was there to answer off the screen. So a folded row
    carries `raises_below`, the count of open raises it is hiding."""
    status, pruned = dfs_tree.effective(t)
    asleep = dfs_tree.dormant(t)
    nodes = dfs_tree.by_id(t)
    on_node, out = {}, []
    for name, shown in TREE_SECTIONS:
        if (t["sections"].get(name) or "").strip():
            out.append(dict(key="section:%s:%s" % (t["task"], name), kind="section",
                            depth=0, name=name, text=t["sections"][name],
                            open_default=shown))
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

    def walk(parent, d):
        for nd in kids_of(parent):
            nid = nd["id"]
            st = ("pruned" if nid in pruned else
                  status[nid] if status[nid] != "open" or nid not in asleep
                  else "dormant")
            kids = kids_of(nid)
            shut = nid in folded and bool(kids)
            below = (sum(len(on_node.get(k["id"], ()))
                         for k in dfs_tree.descendants(t, nid)) if shut else 0)
            out.append(dict(key=nid, kind="node", depth=d, node=nd, status=st,
                            raises=on_node.get(nid, []), kids=len(kids),
                            folded=shut, raises_below=below))
            if nid not in folded:
                walk(nid, d + 1)
    walk(None, 0)
    return out


def archived_fields(text, nid):
    """A node's Approach and Hypothesis as `.dfs/archive/items/<task>.md` holds them.

    A session over the task file's budget moves a determined node's Approach
    and Hypothesis there (`dfs_tree.py shelve`) and leaves `archived.`; the review should
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
    skip = ("Status", "Parent", "Approach", "Hypothesis", "Determination")
    out += [(k, v) for k, v in f.items() if k not in skip]
    out += [("Commit", "%s %s" % (sha[:7], subj)) for sha, subj, _at in commits]
    return out


def strip_comments(text):
    """What the author wrote in an editor, without the `#` lines that framed it."""
    return "\n".join(l for l in text.splitlines() if not l.startswith("#")).strip()


def walk_held_for(seconds):
    """A hold, in the units a person reads: whole minutes, floor 1."""
    return "%dm" % max(1, int(seconds // 60))
POLL_MS = 1000


# ── reading ────────────────────────────────────────────────────────────────────

def load():
    out = subprocess.run([sys.executable, str(HERE / "dfs_state.py"), "--json"],
                         cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit("dfs_state.py failed:\n" + out.stderr)
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
        # definition, measured in dfs_context.py and printed below this.
        out.append(("%s input tokens summed over the turn's requests · %s cached · "
                    "%s output" % (fmt_tokens(usage.get("input_tokens")),
                                   fmt_tokens(usage.get("cached_input_tokens")),
                                   fmt_tokens(usage.get("output_tokens"))), "dim"))
    return out + [("", "dim")] + lines


YOU_CHARS = 600


def you_row(text):
    """What the author said, whole unless it is the runner's briefing.

    ⚠️ A chain's first user message is not something anybody typed: it is the
    briefing `dfs_run.sh` builds, §0-§2 of the roadmap verbatim, around 40,000
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
    other resident and are measured in dfs_context.py, in one place, deliberately.
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

    §0.5b's 125,000 is measured by dfs_context.py and the runner's chain
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
                                                  HERE / "dfs_context.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_sc = _dfs_context()
peak_and_turns, CHECKPOINT, FLAT_TO = _sc.peak_and_turns, _sc.CHECKPOINT, _sc.FLAT_TO


def transcript_path(session_id, agent):
    """One definition, in dfs_context.py; this keeps the Path return the panes use."""
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
    n = len(it.get("open_raises") or ()) + len(it.get("standing") or ())
    return "rev %d" % n if n else ""


def start_agent_update():
    """Move the agent pin on to today's nixpkgs, in the background (dfs_agent.py).

    ⚠️ Background because it is a download, and `c` must not wait for it: the pin
    moves only once the new claude-code is built, so a key pressed meanwhile starts
    the old one at once. Its own session, so a Ctrl-C in a chat started from here
    does not kill it halfway.
    """
    try:
        return subprocess.Popen(
            [sys.executable, str(dfs_paths.SCRIPTS / "dfs_agent.py"), "--update"],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, start_new_session=True)
    except OSError:
        return None


class UI:
    def __init__(self, stdscr):
        self.scr = stdscr
        self.sel = 0
        self.run_sel = 0
        self.art_sel = 0
        self.list_scroll = 0
        self.scroll = 0
        self.pane = "item"          # item | reglog | runs | agentlog | todo | artefact
        # Which half of the item pane the keys drive: the item list at the top, or
        # the item's tree below it. Tab swaps them; see `tree_focused`.
        self.focus = "list"
        self.codex = False
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
        self._sel_row = None
        self.body_h = 1             # rows the detail pane showed at the last draw
        # The walk. Off until somebody turns it on, because it spends a
        # subscription with nobody watching (see `walk_toggle`).
        self.walk_on = False
        self.walk_budget = 20       # SESSIONS, over the whole walk — see walk_toggle
        self.walk_spent = 0         # sessions run by chains this walk has finished
        self.walk_until = 0.0       # epoch; a usage-limit hold
        self.walk_dir = None        # the run directory of the chain THIS started
        self.walk_started = 0.0     # epoch; when it was started — see walk_tick
        self.walk_content = set()   # what the trees said when that chain started
        self.walk_note = ""         # why the last walk stopped, until the next one
        self.walk_next = None       # an item the author pinned for the next chain — walk_pin
        # The review tree. Folds and opened nodes are keyed by node id, which
        # carries its task, so a fold survives a trip to another item and back.
        self.tree_sel = 0
        self.tree_item = None       # the item the cursor was placed on
        self.tree_folded = set()
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
        dfs_runs.py, those are the directories from before a pid was recorded.
        """
        for r in self.chains:
            if r["item"] == item and r["mode"] == mode and r["state"] != "unknown":
                return r
        return None

    def live(self, mode="work"):
        return [r for r in self.chains if r["live"] and r["mode"] == mode]

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
        elif self.tree_focused():
            self.tree_sel = max(0, min(self.tree_sel + step, len(self.tree_rows()) - 1))
        else:
            self.sel = max(0, min(self.sel + step, len(self.items) - 1))
            self.scroll = 0         # a new item is read from its top

    def nodes(self):
        """The tree as the screen is showing it, for `dfs_order` to move a node in.

        ⚠️ THE FENCES GO BACK IN HERE. The derivation hands back items alone, each
        carrying the SEGMENT it is in, and `write_order` writes the whole tree — so a
        list built without the breaks would delete every fence in the file the first
        time somebody pressed `[`. The segment numbers are what they are put back by,
        including a gap left by a segment whose items have all been closed and
        deleted, which is why the inner loop is a `while` and not an `if`.
        """
        out, seg = [], 0
        for i in self.items:
            while seg < i.get("segment", 0):
                out.append((dfs_order.BREAK, 0))
                seg += 1
            out.append((i["id"], i["depth"]))
        return out

    def list_rows(self):
        """The list as ROWS: every item, with a rule wherever a fence sits.

        A fence is DRAWN and not selectable. Everything this screen does to a row —
        run it, answer it, move it, stop its chain — needs an item, so a cursor that
        could land on a rule would make every one of those keys begin by asking
        whether it had. `b` toggles the fence above the selected item instead, which
        is the whole of what a fence has to do from here.
        """
        rows, seg = [], 0
        for idx, it in enumerate(self.items):
            while seg < it.get("segment", 0):
                rows.append(("break", it.get("waiting_on")))
                seg += 1
            rows.append(("item", idx))
        return rows

    def reorder(self, step):
        """Move the selected item among ITS SIBLINGS, carrying its subtree.

        Ids sort by when work was SCOPED, which is rarely the order to do it in, so
        the list's own sequence carried no information and the real one was in the
        author's head. This writes `.dfs/order.md` and nothing else — no entry, no
        item file, nothing above §3 (`dfs_order.py` says why).

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
        if self.retree(dfs_order.break_toggled(nodes, it["id"]), it, refusal):
            self.said(it, "is no longer behind a break" if had
                          else "now waits on everything above the break")

    def said(self, it, did):
        self.msg = "%s %s — %s is uncommitted" % (
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
        dfs_order.write_order(after)
        self.reload()
        self.sel = next((n for n, i in enumerate(self.items) if i["id"] == it["id"]),
                        self.sel)
        self.scroll = 0
        return True

    def reload(self, note=""):
        self.data = load()
        self.dirty = roadmap_dirty()
        self.chains = dfs_runs.run_dirs()
        self.runs = discover_runs()
        self.sig = watch_signature()
        self.sel = min(self.sel, max(0, len(self.items) - 1))
        self.run_sel = min(self.run_sel, max(0, len(self.runs) - 1))
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
        self.walk_tick()
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
                self.msg = self.ended_note(ended[0])
            return
        before = self.data["metrics"]["entries"]
        self.reload()
        after = self.data["metrics"]["entries"]
        ended = [r for r in self.chains
                 if r["dir"] in was_live and not r["live"]]
        if ended:
            self.msg = self.ended_note(ended[0])
        elif after > before:
            # Worth saying out loud: in this tree it is usually a peer session, and
            # an entry appearing under you changes what is safe to start.
            self.msg = "the trees moved: +%d" % (after - before)
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
        if y < 0 or y >= h or x >= w:
            return
        text = printable(text)[: max(0, w - x - 1)]
        try:
            self.scr.addstr(y, x, text, attr)
        except curses.error:
            pass

    def status_attr(self, status):
        return {"blocked": curses.color_pair(1) | curses.A_BOLD,
                "done": curses.color_pair(2),
                "open": curses.color_pair(3)}.get(status, 0)

    def header(self):
        m = self.data["metrics"]
        h, w = self.scr.getmaxyx()
        agent = "codex" if self.codex else "claude"
        live = len(self.live())
        # What a depth-first walk would take next, which is the whole point of the
        # tree and is otherwise only derivable by reading every row's blocked_by.
        nxt = self.data.get("next_item")
        # ⚠️ The walk goes in the HEADER, not the message line: a message is the last
        # thing that happened and scrolls away, while "something is spending money
        # unattended right now" is a state, and one nobody should have to remember
        # they left on.
        walk = self.walk_badge()
        left = " %sroadmap · %d tasks · progress %d · next %s%s " % (
            ("%s · " % walk) if walk else "",
            len(self.items), m["entries"], nxt or "—",
            (" · %d running" % live) if live else "")
        dirty, staged = self.dirty
        warn = ""
        if staged:
            warn = "%d STAGED " % staged
        elif dirty:
            warn = "%d uncommitted " % dirty
        right = warn + ("%s " % agent)
        self.put(0, 0, left.ljust(max(0, w - len(right) - 1)) + right,
                 curses.A_REVERSE | curses.A_BOLD)
        if walk:
            # Painted over the bar it is already part of, the way the item list
            # overlays a status cell — the bar is drawn in one write, so a segment
            # of it can only differ by being written again. ⚠️ Red is reserved for a
            # walk that is SPENDING; the same red on "walk off" would teach the eye
            # to read the one colour that means "something is running unattended" as
            # decoration.
            self.put(0, 1, walk, curses.A_REVERSE | curses.A_BOLD
                     | (curses.color_pair(1) if self.walk_on else curses.A_DIM))
        # §0.6 is the experiment, so its counts are on the screen rather than in a
        # script somebody remembers to run. Cumulative, and labelled as such: the
        # thing being watched is the TRANSITION, never the level.
        #
        # Ordered by what §0.6 says fails first and quietest, because a narrow
        # terminal drops from the END: raises per pick is the metric that decides
        # whether this is a roadmap or a fancy inbox, and the STANDING assumption
        # count is the one that failed silently for 376 entries — nothing closed an
        # ASSUME, so nobody could see that nothing had been reviewed. Neither may be
        # the segment that falls off.
        segs = ["open raises %d" % m["open_raises"],
                "nodes %d" % m["nodes"],
                "confirmed %d" % m["confirmed"],
                "refuted %d" % m["refuted"],
                "parked %d" % m["parked"],
                "pruned %d" % m["pruned"],
                "critic %d ok %d issues" % (m["critic_ok"], m["critic_issues"]),
                "review %d ok %d issues" % (m["review_ok"], m["review_issues"]),
                "corrected %d" % m["corrections"],
                "sessions %d" % m["work_sessions"],
                "~%.0fk tok" % (m["tokens"] / 1000.0)]
        while len(segs) > 1 and len(" · ".join(segs)) > w - 3:
            segs.pop()
        self.put(1, 1, " · ".join(segs), curses.A_DIM)

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
                w = self.scr.getmaxyx()[1] - 1
                self.put(y, 2, (label + "─" * max(0, w - 2 - len(label)))[:max(0, w - 2)],
                         curses.A_DIM)
                continue
            idx = val
            it = self.items[idx]
            marker = ">" if idx == self.sel else " "
            attr = ((curses.A_REVERSE if self.focus != "tree" or self.pane != "item"
                     else curses.A_BOLD) if idx == self.sel else 0)
            tag, tag_attr = tags[it["id"]]
            # ⚠️ The STATUS cell is the item's own fact; where it SITS is a second
            # one. An item whose branch an ancestor is holding reads "↳W10" — the
            # question to go and answer — because "open" would be true and useless.
            state = row_state(it)
            rev = revs[it["id"]]
            cell = ("  " * it["depth"]) + it["id"]
            line = "%s %s %s %s%s%s" % (marker, cell.ljust(idw), state.ljust(statw),
                                        rev.ljust(revw), tag.ljust(tagw), it["goal"])
            self.put(y, 0, line.ljust(self.scr.getmaxyx()[1] - 1), attr)
            if idx != self.sel:
                # ⚠️ `3 + idw`, not `2 + idw`: the line is marker, space, cell, SPACE,
                # state, so the colour re-put has to clear the separator too or it
                # lands a column left of where the format put it — which showed as
                # the selected row's status sitting one column right of every other
                # row's, the hand-written offset the id column above is measured to
                # avoid. The tag is the same distance further on.
                st_at = (curses.A_DIM if state != it["status"]
                         else self.status_attr(it["status"]))
                self.put(y, 3 + idw, state.ljust(statw), st_at)
                # ⚠️ Every later column is measured off the one before it, never
                # spelled: the review cell is between the status and the run tag, so
                # the tag's offset moves with it. A hand-written offset here is what
                # put the selected row's status one column right of every other row's.
                if rev:
                    self.put(y, 3 + idw + statw, rev.ljust(revw), curses.A_DIM)
                if tag:
                    self.put(y, 4 + idw + statw + revw, tag, tag_attr)

    def wrapper(self, width):
        w = max(20, width - 2)
        out = []

        def para(text, attr=0, indent="", hang=None):
            """Wrap, with the continuation lines visibly continuations.

            A wrapped `Options:` field whose second line starts at the same column
            as the first reads as a second field, which is the one thing these
            entries must not do — §0.9's field names are what makes the log
            greppable and what the eye uses to find `Against that` before
            approving anything.
            """
            body = indent if hang is None else indent + hang
            for line in textwrap.wrap(text, w - len(indent),
                                      initial_indent=indent,
                                      subsequent_indent=body) or [indent]:
                out.append((line, attr))
        return out, para

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
                 curses.A_DIM if done else 0, hang="    ")
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
                attr = curses.A_DIM
            out.append((head, attr))
            for line in e["body"].splitlines():
                para(line, curses.A_DIM, indent="  ", hang="    ")
        if len(out) == 2:
            out.append(("  nothing in this task's log yet", curses.A_DIM))
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

    def lines_artefacts(self, width):
        out, para = self.wrapper(width)
        it = self.current()
        arts = self.artefacts()
        self.art_sel = max(0, min(self.art_sel, len(arts) - 1))
        up = art_server_up()
        out.append(("artefacts · a picture for one raise, opened in a browser",
                    curses.A_BOLD))
        out.append(("  enter opens one · ↑↓/jk moves · esc back", curses.A_DIM))
        out.append((("  served on %d" % ART_PORT) if up else
                    ("  nothing on %d — python3 -m http.server %d --directory %s"
                     % (ART_PORT, ART_PORT, dfs_paths.rel(ART_DIR))),
                    curses.A_DIM if up else curses.color_pair(3)))
        out.append(("", 0))
        if not arts:
            out.append(("  no artefact in %s" % dfs_paths.rel(ART_DIR), curses.A_DIM))
            return out
        self._sel_row = None
        group = None
        for i, a in enumerate(arts):
            head = ("named by %s" % (it["id"] if it else "this item")) if a["mine"] \
                else "everything else on disk"
            if head != group:
                group = head
                out.append(("", 0))
                out.append(("  " + head, curses.A_DIM))
            title = art_title(ART_DIR / a["name"]) if a["exists"] else "MISSING"
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
                out.append(("    named in %s" % a["entry"], curses.A_DIM))
        return out

    def lines_runs(self, width):
        """The run selector: which agent run to look at. Live chains at the top."""
        out, para = self.wrapper(width)
        live = len(self.live())
        out.append(("agent runs — %d log%s on disk%s" % (
            len(self.runs), "" if len(self.runs) == 1 else "s",
            (", %d chain%s running" % (live, "" if live == 1 else "s")) if live
            else ", newest first"), curses.A_BOLD))
        out.append(("  enter opens one · ↑↓/jk moves · esc back", curses.A_DIM))
        out.append(("", 0))
        if not self.runs:
            out.append(("  nothing in " + ", ".join(
                "%s/dfs_run_*" % r for r in dfs_runs.run_roots()),
                curses.A_DIM))
            out.append(("  a console appears here the moment a chain starts", curses.A_DIM))
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
        out.append((run["path"], curses.A_DIM))
        if chain["live"]:
            out.append(("the script above, the agent's own turns below · "
                        + ("following · [K] stops the chain" if self.follow
                           else "not following · [G] jumps back to the last line"),
                        curses.A_DIM))
        out.append(("", 0))
        for line in tail_lines(run["path"]):
            out.extend(self.log_wrap(width, line.replace("\t", "    "), 0))
        if len(out) == 4:
            out.append(("  nothing written yet — the agent is starting", curses.A_DIM))
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
        at the end (`dfs_run.sh`, run_session).
        """
        agent = chain["agent"] or "claude"
        if not chain["sid"]:
            if chain["live"]:
                return [("", 0), ("the running session has not named itself yet — a "
                                  "chain started before this was recorded shows its "
                                  "turns only when it ends", curses.A_DIM)]
            return []
        tp = transcript_path(chain["sid"], agent)
        out = [("", 0)]
        if tp is None or not tp.exists():
            out.append(("no transcript at %s yet" % tp, curses.A_DIM))
            return out
        peak, responses = self.measured(tp, agent)
        note = ""
        if peak > CHECKPOINT:
            note = "  ← past the %s checkpoint" % fmt_tokens(CHECKPOINT)
        out.append(("agent · session %s · %d responses · peak context %s%s"
                    % (chain["sid"][:8], responses, fmt_tokens(peak), note),
                    curses.color_pair(1) if peak > CHECKPOINT else curses.A_BOLD))
        out.append((str(tp), curses.A_DIM))
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
        out.append((run["path"], curses.A_DIM))
        out.append(("", 0))

        result, events = read_result(run["path"])
        sid, agent = "", run["agent"] or "claude"
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
            out.append(("session %s" % (sid or "unknown"), curses.A_DIM))
            out.append(("", 0))
            out.append(("result", curses.A_BOLD))
            for line in str(result.get("result", "")).splitlines() or [""]:
                para(line, indent="  ", hang="  ")
        else:
            # codex: a stream of events rather than one object, condensed to the same
            # rows the transcript pane draws instead of dumped back as the JSON it
            # arrived in.
            sid = codex_session_id(events)
            out.append(("session %s" % (sid or "unknown"), curses.A_DIM))
            out.append(("", 0))
            out.extend(self.emit_roles(codex_result_lines(events), width))

        tp = transcript_path(sid, agent)
        out.append(("", 0))
        if not sid:
            out.append(("no session id in this result, so there is no transcript to "
                        "open — which is itself the finding on a run that died before "
                        "it started", curses.A_DIM))
        elif tp is None or not tp.exists():
            out.append(("transcript not on this box: %s" % tp, curses.A_DIM))
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
        budget is written in (dfs_context.py, one definition) — so on a live
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
                "meta": curses.A_DIM,
                "think": curses.A_DIM,
                "run": curses.color_pair(2),
                "out": curses.A_DIM,
                "err": curses.color_pair(1),
                "head": curses.A_BOLD,
                "dim": curses.A_DIM}.get(role, 0)

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
                     curses.A_DIM if meta else curses.color_pair(3))
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
                        made.append(("  ~ thinking, %d chars" % n, curses.A_DIM))
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
                        "   [%d chars]" % len(s or "")), curses.A_DIM))
        return made

    def detail_lines(self, width):
        if self.pane == "todo":
            return self.lines_todo(width)
        if self.pane == "runs":
            return self.lines_runs(width)
        if self.pane == "artefact":
            return self.lines_artefacts(width)
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
        return tree_entries(t, self.tree_folded)

    def tree_row(self):
        rows = self.tree_rows()
        return rows[self.tree_sel] if rows and self.tree_sel < len(rows) else None

    def lines_tree(self, width):
        out, para = self.wrapper(width)
        it = self.current()
        if it is None:
            return [("no items — press [o] to open one", 0)]
        if self.tree_item != it["id"]:
            self.tree_item, self.tree_sel = it["id"], 0
        t, archive, commits = self.tree_of(it["id"])
        rows = tree_entries(t, self.tree_folded)
        self.tree_sel = max(0, min(self.tree_sel, len(rows) - 1))
        para("%s — %s" % (it["id"], it["goal"]), curses.A_BOLD)
        out.append((it["why"], self.status_attr(it["status"])))
        if it.get("corrections"):
            out.append(("corrected, not yet carried out: %s"
                        % ", ".join(it["corrections"]), curses.A_BOLD))
        out.append(("sessions %d of %d since the author spoke · critic in %d"
                    % (it.get("sessions", 0), 10, max(5 - it.get("since_critic", 0), 0)),
                    curses.A_DIM))
        out.append(("", 0))
        if not rows:
            out.append(("no nodes yet", curses.A_DIM))
            return out
        self._sel_row = None
        red = curses.color_pair(1)
        # The cursor is drawn only where the keys go: two highlighted rows on one
        # screen is a question about which one j moves.
        focused = self.tree_focused()
        for i, row in enumerate(rows):
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
                            r"^\s*([-*+]|\d+[.)])\s", line) else "")
                    out.append(("", 0))
                continue
            if row["kind"] == "raise":
                r = row["raise_"]
                on = (" on " + r["args"][0]) if r["args"] else " on the task"
                out.append(("%s ● RAISE %s%s — needs you" % (cursor, r["ts"], on),
                            red | curses.A_BOLD | (curses.A_REVERSE if sel else 0)))
                for line in r["body"].splitlines():
                    para(line, indent="    ", hang="  ")
                continue
            nd, st = row["node"], row["status"]
            fold = ("▸" if row["folded"] else "▾") if row["kids"] else " "
            flag = "  ● raise" if row["raises"] else ""
            if row["raises_below"]:
                flag += "  ● %d raise%s below" % (row["raises_below"],
                                                  "" if row["raises_below"] == 1 else "s")
            attr = (curses.A_DIM if st in TREE_QUIET else 0)
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
            head = "%s %s%s %s %s %s%s" % (cursor, pad, fold, nd["id"],
                                           TREE_MARK.get(st, "?"), nd["title"], flag)
            lead = len(cursor) + 1 + len(pad) + len(fold) + 1
            for line in (textwrap.wrap(
                    head, max(20, width - 2), subsequent_indent=" " * (lead + 2))):
                out.append((line, attr, below if len(out) > first else above))
            if row["key"] not in self.tree_open and not row["raises"]:
                continue
            inner = " " * (lead + 2)
            for r in row["raises"]:
                out.append((inner + "RAISE %s — needs you" % r["ts"], red | curses.A_BOLD))
                for line in r["body"].splitlines():
                    para(line, indent=inner + "  ", hang="  ")
            if row["key"] in self.tree_open:
                out.append((inner + "status: %s" % st, curses.A_DIM))
                for label, text in node_detail(nd, archived_fields(archive, nd["id"]),
                                               commits.get(nd["id"], ())):
                    para("%s: %s" % (label, text),
                         curses.A_DIM if label == "Commit" else 0,
                         indent=inner, hang="    ")
                out.append(("", 0, BLANK_GUIDES))
            out[first:] = [ln if len(ln) > 2 else ln + (below,) for ln in out[first:]]
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
        actions = [enter, "w walk" if not self.walk_on else "w STOP WALK", "n walk next",
                   "r review", "c chat", "o open", "e edit", "[/] order",
                   "{/} branch", "b break", "3 log", "l logs", "v art", "t todo",
                   "x codex"]
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
            actions = ["⏎ open", "space fold", "a answer", "f correct"]
            if it is not None and it["status"] == "done" and self.tree_acceptable(it):
                actions.append("A accept")
            actions += ["R terminal review", "c chat", "tab items"]
            nav = ["↑↓/jk node", "d/u scroll"]
        elif self.pane == "item":
            actions.insert(1, "tab tree")
        elif self.pane == "agentlog":
            actions, nav = ["l run list", "esc back"], ["↑↓ scroll", "d/u half"]
            if self.open_run and is_growing(self.open_run):
                actions.insert(0, "G follow" if not self.follow else "● following")
        segs = actions + nav + ["q quit"]
        while len(segs) > 2 and len(" · ".join(segs)) > w - 2:
            segs.pop(-2)
        return " · ".join(segs)

    def draw(self):
        self.scr.erase()
        h, w = self.scr.getmaxyx()
        self.header()
        list_h = min(len(self.list_rows()), max(3, (h - 6) // 3))
        self.draw_list(3, list_h)
        sep = 3 + list_h
        self.put(sep, 0, "─" * max(0, w - 1), curses.A_DIM)
        name = {"item": "item", "reglog": "task log", "runs": "agent runs",
                "agentlog": "agent log", "todo": "epic todo",
                "artefact": "artefacts"}[self.pane]
        if self.pane == "item":
            name = "tree" if self.tree_focused() else "item"
        self.put(sep, 2, " %s " % name, curses.A_DIM)

        body_top = sep + 1
        body_h = max(1, h - body_top - 2)
        self.body_h = body_h
        self._sel_row = None
        lines = self.detail_lines(w)
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
            if self._sel_row < self.scroll:
                self.scroll = self._sel_row
            elif self._sel_row >= self.scroll + body_h:
                self.scroll = self._sel_row - body_h + 1
        self.scroll = max(0, min(self.scroll, max(0, len(lines) - body_h)))
        for i, line in enumerate(lines[self.scroll:self.scroll + body_h]):
            text, attr = line[0], line[1]
            self.put(body_top + i, 1, text, attr)
            # A third element is indent-guide columns (see `lines_tree`), drawn only
            # into blank cells so a guide never overwrites text, and never across the
            # selected row, whose reverse video is the cursor.
            if len(line) > 2 and not attr & curses.A_REVERSE:
                for c in line[2]:
                    if c >= len(text) or text[c] == " ":
                        self.put(body_top + i, 1 + c, "│", curses.A_DIM)
        if len(lines) > body_h:
            tag = " %d-%d of %d " % (self.scroll + 1,
                                     min(self.scroll + body_h, len(lines)), len(lines))
            self.put(sep, max(0, w - len(tag) - 3), tag, curses.A_DIM)

        self.put(h - 2, 0, self.msg[: w - 1], curses.A_BOLD)
        self.put(h - 1, 0, self.footer(w).ljust(max(0, w - 1)), curses.A_REVERSE)
        self.scr.refresh()

    # ---- acting --------------------------------------------------------------

    def shell(self, argv, pause=True):
        """Hand the real terminal over. Sessions are interactive and stream output."""
        curses.def_prog_mode()
        curses.endwin()
        print("\n$ " + " ".join(argv) + "\n", flush=True)
        try:
            rc = subprocess.call(argv, cwd=ROOT)
        except KeyboardInterrupt:
            rc = 130
            print("\n(interrupted)")
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

    def start_chain(self, item, cap):
        """Start a work chain in the background and come straight back.

        ⚠️ The run directory is made HERE and handed down. The runner would mktemp
        its own, and a parent that never reads the child's stdout could not learn
        the name — so the console this pane tails would be a file nobody could find.

        Detached (`start_new_session`) so it is not in this screen's process group:
        a Ctrl-C aimed at the TUI, or quitting it, must not take a chain that has
        been running for an hour with it. The flip side is that nothing else will
        stop it either, which is what `K` is for.
        """
        live = dfs_runs.live_for(item)
        if live:
            self.msg = "%s already has a chain running (pid %s, %s) — [l] to watch it" % (
                item, live["pid"], dfs_runs.progress(live))
            return
        root = dfs_runs.ensure_run_root()
        rundir = tempfile.mkdtemp(prefix="dfs_run_", dir=root)
        console = os.path.join(rundir, "console.log")
        argv = self.runner(item, cap)
        env = dict(os.environ, RUNDIR=rundir)
        try:
            with open(console, "ab") as fh:
                fh.write(("$ %s\n\n" % " ".join(argv)).encode())
                proc = subprocess.Popen(argv, cwd=ROOT, env=env,
                                        stdin=subprocess.DEVNULL, stdout=fh,
                                        stderr=subprocess.STDOUT,
                                        start_new_session=True)
        except OSError as e:
            self.msg = "could not start %s: %s" % (argv[0], e)
            return None
        self.reload()
        self.msg = "%s: chain started in the background, cap %s, pid %d — [l] to watch" % (
            item, cap, proc.pid)
        # ⚠️ The DIRECTORY, not the pid: the walk has to judge the chain it started
        # and no other, and a pid is reused while a run directory is not (see
        # `dfs_runs.pid_is_chain`, which needs both to answer even "is it alive").
        return rundir

    # ---- the walk ------------------------------------------------------------

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
        raw = self.prompt("walk: how many sessions? (last walk: %d, nothing cancels)"
                          % self.walk_budget, "")
        if not raw:
            self.msg = "walk: not started — it wants a number of sessions"
            return
        try:
            budget = int(raw)
        except ValueError:
            self.msg = "walk: %r is not a number of sessions" % raw
            return
        if budget < 1:
            self.msg = "walk: a budget of %d sessions is not a walk" % budget
            return
        self.walk_budget = budget
        self.walk_spent = 0
        self.walk_on = True
        self.walk_until = 0.0
        self.walk_dir = None
        self.walk_note = ""
        self.msg = "walk: on, %d sessions — w to stop" % budget
        self.walk_tick()

    def walk_off(self, why):
        self.walk_on = False
        self.walk_dir = None
        self.walk_until = 0.0
        self.walk_note = why
        self.msg = "walk: stopped — %s" % why

    def walk_end_chain(self):
        """Turn the walk off by hand, and end its chain after the run it is in.

        ⚠️ The walk's chain carries the walk's whole remaining budget as its cap
        (`walk_tick`), so turning the walk off and leaving that chain alone left it
        free to run every session the walk had left, with nothing watching it — W19
        went from run 3 to run 5 of 33 after the walk was off. The cap is lowered to
        the run it is on (`dfs_runs.lower_cap`), which is a chain of one from here:
        the session in flight finishes its node's work, and no further one starts.
        `K` is still the key for stopping the run in flight as well."""
        run = self.walk_run()
        self.walk_off("stopped by hand")
        if not (run and run["live"]):
            return
        n = int(run["run"] or 0)
        try:
            dfs_runs.lower_cap(run["dir"], n)
        except OSError as e:
            self.msg = ("walk: stopped, but %s's chain could not be told to end (%s) — "
                        "K stops it" % (run["item"], e))
            return
        self.msg = ("walk: stopped — %s's chain ends after run %d; K stops that run too"
                    % (run["item"], n) if n else
                    "walk: stopped — %s's chain ends before its first run" % run["item"])

    def walk_run(self):
        """The run directory this walk started, as `dfs_runs` currently sees it."""
        return next((r for r in self.chains if r["dir"] == self.walk_dir), None)

    def walk_used(self):
        """Sessions spent so far: the finished chains, plus the live one's own count.

        ⚠️ Read off the chain rather than counted here. `dfs_run.sh` records the run
        it is ON before starting it, so this is the number of sessions that have
        BEGUN — which is the one that should be charged against a budget, since a
        session that started has already cost its context.
        """
        run = self.walk_run()
        return self.walk_spent + (int(run["run"] or 0) if run else 0)

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
            return "● WALK HELD %s%s %s" % (walk_held_for(held), pin, spent)
        run = self.walk_run()
        if run and run["live"]:
            return "● WALK %s%s %s" % (run["item"], pin, spent)
        return "● WALK%s %s" % (pin, spent)

    def walk_tick(self):
        """One step of the walk, on the poll this screen already runs.

        ⚠️ It waits on ANY live work chain, not only its own: two agents in one tree
        append to §3 at the same time, which is how this roadmap's ids collided in
        the first place. The walk is a second pair of hands, not a second author.
        """
        if not self.walk_on:
            return
        # ⚠️ THE WALK DECIDES ON A LIST IT MADE ITSELF. `poll` runs this tick BEFORE
        # it re-derives `self.chains`, so what it hands over is the list
        # `start_chain`'s own reload made — and that reload happens BEFORE the chain
        # has written its `meta.json` (see `WALK_START_GRACE`). Every question below
        # is about chains, so every one of them was being asked of a list that could
        # not contain the chain this walk had just started: the walk turned itself
        # off one second after starting its first chain, every time, and the chain
        # ran on unwatched. Nobody saw why either — `walk_off` writes `self.msg` and
        # the same `poll` overwrote it with "refreshed" before the screen was drawn,
        # which is why a stop is now in the HEADER too (`walk_badge`).
        self.refresh_chains()
        if self.live():
            return
        if time.time() < self.walk_until:
            return

        if self.walk_dir is not None:
            run = self.walk_run()
            if run is None:
                # Made, but not yet named by the chain itself. That is a start in
                # progress, not a death — and the difference is how long it has had.
                if time.time() - self.walk_started < WALK_START_GRACE:
                    return
                self.walk_off("the chain it started is no longer on the box")
                return
            if run["live"]:
                return
            # ⚠️ And the ROADMAP is read after the chain ended, for the same reason
            # the chain list is. `poll` reloads only once this tick has returned, so
            # a chain whose last append and whose exit landed inside the same second
            # would be judged against a §3 that predates its own entry — and an
            # entry the walk cannot see is `moved` false, which STOPS the walk and
            # blames the item for appending nothing.
            self.reload()
            moved = bool(set(self.data.get("content", ())) - self.walk_content)
            action, why = walk_decide(run["stop"], moved,
                                      self.walk_next or self.data.get("next_item"))
            self.walk_spent += int(run["run"] or 0)
            self.walk_dir = None
            if action == "hold":
                # Until the stated reset when there is one; the flat hour only when
                # the provider said nothing we can parse (dfs_limit.reset_at).
                now = time.time()
                at = dfs_limit.reset_at(run["resets"], now)
                self.walk_until = at if at is not None else now + WALK_HOLD
                tail = (" (%s)" % run["resets"]) if run["resets"] else ""
                self.msg = "walk: %s hit %s — holding %s%s" % (
                    run["item"], why, walk_held_for(self.walk_until - now), tail)
                return
            if action == "stop":
                self.walk_off("%s: %s" % (run["item"], why))
                return
            if action == "raise":
                if not self.walk_raise(run["item"], why):
                    return

        left = self.walk_budget - self.walk_spent
        if left < 1:
            self.walk_off("%d sessions spent" % self.walk_spent)
            return
        item = self.walk_pinned() or self.data.get("next_item")
        if item is None:
            self.walk_off("every branch is blocked or finished")
            return
        self.walk_next = None
        self.walk_content = set(self.data.get("content", ()))
        self.walk_started = time.time()
        # ⚠️ The chain's cap IS the remaining budget, which is what makes the budget
        # a real bound rather than a thing checked between chains: a chain cannot
        # overshoot it by running one more session than the walk has left to spend.
        self.walk_dir = self.start_chain(item, str(left))
        if self.walk_dir is None:
            self.walk_off("could not start a chain on %s" % item)

    def walk_pinned(self):
        """The pinned item, while it can still be worked; a pin that cannot is dropped.

        ⚠️ Checked when the chain STARTS, not when the key was pressed: a chain on an
        item that finished or raised in between would end "done before run 1" having
        moved nothing, and `walk_decide` would raise on it for that. Only the item's
        OWN status is asked — a fence or an ancestor's raise is exactly the order the
        author is overriding by pinning it."""
        if self.walk_next is None:
            return None
        row = next((i for i in self.items if i["id"] == self.walk_next), None)
        if row is None or row["status"] != "open":
            self.msg = "walk: dropped the pin on %s — it is %s" % (
                self.walk_next, row["status"] if row else "gone")
            self.walk_next = None
            return None
        return self.walk_next

    def walk_pin(self, it):
        """`n`: the walk's next chain goes to the selected item, not `next_item`.

        The walk's chain carries the walk's whole remaining budget, so "next" would
        otherwise mean "whenever this item stops being workable". A chain on another
        item is ended after the run in flight (`dfs_runs.lower_cap`, as `w` does), so
        the switch happens at the next session. After that one chain, `next_item`
        decides as usual — and since a walk finishes what it started
        (`dfs_state.continue_last`), that keeps it on the pinned item while it is
        open and nothing holds it. `n` on the pinned item takes the pin away."""
        if it is None:
            return
        if self.walk_next == it["id"]:
            self.walk_next = None
            self.msg = "walk: pin on %s removed" % it["id"]
            return
        if it["status"] != "open":
            self.msg = "walk: %s is %s — nothing for a chain to do there" % (
                it["id"], it["status"])
            return
        self.walk_next = it["id"]
        run = self.walk_run()
        if not (self.walk_on and run and run["live"]) or run["item"] == it["id"]:
            self.msg = ("walk: its next chain goes to %s" % it["id"] if self.walk_on
                        else "walk: %s goes first when w starts it" % it["id"])
            return
        n = int(run["run"] or 0)
        try:
            dfs_runs.lower_cap(run["dir"], n)
        except OSError as e:
            self.msg = ("walk: %s pinned, but %s's chain could not be told to end (%s)"
                        % (it["id"], run["item"], e))
            return
        self.msg = "walk: %s's chain ends after run %d, then %s" % (
            run["item"], n, it["id"])

    def walk_raise(self, item, why):
        """Put a suspect item to the author and re-read the trees; True to carry on.

        ⚠️ The raise is what moves the walk on, so it is checked rather than trusted:
        if `next_item` still names the item afterwards, the raise did not block it,
        and starting another chain there is the loop this exists to prevent."""
        body = ("The walk suspects a problem with %s: %s. Look at the last chain's "
                "run and the tree (%s --review %s): answer this to let the walk take "
                "it again, or correct the node where it went wrong."
                % (item, why, RUNNER, item))
        try:
            dfs_tree.append_log(item, "raise", (), body)
        except (OSError, ValueError) as e:
            self.walk_off("%s: %s, and the raise failed (%s)" % (item, why, e))
            return False
        self.reload()
        if self.data.get("next_item") == item:
            self.walk_off("%s: %s, and the raise did not block it" % (item, why))
            return False
        self.msg = "walk: raised on %s — %s; moving on" % (item, why)
        return True

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
            os.killpg(r["pid"], signal.SIGINT)
        except OSError as e:
            self.msg = "could not signal pid %s: %s" % (r["pid"], e)
            return
        self.msg = ("interrupted %s's chain — it stops after the run it is in; the "
                    "next session picks up the node's uncommitted work" % item)
        self.reload()

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
        self.msg = "answered %s%s" % (r["ts"], on)

    def tree_correct(self, item):
        row = self.tree_row()
        if not row or row["kind"] != "node":
            self.msg = "put the cursor on the node to correct"
            return
        nid = row["key"]
        v = self.prompt("%s was really [c]onfirmed or [r]efuted (empty cancels):" % nid)
        v = v[:1].lower()
        if v not in ("c", "r"):
            self.msg = "not corrected"
            return
        verdict = "confirmed" if v == "c" else "refuted"
        frame = (["Directive for %s, corrected to %s: what the work should do from here."
                  % (nid, verdict),
                  "Everything below it is pruned%s, and %s reopens."
                  % (" and so are the siblings made after it" if v == "c" else "", item),
                  "Lines starting # are dropped; an empty directive cancels.", ""]
                 + row["node"]["raw"])
        body = self.compose(frame)
        if not body:
            self.msg = "empty directive — not corrected"
            return
        dfs_tree.append_log(item, "correct", [nid, verdict], body)
        self.reload()
        self.msg = "corrected %s to %s; %s reopens" % (nid, verdict, item)

    def tree_accept(self, item):
        it = self.current()
        if it is None or not self.tree_acceptable(it):
            self.msg = "%s is not a finished tree waiting to be accepted" % item
            return
        if self.prompt("accept %s's tree as finished? [y/N]:" % item)[:1].lower() != "y":
            self.msg = "not accepted"
            return
        dfs_tree.append_log(item, "accept")
        self.reload()
        self.msg = "accepted %s" % item

    def prompt(self, label, default=""):
        h, w = self.scr.getmaxyx()
        self.scr.timeout(-1)            # blocking, or getstr races the poll timeout
        curses.echo()
        curses.curs_set(1)
        self.put(h - 2, 0, " " * max(0, w - 1))
        self.put(h - 2, 0, label)
        self.scr.refresh()
        # ⚠️ Clamped to the window. `getstr` MOVES first, and a move off the right
        # edge is an ERR — so on a terminal narrower than the label the typing
        # position is off-screen, curses raises, and the caller is handed the same
        # empty string a deliberate cancel gives. `put` already clips the label;
        # this is the other half of that.
        x = min(len(label) + 1, max(0, w - 2))
        try:
            raw = self.scr.getstr(h - 2, x, 40).decode().strip()
        except Exception:
            raw = ""
        curses.noecho()
        curses.curs_set(0)
        self.scr.timeout(POLL_MS)
        return raw or default

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

    def runner(self, *args):
        """The runner argv for a pass.

        `can_fold` and the `--fold` it put on the line are gone with the fold session:
        the fold is the first act of the session that picks the item up (§0.1 step 3),
        so there is no flag and nothing to ask about.
        """
        argv = [RUNNER]
        if self.codex:
            argv.append("--codex")
        return argv + [a for a in args if a]

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

        if ch in (ord("q"),):
            return False
        if ch == 27:
            # Esc walks BACK one level and never quits. A key that sometimes exits
            # the program and sometimes closes a pane is one you stop pressing;
            # `q` is the only way out, from anywhere.
            self.follow = False
            if self.pane == "agentlog":
                self.pane = "runs"       # back to the list you chose the run from
            elif self.tree_focused():
                self.focus = "list"      # out of the tree, back to the items
            elif self.pane in ("runs", "reglog", "todo", "artefact"):
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
            self.codex = not self.codex
            self.msg = "agent: " + ("codex" if self.codex else "claude")
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
            elif self.pane == "artefact":
                arts = self.artefacts()
                if arts:
                    self.open_artefact(arts[self.art_sel])
                else:
                    self.msg = "no artefact in %s" % dfs_paths.rel(ART_DIR)
            elif self.pane == "runs":
                if self.runs:
                    self.open_run = self.runs[self.run_sel]
                    self.pane = "agentlog"
                    # A live log opens at its END and stays there: the line worth
                    # reading on a running chain is always the last one. Where it
                    # OPENS is all this decides; `follows` in `draw` is what keeps it
                    # there, and arms a moment later if liveness lands a moment later
                    # — a run directory whose `meta.json` is not written yet has none
                    # to read, and that chain then runs for an hour.
                    self.scroll = 10 ** 6 if is_growing(self.open_run) else 0
                else:
                    self.msg = "no run logs on this box yet"
            else:
                action = self.next_action(it)
                if action == "review":
                    self.open_tree()
                elif action == "work":
                    cap = self.prompt("cap [5]:", "5")
                    self.start_chain(item, cap)
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
        elif ch == ord("A") and self.tree_focused():
            self.tree_accept(item)
        elif ch == ord("c"):
            self.shell(self.runner("--chat", item))
        elif ch == ord("C"):
            self.shell(self.runner("--chat", "new"))
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
        curses.curs_set(0)
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
    curses.use_default_colors()
    for i, fg in enumerate((curses.COLOR_RED, curses.COLOR_GREEN,
                            curses.COLOR_CYAN), start=1):
        curses.init_pair(i, fg, -1)
    ui = UI(stdscr)
    # From here and not UI(): the tests build a UI of their own, and one that went to
    # the network and rewrote a pin every time it was constructed would be a test of nix.
    ui.agent_update = start_agent_update()
    ui.loop()


if __name__ == "__main__":
    if not dfs_paths.has_roadmap():
        raise SystemExit("dfs_tui: " + dfs_paths.missing_message())
    curses.wrapper(main)
