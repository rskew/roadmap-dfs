#!/usr/bin/env python3
"""The per-node history file of a new-format task (docs/history-format.md).

One section per node, `### <id> · <title>` to the next such line. The file only grows, so a
section is a transcript: a field that holds one value (Status, Approach, ...) has its LAST
line win, and `Evidence:` bullets add up. `parse` folds a text into the node dicts `tree.parse_nodes`
returns (same keys: id, n, tag, title, fields, evidence, extra, raw, status, parent), plus
`tags`, `supersedes` and `text`, the section exactly as written. `render` puts the
text back byte for byte. `append` adds lines to one section and touches no other.

(`history.py` is session metrics; this is the task history.)
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths as dfs_paths  # noqa: E402
import tree as dfs_tree  # noqa: E402

FIELDS = dfs_tree.FIELDS + ("Tags", "Supersedes")
FIELD_RE = re.compile(r"^(%s):\s?(.*)$" % "|".join(FIELDS + tuple(dfs_tree.RENAMED)))
LIST_FIELDS = ("Tags", "Supersedes")


def _split(text):
    """(text before the first node, [(node head match, the section's text as written)])."""
    pre, secs, cur = [], [], None
    for line in text.splitlines(keepends=True):
        m = dfs_tree.NODE_HEAD.match(line.rstrip("\r\n"))
        if m:
            cur = [m, []]
            secs.append(cur)
        (cur[1] if cur else pre).append(line)
    return "".join(pre), [(m, "".join(ls)) for m, ls in secs]


def _fold(m, text, task):
    tag = m.group(3) or ""
    nd = dict(id=dfs_tree.node_id(task, int(m.group(2)), tag), n=int(m.group(2)), tag=tag,
              title=m.group(4).strip(), fields={}, evidence=[], extra=[], text=text,
              raw=text.rstrip("\n").splitlines())
    last = None
    for line in nd["raw"][1:]:
        fm = FIELD_RE.match(line)
        if fm:
            last = dfs_tree.field_name(fm.group(1))
            if last != "Evidence":
                nd["fields"][last] = fm.group(2).strip()
            continue
        if last == "Evidence" and re.match(r"^\s*- ", line):
            nd["evidence"].append(line.strip()[2:].strip())
        elif line.strip() and last == "Evidence" and nd["evidence"]:
            nd["evidence"][-1] += " " + line.strip()
        elif line.strip() and last:
            nd["fields"][last] = (nd["fields"].get(last, "") + " " + line.strip()).strip()
        elif line.strip():
            nd["extra"].append(line)
    f = nd["fields"]
    nd["status"] = f["Status"].split()[0].lower() if f.get("Status") else "open"
    nd["parent"] = None if f.get("Parent") == task else (f.get("Parent") or None)
    for key in LIST_FIELDS:
        nd[key.lower()] = [x.strip() for x in f.get(key, "").split(",") if x.strip()]
    return nd


def parse(text, task):
    """{"pre": text before the first node, "nodes": [folded node dicts, in file order]}.
    The title line is in `pre`."""
    pre, secs = _split(text or "")
    return {"pre": pre, "nodes": [_fold(m, t, task) for m, t in secs if m.group(1) == task]
            if all(m.group(1) == task for m, _ in secs) else _foreign(secs, task)}


def _foreign(secs, task):
    raise ValueError("history of %s holds a node of another task: %s" % (task, [m.group(0) for m, _ in secs
                                                                               if m.group(1) != task][0]))


def render(parsed):
    """The text `parse` was given, byte for byte."""
    return parsed["pre"] + "".join(nd["text"] for nd in parsed["nodes"])


def append(text, nid, lines):
    """`text` with `lines` added to the end of node `nid`'s section, before the blank lines
    that separate it from the next. Nothing else changes. KeyError if there is no such node."""
    pre, secs = _split(text)
    out, hit = [pre], False
    for m, sec in secs:
        task = m.group(1)
        if dfs_tree.node_id(task, int(m.group(2)), m.group(3) or "") == nid and not hit:
            hit = True
            body = sec.rstrip("\n")
            gap = sec[len(body):][1:]          # the blank lines after the section's last line
            sec = body + "\n" + "".join(l.rstrip("\n") + "\n" for l in lines) + gap
        out.append(sec)
    if not hit:
        raise KeyError(nid)
    return "".join(out)


# ── migration: an old task (file, archive, log file) into a history file ─────

ARCHIVE_EV_HEAD = re.compile(r"^\*\*(%s) Evidence\*\*:\s*$" % dfs_tree.NODE_RE)
KEPT_IN_STATUS = ("Goal", "Summary")      # sections the status file keeps; nothing moves
OWN_SECTIONS = KEPT_IN_STATUS + ("Tree", "Log")


history_part = dfs_tree.history_part


def _norm(text):
    return " ".join(text.split())


def scan_archive(archive):
    """Every line of an archive, placed: {"fields": {id: {field: [[line, ...], ...]}} (one list
    per `**<id> <Field>**: text` line, the lines after it that belong to it), "evidence": {id:
    [[line, ...], ...]} (one list per `- ` item), "log": [lines], "other": [lines]}. A line
    that follows no head of a node's, or follows one for a node the task lacks, is `other`."""
    out = dict(fields={}, evidence={}, log=[], other=[])
    cur = None                                   # where a line that is no head goes
    for line in (archive or "").splitlines():
        line = line.rstrip()
        fm = dfs_tree.ARCHIVE_FIELD.match(line)
        em = ARCHIVE_EV_HEAD.match(line)
        if fm:
            cur = [fm.group(3).strip()]
            out["fields"].setdefault(fm.group(1), {}).setdefault(
                dfs_tree.field_name(fm.group(2)), []).append(cur)
            cur = ("field", cur)
        elif em:
            cur = ("ev", em.group(1))
        elif line.strip() == dfs_tree.ARCHIVE_LOG:
            cur = ("log", None)
        elif line.startswith(("#", "**")) and (line.endswith(":") or cur is None
                                               or cur[0] in ("log", "ev", "other")):
            cur = ("other", None)
            out["other"].append(line)
        elif not line.strip():
            if cur and cur[0] == "log":                  # a blank line inside an entry's body
                out["log"].append("")
        elif cur is None or cur[0] == "other":
            out["other"].append(line)
        elif cur[0] == "field":
            cur[1].append(line)
        elif cur[0] == "log":
            out["log"].append(line)
        elif line.lstrip().startswith("- "):
            out["evidence"].setdefault(cur[1], []).append([line])
        elif out["evidence"].get(cur[1]):
            out["evidence"][cur[1]][-1].append(line)
        else:
            out["other"].append(line)
    return out


def _section(nd, fields, evidence):
    """One node's section text: the task file's lines, with each stub put back as the
    archive's words (every archived version, oldest first, so the last wins as it does there)."""
    raw, out, seen = nd["raw"], [nd["raw"][0]], set()
    own_ev = {_norm(e) for e in nd["evidence"]}
    new_ev = []
    for it in evidence:
        if _norm(" ".join(it)).removeprefix("- ") not in own_ev and it not in new_ev:
            new_ev.append(it)

    def evidence_lines():
        return [l for it in new_ev for l in it]

    i = 1
    while i < len(raw):
        fm = dfs_tree.FIELD_RE.match(raw[i])
        if not fm:
            out.append(raw[i])
            i += 1
            continue
        j = i + 1
        while j < len(raw) and not dfs_tree.FIELD_RE.match(raw[j]):
            j += 1
        name, value, block = dfs_tree.field_name(fm.group(1)), fm.group(2).strip(), raw[i:j]
        first = name not in seen
        seen.add(name)
        if name == "Status" and value.split()[:1] == ["started"]:
            out.append("Status: open")                  # `started` is a mark of the working copy
        elif name in dfs_tree.SHELVED and first and fields.get(name):
            for lines in fields[name]:
                out += ["%s: %s" % (name, lines[0])] + lines[1:]
            if not (dfs_tree.is_stub(name, value) and not any(l.strip() for l in block[1:])):
                out += block
        elif name == "Evidence" and first and new_ev:
            out.append("Evidence:" if value == dfs_tree.SHELF_STUB else block[0])
            out += evidence_lines() + block[1:]
        else:
            out += block
        i = j
    for name in dfs_tree.SHELVED:                        # archived, and the file never had the field
        if name not in seen:
            for lines in fields.get(name, ()):
                out += ["%s: %s" % (name, lines[0])] + lines[1:]
    if "Evidence" not in seen and new_ev:
        out += ["Evidence:"] + evidence_lines()
    return "\n".join(out) + "\n"


def migrate(task, text, archive="", logtext=""):
    """The history of one part of an old-format task: `{"history": text, "log": entries,
    "log_text": text, "missing": [(source, line), ...]}`. `text` is the task file, `archive` its
    archive, `logtext` its log file. Each node's section is its file lines with the archive's
    words put back; the log is the three logs merged by time, each entry once. `missing` is
    `lost`: it is empty when nothing was dropped."""
    t = dfs_tree.with_log_file(dfs_tree.parse(text, task), logtext)
    held = scan_archive(archive)
    have = {e["text"].rstrip() for e in t["log"]}
    log = list(t["log"])
    for e in dfs_tree.parse_log("\n".join(held["log"])):
        if e["text"].rstrip() not in have:
            have.add(e["text"].rstrip())
            log.append(dict(e, archived=True))
    log.sort(key=lambda e: e["ts"])
    ids = {nd["id"] for nd in t["nodes"]}
    other = []
    if t["tree_pre"].strip():
        other.append(t["tree_pre"])
    for name in t["order"]:
        if name not in OWN_SECTIONS and t["sections"].get(name, "").strip():
            other.append("## %s\n\n%s" % (name, t["sections"][name]))
    stray = [l for nid, f in held["fields"].items() if nid not in ids for k, v in f.items() for ls in v
             for l in ["**%s %s**: %s" % (nid, k, ls[0])] + ls[1:]]
    stray += [l for nid, items in held["evidence"].items() if nid not in ids for it in items for l in it]
    if held["other"] or stray:
        other.append("\n".join(held["other"] + stray))
    pre = "# %s · %s · history\n\n" % (task, t["name"]) + "".join(o.rstrip("\n") + "\n\n" for o in other)
    secs = [_section(nd, held["fields"].get(nd["id"], {}), held["evidence"].get(nd["id"], []))
            for nd in t["nodes"]]
    history = pre + "\n".join(secs)
    got = [nd["id"] for nd in parse(history, task)["nodes"]]
    if got != [nd["id"] for nd in t["nodes"]]:
        raise ValueError("migrating %s would change its nodes: %s" % (task, [i for i in got if i not in ids]
                                                                      or [i for i in ids if i not in got]))
    log_text = dfs_tree._log_file_text(task, log)
    return dict(history=history, log=log, log_text=log_text,
                missing=lost(task, text, archive, logtext, history, log_text))


def _is_stub_line(line):
    fm = dfs_tree.FIELD_RE.match(line.strip())
    if not fm:
        return False
    name, value = dfs_tree.field_name(fm.group(1)), fm.group(2).strip()
    return (name in dfs_tree.SHELVED and dfs_tree.is_stub(name, value)) or \
        (name == "Evidence" and value == dfs_tree.SHELF_STUB) or \
        (name == "Status" and value.split()[:1] == ["started"])


def lost(task, text, archive, logtext, history, log_text):
    """Every nonblank line of the old task file, archive and log file that is in neither the
    history nor the merged log, as `(source, line)`. A line is compared with its whitespace
    folded. What is not counted: a line of `## Goal` or `## Summary` (the status file keeps
    them), a title or section heading the new files rewrite, the archive's `**Log**:` marker,
    and a stub (`Approach: archived.`) or `Status: started`, which carry no words of their own."""
    have = {_norm(l) for l in (history + "\n" + log_text).splitlines()}
    out, sec = [], None

    def check(source, line):
        n = _norm(line)
        if n and n not in have and not _is_stub_line(line):
            out.append((source, line.strip()))

    for line in text.splitlines():
        m = dfs_tree.SECTION_HEAD.match(line)
        if m:
            sec = m.group(1).strip()
            if sec in OWN_SECTIONS:
                continue
        elif sec is None and line.startswith("# "):
            continue
        elif sec in KEPT_IN_STATUS:
            continue
        check("task file", line)
    for line in (archive or "").splitlines():
        m = dfs_tree.ARCHIVE_FIELD.match(line.rstrip())
        if m:                       # held as `<Field>: text` in a section, or as this line in a stray block
            name = dfs_tree.field_name(m.group(2))
            if not ({_norm("%s: %s" % (name, m.group(3))), _norm("**%s %s**: %s" % (m.group(1), name, m.group(3)))}
                    & have):
                out.append(("archive", line.strip()))
        elif line.strip() != dfs_tree.ARCHIVE_LOG and not ARCHIVE_EV_HEAD.match(line.rstrip()):
            check("archive", line)
    for line in (logtext or "").splitlines():
        if not line.startswith("# "):
            check("log file", line)
    return out


def migrate_task(task, write=False):
    """`migrate` for each part of `task` on disk: `{tag: result}`. With `write`, when no part
    is missing a line, writes each part's history file and its merged log file (the task
    file and archive stay as they were) and refuses a part that already has a history."""
    out = {}
    for tag in dfs_tree.part_tags(task):
        def read(p):
            return p.read_text() if p.exists() else ""
        out[tag] = migrate(task, read(dfs_tree.part_path(task, tag)),
                           read(dfs_tree.archive_part(task, tag)), read(dfs_tree.log_part(task, tag)))
    if write:
        bad = [(tag, m) for tag, r in out.items() for m in r["missing"]]
        if bad:
            raise ValueError("migrating %s would lose %d line%s, so nothing is written; first: %s: %r"
                             % (task, len(bad), "" if len(bad) == 1 else "s", bad[0][1][0], bad[0][1][1][:80]))
        done = [tag for tag in out if history_part(task, tag).exists()]
        if done:
            raise ValueError("%s already has a history file: %s"
                             % (task, ", ".join(dfs_paths.rel(history_part(task, g)) for g in done)))
        for tag, r in out.items():
            dfs_paths.write_atomically(history_part(task, tag), r["history"])
            dfs_paths.write_atomically(dfs_tree.log_part(task, tag), r["log_text"])
    return out


# ── the status file: the Live tree and Raises are generated from the history ──

GENERATED = ("Live tree", "Raises")      # sections `status_text` writes; the rest is kept by hand
LEGACY = ("Tree", "Log")                 # old-format sections, dropped once the history holds them
MARK = "<!-- generated by tree.py status %s: do not edit by hand -->"
NEW_SECTIONS = ("Summary", "Goal", "Decisions in force", "Not taken", "Live tree", "Raises")


def live_ids(t):
    """The ids of the nodes the Live tree holds whole: open, started or parked, and neither
    pruned (below a refuted node, or cut by a correction) nor superseded."""
    status, pruned = dfs_tree.effective(t)
    sup = dfs_tree.superseded_by(t, status)
    return {nd["id"] for nd in t["nodes"]
            if status[nd["id"]] in ("open", "parked") and nd["id"] not in pruned and nd["id"] not in sup}


def live_tree(t, tag=""):
    """The Live tree's lines for the part `tag`: each of its live nodes whole as it stands now,
    under one title line for each ancestor that is not live itself, in tree order. A node that
    is confirmed or refuted leaves it unless a live node hangs below it, and then it is that
    one line."""
    nodes = dfs_tree.by_id(t)
    status, _pruned = dfs_tree.effective(t)
    sup = dfs_tree.superseded_by(t, status)
    live, done, out = live_ids(t), set(), []

    def emit(nd):
        if nd["id"] in done:
            return
        done.add(nd["id"])
        if nd["parent"] in nodes:
            emit(nodes[nd["parent"]])
        if nd["id"] in live:
            out.extend(dfs_tree.folded_text(nd).split("\n"))
        else:
            out.append("- %s · %s (%s)" % (nd["id"], nd["title"],
                                           "superseded" if nd["id"] in sup else status[nd["id"]]))

    for nd in sorted(t["nodes"], key=dfs_tree.order_key):
        if nd["id"] in live and nd.get("tag", "") == tag:
            emit(nd)
    return out


# What the briefing adds from the history for one node in hand, in tokens (a token is four
# characters, as everywhere else): docs/history-format.md, settled choice 2. A typical section is
# a few hundred tokens, so this holds the node, its parents and a few neighbours.
PULL_ALLOWANCE = 4_000
PULL_NEIGHBOURS = 5      # "a few": the newest nodes that share a tag, before the allowance cuts


def _tokens(text):
    return len(text) // 4


def _short(nd, shown):
    """A node cut to its title and Determination (and its status now, which says what it is)."""
    out = ["### %s · %s" % (nd["id"], nd["title"]), "Status now: %s" % shown]
    if nd["fields"].get("Determination"):
        out.append("Determination: %s" % nd["fields"]["Determination"])
    return "\n".join(out)


def pull(t, nid, allowance=PULL_ALLOWANCE, seen=None, with_parents=True, with_tags=True):
    """The history a session needs to work the node `nid`, as text, in at most `allowance`
    tokens besides the node's own section, which never drops: its section whole, then the chain
    of its parents (nearest first), the nodes it supersedes (nearest first) and the newest nodes
    that share a tag with it, each section folded. When over, drop the tag neighbours oldest
    first; then cut the superseded nodes, furthest first, to title and Determination; then the
    parents, furthest first, the same. `seen` is a set of ids already given to the reader (a
    critic judging several nodes): their sections are named, not repeated, and it is added to.
    `with_parents=False` leaves out the chain of parents and `with_tags=False` the tag neighbours (the
    reviewer, who asks only what a node replaced)."""
    nodes = {nd["id"]: nd for nd in t["nodes"]}
    seen = set() if seen is None else seen
    me = nodes[nid]
    status, _ = dfs_tree.effective(t)
    sup = dfs_tree.superseded_by(t, status)
    shown = lambda n: "superseded" if n["id"] in sup else status[n["id"]]   # as the tools show it
    parents, cur = [], me
    while with_parents and cur["parent"] in nodes and cur["parent"] != nid and cur["parent"] not in [p["id"] for p in parents]:
        cur = nodes[cur["parent"]]
        parents.append(cur)
    chain = dfs_tree.supersession_chain(t, nid, status)
    before = [nodes[i] for i in chain[:chain.index(nid)]][::-1]          # nearest first
    used = {nid} | {p["id"] for p in parents} | {n["id"] for n in before}
    mine = set(me.get("tags") or ()) if with_tags else set()
    near = sorted((n for n in t["nodes"] if n["id"] not in used and mine & set(n.get("tags") or ())),
                  key=dfs_tree.order_key, reverse=True)[:PULL_NEIGHBOURS]
    full = {n["id"]: dfs_tree.folded_text(n) + "\nStatus now: %s" % shown(n) for n in parents + before + near}
    cut = set()                                                         # ids shown short
    dropped = set()
    def body(n):
        return _short(n, shown(n)) if n["id"] in cut else full[n["id"]]
    def total():
        return sum(_tokens(body(n)) for n in parents + before + near if n["id"] not in dropped
                   and n["id"] not in seen)
    for n in reversed(near):                                            # oldest neighbour first
        if total() > allowance:
            dropped.add(n["id"])
    for group in (reversed(before), reversed(parents)):                 # furthest first
        for n in group:
            if total() > allowance:
                cut.add(n["id"])
    out = [("(%s: given above)" % nid) if nid in seen else
           dfs_tree.folded_text(me) + "\nStatus now: %s" % shown(me)]
    for label, group in (("Its parents, nearest first", parents), ("What it supersedes, nearest first", before),
                         ("Recent nodes that share a tag with it, newest first", near)):
        group = [n for n in group if n["id"] not in dropped]
        if not group:
            continue
        out += ["", label + ":"]
        for n in group:
            out += ["", ("(%s: given above)" % n["id"]) if n["id"] in seen else body(n)]
    seen.update({nid} | {n["id"] for n in parents + before + near if n["id"] not in dropped})
    return "\n".join(out)


def raise_entries(t):
    """The open raises and the answers not yet acted on, oldest first, each whole from the log."""
    ents = dfs_tree.open_raises(t) + dfs_tree.unacted_answers(t)
    return [e["text"] for e in sorted(ents, key=lambda e: e["ts"])]


def _split_status(text):
    """(lines before the first `## ` heading, [(heading name, its lines)]): a `## ` heading is only
    taken at the start of a line, and nothing generated holds one."""
    head, secs = [], []
    for line in text.splitlines():
        m = dfs_tree.SECTION_HEAD.match(line)
        if m:
            secs.append((m.group(1).strip(), []))
        (secs[-1][1] if secs else head).append(line)
    return head, secs


def status_text(text, t, tag=""):
    """The status file `text` of part `tag` with its Live tree and Raises written from the loaded
    task `t` (its history and log), and every other section as it was. Raises are the task's, so
    only the part with the Goal (`t["home"]`) has them. A `## Tree` or `## Log` left by
    the old format is dropped, because the history and the log file hold all of it. The
    sections come in the order of `NEW_SECTIONS`, each kept one in its place and any other
    after the last of them it followed. A `started` node keeps its mark, since `load` read it
    from the file being replaced."""
    head, secs = _split_status(text)
    body = {n: ls for n, ls in secs if n not in GENERATED + LEGACY}
    order = [n for n, _ in secs if n not in GENERATED + LEGACY]
    task = t["task"]
    tree = live_tree(t, tag) or ["(nothing is open or parked)"]
    body["Live tree"] = ["## Live tree", "", MARK % task] + tree
    body["Raises"] = ["## Raises", "", MARK % task] + (
        [l for e in raise_entries(t) for l in e.split("\n")] or ["(none)"])
    if tag != (t.get("home") or ""):
        body.pop("Raises")
    for name in ("Live tree",) + (("Raises",) if "Raises" in body else ()):
        if name not in order:
            order.append(name)
    out = list(head)
    while out and not out[-1].strip():
        out.pop()
    for name in order:
        out += [""] + _trim(body[name])
    return "\n".join(out).rstrip() + "\n"


def _trim(lines):
    lines = list(lines)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def write_status(task, tag="", t=None):
    """Regenerate this part's status file from its history and log, atomically. `t` is the loaded
    task when the caller has changed it (a `started` mark). Returns the text."""
    p = dfs_tree.part_path(task, tag)
    if not history_part(task, tag).exists():
        raise ValueError("%s has no history file: `tree.py migrate %s` moves it first" % (task, task))
    new = status_text(p.read_text(), t or dfs_tree.load(task), tag)
    dfs_paths.write_atomically(p, new)
    return new


def start(task, nid):
    """Mark an open node `started` in the status file's Live tree, and nowhere else: the mark is
    never written to the history, which holds what a commit would show. Returns `nid`."""
    t = dfs_tree.load(task)
    nd = dfs_tree.by_id(t).get(nid)
    if not nd:
        raise ValueError("no node %s in %s" % (nid, task))
    if nd["status"] not in ("open", "started"):
        raise ValueError("%s is %s: only an open node can be started" % (nid, nd["status"]))
    if nid not in live_ids(t):
        raise ValueError("%s is not in the live tree, so it cannot be started" % nid)
    nd["status"] = "started"
    nd["fields"]["Status"] = "started"
    write_status(task, nd.get("tag", ""), t)
    return nid


def add(task, title, parent, approach, tag):
    """Add an open node to this part's history as a new section at its end, and regenerate the
    status file. The id is the next one this part has not used. Returns the id."""
    t = dfs_tree.load(task)
    nid = dfs_tree.next_node_id(t, tag)
    lines = ["### %s · %s" % (nid, title)]
    if parent:
        lines.append("Parent: %s" % parent)
    lines.append("Status: open")
    approach = " ".join((approach or "").split())
    if approach:
        lines.append("Approach: %s" % approach)
    hp = history_part(task, tag)
    old = hp.read_text()
    dfs_paths.write_atomically(hp, old.rstrip("\n") + "\n\n" + "\n".join(lines) + "\n")
    write_status(task, tag)
    return nid
