#!/usr/bin/env python3
"""A task's decision tree: the one place its file format means anything.

Each task is one file, `.dfs/items/<task>.md`:

    # W13 · short name

    ## Goal
    <what done looks like, and why>

    ## Background
    <optional: history, the author's notes>

    ## Tree

    ### W13.1 · <title>
    Parent: W13.0                 (optional; absent means a child of the task)
    Status: open | parked | confirmed | refuted
    Approach: <what this node does and how: the files, the change, the check>
    Hypothesis: <optional: an important, questionable assumption, and what would show it wrong>
    Evidence:
    - for: <an observation bearing on an assumption>
    - against: <an observation bearing on an assumption>
    Determination: <what was concluded, and so where the work goes next>
    Ask: <optional: a question only the author can answer, that the work does not wait on>
    Corrects: <the ts of the author's `correct` entry this node carries out>

    ## Log

    - 2026-09-25T09:00:00Z · session · work
    - 2026-09-25T09:40:00Z · raise · W13.2
      <the question, indented two spaces>

Node ids are `<task>.<n>`, numbered in creation order, and the tree is the `Parent:`
pointers. On a branch other than main or master every id carries the branch's tag
(`W13.4@fix-sync`, `dfs_paths.branch_tag`), and the branch writes only its own PART
of the task, `.dfs/items/<task>/<tag>.md`: the default branch's part is
`.dfs/items/<task>.md`. A task is the union of its parts, so two branches working one
task merge with no conflict unless both edited the same node. Numbers count per tag. Siblings are ALTERNATIVE ways to reach their parent's end: the one being
pursued is `open`, the others `parked` with the reason, and evidence against the
pursued one is what says to jump to a parked sibling. A linear chain of steps is each
node naming the one before it. Most nodes only say that something was done, and have no Hypothesis or Evidence. A
Hypothesis is for the few where the session decided something it could not settle
itself and the work now rests on it: the Evidence bears on it, the author reads those
nodes before accepting the tree, and the screen marks them. A node's commit is found by
its subject (`W13.4: ...`), never written in the file, because a commit cannot name
its own hash.

The log is the only thing more than one party writes: the runner (session, end, and
the session-limit raise), the agent (raise), the critic (critic, backtrack), the
reviewer (review), and the author's passes (answer, correct, accept). Every entry is
keyed by its timestamp, so two branches' logs merge by union, and an answer or a
correction names what it settles by that timestamp.

The log is bounded by moving its past, never by editing it: `compact` moves every
entry older than the author's last word (answer, correct or accept), verbatim, to the
END of the task's archive (`.dfs/archive/items/<task>.md`) under a `**Log**:` line,
and `load` merges them back. So every count read off `load` (sessions since the
author, the critic's cadence, open raises) is the same after a move as before it; a
reader that parses the task FILE alone sees only the log since the author spoke.

A determined node's Approach and Hypothesis are bounded the same way: `shelve` moves
each, verbatim, to the archive's end as a `**<id> Approach**: <text>` line and leaves
`Approach: archived.` in the file, and `load` puts the words back. `Plan` is the
Approach's old name, read as it wherever it appears, in a task file or an archive. Status, Parent and
Determination never move, so the tree reads the same from the file alone.

A raise is only for what needs the author: a decision, a fact, or access only they
have. What a critic or a reviewer FINDS is work, so it lands in the tree, not in a
raise: the reviewer adds an open node, and the critic adds one or BACKTRACKS to the
node where the reasoning stopped holding (`backtrack <node>`, which withdraws that
node and everything under it, the way a correction does).

Usage:
    tree.py log <task> <kind> [args...] < body   append one log entry
    tree.py show <task>                          the tree, one line per node
    tree.py add <task> <parent|-> <title> [< approach]   add an open node
    tree.py compact <task>                       move the log's past to the archive
    tree.py shelve <task> [<ids>]                move determined nodes' Approach
                                                     and Hypothesis to the archive
"""
import calendar
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths as dfs_paths            # noqa: E402

# `parked` is an alternative not being pursued now: not determined, so it may be
# reopened, and neither it nor anything planned under it holds the task open.
STATUSES = ("open", "parked", "confirmed", "refuted")
DETERMINED = ("confirmed", "refuted")
# Who writes each kind is a convention the checker enforces only by shape; the
# runner, the agent, the critic and the author's passes each append their own.
KINDS = ("session", "end", "raise", "answer", "critic", "review", "correct", "backtrack",
         "accept")
FIELDS = ("Parent", "Status", "Approach", "Hypothesis", "Evidence", "Determination",
          "Ask", "Corrects")
# Old names still read, as the field they became: determined nodes are history, so
# trees written before a rename keep them.
RENAMED = {"Plan": "Approach"}
# The two cadences. The environment overrides exist for trial runs, where waiting
# five sessions to see the critic once is most of the cost of trying it.
SESSION_LIMIT = int(os.environ.get("DFS_SESSION_LIMIT", 10))  # work sessions before a raise for review
CRITIC_EVERY = int(os.environ.get("DFS_CRITIC_EVERY", 5))     # work sessions between critic reviews
REVIEW_ROUNDS = int(os.environ.get("DFS_REVIEW_ROUNDS", 2))   # unsettled reviews before a raise

TS_RE = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ"
LOG_HEAD = re.compile(r"^- (%s) · (\w+)((?: · [^\n]*)?)$" % TS_RE)
TAG_RE = dfs_paths.TAG_RE
# A task id, then a node id: `W13`, `W30@fx`; `W13.4`, `W13.4@fx`, `W30@fx.2@fx`.
TASK_RE = r"[A-Za-z]+\d+(?:@%s)?" % TAG_RE
NODE_RE = r"%s\.\d+(?:@%s)?" % (TASK_RE, TAG_RE)
NODE_HEAD = re.compile(r"^### (%s)\.(\d+)(?:@(%s))? · (.*)$" % (TASK_RE, TAG_RE))
FIELD_RE = re.compile(r"^(%s):\s?(.*)$" % "|".join(FIELDS + tuple(RENAMED)))


def field_name(name):
    """The current name of a field, given it or an old one."""
    return RENAMED.get(name, name)


def split_id(nid):
    """(task, n, tag) of a node id, or None."""
    m = re.fullmatch(r"(%s)\.(\d+)(?:@(%s))?" % (TASK_RE, TAG_RE), nid or "")
    return (m.group(1), int(m.group(2)), m.group(3) or "") if m else None


def node_id(task, n, tag=""):
    return "%s.%d%s" % (task, n, "@" + tag if tag else "")


def order_key(nd):
    """Creation order where it is known: the default branch's nodes, then each tag's,
    each by number. Numbers on two tags were counted independently, so nothing here
    says which of two branches' nodes came first."""
    return (nd.get("tag", "") != "", nd.get("tag", ""), nd["n"])


# ── parts ────────────────────────────────────────────────────────────────────

def part_path(task, tag=""):
    """The file a branch with `tag` writes for `task`."""
    return dfs_paths.items() / (("%s.md" % task) if not tag else "%s/%s.md" % (task, tag))


def archive_part(task, tag=""):
    base = dfs_paths.state() / "archive" / "items"
    return base / (("%s.md" % task) if not tag else "%s/%s.md" % (task, tag))


def part_of(rel):
    """(task, tag) for a path relative to `items/` or `archive/items/`, or None:
    `W13.md` -> (W13, ""), `W13/fx.md` -> (W13, fx). A name starting `_` is a
    template, not a task."""
    parts = Path(rel).parts
    if len(parts) == 1 and re.fullmatch(r"(%s)\.md" % TASK_RE, parts[0]):
        return parts[0][:-3], ""
    if len(parts) == 2 and re.fullmatch(TASK_RE, parts[0]) \
            and re.fullmatch(r"(%s)\.md" % TAG_RE, parts[1]):
        return parts[0], parts[1][:-3]
    return None


def part_tags(task, base=None):
    """The tags `task` has a part for under `base` (default `items/`), default first."""
    base = base or dfs_paths.items()
    tags = [""] if (base / ("%s.md" % task)).exists() else []
    d = base / task
    if d.is_dir():
        tags += sorted(p.stem for p in d.glob("*.md") if part_of("%s/%s" % (task, p.name)))
    return tags


def task_ids():
    """Every task with a part on disk: a top-level file, a directory of branch parts,
    or both."""
    out = set()
    for p in dfs_paths.items().glob("*"):
        rel = p.name if p.is_file() else None
        if rel and part_of(rel):
            out.add(part_of(rel)[0])
        elif p.is_dir() and re.fullmatch(TASK_RE, p.name) and part_tags(p.name):
            out.add(p.name)
    return sorted(out)


def exists(task):
    return bool(part_tags(task))


def path_for(task):
    """The file THIS branch writes for `task`: what a session edits and commits.
    Raises dfs_paths.NoBranch on a detached HEAD."""
    return part_path(task, dfs_paths.branch_tag())


def part_files(task):
    """Every part of `task` on disk, default first: what a session reads."""
    return [part_path(task, tag) for tag in part_tags(task)]


def home_path(task):
    """The part holding the task's Goal: the default part if it has one, else the
    first part that does. What an editor opens when this branch has no part yet."""
    for p in part_files(task):
        if re.search(r"^## Goal\s*$", p.read_text(), re.M):
            return p
    files = part_files(task)
    return files[0] if files else part_path(task)


# ── parsing ──────────────────────────────────────────────────────────────────

def split_sections(text):
    """(title line, {section name: body text}, [section names in order])."""
    title, sections, order, cur = "", {}, [], None
    for line in text.splitlines():
        if cur is None and line.startswith("# ") and not title:
            title = line
            continue
        m = re.match(r"^## (\w[\w ]*)\s*$", line)
        if m:
            cur = m.group(1).strip()
            sections[cur] = []
            order.append(cur)
            continue
        if cur is not None:
            sections[cur].append(line)
    return title, {k: "\n".join(v).strip("\n") for k, v in sections.items()}, order


def parse_nodes(body, task, pre=None):
    """The nodes of a Tree section. Lines before the first node go to `pre`, so a
    re-render never drops what somebody wrote there."""
    nodes, cur = [], None
    for line in (body or "").splitlines():
        m = NODE_HEAD.match(line)
        if m and m.group(1) == task:
            tag = m.group(3) or ""
            cur = dict(id=node_id(task, int(m.group(2)), tag), n=int(m.group(2)), tag=tag,
                       title=m.group(4).strip(), fields={}, evidence=[], extra=[],
                       raw=[line])
            nodes.append(cur)
            continue
        if cur is None:
            if pre is not None:
                pre.append(line)
            continue
        cur["raw"].append(line)
        fm = FIELD_RE.match(line)
        if fm:
            cur["last"] = field_name(fm.group(1))
            if cur["last"] != "Evidence":
                cur["fields"][cur["last"]] = fm.group(2).strip()
            continue
        if cur.get("last") == "Evidence" and re.match(r"^\s*- ", line):
            cur["evidence"].append(line.strip()[2:].strip())
        elif line.strip() and cur.get("last") and cur["last"] != "Evidence":
            # A wrapped field value: joined to the field it continues.
            k = cur["last"]
            cur["fields"][k] = (cur["fields"].get(k, "") + " " + line.strip()).strip()
        elif line.strip() and cur.get("last") == "Evidence" and cur["evidence"]:
            cur["evidence"][-1] += " " + line.strip()
        elif line.strip():
            cur["extra"].append(line)
    for nd in nodes:
        nd.pop("last", None)
        while nd["raw"] and not nd["raw"][-1].strip():
            nd["raw"].pop()
        f = nd["fields"]
        nd["status"] = f.get("Status", "open").split()[0].lower() if f.get("Status") else "open"
        nd["parent"] = f.get("Parent") or None
    return nodes


def parse_log(body):
    entries, cur = [], None
    for line in (body or "").splitlines():
        m = LOG_HEAD.match(line)
        if m:
            args = [a.strip() for a in m.group(3).split(" · ")[1:]] if m.group(3) else []
            cur = dict(ts=m.group(1), kind=m.group(2), args=args, body_lines=[],
                       head=line)
            entries.append(cur)
            continue
        if cur is not None:
            # Anything under an entry is its body, indented or not, so a re-render
            # never drops a line somebody forgot to indent.
            cur["body_lines"].append(line[2:] if line.startswith("  ") else line)
    for e in entries:
        while e["body_lines"] and not e["body_lines"][-1].strip():
            e["body_lines"].pop()
        e["body"] = "\n".join(e["body_lines"]).strip()
        e["text"] = "\n".join([e["head"]] + ["  " + b if b else "" for b in e["body_lines"]])
    return entries


def parse(text, task):
    title, sections, order = split_sections(text)
    name = re.sub(r"^#\s*\S+\s*·\s*", "", title).strip()
    pre = []
    nodes = parse_nodes(sections.get("Tree", ""), task, pre)
    while pre and not pre[-1].strip():
        pre.pop()
    return dict(task=task, title=title, name=name, sections=sections, order=order,
                goal=sections.get("Goal", "").strip(), nodes=nodes,
                tree_pre="\n".join(pre).strip("\n"),
                log=parse_log(sections.get("Log", "")))


def archive_path(task):
    """This branch's archive part for `task`."""
    return archive_part(task, dfs_paths.branch_tag())


ARCHIVE_LOG = "**Log**:"


def archived_log(archive):
    """The log entries an archive holds: whatever follows a `**Log**:` line, up to the
    next heading or bold line that starts a line. An entry's body is indented, so a
    `**` inside one does not end the block."""
    keep, on = [], False
    for line in (archive or "").splitlines():
        if line.startswith(("#", "**")):
            on = line.strip() == ARCHIVE_LOG
        elif on:
            keep.append(line)
    return parse_log("\n".join(keep))


# The fields a determined node may move to the archive, and what it leaves behind.
SHELVED = ("Approach", "Hypothesis")
SHELF_STUB = "archived."
ARCHIVE_FIELD = re.compile(r"^\*\*(%s) (%s)\*\*: (.*)$"
                           % (NODE_RE, "|".join(SHELVED + tuple(RENAMED))))


def archived_fields(archive):
    """{node id: {field: text}} for every `**<id> Approach**: <text>` line an archive
    holds. The latest line for a field wins, since the archive only grows."""
    out = {}
    for line in (archive or "").splitlines():
        m = ARCHIVE_FIELD.match(line.rstrip())
        if m:
            out.setdefault(m.group(1), {})[field_name(m.group(2))] = m.group(3).strip()
    return out


def with_archive(t, archive):
    """`t` with the archive's log entries merged into its log, by time, each once, and
    each shelved Approach or Hypothesis put back in place of its stub. A field put back is
    named in the node's `from_archive`, so a reader can say where the words came from."""
    held = archived_fields(archive)
    for nd in t["nodes"]:
        for k, v in held.get(nd["id"], {}).items():
            if nd["fields"].get(k) == SHELF_STUB:
                nd["fields"][k] = v
                nd.setdefault("from_archive", set()).add(k)
    have = {e["text"].rstrip() for e in t["log"]}
    old = []
    for e in archived_log(archive):
        # Once, even when the archive holds an entry twice (a hand-resolved merge).
        if e["text"].rstrip() not in have:
            have.add(e["text"].rstrip())
            old.append(dict(e, archived=True))
    t["log"] = sorted(old + t["log"], key=lambda e: e["ts"])
    return t


def combine(task, parts, here=None):
    """One task from its parts, `[(tag, text, archive text)]`: every part's nodes, the
    logs merged by time, and the Goal and sections from the default part if it has a
    Goal, else from the first part that does. `here` is the reading branch's tag, which
    the frontier puts first; `t["parts"]` keeps each part as parsed."""
    parsed = {}
    for tag, text, arc in parts:
        parsed[tag] = with_archive(parse(text or "", task), arc or "")
        parsed[tag]["part"] = tag
    tags = sorted(parsed, key=lambda g: (g != "", g))
    home = next((g for g in tags if parsed[g]["goal"]), tags[0] if tags else None)
    t = dict(parsed[home]) if home is not None else parse("", task)
    t["nodes"] = [nd for g in tags for nd in parsed[g]["nodes"]]
    # Not de-duplicated across parts: two branches can each log a `session · work` in
    # the same second, and those are two sessions.
    t["log"] = sorted((e for g in tags for e in parsed[g]["log"]), key=lambda e: e["ts"])
    t.update(parts=parsed, home=home, here=here)
    return t


def load(task, here=None):
    """The task as every reader should see it: every part, with each log's archived
    past merged back in. `here` defaults to this checkout's tag (None if detached)."""
    def read(p):
        return p.read_text() if p.exists() else ""
    tags = part_tags(task)
    if here is None:
        here = dfs_paths.branch_tag_or_none()
    return combine(task, [(g, read(part_path(task, g)), read(archive_part(task, g)))
                          for g in tags], here)


def new_part(task, name=""):
    """A part for a branch that has not written to `task` yet: nodes and log only, the
    Goal stays in the part that has it."""
    return "# %s%s\n\n## Tree\n\n## Log\n" % (task, " · " + name if name else "")


def ensure_part(task):
    """This branch's part of `task`, created if the task exists elsewhere."""
    p = path_for(task)
    if not p.exists():
        if not exists(task):
            raise FileNotFoundError(part_path(task))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(new_part(task, load(task)["name"]))
    return p


def next_node_id(t, tag=None):
    """One past the highest number `tag` (default this branch's) has used in `t`."""
    tag = dfs_paths.branch_tag() if tag is None else tag
    return node_id(t["task"], max((nd["n"] for nd in t["nodes"] if nd.get("tag", "") == tag),
                                  default=0) + 1, tag)


def log_cutoff(log):
    """The author's last word: log entries before it may move to the archive."""
    return max((e["ts"] for e in log if e["kind"] in ("answer", "correct", "accept")),
               default="")


def compact(task):
    """Move every file log entry older than the author's last word to the END of the
    archive, verbatim, under a new `**Log**:` line. Returns how many moved."""
    p, a = path_for(task), archive_path(task)
    if not p.exists():
        return 0
    t = parse(p.read_text(), task)
    arc = a.read_text() if a.exists() else ""
    cut = log_cutoff(load(task)["log"])
    move = [e for e in t["log"] if e["ts"] < cut]
    if not move:
        return 0
    t["log"] = [e for e in t["log"] if e["ts"] >= cut]
    a.parent.mkdir(parents=True, exist_ok=True)
    a.write_text(arc.rstrip("\n") + ("\n\n" if arc.strip() else "") + ARCHIVE_LOG + "\n"
                 + "\n".join(e["text"].rstrip() for e in move) + "\n")
    p.write_text(render(t))
    return len(move)


def _unfield(raw, key):
    """`raw` with the `key:` line and its continuation lines replaced by the stub."""
    out, skipping = [], False
    for line in raw:
        fm = FIELD_RE.match(line)
        if fm:
            skipping = field_name(fm.group(1)) == key
            if skipping:
                out.append("%s: %s" % (key, SHELF_STUB))
                continue
        elif skipping and line.strip():
            continue
        out.append(line)
    return out


def shelve(task, ids=()):
    """Move the Approach and Hypothesis of determined nodes (all of them, or `ids`) to the
    END of the archive, verbatim, each as one `**<id> <field>**: <text>` line, and leave
    `<field>: archived.` in the task file. Returns how many fields moved."""
    p, a = path_for(task), archive_path(task)
    t = parse(p.read_text() if p.exists() else "", task)
    arc = a.read_text() if a.exists() else ""
    unknown = set(ids) - {nd["id"] for nd in t["nodes"]}
    if unknown:
        raise ValueError("no node %s in %s, the part this branch writes; a node is "
                         "shelved on the branch that made it"
                         % (", ".join(sorted(unknown)), dfs_paths.rel(p)))
    moved = []
    for nd in sorted(t["nodes"], key=order_key):
        if ids and nd["id"] not in ids:
            continue
        if nd["status"] not in DETERMINED:
            if ids:
                raise ValueError("%s is %s; only a determined node is history"
                                 % (nd["id"], nd["status"]))
            continue
        for k in SHELVED:
            v = nd["fields"].get(k)
            if v and v != SHELF_STUB:
                moved.append("**%s %s**: %s" % (nd["id"], k, v))
                nd["raw"] = _unfield(nd["raw"], k)
    if not moved:
        return 0
    a.parent.mkdir(parents=True, exist_ok=True)
    a.write_text(arc.rstrip("\n") + ("\n\n" if arc.strip() else "")
                 + "\n\n".join(moved) + "\n")
    p.write_text(render(t))
    return len(moved)


# ── rendering ────────────────────────────────────────────────────────────────

def render(t):
    """The file, canonical: sections in their order, nodes by number, log by time."""
    out = [t["title"] or "# %s" % t["task"], ""]
    names = list(t["order"])
    # Goal only where it already is: a branch's part carries nodes and log alone.
    for must in (("Goal",) if not t["order"] else ()) + ("Tree", "Log"):
        if must not in names:
            names.append(must)
    for name in names:
        out += ["## %s" % name, ""]
        if name == "Tree":
            if t.get("tree_pre"):
                out += [t["tree_pre"], ""]
            for nd in sorted(t["nodes"], key=order_key):
                out += nd["raw"] + [""]
        elif name == "Log":
            for e in sorted(t["log"], key=lambda e: e["ts"]):
                out += [e["text"]]
            out += [""]
        else:
            body = t["sections"].get(name, "")
            out += ([body, ""] if body else [])
    return "\n".join(out).rstrip() + "\n"


def now_ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _ts_after(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(
        calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")) + 1))


def make_entry(ts, kind, args=(), body=""):
    head = "- %s · %s%s" % (ts, kind, "".join(" · %s" % a for a in args if a != ""))
    return parse_log("\n".join(
        [head] + ["  " + b if b.strip() else "" for b in (body or "").strip("\n").splitlines()]))[0]


def append_log(task, kind, args=(), body=""):
    """Append one entry, keyed by a timestamp later than every other in this file,
    and re-render: the Log section is found by name, wherever it is."""
    if kind not in KINDS:
        raise ValueError("unknown log kind %r (one of %s)" % (kind, ", ".join(KINDS)))
    p = ensure_part(task)
    t = parse(p.read_text(), task)
    ts = now_ts()
    # Later than every entry of the TASK, not just of this part: an answer or a
    # correction names what it settles by timestamp, across parts.
    latest = max((e["ts"] for e in load(task)["log"]), default="")
    if ts <= latest:
        ts = _ts_after(latest)
    t["log"].append(make_entry(ts, kind, args, body))
    p.write_text(render(t))
    return ts


def next_task_id():
    """The id a new task gets: one past the highest W on ANY branch this checkout can see,
    tagged with this branch's. Raises dfs_paths.NoBranch on a detached HEAD."""
    tag = dfs_paths.branch_tag()
    nums = [int(m.group(1)) for m in (re.fullmatch(r"W(\d+)(?:@.*)?", i) for i in task_ids()) if m]
    return "W%d%s" % (max(nums, default=0) + 1, "@" + tag if tag else "")


def create_task(name, goal="", todos=(), task=None):
    """Write a new task file: the author's words unchanged, the todo lines a LINEAR chain of
    open nodes (each the child of the one before, the shape a task starts in; the sessions
    add nodes, branch and back up from there). `task` is an id the author chose (letters then
    a number, tagged here), else the next one. Returns the id; ValueError says what is wrong."""
    tag = dfs_paths.branch_tag()
    suffix = "@" + tag if tag else ""
    name = " ".join((name or "").split())
    if not name:
        raise ValueError("a task needs a name")
    task = (task or "").strip() or next_task_id()
    if re.fullmatch(r"[A-Za-z]+\d+", task):
        task += suffix
    if not re.fullmatch(r"[A-Za-z]+\d+%s" % re.escape(suffix), task):
        raise ValueError("a task id is letters then a number (W25)%s, not %r"
                         % (", tagged %s on this branch" % suffix if suffix else "", task))
    if exists(task):
        raise ValueError("%s already exists: pick another id" % task)
    path = part_path(task, tag)
    todos = [" ".join(x.split()) for x in todos if x and x.strip()]
    body = ["# %s · %s" % (task, name), "", "## Goal", "", (goal or "").strip() or name, "", "## Tree", ""]
    for n, todo in enumerate(todos, 1):
        body += ["### %s · %s" % (node_id(task, n, tag), todo)]
        if n > 1:
            body += ["Parent: %s" % node_id(task, n - 1, tag)]
        body += ["Status: open", ""]
    body += ["## Log", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(body).rstrip() + "\n")
    return task


def edit_task(task, name, goal):
    """Change a task's name and Goal, the author's words, in the part that holds the Goal
    (the title line and the `## Goal` section; nothing else in the file moves). A blank
    goal is refused rather than written as an empty section. ValueError says what is wrong."""
    name = " ".join((name or "").split())
    goal = (goal or "").strip()
    if not name:
        raise ValueError("a task needs a name")
    if not goal:
        raise ValueError("a task needs a goal")
    if not exists(task):
        raise FileNotFoundError(part_path(task))
    p = home_path(task)
    t = parse(p.read_text(), task)
    t["title"] = "# %s · %s" % (re.match(r"#\s*(\S+)", t["title"] or "# " + task).group(1), name)
    if "Goal" not in t["order"]:
        t["order"].insert(0, "Goal")
    t["sections"]["Goal"] = goal
    p.write_text(render(t))
    return task


def edit_node(task, nid, title, approach=""):
    """Change the title and Approach of an open or parked node, in the part that holds it.
    A confirmed or refuted node is history and is refused, as is one whose Approach was
    shelved to the archive. An empty approach removes the field. Returns `nid`;
    ValueError says what is wrong."""
    title = " ".join((title or "").split())
    approach = " ".join((approach or "").split())
    if not title:
        raise ValueError("a node needs a title")
    parts = split_id(nid)
    if not parts or parts[0] != task or not exists(task):
        raise ValueError("no node %s in %s" % (nid, task))
    p = part_path(task, parts[2])
    t = parse(p.read_text(), task) if p.exists() else None
    nd = by_id(t).get(nid) if t else None
    if not nd:
        raise ValueError("no node %s in %s" % (nid, task))
    if nd["status"] not in ("open", "parked"):
        raise ValueError("%s is %s, and a determined node is history: add a node instead"
                         % (nid, nd["status"]))
    if nd["fields"].get("Approach") == "archived.":
        raise ValueError("%s's Approach is in the archive" % nid)
    kept, last, at = [], None, None
    for line in nd["raw"][1:]:
        fm = FIELD_RE.match(line)
        if fm:
            last = field_name(fm.group(1))
        if (fm and last == "Approach") or (not fm and last == "Approach" and line.strip()):
            at = len(kept) if at is None else at       # the field, and any wrapped lines
            continue
        kept.append(line)
    if at is None:                      # none yet: after Parent and Status, before the rest
        at = next((i for i, l in enumerate(kept)
                   if not re.match(r"(Parent|Status):", l)), len(kept))
    if approach:
        kept.insert(at, "Approach: %s" % approach)
    nd["raw"] = ["### %s · %s" % (nid, title)] + kept
    nd["title"] = title
    p.write_text(render(t))
    return nid


def add_node(task, title, parent=None, approach=""):
    """Add an open node to this branch's part: the author's way of putting work in the
    tree between sessions. It takes the next number this branch has not used, so it
    comes after its parent (a node's parent comes first), and it is the child of
    `parent`, or of the task when there is none. Returns the new id."""
    title = " ".join((title or "").split())
    if not title:
        raise ValueError("a node needs a title")
    if parent and parent not in by_id(load(task)):
        raise ValueError("no node %s in %s to put it under" % (parent, task))
    p = ensure_part(task)
    t = parse(p.read_text(), task)
    nid = next_node_id(t)
    raw = ["### %s · %s" % (nid, title)]
    if parent:
        raw.append("Parent: %s" % parent)
    raw.append("Status: open")
    approach = " ".join((approach or "").split())
    if approach:
        raw.append("Approach: %s" % approach)
    t["nodes"].append(parse_nodes("\n".join(raw), task)[0])
    p.write_text(render(t))
    return nid


# ── derivation ───────────────────────────────────────────────────────────────

def by_id(t):
    return {nd["id"]: nd for nd in t["nodes"]}


def children(t, nid):
    return [nd for nd in t["nodes"] if nd["parent"] == nid]


def descendants(t, nid):
    out, stack = [], [nid]
    while stack:
        for c in children(t, stack.pop()):
            out.append(c)
            stack.append(c["id"])
    return out


def keeps(verdict, was):
    """Whether a correction to `verdict` of a node that stands `was` prunes nothing: confirming
    what already stands confirmed only adds the author's words. The one rule, for the
    derivation (`corrections`) and for every place that tells the author what a correction
    will do before it is written."""
    return verdict == "confirmed" and was == "confirmed"


def corrections(t):
    """The author's corrections and the critic's backtracks, each with whether a node
    has carried it out. A backtrack's `disp` is "backtrack"."""
    carried = {nd["fields"].get("Corrects") for nd in t["nodes"]}
    now = {nd["id"]: nd["status"] for nd in t["nodes"]}
    out = []
    for e in t["log"]:
        if e["kind"] not in ("correct", "backtrack") or not e["args"]:
            continue
        disp = ("backtrack" if e["kind"] == "backtrack" else
                e["args"][1] if len(e["args"]) > 1 else "refuted")
        node = e["args"][0]
        # `keeps`: confirming what already stands confirmed only adds the author's words;
        # nothing under it was built on a wrong verdict, so nothing is pruned.
        kept = keeps(disp, now.get(node))
        if disp != "backtrack" and node in now:
            now[node] = disp if disp in DETERMINED else "refuted"
        out.append(dict(ts=e["ts"], node=node, disp=disp, body=e["body"],
                        done=e["ts"] in carried, keeps=kept))
    return out


def effective(t):
    """Each node's status once the author's corrections are applied, and whether it
    is PRUNED — below a refuted node, or cut off by a correction.

    ⚠️ A correction at N makes N's determination the author's and prunes N's
    descendants, which were built on the old one. Siblings are ALTERNATIVES, so what
    happens to them depends on the verdict: refuted, and they stay — they are what the
    work jumps to; confirmed, and every sibling made after N is pruned too — those were
    the alternatives taken when N was wrongly abandoned. Work resumes under N if
    confirmed, at a sibling if refuted. The exception: confirming a node that already
    stands confirmed (`keeps`) abandoned nothing, so it prunes nothing and only adds
    the author's words. Nothing in the file is edited: a determined
    node is history, and what it means now is derived here.

    A critic's BACKTRACK at N says neither: N's evidence does not carry its
    determination, so N is withdrawn with everything under it, and the work redoes
    N's step beside it, under its parent. N may well come out the same way; it has to
    be shown this time.
    """
    nodes = by_id(t)
    status = {nid: nd["status"] for nid, nd in nodes.items()}
    cut = set()
    for c in corrections(t):
        if c["node"] not in nodes:
            continue
        nd = nodes[c["node"]]
        if c["disp"] == "backtrack":
            if nd["fields"].get("Corrects") != c["ts"]:
                cut.add(nd["id"])
            cut.update(d["id"] for d in descendants(t, nd["id"])
                       if d["fields"].get("Corrects") != c["ts"])
            continue
        status[c["node"]] = c["disp"] if c["disp"] in DETERMINED else "refuted"
        if c["keeps"]:
            continue
        for d in descendants(t, nd["id"]):
            # A node made to carry the correction out is not cut by it.
            if d["fields"].get("Corrects") != c["ts"]:
                cut.add(d["id"])
        if status[c["node"]] == "confirmed":
            # "Made after" is only known within one tag: another branch's sibling is
            # a parallel alternative, not one taken when N was abandoned.
            for s_ in t["nodes"]:
                if s_["parent"] == nd["parent"] and s_.get("tag", "") == nd.get("tag", "") \
                        and s_["n"] > nd["n"] \
                        and s_["fields"].get("Corrects") != c["ts"]:
                    cut.add(s_["id"])
                    cut.update(d["id"] for d in descendants(t, s_["id"]))
    pruned = set(cut)
    for nid, nd in sorted(nodes.items(), key=lambda kv: order_key(kv[1])):
        p = nd["parent"]
        while p and p in nodes:
            if p in pruned or status.get(p) == "refuted":
                pruned.add(nid)
                break
            p = nodes[p]["parent"]
    return status, pruned


def dormant(t):
    """Parked nodes and everything planned under them: live, but not being worked."""
    nodes = by_id(t)
    status, pruned = effective(t)
    out = set()
    for nid, nd in nodes.items():
        p = nid
        while p and p in nodes:
            if status.get(p) == "parked":
                out.add(nid)
                break
            p = nodes[p]["parent"]
    return out - pruned


def frontier(t):
    """The open, unpruned nodes whose ancestors are all confirmed: what is workable
    now, in creation order, THIS branch's own first (`t["here"]`): a branch exists to
    pursue its alternative, while the default branch's open node is still open beside
    it. The first is the current node."""
    nodes = by_id(t)
    status, pruned = effective(t)
    here = t.get("here")
    out = []
    for nd in sorted(t["nodes"], key=lambda n: (here is not None and n.get("tag", "") != here,
                                                 order_key(n))):
        if nd["id"] in pruned or status[nd["id"]] != "open":
            continue
        p, ok = nd["parent"], True
        while p and p in nodes:
            if status[p] != "confirmed":
                ok = False
                break
            p = nodes[p]["parent"]
        if ok:
            out.append(nd)
    return out


def open_raises(t):
    answered = {e["args"][0] for e in t["log"] if e["kind"] == "answer" and e["args"]}
    return [e for e in t["log"] if e["kind"] == "raise" and e["ts"] not in answered]


def unacted_answers(t):
    """Answers no work session has acted on yet: none ended `ok` after the answer,
    and the author has not accepted the tree since. An answer REOPENS the task — the
    author spoke because something is to be done — so a work session that hit a
    limit, failed or changed nothing has not acted on it, and a review of the tree
    as it stood would be reviewing the question, not the answer."""
    last_ok = max((e["ts"] for e in t["log"] if e["kind"] == "end"
                   and e["args"][:2] == ["work", "ok"]), default="")
    last_acc = max((e["ts"] for e in t["log"] if e["kind"] == "accept"), default="")
    return [e for e in t["log"] if e["kind"] == "answer"
            and e["ts"] > last_ok and e["ts"] > last_acc]


def answer_for(t, ts):
    return next((e for e in t["log"] if e["kind"] == "answer" and e["args"]
                 and e["args"][0] == ts), None)


def author_marks(t):
    return [e for e in t["log"] if e["kind"] in ("answer", "correct", "accept")]


def sessions_since_author(t):
    """Work sessions since the author last spoke on this task — the session limit's
    count, so an answer to its raise is what resets it."""
    last = max((e["ts"] for e in author_marks(t)), default="")
    return sum(1 for e in t["log"] if e["kind"] == "session" and e["args"][:1] == ["work"]
               and e["ts"] > last)


def sessions_since_critic(t):
    last = max((e["ts"] for e in t["log"] if e["kind"] == "session"
                and e["args"][:1] == ["critic"]), default="")
    return sum(1 for e in t["log"] if e["kind"] == "session" and e["args"][:1] == ["work"]
               and e["ts"] > last)


def last_verdict(t, kind):
    v = [e for e in t["log"] if e["kind"] == kind]
    return v[-1] if v else None


def last_session(t):
    s = [e for e in t["log"] if e["kind"] == "session"]
    return s[-1] if s else None


def cut_off(t):
    """The last work session if it has no `end` after it — it was killed outright."""
    last = None
    for e in t["log"]:
        if e["kind"] == "session" and e["args"][:1] == ["work"]:
            last = e
        elif e["kind"] == "end" and last is not None:
            last = None
    return last


def status_of(t):
    """(status, why) — the task's one status, from its tree and its log."""
    raises = open_raises(t)
    if raises:
        return "blocked", "open raise %s" % ", ".join(
            "%s%s" % (r["ts"], (" on " + r["args"][0]) if r["args"] else "") for r in raises)
    answers = unacted_answers(t)
    if answers:
        return "open", "the author answered the raise of %s — act on it first" % (
            answers[0]["args"][0] if answers[0]["args"] else answers[0]["ts"])
    todo = [c for c in corrections(t) if not c["done"]]
    if todo:
        return "open", ("the critic backtracked to %s — redo it first" if todo[0]["disp"]
                        == "backtrack" else "the author corrected %s — carry it out first"
                        ) % todo[0]["node"]
    if not t["nodes"]:
        return "open", "no nodes yet — plan the task"
    fr = frontier(t)
    if fr:
        return "open", "current node %s · %s" % (fr[0]["id"], fr[0]["title"])
    why = incomplete(t)
    if why:
        return "open", why
    if review_due(t):
        return "open", "the tree is complete; the implementation review is due"
    rv = last_verdict(t, "review")
    if rv and rv["args"][:1] == ["issues"] and not any(
            e["kind"] == "accept" and e["ts"] > rv["ts"] for e in t["log"]):
        # The reviewer puts each issue in the tree as an open node, which reopens the
        # task by itself; this is the review that said `issues` and added none. The
        # fix is still work, in new nodes, and completing those earns another review.
        # The author's `accept` is what takes the code as it is instead.
        return "open", "the implementation review found issues; fix them in new nodes"
    last_acc = max((e["ts"] for e in t["log"] if e["kind"] == "accept"), default="")
    if last_acc and (not rv or last_acc > rv["ts"]):
        return "done", "every live node confirmed, and accepted by the author"
    if rv:
        return "done", "every live node confirmed, and the implementation reviewed"
    return "done", "every live node confirmed"


def incomplete(t):
    """Why the tree is not complete, or None when it is. A refuted node is a path
    backed out of and a parked one an alternative not taken, so neither holds the task
    open; the tree is complete when no live, non-dormant node is open and at least one
    is confirmed."""
    if not t["nodes"]:
        return "no nodes yet — plan the task"
    if frontier(t):
        return "there is a workable node"
    status, pruned = effective(t)
    asleep = dormant(t)
    live = [nd for nd in t["nodes"] if nd["id"] not in pruned and nd["id"] not in asleep]
    if any(status[nd["id"]] == "open" for nd in live):
        return "no workable node — an open node sits under one that is not confirmed"
    if not any(status[nd["id"]] == "confirmed" for nd in live):
        return "no workable node — every path is refuted or parked; re-plan or unpark one"
    return None


def review_due(t):
    """A tree some session completed has not had its implementation reviewed since the
    last work session. A task finished before the tree existed has no sessions, and
    nothing of its to review here. ⚠️ The author's `accept` counts as that review: it
    takes the code as it is, and a review after it would judge code already taken
    (W13 read review-due for this reason after its accept)."""
    last_work = max((e["ts"] for e in t["log"] if e["kind"] == "end"
                     and e["args"][:1] == ["work"]), default="")
    if not last_work or incomplete(t) or unacted_answers(t) \
            or any(not c["done"] for c in corrections(t)):
        return False
    last_seen = max((e["ts"] for e in t["log"] if e["kind"] in ("review", "accept")),
                    default="")
    return last_seen < last_work


def review_rounds_unsettled(t):
    """Implementation reviews that found issues since the author last spoke and since
    a review last came back ok: the rounds a fix has not settled.

    ⚠️ The count is what stops the review loop. A review's issues become nodes, the
    nodes' fixes complete the tree, and a complete tree is due another review, which
    always finds something smaller: W20 ran five rounds in one evening, down to the
    wording of a refusal message. At REVIEW_ROUNDS the runner raises instead of
    reviewing again, and the author's answer or accept starts the count over."""
    last = max([e["ts"] for e in author_marks(t)] +
               [e["ts"] for e in t["log"] if e["kind"] == "review"
                and e["args"][:1] == ["ok"]], default="")
    return sum(1 for e in t["log"] if e["kind"] == "review"
               and e["args"][:1] == ["issues"] and e["ts"] > last)


def accepted(t):
    """Whether the author accepted the tree after its last change."""
    last_acc = max((e["ts"] for e in t["log"] if e["kind"] == "accept"), default="")
    last_work = max((e["ts"] for e in t["log"] if e["kind"] in ("end", "correct", "backtrack")),
                    default="")
    return bool(last_acc) and last_acc >= last_work


def assumed(t):
    """The live nodes that rest on an assumption (they state a Hypothesis) or put a
    question to the author (they state an Ask), in tree order. Most nodes only say
    something was done and are neither. A refuted or pruned node's assumption is moot."""
    status, pruned = effective(t)
    return [nd["id"] for nd in sorted(t["nodes"], key=order_key)
            if (nd["fields"].get("Hypothesis") or nd["fields"].get("Ask"))
            and status[nd["id"]] != "refuted" and nd["id"] not in pruned]


def split_falsifier(text):
    """A Hypothesis as `(the assumption, what would show it wrong)`. The skill asks
    for both in one field, the second after `Wrong if`; `("...", "")` when it has none."""
    m = re.search(r"\bwrong if\b[:,]?\s*", text or "", re.I)
    if not m:
        return (text or "").strip(), ""
    return text[:m.start()].strip(" .;—-") + ".", text[m.end():].strip()


def story(t, nid):
    """What the author has SAID about one node, so the screen can keep it with the
    node instead of in the log pane: the raises on it and their answers, the
    corrections at it (and a determination that now contradicts one), and the
    correction a node carries out.

    ⚠️ A node's Determination is history and is never edited, so after the author
    overrules it the file still says `Confirmed`. `overruled` says so, with the
    author's own reason."""
    nodes = by_id(t)
    nd = nodes.get(nid)
    if nd is None:
        return dict(answered=[], corrections=[], carries=None, overruled=None)
    answers = {e["args"][0]: e for e in t["log"] if e["kind"] == "answer" and e["args"]}
    answered = [(e, answers[e["ts"]]) for e in t["log"]
                if e["kind"] == "raise" and e["args"][:1] == [nid] and e["ts"] in answers]
    corr = [c for c in corrections(t) if c["node"] == nid]
    carries = next((c for c in corrections(t) if c["ts"] == nd["fields"].get("Corrects")), None)
    overruled = None
    for c in corr:
        if c["disp"] in DETERMINED and nd["status"] in DETERMINED and c["disp"] != nd["status"]:
            overruled = c
    return dict(answered=answered, corrections=corr, carries=carries, overruled=overruled)


def notes(t):
    """The critic's and the reviewer's remarks that came with a verdict, newest last:
    `(kind, ts, verdict, body)`. A review's `ok` carries its MINOR findings in the
    body and the skill says the author reads them when they accept the tree, so they
    are listed here for the screen to put in front of the author."""
    return [(e["kind"], e["ts"], e["args"][0] if e["args"] else "", e["body"].strip())
            for e in t["log"]
            if e["kind"] in ("critic", "review") and e["body"].strip()]


# ── commits ──────────────────────────────────────────────────────────────────

def node_commits(task, cwd=None):
    """{node id: [(sha, subject, committed-at ts)]}, newest first, from subjects
    `<task>.<n>[@<tag>]: <subject>`, on every branch HEAD can reach."""
    try:
        out = subprocess.run(
            ["git", "log", "--format=%H%x09%ct%x09%s", "--extended-regexp",
             "--grep=^%s\\.[0-9]+(@[a-z0-9-]+)?:" % re.escape(task)],
            capture_output=True, text=True, cwd=cwd or dfs_paths.work_root()).stdout
    except OSError:
        return {}
    found = {}
    for line in out.splitlines():
        sha, ct, subj = (line.split("\t", 2) + ["", ""])[:3]
        m = re.match(r"^(%s\.\d+(?:@%s)?):" % (re.escape(task), TAG_RE), subj)
        if m:
            at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ct or 0)))
            found.setdefault(m.group(1), []).append((sha, subj, at))
    return found


def reverted_shas(cwd=None):
    try:
        out = subprocess.run(["git", "log", "--format=%B", "--grep=This reverts commit"],
                             capture_output=True, text=True,
                             cwd=cwd or dfs_paths.work_root()).stdout
    except OSError:
        return set()
    return set(re.findall(r"This reverts commit ([0-9a-f]{7,40})", out))


def revert_recipe(sha):
    """The command that reverts a node commit's code and leaves `.dfs` alone.
    A node's commit carries its task file, so `git revert` would take the
    determined node's own text out with the code; and `git revert --no-commit`
    refuses to start while the task file is dirty, which it is once the session
    has written the node doing the revert. `--3way` conflicts where later
    commits moved the same lines, as a revert would. It commits nothing: the
    session commits it under its own node, with `This reverts commit <sha>.` in
    the body, which is what `reverted_shas` reads."""
    return "git diff %s^ %s -- . ':(exclude).dfs' | git apply -R --3way --index" % (sha, sha)


def kept_shas(t):
    """Commits a Determination says stay (`<sha> stays`), which are not reverted."""
    return set(m for nd in t["nodes"]
               for m in re.findall(r"\b([0-9a-f]{7,40}) stays\b",
                                   nd["fields"].get("Determination", "")))


def unreverted_pruned(t, cwd=None):
    """Pruned or refuted nodes whose commits are still in the code, per git,
    less those a Determination says stay."""
    status, pruned = effective(t)
    commits = node_commits(t["task"], cwd)
    gone = reverted_shas(cwd) | kept_shas(t)
    out = []
    for nd in sorted(t["nodes"], key=order_key):
        if nd["id"] in pruned or status[nd["id"]] == "refuted":
            for sha, subj, _at in commits.get(nd["id"], []):
                if not any(sha.startswith(g) or g.startswith(sha) for g in gone):
                    out.append((nd["id"], sha, subj))
    return out


# ── the one-line views ───────────────────────────────────────────────────────

def outline(t):
    """The tree as indented lines: id, effective status, title, determination."""
    status, pruned = effective(t)
    asleep = dormant(t)
    nodes = by_id(t)
    depth = {}
    for nd in sorted(t["nodes"], key=order_key):
        p = nd["parent"]
        depth[nd["id"]] = depth.get(p, -1) + 1 if p in nodes else 0
    lines = []

    def walk(parent, d):
        for nd in sorted((n for n in t["nodes"]
                          if (n["parent"] if n["parent"] in nodes else None) == parent),
                         key=order_key):
            st = ("pruned" if nd["id"] in pruned else
                  status[nd["id"]] if status[nd["id"]] != "open" or nd["id"] not in asleep
                  else "dormant")
            det = nd["fields"].get("Determination", "")
            lines.append("%s%s [%s] %s%s" % ("  " * d, nd["id"], st, nd["title"],
                                             (" — " + det) if det else ""))
            walk(nd["id"], d + 1)
    walk(None, 0)
    return lines


def main(argv):
    try:
        return _main(argv)
    except (ValueError, FileNotFoundError, dfs_paths.NoBranch) as e:
        print("dfs_tree: %s" % e, file=sys.stderr)
        return 2


def _main(argv):
    if len(argv) >= 3 and argv[1] == "log":
        task, kind, args = argv[2], argv[3] if len(argv) > 3 else "", argv[4:]
        body = "" if sys.stdin.isatty() else sys.stdin.read()
        print(append_log(task, kind, args, body))
        return 0
    if len(argv) >= 5 and argv[1] == "add":
        approach = "" if sys.stdin.isatty() else sys.stdin.read()
        print(add_node(argv[2], " ".join(argv[4:]), None if argv[3] == "-" else argv[3], approach))
        return 0
    if len(argv) == 3 and argv[1] == "compact":
        print("dfs_tree: moved %d log entries to %s"
              % (compact(argv[2]), dfs_paths.rel(archive_path(argv[2]))))
        return 0
    if len(argv) >= 3 and argv[1] == "shelve":
        n = shelve(argv[2], tuple(argv[3:]))
        print("dfs_tree: moved %d Approach and Hypothesis field%s to %s"
              % (n, "" if n == 1 else "s", dfs_paths.rel(archive_path(argv[2]))))
        return 0
    if len(argv) == 3 and argv[1] == "show":
        t = load(argv[2])
        print("\n".join(outline(t)) or "(no nodes)")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
