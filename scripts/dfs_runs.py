#!/usr/bin/env python3
"""Which chains are RUNNING — a fact about processes, not about the roadmap.

`dfs_state.py` answers what the roadmap SAYS about an item: blocked,
done, open. That question has nothing to do with whether a work chain is on the box
right now, and conflating them would be the same mistake `localModeOn()` versus
`localStatus()` is written down for in AGENTS.md — which mode was CHOSEN is not
whether anything is actually answering. So running-ness lives here, and an item
carries both: its roadmap status and, separately, whether something is working it.

The record is `meta.json` in the run directory, which `dfs_run.sh`
writes at startup and updates as the chain advances (`run`, then `finished`/`rc`).
⚠️ **A missing `finished` does not mean live** — a chain that was killed, or a box
that rebooted, leaves exactly that. Liveness is asked of the PROCESS: the pid is in
`/proc` and its cmdline still says `dfs_run.sh`, so a reused pid cannot be mistaken
for a chain that is still going. The three states are therefore
`live | finished | stopped`, and `stopped` — started, never finished, process gone —
is the one worth seeing, because it is the shape a usage limit or an OOM leaves.

Usage:
    python3 <scripts>/dfs_runs.py --json            # every run directory
    python3 <scripts>/dfs_runs.py --live <item>     # exit 0 iff a chain is live on it
    python3 <scripts>/dfs_runs.py --root            # where a new chain's logs go
    python3 <scripts>/dfs_runs.py --left <item>     # what the last session wrote
"""
import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dfs_paths  # noqa: E402

REPO = dfs_paths.work_root()   # the repo being worked on: the CWD
TMP = os.environ.get("TMPDIR", "/tmp")
GLOB = "dfs_run_*"


def _dfs_context():
    """The transcript reader, imported rather than restated (hyphenated filename)."""
    import importlib.util as ilu
    spec = ilu.spec_from_file_location(
        "dfs_context", str(Path(__file__).resolve().parent / "dfs_context.py"))
    mod = ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def left_behind(item):
    """What the last session on this item WROTE, from its own transcript.

    ⚠️ This exists because `git status --porcelain` cannot answer the question §0.1
    step 4 asks. A tree holding several sessions' uncommitted work shows the same
    porcelain list before and after an edit to an already-modified file, so a session
    that was cut off mid-work looks exactly like one that did nothing — which is how
    entry 73 recorded four real edits as "left the working tree unchanged".

    ⚠️ It answers about the SESSION, not the tree, so a concurrent peer's edits are
    never attributed here. The tree is then consulted only to say which of those
    paths still carries a change, because a write the session made and later reverted
    is not work left behind.

    ⚠️ **AND IT SKIPS THE READER'S OWN SESSION.** The caller is a session that has
    just picked this item, so a chain-started one has its OWN run directory on the
    item already — newest, live, and zero writes in, because asking is the first
    thing it does. Answered with that, the question §0.1 step 2 asks gets the
    reader's own emptiness back: entry 431's session had 59 responses and 8 write
    calls, and this function said "nothing written" about a session that was not it.
    That is entry 73's failure returning through the tool written to prevent it, so
    the exclusion is by session id read from the environment — the same ids
    `dfs_context` resolves itself by — and never by liveness, since a genuine peer
    chain on the same item is also live and IS an answer worth having.
    """
    sc = _dfs_context()
    mine = (os.environ.get("CLAUDE_CODE_SESSION_ID")
            or os.environ.get("CODEX_SESSION_ID")
            or os.environ.get("CODEX_THREAD_ID") or "")
    for r in run_dirs():
        if item and r["item"] != item:
            continue
        if not r["sid"]:
            continue
        if mine and r["sid"] == mine:
            continue
        tp = sc.transcript_path(r["sid"], r["agent"] or "claude", root=REPO)
        if not tp or not os.path.exists(tp):
            continue
        paths, calls = sc.files_written(tp, r["agent"] or "claude")
        peak, turns = sc.peak_and_turns(tp, r["agent"] or "claude")
        tracked, scratch = [], []
        for f in paths:
            rel = _repo_relative(f)
            (tracked if rel else scratch).append(rel or f)
        dirty = set(_dirty_paths())
        # A gitignored `.dfs` has no committed copy, so git cannot say whether a write
        # there is still a change; `planning` says so rather than "written".
        planning = [f for f in tracked if dfs_paths.ignored() and (
            f == dfs_paths.STATE_DIR_NAME or f.startswith(dfs_paths.STATE_DIR_NAME + "/"))]
        return {"run": r, "transcript": tp, "calls": calls, "turns": turns,
                "peak": peak, "paths": tracked,
                "still_changed": [f for f in tracked if f in dirty],
                "planning": planning,
                "scratch": scratch}
    return None


def _repo_relative(path):
    """A path as git names it, or None for one outside this tree.

    ⚠️ AN ABSOLUTE PATH INSIDE THE REPO WAS FILED AS SCRATCH until 2026-09-21,
    which is the second half of entry 685's false answer: a codex patch names
    `/workspace/shop-app/docs/…` and `git status --porcelain` names paths from the
    root, so every one of them read as written-but-not-in-the-tree. The comparison
    has to be made in the root's own terms, and a path that cannot be brought into
    them is the thing `scratch` is for.
    """
    if os.path.isabs(path):
        try:
            rel = os.path.relpath(path, str(REPO))
        except ValueError:
            return None
        return None if rel.startswith("..") else rel
    return None if path.startswith("/tmp") else path


def _dirty_paths():
    """Every path git sees as modified or untracked, for confirming a write landed."""
    # ⚠️ Narrow on purpose. A broad `except Exception` here returned [] for a missing
    # import, so every path read as "written but no longer changed" — a wrong answer
    # that looks like a finding, in the one function whose job is to stop a wrong
    # answer looking like a finding.
    try:
        out = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain"],
                             capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [ln[3:].strip().strip('"') for ln in out.splitlines() if ln[3:].strip()]


def run_root():
    """Where a new chain's logs go: `.dfs/runs`, in the project, gitignored.

    A run directory is the only INDEX there is: the transcripts themselves live
    under the provider's own directory and carry nothing that says which roadmap
    item they belonged to, so `meta.json` is the sole mapping from a session id to
    `W4, run 3 of 5`. Losing it to a reboot or a `/tmp` sweep leaves a hundred
    unlabelled jsonl files and an empty screen — and the epic's end state is an item
    whose log is inspectable, which a temp directory cannot promise.

    ⚠️ IN THE PROJECT, beside its own roadmap, which is what makes it durable
    WITHOUT anything having to know where it is running — and what keeps a chain's
    logs with the work they are about rather than with the tool that ran them.
    `scripts/container.sh` gives `HOME` and `/tmp` a tmpfs each
    and `--rm`, so a cache directory under either dies with the container; the repo
    is bind-mounted, so this one survives there, on a plain host, and in a clone,
    with no mount to keep in step with this file. Gitignored: these are artifacts,
    while the DECISION is in `ROADMAP.md` and is what gets committed.

    ⚠️ `TMPDIR` is still HONOURED for `RUNDIR` and still READ (see `run_roots`):
    chains already running were started there, and their directories are their own.
    """
    root = os.environ.get("DFS_RUN_DIR")
    if root:
        return root
    return str(dfs_paths.runs())


def ensure_run_root():
    """The root to WRITE a new chain into, made, and honest when it cannot be.

    ⚠️ A root that cannot be created must not take the chain down with it. Measured
    the hard way on 2026-09-11, when this root was a cache directory: `~/.cache` is
    root-owned in this container, and `makedirs` raising inside the key that starts a
    run killed the whole screen — a durability improvement that cost the thing it was
    improving. A read-only checkout would do the same to the repo root now. The temp
    directory is the fallback, which is exactly where these lived before, so the
    worst case is the old behaviour rather than no run at all.
    """
    root = run_root()
    try:
        os.makedirs(root, exist_ok=True)
        if os.access(root, os.W_OK):
            return root
    except OSError:
        pass
    return TMP


def run_roots():
    """Every directory a run log may be under, newest home first.

    Two, and the second is not legacy handling that can be dropped next week: a
    chain started before this moved is STILL RUNNING out of the temp directory, and
    `RUNDIR`/`TMPDIR` can still put one there deliberately. A screen that listed only
    the new root would have lost sight of a live chain at the moment it changed.
    """
    roots, seen = [], set()
    for r in (run_root(), TMP):
        real = os.path.abspath(r)
        if real not in seen:
            seen.add(real)
            roots.append(r)
    return roots


def pid_is_chain(pid):
    """Is this pid still a dfs_run.sh?

    Two questions in one, deliberately: a pid that is gone answers no, and a pid that
    has been REUSED by something else answers no as well. `os.kill(pid, 0)` can only
    do the first, which on a box that has been up for weeks is the difference between
    "the chain is live" and "some other program has that number now".

    ⚠️ The EXTENSION is part of the match, not decoration: `dfs_run` alone is also a
    substring of THIS file's own name, so a pid reused by a `dfs_runs.py --json` would
    read as a live chain — the exact mistake the paragraph above says this avoids.
    """
    if not pid:
        return False
    cmd = Path("/proc/%d/cmdline" % pid)
    try:
        return b"dfs_run.sh" in cmd.read_bytes()
    except OSError:
        pass
    if cmd.parent.exists():          # /proc is here and the pid is not in it
        return False
    try:                             # no /proc at all: the weaker check is all there is
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def read_meta(d):
    try:
        meta = json.loads(Path(d, "meta.json").read_text())
    except Exception:
        return None
    if not isinstance(meta, dict):
        return None
    return meta


def run_dirs():
    """Every run directory on the box, newest first, with its state settled."""
    out = []
    found = []
    for root in run_roots():
        found += glob.glob(os.path.join(root, GLOB))
    for d in sorted(found):
        meta = read_meta(d)
        if meta is None:
            continue
        try:
            pid = int(meta.get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        finished = str(meta.get("finished") or "")
        live = not finished and pid_is_chain(pid)
        # ⚠️ A run directory from before the runner recorded a pid cannot be asked
        # about: no pid, no finish line, and the terminal it scrolled past in is
        # gone. That is `unknown`, not `stopped` — calling it stopped would put a
        # red marker on every historical run and teach the eye to ignore the colour
        # that means a chain died on its feet.
        if live:
            state = "live"
        elif finished:
            state = "finished"
        elif pid:
            state = "stopped"
        else:
            state = "unknown"
        console = os.path.join(d, "console.log")
        try:
            started = os.stat(os.path.join(d, "meta.json")).st_mtime
        except OSError:
            started = 0
        out.append(dict(
            dir=d, pid=pid, live=live, state=state,
            item=str(meta.get("item") or ""), agent=str(meta.get("agent") or ""),
            mode=str(meta.get("mode") or ""), cap=str(meta.get("cap") or ""),
            run=str(meta.get("run") or ""), rc=str(meta.get("rc") or ""),
            # ⚠️ WHY it stopped, which is a fact the chain recorded rather than a
            # sentence a caller greps for. `dfs_run.sh` lists the values; `limit` is
            # the only one that means "wait and try the same thing again", so the
            # walk cannot infer it from an exit code (a usage limit and a crash are
            # both rc 1).
            stop=str(meta.get("stop") or ""), resets=str(meta.get("resets") or ""),
            # The session the chain is in RIGHT NOW, recorded before it starts so
            # the provider's transcript can be read while it is being written.
            sid=str(meta.get("sid") or ""),
            peak=str(meta.get("peak") or ""), turns=str(meta.get("turns") or ""),
            started=str(meta.get("started") or ""), finished=finished,
            console=console if os.path.exists(console) else "",
            mtime=started,
        ))
    out.sort(key=lambda r: r["mtime"], reverse=True)
    return out


def live_runs(mode=None):
    return [r for r in run_dirs() if r["live"] and (mode is None or r["mode"] == mode)]


def live_for(item, mode="work"):
    """The live chain working this item, or None. What the screen marks a row with."""
    for r in live_runs(mode):
        if r["item"] == item:
            return r
    return None


def lower_cap(d, cap):
    """Tell the chain in run directory `d` to start no run past `cap`.

    Read by `dfs_run.sh`'s `lowered_cap` between runs, so the run in flight finishes
    and nothing after it starts. A file of its own rather than a key in `meta.json`,
    which the runner rewrites whole on every `meta_set` and would race with."""
    path = os.path.join(d, "cap")
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        fh.write("%d\n" % cap)
    os.replace(tmp, path)


def progress(r):
    """`run 2/5`, as far as the chain has said — the one line a marker can carry."""
    if r["run"] and r["cap"]:
        return "run %s/%s" % (r["run"], r["cap"])
    if r["run"]:
        return "run %s" % r["run"]
    return "starting"


def age(r, now=None):
    secs = max(0, int((now or time.time()) - r["mtime"]))
    if secs < 90:
        return "%ds" % secs
    if secs < 5400:
        return "%dm" % (secs // 60)
    return "%dh" % (secs // 3600)


def main():
    args = sys.argv[1:]
    if args and args[0] == "--live":
        item = args[1] if len(args) > 1 else ""
        r = live_for(item) if item else (live_runs("work") or [None])[0]
        if r is None:
            return 1
        print("%s %s %s %s" % (r["item"], r["pid"], progress(r), r["dir"]))
        return 0
    if args and args[0] == "--left":
        # §0.1 step 2: what did the session behind an unclosed PICK actually leave?
        info = left_behind(args[1] if len(args) > 1 else "")
        if info is None:
            print("no run with a readable transcript for that item")
            return 1
        r = info["run"]
        print("%s · %s · %s · %d responses · peak context %d"
              % (r["item"], r["dir"], r["state"], info["turns"], info["peak"]))
        print("%d write call(s) in its own transcript" % info["calls"])
        for f in info["paths"]:
            print("  %s %s" % ("changed " if f in info["still_changed"] else
                               "planning" if f in info["planning"] else "written ", f))
        if info["scratch"]:
            print("  (%d scratch path(s) outside the repo)" % len(info["scratch"]))
        if not info["calls"]:
            print("nothing written — 'abandoned' may fairly say the work was lost")
        return 0
    if args and args[0] == "--root":
        # For dfs_run.sh, so the path is not spelled a second time.
        # The EFFECTIVE root: created, and the temp directory if it cannot be.
        print(ensure_run_root())
        return 0
    if not args or args[0] == "--json":
        print(json.dumps(run_dirs(), indent=2))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
