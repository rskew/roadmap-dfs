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
