#!/usr/bin/env python3
"""What the roadmap SAYS, derived in one place.

Each task's status, the walk over the task tree, the §0 counts, and the briefings a
session is handed are all facts about `.dfs/items/<task>.md` (one decision tree per
task, parsed only by `dfs_tree.py`) and `.dfs/order.md` (the order tasks are worked
in). Two readers of them must not be two answers: `dfs_run.sh` reads its stop
conditions through `--sh`, `dfs_tui.py` the whole picture through `--json`, and a
session its state through `--brief`.

Usage:
    python3 <scripts>/dfs_state.py --sh <task>          # for eval
    python3 <scripts>/dfs_state.py --json               # for the TUI
    python3 <scripts>/dfs_state.py --brief <task>       # a work session
    python3 <scripts>/dfs_state.py --critic-brief <task> # a critic session
    python3 <scripts>/dfs_state.py --review-brief <task> # an implementation review
    python3 <scripts>/dfs_state.py --fingerprint [--outside-dfs]
    python3 <scripts>/dfs_state.py --foreign-dirt <task>
    python3 <scripts>/dfs_state.py --verdicts <task> <critic|review>
"""
import hashlib
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dfs_order            # noqa: E402  (needs this file's own dir on the path)
import dfs_paths           # noqa: E402  (the same)
import dfs_tree            # noqa: E402  (the same)

# A task file is re-read whole by every session on the task, so its size is the
# per-session tax this budget watches. ⚠️ ONE PLACE, because `dfs_check.py` refuses
# growth past it and `brief` warns the session that could prune — two numbers would
# let the check and the warning disagree about the same file.
HANDOFF_BUDGET = 10_000


def read_roadmap() -> str:
    p = dfs_paths.roadmap()
    return p.read_text() if p.exists() else ""


def task_ids():
    return dfs_tree.task_ids()


def items():
    """Every task, in the author's tree order (`order.md`), with depth and segment.

    ⚠️ The list comes back in the tree's DEPTH-FIRST PREORDER, which is the file's
    own order, so nothing here traverses anything. A task nobody has placed yet is a
    root at the end, in the LAST segment — held by every fence, because that is
    where the file says it is.
    """
    found = {}
    for task in task_ids():
        t = dfs_tree.load(task)
        found[task] = t["name"] or (t["goal"].splitlines()[0] if t["goal"] else "")
    placed = dfs_order.nodes_of(dfs_order.read_text())
    out, seen, seg = [], set(), 0
    for name, depth in placed:
        if dfs_order.is_break(name):
            seg += 1
            continue
        if name in found:
            out.append(dict(id=name, goal=found[name], depth=depth, segment=seg))
            seen.add(name)
    for name in sorted(found, key=dfs_order.sort_key("")):
        if name not in seen:
            out.append(dict(id=name, goal=found[name], depth=0, segment=seg))
    return out


def progress(t):
    """What a session can have moved in a task file: nodes, their fields, evidence,
    and the agent's own log entries. The runner's session/end lines do not count."""
    n = 0
    for nd in t["nodes"]:
        n += 1 + len(nd["evidence"]) + len(nd["fields"])
    n += sum(1 for e in t["log"] if e["kind"] in ("raise", "critic", "backtrack"))
    return n


def content(t):
    """What a task tree SAYS, as a set of short hashes: every node, every field value,
    every evidence line, and the agent's own log entries (the kinds `progress` counts).

    ⚠️ The walk asks whether a chain MOVED a tree, and a count cannot answer that.
    `dfs_check` refuses a commit that grows a task file past its budget and tells the
    session to move settled history to `.dfs/archive/`, so a session that does good
    work AND obeys that shrinks `progress`. Measured 2026-09-27 on W13: two nodes
    determined, one opened and nine evidence lines added, while the determined
    nodes' Plans and Hypotheses went to the archive; `progress` fell 148 -> 143 and
    the walk stopped, blaming the item. A move only REMOVES elements from this set,
    so "something is here that was not" is true of the work and false of the tidy."""
    out = set()

    def put(*parts):
        out.add(hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:16])
    for nd in t["nodes"]:
        put("node", nd["id"])
        for k, v in nd["fields"].items():
            put("field", nd["id"], k, v)
        for ev in nd["evidence"]:
            put("evidence", nd["id"], ev)
    for e in t["log"]:
        if e["kind"] in ("raise", "critic", "backtrack"):
            put("log", e["ts"], e["kind"])
    return out


def state_for(task: str):
    """The runner's stop facts and the screen's row, for one task."""
    t = dfs_tree.load(task)
    status, why = dfs_tree.status_of(t)
    fr = dfs_tree.frontier(t)
    since_author = dfs_tree.sessions_since_author(t)
    since_critic = dfs_tree.sessions_since_critic(t)
    raises = dfs_tree.open_raises(t)
    corr = [c for c in dfs_tree.corrections(t) if not c["done"]]
    return dict(
        item=task, status=status, why=why,
        sessions=since_author, since_critic=since_critic,
        critic_due=int(since_critic >= dfs_tree.CRITIC_EVERY),
        review_due=int(dfs_tree.review_due(t) and status == "open"
                       and not dfs_tree.open_raises(t)),
        review_rounds=dfs_tree.review_rounds_unsettled(t),
        review_limit=int(dfs_tree.review_rounds_unsettled(t) >= dfs_tree.REVIEW_ROUNDS),
        limit=int(since_author >= dfs_tree.SESSION_LIMIT),
        open_raises=[r["ts"] for r in raises],
        corrections=[c["node"] for c in corr],
        frontier=[nd["id"] for nd in fr],
        accepted=dfs_tree.accepted(t),
        progress=progress(t),
        # The screen's `next`: what is workable now, in order.
        next=["%s · %s" % (nd["id"], nd["title"]) for nd in fr],
        outline=dfs_tree.outline(t),
        # The author's pass over this row has something to show them: an open raise,
        # or a finished tree nobody has accepted since it last changed.
        standing=(["tree"] if status == "done" and not dfs_tree.accepted(t) else []),
    )


def walk(items):
    """Fill in each item's `parent`, and what BLOCKS it, over the preorder.

    ⚠️ **A RAISE blocks the whole SUBTREE, not one item.** A child is work that only
    makes sense under its parent, so a question the parent cannot answer is one the
    child cannot either — and that is the whole of the depth-first move this tool is
    named for: a walk that meets a blocked node steps sideways onto the next branch
    instead of stopping. `blocked_by` names the ANCESTOR that did it, so a screen can
    say which question to answer rather than only that something is wrong.

    `blocked` (the item's own open RAISE) stays a fact about the item; `blocked_by` is
    a fact about where it sits. Conflating them would make "answer this" and "answer
    something above this" the same instruction.

    ⚠️ **A BREAK holds everything after it until everything before it is DONE**, and
    that is a THIRD question, so it gets a third field: `waiting_on` names the first
    unfinished item on the far side of the fence. Not-done covers blocked as well as
    open — a fence the author drew across the whole forest is not satisfied by a
    branch that merely has nobody asking a question about it. Nesting could not say
    this: it is a claim about every branch above at once, and re-parenting the roadmap
    into one spine to fake it would make every RAISE stall the whole tree, which is
    the exact stall the depth-first move exists to avoid.

    `reachable` is the AND of all three, and stays the only thing `next_item` reads.

    ⚠️ All three are facts about the POSITION, not about how the item is getting on:
    a `done` item behind a fence still carries the fence's `waiting_on`, because the
    fence is a fact about the segment and the item is where it is. What to SAY about
    a given row is the screen's decision and it makes it there — neither marker ever
    replaces anything but `open`, since "waiting on W2" about a finished item is
    false, and a fence would otherwise have said it about whole segments at a time.
    """
    stack = []                       # (depth, id) of blocking ancestors
    lineage = {}                     # depth -> the id currently open at it
    seg, gate, pending = 0, None, None
    for it in items:
        # A fence: everything in every earlier segment has to be DONE, so whatever
        # this segment leaves unfinished becomes the gate for the next one. `gate`
        # keeps the FIRST such item rather than the nearest — that is the one a walk
        # would pick up next, so it is the one worth naming on a held row.
        if it.get("segment", 0) != seg:
            gate, pending, seg = gate or pending, None, it["segment"]
        while stack and stack[-1][0] >= it["depth"]:
            stack.pop()
        it["parent"] = lineage.get(it["depth"] - 1)
        lineage[it["depth"]] = it["id"]
        it["blocked_by"] = stack[-1][1] if stack else None
        it["waiting_on"] = gate
        it["reachable"] = not stack and gate is None
        if it["status"] == "blocked":
            stack.append((it["depth"], it["id"]))
        if it["status"] != "done" and pending is None:
            pending = it["id"]
    return items


def next_item(items):
    """What the ORDER says to work: the first item in preorder that is open and whose
    branch nothing is holding. None means every branch is blocked or finished — which
    is a fact worth having, not an error.

    It is the order's answer alone, which is not quite the walk's: a walk continues
    the item its last run was on before it reads the order at all (`continue_last`)."""
    return next((it["id"] for it in items
                 if it["reachable"] and it["status"] == "open"), None)


def metrics(trees):
    """The counts the system is judged by, over every task. On the screen, not in a
    script somebody remembers to run."""
    m = dict(tasks=len(trees), nodes=0, confirmed=0, refuted=0, pruned=0, open=0,
             raises=0, open_raises=0, answers=0, corrections=0, backtracks=0, critic_ok=0,
             critic_issues=0, review_ok=0, review_issues=0, parked=0,
             work_sessions=0, critic_sessions=0, review_sessions=0, entries=0, chars=0)
    for t in trees:
        status, pruned = dfs_tree.effective(t)
        m["nodes"] += len(t["nodes"])
        m["pruned"] += len(pruned)
        for nd in t["nodes"]:
            if nd["id"] not in pruned:
                m[status[nd["id"]]] = m.get(status[nd["id"]], 0) + 1
        for e in t["log"]:
            k, a = e["kind"], e["args"]
            if k == "raise":
                m["raises"] += 1
            elif k == "answer":
                m["answers"] += 1
            elif k == "correct":
                m["corrections"] += 1
            elif k == "backtrack":
                m["backtracks"] += 1
            elif k in ("critic", "review"):
                m["%s_%s" % (k, "issues" if a[:1] == ["issues"] else "ok")] += 1
            elif k == "session":
                m["%s_sessions" % (a[0] if a[:1] in (["critic"], ["review"]) else "work")] += 1
        m["open_raises"] += len(dfs_tree.open_raises(t))
        m["entries"] += progress(t)
        m["chars"] += sum(len(p.read_text()) for p in dfs_tree.part_files(t["task"]))
    m["chars"] += len(read_roadmap())
    m["tokens"] = m["chars"] // 4
    # Retained names the screen already shows.
    m["open_proposals"] = 0
    return m


def full():
    its = items()
    trees = [dfs_tree.load(i["id"]) for i in its]
    raises, entries = [], []
    for t in trees:
        for r in dfs_tree.open_raises(t):
            raises.append(dict(id=r["ts"], item=t["task"],
                               node=r["args"][0] if r["args"] else "",
                               body=r["body"]))
        for e in t["log"]:
            entries.append(dict(id=e["ts"], type=e["kind"].upper(), target=t["task"],
                                disp=" · ".join(e["args"]), body=e["body"]))
        for nd in t["nodes"]:
            entries.append(dict(id=nd["id"], type="NODE", target=t["task"],
                                disp=nd["status"], body="\n".join(nd["raw"][1:])))
    walked = walk([dict(i, **state_for(i["id"])) for i in its])
    moved = set()
    for t in trees:
        moved |= content(t)
    return dict(items=walked, next_item=continue_last(walked), open_raises=raises,
                open_proposals=[], entries=entries, metrics=metrics(trees),
                content=sorted(moved))


def continue_last(walked):
    """What the walk works NEXT: the item the last run was on, while that item can
    still be worked, and otherwise `next_item`.

    ⚠️ **A walk finishes what it started.** `next_item` reads the order and nothing
    else, so the moment something EARLIER in it becomes workable — a raise answered,
    a done item reopened, an item moved up — the walk would leave a half-built tree
    where it stood and begin somewhere else. The item that had the last session is
    the one with an open node, evidence half-gathered, and (after a `cap` or a usage
    limit) work nobody has seen the end of; so it goes first, and the order decides
    where the walk goes after it rather than whether it may abandon where it is.

    ⚠️ This SUBSUMES the uncommitted-work resume it grew out of, rather than sitting
    beside it: uncommitted work outside `.dfs/` belongs to whichever task had the
    last session (`foreign_dirt`), so naming that task here is the same answer on a
    dirty tree as on a clean one — one rule, and no second reason for the walk to
    name an item.

    When the last run's item CANNOT be worked — finished, blocked by its own raise,
    or held by the tree (an ancestor's raise, a fence) — the order's answer stands.
    On a dirty tree the item the order names then starts by committing or stashing
    that half-done work (`foreign_dirt`), so the walk goes on rather than waiting.
    """
    owner, _ = latest_session()
    row = next((i for i in walked if i["id"] == owner), None)
    if row and row["status"] == "open" and row["reachable"]:
        return owner
    return next_item(walked)


# ── the working tree ────────────────────────────────────────────────────────────

def _git(*args):
    return subprocess.run(["git", *args], capture_output=True,
                          cwd=dfs_paths.work_root()).stdout


def dirty_paths(outside_dfs=True):
    """Changed and untracked paths, `.dfs/` left out: the author's passes and the
    runner write there, and a node's commit takes those along with the code."""
    out = _git("status", "--porcelain", "--untracked-files=all").decode(errors="replace")
    paths = []
    for line in out.splitlines():
        p = line[3:].split(" -> ")[-1].strip().strip('"')
        if outside_dfs and (p == dfs_paths.STATE_DIR_NAME
                            or p.startswith(dfs_paths.STATE_DIR_NAME + "/")):
            continue
        paths.append(p)
    return paths


def fingerprint(outside_dfs=False, exclude_runs=True):
    """A hash of HEAD and everything uncommitted. Equal before and after a session
    means the session changed nothing — the runner's `idle` stop, asked of the repo
    rather than of any one file."""
    h = hashlib.sha256()
    root = dfs_paths.work_root()
    if subprocess.run(["git", "rev-parse", "--git-dir"], capture_output=True,
                      cwd=root).returncode != 0:
        # Not a repository: every file, `.dfs/runs` and (with outside_dfs) `.dfs` left out.
        for f in sorted(root.rglob("*")):
            rel = f.relative_to(root).as_posix()
            if not f.is_file() or rel.startswith(dfs_paths.STATE_DIR_NAME + "/runs/") or (
                    outside_dfs and rel.startswith(dfs_paths.STATE_DIR_NAME + "/")):
                continue
            h.update(rel.encode())
            h.update(f.read_bytes())
        return h.hexdigest()
    h.update(_git("rev-parse", "HEAD"))
    spec = ["--", "."]
    if outside_dfs:
        spec.append(":(exclude)%s" % dfs_paths.STATE_DIR_NAME)
    elif exclude_runs:
        spec.append(":(exclude)%s/runs" % dfs_paths.STATE_DIR_NAME)
    h.update(_git("diff", "HEAD", "--binary", *spec))
    for p in sorted(_git("ls-files", "--others", "--exclude-standard", *spec)
                    .decode(errors="replace").splitlines()):
        h.update(p.encode())
        try:
            h.update((dfs_paths.work_root() / p).read_bytes())
        except OSError:
            pass
    if not outside_dfs and dfs_paths.ignored():
        # ⚠️ A gitignored `.dfs` is invisible to both git calls above, so a session
        # that wrote only its task file (evidence so far, a raise) would read as idle.
        # Read from disk instead, `runs/` still left out: the runner writes there.
        state = dfs_paths.state()
        for f in sorted(state.rglob("*")) if state.is_dir() else []:
            r = f.relative_to(state).as_posix()
            if not f.is_file() or (exclude_runs and r.startswith("runs/")):
                continue
            h.update(("%s/%s" % (dfs_paths.STATE_DIR_NAME, r)).encode())
            try:
                h.update(f.read_bytes())
            except OSError:
                pass
    return h.hexdigest()


def latest_session():
    """(task, entry) of the most recent session over every task, or (None, None)."""
    best = (None, None)
    for task in task_ids():
        s = dfs_tree.last_session(dfs_tree.load(task))
        if s and (best[1] is None or s["ts"] > best[1]["ts"]):
            best = (task, s)
    return best


def foreign_dirt(task):
    """(paths, owner): the uncommitted paths outside `.dfs/` that are NOT `task`'s
    own unfinished node, and whose they are. Agents work serially, so uncommitted
    work belongs to whichever task had the last session — and to nobody's node when
    that was another task, or when no session has run at all (the dirt is the
    author's). Empty when the tree is clean or the dirt is this task's.

    ⚠️ A chain STARTS over foreign dirt rather than refusing: its first session
    commits or stashes it before touching its node (`brief`), and the runner makes
    that first session a work session (`dfs_run.sh`), since a critic or reviewer may
    not change code, and once one of those had run on this task the dirt would read
    as this task's own."""
    dirty = dirty_paths()
    owner, _ = latest_session()
    if not dirty or owner == task:
        return [], owner
    return dirty, owner


# ── what a session is handed ────────────────────────────────────────────────────

def _size_note(path) -> str:
    if not path.exists():
        return "not written yet"
    n = len(path.read_text()) // 4
    task = dfs_tree.part_of(path.relative_to(dfs_paths.items()))[0]
    over = (" — ⚠️ over the %s-token budget; prune Background, and move settled "
            "history to the archive (`dfs_tree.py shelve %s`)"
            % (f"{HANDOFF_BUDGET:,}", task)) if n > HANDOFF_BUDGET else ""
    return "~%s tokens%s" % (f"{n:,}", over)


def _parts_note(task, path):
    """What a session on a branch must know about the task's other parts. Nothing on a
    task only the default branch has written, which is every task until one branches."""
    tag = dfs_paths.branch_tag()
    others = [p for p in dfs_tree.part_files(task) if p != path]
    if not tag and not others:
        return []
    out = ["- **This branch writes only `%s`**: every node you add is `%s.<n>%s`, "
           "numbered one past the highest with that tag (next: `%s`), and every log entry "
           "lands there through `dfs_tree.py log`. Editing a node that lives in another "
           "part (to determine it) is allowed; adding to one is not."
           % (dfs_paths.rel(path), task, "@" + tag if tag else "",
              dfs_tree.next_node_id(dfs_tree.load(task), tag))]
    if others:
        out.append("- **Read the task's other parts too**, whole: %s. The task is all of "
                   "them; the Goal is in `%s`." % (
                       ", ".join("`%s`" % dfs_paths.rel(p) for p in others),
                       dfs_paths.rel(dfs_tree.home_path(task))))
    return out


def _ignored_note(task):
    """What a session must do differently when `.dfs` is gitignored. Nothing when it
    is committed, which the skill's rules already describe."""
    if not dfs_paths.ignored():
        return []
    return ["- **`.dfs/` is gitignored here**, so the task file is planning and never "
            "committed: git history alone records the walk. Every node still ends in a "
            "commit whose subject starts with its id, staging only the code (`git commit "
            "--allow-empty` for a node that changed no tracked file, a refuted one "
            "included), and its body carries the node's `Parent:`, `Status:` and "
            "`Determination:` lines verbatim, since the commit is the only copy git "
            "keeps. `dfs_check` cannot compare against HEAD, so a determined node staying "
            "unedited is on you."]


def _author_since_last_ok(t):
    last_ok = max((e["ts"] for e in t["log"] if e["kind"] == "end"
                   and e["args"][:2] == ["work", "ok"]), default="")
    return [e for e in t["log"] if e["kind"] in ("answer", "correct") and e["ts"] > last_ok]


def brief(task: str) -> str:
    """Everything a work session needs before it starts, as text it is HANDED.

    ⚠️ The law is carried VERBATIM, never summarised: a summary of the law is a
    second copy of it. The task file is NOT carried — it is the next thing the
    session reads, whole.
    """
    t = dfs_tree.load(task)
    st = state_for(task)
    path = dfs_tree.path_for(task)
    rel = dfs_paths.rel(path)
    out = [read_roadmap().rstrip(), "",
           "## Your briefing — %s" % task, "",
           "- **Status**: %s — %s" % (st["status"], st["why"]),
           "- **Your task file is `%s`** (%s). Read it next: its Tree is the state of the "
           "work, its Log what everyone has said about it." % (rel, _size_note(path)),
           ] + _parts_note(task, path) + [
           "- **The tool's scripts are in `%s`**: `<scripts>` in the skill means that "
           "directory." % dfs_paths.rel(dfs_paths.SCRIPTS),
           ] + _ignored_note(task) + [
           "- **Session %d of %d** since the author last spoke on this task; at %d the "
           "task raises for their review. The critic reviews after %d more."
           % (st["sessions"] + 1, dfs_tree.SESSION_LIMIT, dfs_tree.SESSION_LIMIT,
              max(dfs_tree.CRITIC_EVERY - st["since_critic"] - 1, 0)),
           ""]
    said = _author_since_last_ok(t)
    todo = [c for c in dfs_tree.corrections(t) if not c["done"]]
    if said or todo:
        out += ["### First: what the author and the critic have said since the last "
                "finished session", "",
                "An answer reopens the task: act on it in a node, even when what it "
                "settles is that nothing changes, and commit that node.", ""]
        for e in said:
            if e["kind"] == "answer":
                raise_e = next((r for r in t["log"] if r["ts"] == e["args"][0]), None)
                out += ["- **Answer** to the raise of %s%s:" % (
                    e["args"][0], (" on " + raise_e["args"][0]) if raise_e and raise_e["args"] else "")]
                if raise_e:
                    out += ["  > " + ln for ln in raise_e["body"].splitlines()]
                out += ["  " + ln for ln in e["body"].splitlines()]
        for c in todo:
            if c["disp"] == "backtrack":
                out += ["- **The critic backtracked to %s.** Not yet carried out. %s and "
                        "everything below it is withdrawn: its evidence did not carry its "
                        "determination. Redo its step beside it, under its parent, as a new "
                        "node with `Corrects: %s`; it may come out the same way, but this "
                        "time shown." % (c["node"], c["node"], c["ts"])]
                out += ["  " + ln for ln in c["body"].splitlines()]
                continue
            confirmed = c["disp"] == "confirmed"
            out += ["- **Correction at %s — the author says %s.** Not yet carried out. Everything "
                    "below %s%s is pruned. Resume %s: make a new node with `Corrects: %s`." % (
                        c["node"], c["disp"], c["node"],
                        ", and every sibling made after it," if confirmed else "",
                        ("under %s" % c["node"]) if confirmed
                        else "at a sibling, under its parent (unpark one or add one)", c["ts"])]
            out += ["  " + ln for ln in c["body"].splitlines()]
        out += [""]
    raises = dfs_tree.open_raises(t)
    if raises:
        out += ["### Open raises — this task is blocked until they are answered", ""]
        out += ["- %s%s: %s" % (r["ts"], (" on " + r["args"][0]) if r["args"] else "",
                                (r["body"].splitlines() or [""])[0]) for r in raises]
        out += [""]
    cut = dfs_tree.cut_off(t)
    dirty = dirty_paths()
    if dirty or cut:
        out += ["### Uncommitted work, and the previous session", ""]
        if cut:
            out += ["The session started %s has no `end`: it was killed outright." % cut["ts"]]
        foreign, owner = foreign_dirt(task)
        if dirty and not foreign:
            out += ["The working tree holds %d uncommitted path%s outside `.dfs/`. They are "
                    "the unfinished work of the current node: review them, then finish and "
                    "commit the node." % (len(dirty), "" if len(dirty) == 1 else "s"), ""]
        elif foreign:
            n = len(foreign)
            out += ["⚠️ **Before anything else, clear this.** The working tree holds %d "
                    "uncommitted path%s outside `.dfs/` that are NOT this task's: the last "
                    "session was %s. Nothing of them may go into your node's commits." % (
                        n, "" if n == 1 else "s",
                        "%s's" % owner if owner else "nobody's, so they are the author's work"),
                    ""]
            out += ["    " + p for p in foreign[:30]]
            if n > 30:
                out += ["    (and %d more)" % (n - 30)]
            out += ["", "Read their diff, then either:", "",
                    "- **commit** them, when they read as a finished change: in a commit of "
                    "their own, staging exactly these paths, whose subject says what the "
                    "change is and whose it was (no node id: it is not a node's), or",
                    "- **stash** them, when they are half-done or you cannot tell what they "
                    "are for: `git stash push --include-untracked -m \"dfs: set aside before "
                    "%s — <what it is>\" -- <the paths>`." % task,
                    "",
                    "Then note which you did, with the commit's sha or the stash's message, "
                    "in the Evidence of the node you are on, so the author can find it, and "
                    "go on with the session as usual."]
        out += [""]
    stale = dfs_tree.unreverted_pruned(t)
    if stale:
        out += ["### Refuted or pruned nodes whose commits are still in the code", "",
                "Revert them, newest first, as part of the node you are on, unless the "
                "evidence says why a change should stay (then write `<sha> stays` in its "
                "Determination). Not with `git revert`: %s Each line's command "
                "reverts the code alone; commit it under your node with `This reverts "
                "commit <sha>.` in the body." % (
                    "it commits under its own subject, which names no node, and refuses "
                    "a dirty tree."
                    if dfs_paths.ignored() else
                    "the commit carries its task file, and reverting that takes a "
                    "determined node's text out."), ""]
        out += ["- %s %s %s\n\n      %s\n" % (nid, sha[:10], subj, dfs_tree.revert_recipe(sha[:10]))
                for nid, sha, subj in stale]
        out += [""]
    if st["outline"]:
        out += ["### The tree", ""] + ["    " + ln for ln in st["outline"]] + [""]
    return "\n".join(out).rstrip() + "\n"


def _since_last(t, kind):
    """The ts of the last `kind` session before the one now running, or ""."""
    now = (dfs_tree.last_session(t) or {}).get("ts", "~")
    return max((e["ts"] for e in t["log"] if e["kind"] == "session"
                and e["args"][:1] == [kind] and e["ts"] < now), default="")


def _record_block(task, kind):
    """How a critic or a reviewer records what it finds. ⚠️ What it finds is WORK, so
    it goes in the tree, where the next session picks it up; a raise blocks the task
    on the author and is kept for what only the author can settle. Every finding used
    to be a raise, and the author spent a pass answering "ok" to bugs."""
    tree_cmd = "python3 %s/dfs_tree.py" % dfs_paths.rel(dfs_paths.SCRIPTS)
    rel = dfs_paths.rel(dfs_tree.path_for(task))
    tag = dfs_paths.branch_tag()
    node_shape = ["    ### %s · <the issue, named as what to fix>"
                  % dfs_tree.next_node_id(dfs_tree.load(task), tag),
                  "    Parent: <the node whose work it is in; none for something the "
                  "Goal asks that no node attempted>",
                  "    Status: open",
                  "    Approach: <what is wrong, what you checked and what it showed, and "
                  "what would settle it>", ""]
    out = ["⚠️ **Fix nothing.** No code edits, no commits: the runner checks that the "
           "working tree outside `.dfs/` is exactly as you found it. What you find is "
           "WORK for the next session, and you put it in `%s`:" % rel, ""]
    if kind == "critic":
        out += ["- **Where a node's evidence does not carry its determination, BACKTRACK** "
                "to the first node where that happens: the earliest, since everything "
                "under it was built on it. It withdraws that node and all below it, and "
                "the next session redoes the step beside it:", "",
                "    %s log %s backtrack <node> <<'EOF'" % (tree_cmd, task),
                "    <what does not hold, what you checked, and what the redo must show>",
                "    EOF", "",
                "- **Where something is missing rather than wrong** (an alternative parked "
                "or refuted without a fair try, a check nobody ran), add an open node to "
                "the Tree, one past the highest id with this branch's tag:", ""] + node_shape
    else:
        out += ["- **A finding is a node only if it shows the Goal is not met**: "
                "something the Goal asks for that nothing delivers, or a fault in what "
                "was built that its user would meet (a wrong answer, lost data, a check "
                "that passes when it should fail). Each such finding is a new open node "
                "in the Tree, one past the highest id with this branch's tag. Adding it "
                "reopens the task, and the next session fixes it:", ""] + node_shape + [
                "- **Everything else is MINOR and never a node of its own**: a wording, a "
                "stale sentence in a doc or comment, a message that names the wrong "
                "reason, a missing test for a guard, a style rule, tidying. A node "
                "reopens the task and earns another review, and a review always finds "
                "something smaller, so these must not be what keeps a task open. If you "
                "added a node above, add ONE more for all of them together (`<id> · "
                "Minor findings from the review`, its Approach one line per finding), since "
                "the task is reopened anyway. If you added none, give the verdict `ok` "
                "and list them in its body, one per line: the author reads them when "
                "they accept the task.", ""]
    out += ["  Keep the Approach concise, clear and standalone: name the file, "
            "function, fault and check themselves, so a session that reads only this "
            "node knows what to do, never just \"see <id>\" or \"as <id> found\".", "",
            "  Leave Hypothesis and Evidence to the session that takes it up, and do not "
            "edit an existing node: a determined node is history.", "",
            "- **Raise only on the grounds in §0**; anything else is a %s. An issue "
            "with the roadmap tooling itself (roadmap-dfs, the `.dfs` rules, their "
            "hooks) is one of those grounds: raise it, never add it as a node%s:" % (
                "node or a backtrack" if kind == "critic" else "node",
                ", and so is scope drift" if kind == "critic" else ""), "",
            "    %s log %s raise <node> <<'EOF'" % (tree_cmd, task),
            "    <the decision: the options, what each commits the work to, what you "
            "would pick, and the strongest case against it>",
            "    EOF", "",
            "Then, exactly once, the verdict:", "",
            "    %s log %s %s ok%s" % (tree_cmd, task, kind,
                                     " <<'EOF'\n    <the minor findings, one per line, "
                                     "or nothing>\n    EOF" if kind == "review" else ""),
            "    %s log %s %s issues <<'EOF'" % (tree_cmd, task, kind),
            "    <one line per %s, naming it>" % (
                "node added, backtrack or raise" if kind == "critic" else "node added or raise"),
            "    EOF", ""]
    return out


def critic_brief(task: str) -> str:
    """What a critic session is handed: the law, what to judge, and the nodes.

    ⚠️ The critic judges JUDGEMENTS and EVIDENCE — whether the tree's reasoning is
    sound — and not the implementation, which the review after completion does. The
    failure modes it looks for are named, because a critic asked only "is this right?"
    re-reads the work in the worker's own frame and agrees with it.
    """
    t = dfs_tree.load(task)
    rel = "`, `".join(dfs_paths.rel(p) for p in dfs_tree.part_files(task))
    last = _since_last(t, "critic")
    commits = dfs_tree.node_commits(task)
    status, pruned = dfs_tree.effective(t)
    out = [read_roadmap().rstrip(), "",
           "## What to judge in %s" % task, "",
           "You judge the tree's REASONING: its hypotheses, its evidence, and the "
           "determinations drawn from them. You do not review the implementation, the "
           "code's quality or its correctness — a review after the task is complete does "
           "that. Look at code only to check whether a piece of evidence is TRUE.", "",
           "Read `%s`, then for the nodes below ask:" % rel, "",
           "1. **Is each piece of evidence true, and independent?** Check a claim against "
           "what it cites — re-run the command, `git show` the commit. Then ask whether "
           "it adds support of its own: **spurious evidence chains** look coherent while "
           "each link only restates the one before it (the same test described twice, "
           "a conclusion cited back as its own evidence, a reading of the code offered "
           "as a measurement of it).",
           "2. **Does the evidence entail the determination?** Watch for "
           "**over-trusting self-generated interpretations**: the session's own reading "
           "of a log, a diff or a comment treated as if it were an observation, and a "
           "hypothesis confirmed on reasoning alone, never put where it could fail. A "
           "hypothesis that restates the approach (\"the fix works\") instead of naming "
           "what it assumes has nothing that could fail.",
           "3. **Was a branch pruned too early?** **Premature branch pruning**: an "
           "alternative refuted or parked on thin or indirect evidence, or never tried "
           "at all, while the branch taken was given every chance.",
           "4. **Is agreement standing in for evidence?** **False convergence**: several "
           "sessions agreeing because they share "
           "the same blind spot, not because anything independent checked it. Ask what "
           "every one of them failed to look at.",
           "5. **Is the work still the Goal's?** **Scope drift**: each node sound on its "
           "own terms while the tree has moved on to something the Goal does not ask for, "
           "typically by hardening the check a previous node built, then the check on "
           "that, one step further out each time. Read the Goal, then ask of each node "
           "what it advanced of it; a Determination that only says the Goal still holds "
           "does not answer that. Look at which paths the nodes' commits touch. Where it "
           "has drifted, raise, naming the first node that left the Goal: whether that "
           "work stays, moves to another task or stops is the author's call.", "",
          ] + _record_block(task, "critic") + [
           "### Nodes to judge (%s)" % ("determined since the critic last ran at " + last
                                        if last else "all of them — the critic has not run yet"), ""]
    for nd in sorted(t["nodes"], key=lambda n: n["n"]):
        cs = commits.get(nd["id"], [])
        fresh = (not last or not cs or cs[0][2] >= last)
        if not fresh:
            continue
        out += ["- %s [%s%s] %s — commits: %s" % (
            nd["id"], "pruned " if nd["id"] in pruned else "", status[nd["id"]], nd["title"],
            ", ".join(sha[:10] for sha, _s, _a in cs) or "none")]
    out += ["", "### The tree", ""] + ["    " + ln for ln in dfs_tree.outline(t)]
    return "\n".join(out).rstrip() + "\n"


def _round_note(t):
    """Which review round this is, and what the one after it does."""
    n = dfs_tree.review_rounds_unsettled(t)
    if not n:
        return []
    last = max(e["ts"] for e in t["log"] if e["kind"] == "review")
    return ["This is review round %d: %d review%s since the author last spoke found "
            "issues, and the fixes are in. Start from the commits made since %s. %s" % (
                n + 1, n, "" if n == 1 else "s", last,
                "If this round adds a node, the task is raised to the author instead of "
                "being reviewed again." if n + 1 >= dfs_tree.REVIEW_ROUNDS else ""), ""]


def review_brief(task: str) -> str:
    """What the implementation review is handed, once a task's tree is complete: the
    goal, the tree, and every commit the task made — the code, judged against the
    goal, which the critic deliberately does not look at."""
    t = dfs_tree.load(task)
    rel = "`, `".join(dfs_paths.rel(p) for p in dfs_tree.part_files(task))
    commits = dfs_tree.node_commits(task)
    status, pruned = dfs_tree.effective(t)
    stale = dfs_tree.unreverted_pruned(t)
    out = [read_roadmap().rstrip(), "",
           "## What to review in %s" % task, "",
           "Every live node in `%s` is confirmed: the sessions say the Goal is met. Review "
           "what they BUILT, against the Goal and against this repo's own rules "
           "(AGENTS.md):" % rel, "",
           "1. **Does the change do what the Goal says?** Read the commits below whole. "
           "Name what the Goal asks for that nothing in them delivers.",
           "2. **Is it correct?** Bugs, missed cases, a state the code does not handle, "
           "a test that cannot fail. Run the tests that cover it.",
           "3. **Is anything left behind?** Refuted or pruned work still in the code, "
           "scaffolding, a comment or doc the change made false.", "",
          ] + _round_note(t) + _record_block(task, "review") + [
           "### The task's commits, oldest first", ""]
    for nd in sorted(t["nodes"], key=lambda n: n["n"]):
        for sha, subj, _at in reversed(commits.get(nd["id"], [])):
            tag = "pruned" if nd["id"] in pruned else status[nd["id"]]
            out += ["- %s %s (%s)" % (sha[:10], subj, tag)]
    if stale:
        out += ["", "Refuted or pruned, and not reverted: " +
                ", ".join("%s %s" % (nid, sha[:10]) for nid, sha, _ in stale)]
    out += ["", "### The tree", ""] + ["    " + ln for ln in dfs_tree.outline(t)]
    return "\n".join(out).rstrip() + "\n"


def verdicts(task, kind):
    """How many `kind` entries (critic or review verdicts) the task's log holds, read
    through `load`: a `compact` during the session moves earlier verdicts to the
    archive, and a count of the file alone would then not rise for a new one."""
    return sum(1 for e in dfs_tree.load(task)["log"] if e["kind"] == kind)


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--sh":
        if len(args) < 2:
            print("usage: dfs_state.py --sh <task>", file=sys.stderr)
            return 2
        st = state_for(args[1])
        for k in ("status", "why", "sessions", "since_critic", "critic_due", "review_due",
                  "review_rounds", "review_limit", "limit", "progress"):
            print("%s=%s" % (k, shlex.quote(str(st[k]))))
        return 0
    if args and args[0] == "--verdicts":
        if len(args) < 3:
            print("usage: dfs_state.py --verdicts <task> <critic|review>", file=sys.stderr)
            return 2
        print(verdicts(args[1], args[2]))
        return 0
    if args and args[0] in ("--brief", "--critic-brief", "--review-brief"):
        if len(args) < 2:
            print("usage: dfs_state.py %s <task>" % args[0], file=sys.stderr)
            return 2
        if not dfs_tree.exists(args[1]):
            print("dfs_state: no task file for %s" % args[1], file=sys.stderr)
            return 2
        sys.stdout.write({"--brief": brief, "--critic-brief": critic_brief,
                          "--review-brief": review_brief}[args[0]](args[1]))
        return 0
    if args and args[0] == "--fingerprint":
        print(fingerprint(outside_dfs="--outside-dfs" in args))
        return 0
    if args and args[0] == "--foreign-dirt":
        print("\n".join(foreign_dirt(args[1])[0]))
        return 0
    if args and args[0] == "--json" or not args:
        print(json.dumps(full(), indent=2))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
