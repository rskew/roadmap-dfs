#!/usr/bin/env python3
"""The roadmap's own invariants, checked against HEAD.

Every task file is a decision tree (`tree.py`), and the mechanisms built on it
fail silently when its shape is wrong: an answer naming no raise leaves the task
blocked for ever, a node whose parent does not exist falls off the walk, and an
edited log line or a rewritten determination erases what the author reviews. So:

  refused, even with --warn:
    - a task file whose heading does not name its own id
    - a node in a part that is not its tag's: `W13.4@fx` lives in `items/W13/fx.md`,
      `W13.4` in `items/W13.md`
    - a node or log entry ADDED to a part other than this branch's (`dfs_paths.
      branch_tag`), or added at all on a detached HEAD: each branch writes only its
      own part, which is what lets two branches on one task merge without conflict.
      Skipped while git is committing a merge, which brings other branches' parts
    - a node id used twice, a Parent that does not exist or comes later, a Status
      that is not open | parked | confirmed | refuted, a Corrects naming no correction
      or backtrack
    - a log line that is not a well-formed entry, an unknown kind, an answer naming
      no raise, a correction or a backtrack naming no node
    - AGAINST HEAD: a log entry removed or edited (the log only grows; merges may
      reorder it; an entry older than the author's last answer, correction or accept
      may LEAVE when the archive holds it whole under a `**Log**:` line, which is
      `tree.py compact`), a node removed, or a DETERMINED node whose status,
      determination, evidence, approach or hypothesis changed — a determined node is
      history, and what the author makes of it later is a correction in the log,
      never an edit. Two exceptions, which are how a file over its budget shrinks:
      an evidence line may LEAVE the task file when the task's archive
      (`.dfs/archive/items/<task>.md`) holds it verbatim, as one whole item under
      that node's own heading; and an Approach or Hypothesis may become `archived.`
      when the archive's latest `**<id> <field>**:` line holds it verbatim
      (`tree.py shelve`). The archive is then the only copy, so it only grows,
      at its end: HEAD's nonblank lines must open it, in order, since a line's
      heading is the nearest one above it
    - a live node, new in this commit or with its Hypothesis changed, that states a
      Hypothesis naming no interactive artefact: a `.dfs/artefacts/<name>.html` that
      exists and has an input, button, select, textarea or details with a handler
      (addEventListener or onclick and the like) answering it (`check_assumptions`). A node already at HEAD is judged as committed
    - a task file or an archive HEAD holds, deleted (the limit case of the two
      rules above; a staged rename is followed, not refused). A task retired on
      purpose is the author's rewrite of history, committed with --no-verify
    - a task file over the size budget that this commit grew
  warned:
    - a coordinate (`file.js:123`) in a task file: it rots with no sign

⚠️ A GITIGNORED `.dfs` (`dfs_paths.ignored`) holds nothing at HEAD, so every check
AGAINST HEAD above has nothing to compare and passes, and the hook never calls this,
since no commit stages `.dfs`. What still runs is the rest, on the working tree: `run.sh`
prints it after each session there, as a warning. That is the cost of keeping `.dfs` as planning:
an edited determined node is not caught, and the session's own rule is what keeps it.

With --staged (the pre-commit hook passes it) every task file and archive is read
from the INDEX, which is what the commit will hold; without it, from the working
tree. A move staged without its archive copy is refused only under --staged.

Run:  python3 <scripts>/check.py [--warn] [--staged] [--merge]
Exit: 0 clean, 1 violation (0 with --warn when only warnings).
"""
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths as dfs_paths  # noqa: E402
import state as dfs_state  # noqa: E402  (HANDOFF_BUDGET lives with the brief that warns about it)
import tree as dfs_tree   # noqa: E402

# A coordinate is a path carrying a line number. It is the most rot-prone thing a
# handoff can say: it survives no refactor and announces nothing when it breaks.
COORD = re.compile(r"\b[\w./-]+\.(?:js|mjs|ts|tsx|py|hs|sql|l|css|html|sh|md):\d+")


def at_head(path: Path):
    """HEAD's copy, or None when the file is not in HEAD yet.

    Asked of the roadmap's OWN checkout, and spelled `HEAD:./<name>` so the answer
    does not depend on where in that repo the roadmap folder sits — it is meant to
    be lifted out, and `HEAD:<repo-relative path>` would have to be rewritten when
    it is.
    """
    state = dfs_paths.state()
    rel = path.relative_to(state)
    r = subprocess.run(["git", "show", f"HEAD:./{rel}"], cwd=state,
                       capture_output=True, text=True)
    if r.returncode == 0:
        return r.stdout
    was = renamed_from(rel)
    if was:
        r = subprocess.run(["git", "show", f"HEAD:{was}"], cwd=state,
                           capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else None
    return None


def staged(path: Path):
    """The index's copy, or None when the file is not staged at all."""
    rel = path.relative_to(dfs_paths.state())
    r = subprocess.run(["git", "show", f":./{rel}"], cwd=dfs_paths.state(),
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def _parts(paths, sub):
    """The task parts among `paths` (relative to `.dfs`), as paths relative to `sub`."""
    out = set()
    for p in paths:
        if p.startswith(sub + "/") and dfs_tree.part_of(p[len(sub) + 1:]):
            out.add(p[len(sub) + 1:])
    return out


def staged_names(sub: str):
    """The task parts the index holds under `.dfs/<sub>`: `W13.md`, `W13/fx.md`."""
    r = subprocess.run(["git", "ls-files", "--", f"{sub}/"], cwd=dfs_paths.state(),
                       capture_output=True, text=True)
    return _parts(r.stdout.split(), sub)


def head_names(sub: str):
    """The task parts HEAD holds under `.dfs/<sub>`: a file this commit deletes is
    still one to judge, or deleting it would be the one edit nothing checks."""
    r = subprocess.run(["git", "ls-tree", "-r", "--name-only", "HEAD", "--", f"{sub}/"],
                       cwd=dfs_paths.state(), capture_output=True, text=True)
    return _parts(r.stdout.split(), sub)


def disk_names(sub: str):
    base = dfs_paths.state() / sub
    return _parts((str(p.relative_to(dfs_paths.state())) for p in base.glob("**/*.md")), sub)


def merging():
    """Whether git is committing a merge, which brings in other branches' parts:
    `--merge` from the pre-merge-commit hook, which git runs before it writes
    MERGE_HEAD, or MERGE_HEAD for a conflicted merge finished by `git commit`."""
    if "--merge" in sys.argv:
        return True
    r = subprocess.run(["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"],
                       cwd=dfs_paths.state(), capture_output=True, text=True)
    return r.returncode == 0


def check_writer(task, tag, here, text, head):
    """What this commit ADDED to a part that is not this branch's: the one rule that
    keeps two branches from writing the same file. `here` is None on a detached HEAD,
    where nothing may be added at all."""
    if here == tag:
        return []
    now, was = dfs_tree.parse(text or "", task), dfs_tree.parse(head or "", task)
    had_nodes = {nd["id"] for nd in was["nodes"]}
    had_log = {e["text"].rstrip() for e in was["log"]}
    added = [nd["id"] for nd in now["nodes"] if nd["id"] not in had_nodes]
    added += ["the log entry at %s" % e["ts"] for e in now["log"]
              if e["text"].rstrip() not in had_log]
    if not added:
        return []
    if here is None:
        return ["this commit adds %s on a detached HEAD, which has no tag to write under. "
                "Commit from a branch" % ", ".join(added)]
    return ["this commit adds %s to the part of %s, and this branch writes only its own "
            "(%s). Add it there, with ids tagged %s" % (
                ", ".join(added), "the default branch" if not tag else "branch tag " + tag,
                dfs_paths.rel(dfs_tree.part_path(task, here)),
                "@" + here if here else "with nothing (the default branch)")]


def renamed_sources():
    """The paths, relative to `.dfs`, that a staged rename moves away from."""
    state = dfs_paths.state()
    prefix = subprocess.run(["git", "rev-parse", "--show-prefix"], cwd=state,
                            capture_output=True, text=True).stdout.strip()
    r = subprocess.run(["git", "diff", "--cached", "-M", "--diff-filter=R",
                        "--name-status", "HEAD"], cwd=state,
                       capture_output=True, text=True)
    return {parts[1][len(prefix):] for parts in (ln.split("\t") for ln in r.stdout.splitlines())
            if len(parts) == 3 and parts[1].startswith(prefix)}


def check_archive(task, now, head):
    """The archive holds the only copy of what moved into it, so it only grows, and
    only at its END: HEAD's nonblank lines must open the new archive, in order.
    Order is part of the record, because `archived_evidence` gives each line to the
    nearest heading above it; a reorder, or a heading wedged into a node's section,
    would hand one node's evidence to another with every line still present."""
    was = [ln.rstrip() for ln in head.splitlines() if ln.strip()]
    got = [ln.rstrip() for ln in now.splitlines() if ln.strip()]
    diff = next((k for k, ln in enumerate(was) if k >= len(got) or got[k] != ln), None)
    if diff is None:
        return []
    return ["the archive's line %d at HEAD (%r) is not where HEAD had it: removed, "
            "edited, moved, or something was inserted before it; the archive is the "
            "only copy of what moved into it, so it only grows, at its end"
            % (diff + 1, was[diff][:60])]


def renamed_from(rel: Path):
    """Where this file was in HEAD, when the commit in front of you MOVES it.

    ⚠️ Without this the guard fails open exactly when the roadmap is relocated: no
    copy at the new path means no HEAD to compare against, so the append-only check
    is skipped and every standing id reads as one this commit is adding. The state
    directory can move — out of the tool folder, into `.dfs`, or with a repo being
    reorganised — so its own move is a case the check has to survive rather than a
    one-off. Only a STAGED rename is followed: an unstaged move is not yet a commit
    to judge.
    """
    state = dfs_paths.state()
    prefix = subprocess.run(["git", "rev-parse", "--show-prefix"], cwd=state,
                            capture_output=True, text=True)
    if prefix.returncode != 0:
        return None
    want = prefix.stdout.strip() + str(rel)
    r = subprocess.run(["git", "diff", "--cached", "-M", "--diff-filter=R",
                        "--name-status", "HEAD"], cwd=state,
                       capture_output=True, text=True)
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[2] == want:
            return parts[1]
    return None


def handoff_verdict(now_tok: int, was_tok: int, budget: int):
    """What a handoff's size says about the commit in front of you: refuse, note, or nothing.

    A TRANSITION, not a count — the same rule the ids below are held to. Three of these
    files are already over the budget, so a flat `<= budget` would refuse every commit
    that touched one and be switched off inside a day. What is refused is a commit that
    makes an over-budget file WORSE; a file over it that shrinks or holds still is a
    note, which is the state a session is expected to work out of and improve.

    ⚠️ Crossing the budget refuses too, and falls out of the same expression: a file
    under it in HEAD and over it now has grown, by definition.
    """
    if now_tok > budget and now_tok > was_tok:
        return "refuse"
    if now_tok > budget:
        return "note"
    return None


def check_shape(task, text, archive="", tag="", whole=None):
    """What is wrong with one part of a task, as a list of sentences. What an entry
    names (an answer's raise, a Corrects' correction, a Parent) is looked up in
    `whole`, the task with every part combined (`dfs_tree.combine`), since it may be in
    another branch's part; alone, the part is the whole task."""
    bad = []
    t = dfs_tree.with_archive(dfs_tree.parse(text, task), archive)
    w = whole or t
    if not re.match(r"^# %s(?![\w@.-])" % re.escape(task), t["title"] or ""):
        bad.append("its heading is %r, which does not name %s" % (t["title"], task))
    seen, nums = set(), {nd["id"]: nd for nd in w["nodes"]}
    for nd in t["nodes"]:
        if nd["id"] in seen:
            bad.append("node %s is defined twice" % nd["id"])
        seen.add(nd["id"])
        if nd.get("tag", "") != tag:
            bad.append("node %s is in the part of %s; it belongs in %s" % (
                nd["id"], "tag " + tag if tag else "the default branch",
                dfs_paths.rel(dfs_tree.part_path(task, nd.get("tag", "")))))
        if nd["status"] not in dfs_tree.STATUSES:
            bad.append("%s has Status %r (one of %s)" % (
                nd["id"], nd["fields"].get("Status"), ", ".join(dfs_tree.STATUSES)))
    corrections = {e["ts"] for e in w["log"] if e["kind"] in ("correct", "backtrack")}
    for nd in t["nodes"]:
        p = nd["parent"]
        if p and p not in nums:
            bad.append("%s names Parent %s, which does not exist" % (nd["id"], p))
        elif p and nums[p].get("tag", "") == nd.get("tag", "") and nums[p]["n"] >= nd["n"]:
            bad.append("%s names Parent %s, made after it — a node's parent comes first"
                       % (nd["id"], p))
        c = nd["fields"].get("Corrects")
        if c and c not in corrections:
            bad.append("%s says Corrects %s, and no correction or backtrack has that timestamp"
                       % (nd["id"], c))
    raises = {e["ts"] for e in w["log"] if e["kind"] == "raise"}
    for line in t["sections"].get("Log", "").splitlines():
        if line.startswith("- ") and not dfs_tree.LOG_HEAD.match(line):
            bad.append("log line %r is not `- <ts> · <kind> · <args>`" % line[:60])
    for e in t["log"]:
        if e["kind"] not in dfs_tree.KINDS:
            bad.append("log entry %s has unknown kind %r" % (e["ts"], e["kind"]))
        if e["kind"] == "answer" and (not e["args"] or e["args"][0] not in raises):
            bad.append("the answer at %s names no raise" % e["ts"])
        if e["kind"] in ("correct", "backtrack"):
            if not e["args"] or e["args"][0] not in nums:
                bad.append("the %s at %s names no node" % (
                    "correction" if e["kind"] == "correct" else "backtrack", e["ts"]))
            elif e["kind"] == "correct" and len(e["args"]) > 1 \
                    and e["args"][1] not in ("confirmed", "refuted"):
                bad.append("the correction at %s says %r (confirmed | refuted)"
                           % (e["ts"], e["args"][1]))
    return bad


ARCHIVE_NODE = re.compile(r"^(?:#+\s+|\*\*)(%s)(?![\w@.-])" % dfs_tree.NODE_RE)


def archived_evidence(archive):
    """Each node's evidence as the archive holds it: {node id: {item text}}.

    A node's section starts at a heading or bold line that opens with its id
    (`**W13.1 Evidence**:`, `## W13.1`) and ends at the next heading or bold line.
    An item is a `- ` line with its continuation lines joined and whitespace
    collapsed, and is compared WHOLE: a moved line must be one item under its own
    node, never a substring of the archive, or `for: it ran` would count as held
    by any other node's `for: it ran on nothing`."""
    held, node, item = {}, None, None
    for line in archive.splitlines():
        s = line.strip()
        if s.startswith("#") or s.startswith("**"):
            m = ARCHIVE_NODE.match(s)
            node, item = (m.group(1) if m else None), None
        elif s.startswith("- ") and node:
            item = [s[2:]]
            held.setdefault(node, []).append(item)
        elif s and item is not None:
            item.append(s)
        else:
            item = None
    return {n: {" ".join(" ".join(it).split()) for it in items}
            for n, items in held.items()}


def check_history(task, text, head, archive=""):
    """What this commit rewrote that it may only have added to.

    `archive` is the task's `.dfs/archive/items/<task>.md`. A determined node's
    evidence line that is gone from the task file is MOVED, not rewritten, when the
    archive holds it word for word as one whole item under that node's own section
    (whitespace aside, since a parsed line has its continuations joined; see
    `archived_evidence`). The author's answer to W13's raise of 2026-09-27 chose
    this over exempting the runner's log lines or committing with --no-verify:
    without it a task file over its budget can neither shrink nor grow, so no node
    on it has a legal commit. Status, Parent and Determination stay immutable; they
    are what the tree and the critic read.

    A determined node's Approach and Hypothesis move the same way, by the author's answer
    to W19's raise of 2026-09-28: the field becomes `archived.` only when the
    archive's latest `**<id> <field>**:` line holds HEAD's wording verbatim, which is
    what `dfs_tree.load` puts back. Any other change to either is an edit of history."""
    bad = []
    held = archived_evidence(archive)
    now, was = dfs_tree.parse(text, task), dfs_tree.parse(head, task)
    now_log = {e["text"].rstrip() for e in now["log"]}
    moved = {e["text"].rstrip() for e in dfs_tree.archived_log(archive)}
    # A fresh parse, not `dict(now)`: `with_archive` writes shelved Approaches
    # and Hypotheses back into the nodes it is given, and a shallow copy shares them with
    # `now`, which then reads as a commit that un-shelved every one of them.
    cut = dfs_tree.log_cutoff(dfs_tree.with_archive(dfs_tree.parse(text, task),
                                                    archive)["log"])
    for e in was["log"]:
        key = e["text"].rstrip()
        if key in now_log or (key in moved and e["ts"] < cut):
            continue
        bad.append("the log entry %s · %s was removed or edited; the log only grows, and "
                   "only an entry older than the author's last word (%s) may leave, "
                   "whole, to the archive's `**Log**:` (`tree.py compact`)"
                   % (e["ts"], e["kind"], cut or "none yet"))
    now_nodes = dfs_tree.by_id(now)
    for nd in was["nodes"]:
        cur = now_nodes.get(nd["id"])
        if cur is None:
            bad.append("node %s was removed; a node is history — refute it instead"
                       % nd["id"])
            continue
        if nd["status"] not in dfs_tree.DETERMINED:
            continue
        for k in ("Status", "Determination", "Parent"):
            if (cur["fields"].get(k) or "") != (nd["fields"].get(k) or ""):
                bad.append("%s was %s at HEAD and this commit changed its %s. A determined "
                           "node is history; a later view of it is a correction in the log"
                           % (nd["id"], nd["status"], k))
        shelf = dfs_tree.archived_fields(archive).get(nd["id"], {})
        for k in dfs_tree.SHELVED:
            was_v, now_v = nd["fields"].get(k) or "", cur["fields"].get(k) or ""
            if now_v == was_v:
                continue
            if now_v == dfs_tree.SHELF_STUB and \
                    " ".join(shelf.get(k, "").split()) == " ".join(was_v.split()):
                continue
            bad.append("%s was %s at HEAD and this commit changed its %s. A determined "
                       "node's %s may only become `%s`, with the archive's latest "
                       "`**%s %s**:` line holding it verbatim (`tree.py shelve %s`)"
                       % (nd["id"], nd["status"], k, k, dfs_tree.SHELF_STUB,
                          nd["id"], k, task))
        missing = [ev for ev in nd["evidence"] if ev not in cur["evidence"]
                   and " ".join(ev.split()) not in held.get(nd["id"], ())]
        if missing:
            bad.append("%s was %s at HEAD and this commit removed or edited its evidence "
                       "(%r), and its part's archive does not hold it verbatim"
                       % (nd["id"], nd["status"], missing[0][:60]))
    return bad


# An assumption's artefact is named by its path inside the Hypothesis; the name has no
# slash, so it cannot leave `.dfs/artefacts`. Interactive is the page having a control
# and a handler that answers it: a looked-at picture is a raise's artefact, not an
# assumption's, and a button nothing listens to is a picture. A floor, not a proof of
# a good page: the author's reading of the page is what judges that.
ARTEFACT_REF = re.compile(r"\.dfs/artefacts/([\w.-]+\.html)\b")
_TAGS = "input|button|select|textarea|details"
CONTROL = re.compile(r"<(?:%s)\b|createElement\(\s*['\"](?:%s)['\"]" % (_TAGS, _TAGS), re.I)
HANDLER = re.compile(r"addEventListener\s*\(|\bon(?:click|input|change|submit|toggle|key\w+|"
                     r"pointer\w+|mouse\w+|focus|select)\s*=", re.I)


def check_assumptions(task, text, head, read, archive="", head_archive=""):
    """A Hypothesis must name an interactive artefact (`.dfs/artefacts/<name>.html`).

    Judged on a live node that is new in this commit, whose Hypothesis changed since
    HEAD, or that was parked at HEAD and is open again: a node already at HEAD with
    its Hypothesis unchanged is history and keeps the wording it was committed with.
    A parked or refuted node's assumption is moot (`dfs_tree.assumed`) and is not
    judged. `read` gives a file's text as it will be committed (None when absent).
    HEAD's text is read with HEAD's archive (`head_archive`), as the current text is
    with its own: a Hypothesis shelved since HEAD is the same Hypothesis."""
    bad = []
    t = dfs_tree.with_archive(dfs_tree.parse(text, task), archive)
    was = ({nd["id"]: nd for nd in
            dfs_tree.with_archive(dfs_tree.parse(head, task), head_archive)["nodes"]}
           if head else {})
    for nd in t["nodes"]:
        h = (nd["fields"].get("Hypothesis") or "").strip()
        if not h or h == dfs_tree.SHELF_STUB or nd["status"] in ("parked", "refuted"):
            continue
        old = was.get(nd["id"])
        if old and (old["fields"].get("Hypothesis") or "").strip() == h \
                and old["status"] not in ("parked", "refuted"):
            continue
        names = ARTEFACT_REF.findall(h)
        if not names:
            bad.append("%s states a Hypothesis and names no artefact. Draw an interactive "
                       "page that explains the assumption and lets the author decide it, "
                       "and name it in the Hypothesis as `.dfs/artefacts/<uuid>.html` "
                       "(templates/_TEMPLATE_ASSUMPTION.html)" % nd["id"])
            continue
        why = []
        for name in names:
            page = read(dfs_paths.artefacts() / name)
            if page is None:
                why.append("%s does not exist" % name)
            elif not CONTROL.search(page) or not HANDLER.search(page):
                why.append("%s is not interactive (it needs an <input>, <button>, <select>, "
                           "<textarea> or <details> and a handler that answers it: "
                           "addEventListener or an onclick-style attribute)" % name)
            else:
                break
        else:
            bad.append("%s names no usable artefact for its Hypothesis: %s"
                       % (nd["id"], "; ".join(why)))
    return bad


# A node that points at another instead of saying the thing. SKILL.md: a session, the
# critic and the screen each read ONE node without the rest of the tree.
BACKREF = re.compile(r"^\s*(see|as in|same as|as for|fix what|the other|per|like)\b|"
                     r"\b(see|as in|same as|what) %s\b" % dfs_tree.NODE_RE, re.I)


def standalone_notes(task, text, head=None, archive=""):
    """Notes (not violations) on nodes new in `text`, against the standalone rule: an
    Approach that is a pointer ("Fix what W9.6 found", "See W9.6.") or too short to
    say what to do. A node with no Approach at all is one `--open` wrote and is left
    alone. Advice only: the guard does not refuse a commit for it."""
    t = dfs_tree.with_archive(dfs_tree.parse(text, task), archive)
    old = {nd["id"] for nd in dfs_tree.parse(head, task)["nodes"]} if head else set()
    out = []
    for nd in t["nodes"]:
        if nd["id"] in old:
            continue
        for field in ("Approach", "Determination"):
            v = (nd["fields"].get(field) or "").strip()
            if not v or v == "archived.":
                continue
            if BACKREF.search(v) or (field == "Approach" and len(v) < 25):
                out.append("%s %s reads as a pointer or too short to stand alone: %r. "
                           "Name the file, the fault and the check itself." % (
                               nd["id"], field, v[:50]))
    return out


def main() -> int:
    warn_only = "--warn" in sys.argv
    # Silent when this directory has no roadmap: the guard runs from a repo's
    # pre-commit hook, and a repo with no roadmap is not a violation.
    if not dfs_paths.has_roadmap():
        return 0
    from_index = "--staged" in sys.argv

    def read(path):
        if from_index:
            return staged(path)
        return path.read_text() if path.exists() else None

    def names(sub):
        return staged_names(sub) if from_index else disk_names(sub)

    fatal, bad = [], []
    items_dir = dfs_paths.items()
    arc_dir = dfs_paths.state() / "archive" / "items"
    moved = renamed_sources()
    now_items = names("items")
    for rel in sorted(names("archive/items") | head_names("archive/items") | now_items):
        arc = arc_dir / rel
        was = at_head(arc)
        if was is not None:
            fatal += ["%s: %s" % (dfs_paths.rel(arc), b)
                      for b in check_archive(dfs_tree.part_of(rel)[0], read(arc) or "", was)]
    for rel in sorted(head_names("items") - now_items):
        if "items/%s" % rel not in moved:
            fatal.append("%s was deleted. A task file's log and determined nodes are "
                         "history, and a deleted file is every node removed at once"
                         % dfs_paths.rel(items_dir / rel))
    try:
        here = dfs_paths.branch_tag()
    except dfs_paths.NoBranch as e:
        here = None
        if dfs_paths.branch() is not None:   # two branches with one tag, not detached
            fatal.append(str(e))
    judge_writer = not merging()
    tasks = {}
    for rel in now_items:
        task, tag = dfs_tree.part_of(rel)
        tasks.setdefault(task, {})[tag] = rel
    for task in sorted(tasks):
        parts = {tag: (read(items_dir / rel) or "", read(arc_dir / rel) or "")
                 for tag, rel in tasks[task].items()}
        whole = dfs_tree.combine(task, [(g, x, a) for g, (x, a) in parts.items()])
        for tag, (text, arc) in sorted(parts.items()):
            path = items_dir / tasks[task][tag]
            rel = dfs_paths.rel(path)
            fatal += ["%s: %s" % (rel, b) for b in check_shape(task, text, arc, tag, whole)]
            head = at_head(path)
            if head is not None:
                fatal += ["%s: %s" % (rel, b) for b in check_history(task, text, head, arc)]
            fatal += ["%s: %s" % (rel, b)
                      for b in check_assumptions(task, text, head, read, arc,
                                                 at_head(arc_dir / tasks[task][tag]) or "")]
            if judge_writer and (here is not None or dfs_paths.branch() is None):
                fatal += ["%s: %s" % (rel, b) for b in check_writer(task, tag, here, text, head)]
            now_tok = len(text) // 4
            was_tok = (len(head) // 4) if head is not None else 0
            budget = dfs_state.HANDOFF_BUDGET
            verdict = handoff_verdict(now_tok, was_tok, budget)
            if verdict == "refuse":
                fatal.append(
                    f"{rel} is ~{now_tok:,} tokens, over the {budget:,} budget, and this commit\n"
                    f"    GREW it (~{was_tok:,} before). A task file is re-read whole by every\n"
                    "    session on the task. Prune its Background, or move settled history to\n"
                    "    .dfs/archive/ (`tree.py shelve %s` moves determined nodes'\n"
                    "    Approaches and Hypotheses there)." % task)
            elif verdict == "note":
                print(f"dfs_check: note — {rel} is ~{now_tok:,} tokens, over the "
                      f"{budget:,} budget. Not grown here.")
            for note in standalone_notes(task, text, head, arc):
                print(f"dfs_check: note — {rel}: {note}")
            in_log = False
            for n, line in enumerate(text.splitlines(), 1):
                if line.startswith("## "):
                    in_log = line.strip() == "## Log"
                if in_log or line.lstrip().startswith(("<", "#")):
                    continue
                for hit in COORD.findall(line):
                    bad.append(f"{rel} line {n} carries a coordinate, '{hit}'. Name a thing by "
                               "what makes it that thing; a line number rots with no sign.")
    tasks = now_items

    if not bad and not fatal:
        print("dfs_check: clean (%d task file%s)" % (len(tasks), "" if len(tasks) == 1 else "s"))
        return 0
    print("dfs_check: the roadmap broke its own rules.\n")
    for b in fatal + bad:
        print(f"  - {b}")
    if warn_only and not fatal:
        print("\n  (warning only — not blocking this commit)")
        return 0
    if warn_only:
        print("\n  (--warn covers only the warnings above)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
