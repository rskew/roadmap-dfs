#!/usr/bin/env python3
"""Where the tool is, where the project's state is — in one place.

Two roots, and they are different questions:

    TOOL_ROOT    roadmap-dfs itself: the scripts, SKILL.md, the templates, the docs.
                 Derived from this file, so it is wherever the tool was installed.
    work_root()  THE CURRENT DIRECTORY. The repo being worked on, and the CWD every
                 session runs at and every prompt path is relative to.
    state()      `<work root>/.dfs` — that project's roadmap, items, artefacts and
                 chain logs. Committed, except `runs/` — or gitignored whole, which is
                 supported: see `ignored()`.

⚠️ **THE WORK ROOT IS `pwd`, WITH NO OVERRIDE AND NOTHING REMEMBERED.** The tool
serves whichever repo you run it in, so which project's roadmap you are working must
be a fact about the invocation and not a setting; a stored root is the
mode-versus-state confusion that gets written down every time it is reintroduced — it
answers "what was CONFIGURED", never "which repo is this". Run it from the work root,
or it reads and writes somebody else's roadmap.

⚠️ Everything that names a roadmap file goes through here; nothing else spells
`.dfs`, `items/` or `ROADMAP.md`. One place, for the same reason `state.py`
is the one place "blocked" is defined: a runner that spelled a prompt path one way
while the screen opened another would be the disagreement this repo keeps writing
down.

    eval "$(python3 <this file> --sh)"   # the same answers, for the shell
    python3 <this file> --require       # exit 0 if this directory has a roadmap
"""
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = TOOL_ROOT / "scripts"
SKILL = TOOL_ROOT / "SKILL.md"
TEMPLATES = TOOL_ROOT / "templates"
EPIC = TOOL_ROOT / "docs" / "epic-agent-roadmap.md"

STATE_DIR_NAME = ".dfs"

# Where the tool's flake is, for a store copy to name itself: `nix run` puts its
# commands on no PATH, and the store path is gone at the next garbage collection.
FLAKE = "github:rskew/roadmap-dfs"


def work_root() -> Path:
    return Path.cwd()


def state() -> Path:
    return work_root() / STATE_DIR_NAME


def roadmap() -> Path:
    return state() / "ROADMAP.md"


def items() -> Path:
    return state() / "items"


def artefacts() -> Path:
    return state() / "artefacts"


def order() -> Path:
    return state() / "order.md"


def runs() -> Path:
    return state() / "runs"


def playwright_shell() -> Path:
    """One line naming the nix dev shell that supplies `playwright` for the artefact
    check, when the project has one (`.#e2e`). Optional: without it the check takes
    `playwright` from PATH, then from nixpkgs."""
    return state() / "playwright-shell"


# ── the branch, and the tag it writes under ──────────────────────────────────
#
# A task can be worked on several git branches at once, each pursuing its own
# alternative. What keeps their merge free of conflicts is that no two branches write
# the same file: every id a branch creates carries its TAG (`W13.4@fix-sync`, a task
# `W30@fix-sync`), and every node and log entry it writes goes in its own PART of the
# task (`items/W13/fix-sync.md`). The default branch has the empty tag, so its ids are
# plain (`W13.4`) and its part is the task's top-level file (`items/W13.md`).
#
# ⚠️ The tag is the branch the id was CREATED on, forever. It says nothing about which
# branch holds the node now, and a merge does not rename anything.

DEFAULT_BRANCHES = ("main", "master")
# Lowercase, starts with a letter, single hyphens between runs. No `.` and no `@`, so
# an id splits unambiguously: `W30@fx.2@fx` is node 2, made on fx, of task W30@fx.
TAG_RE = r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*"
TAG_MAX = 32


class NoBranch(Exception):
    """This checkout has no branch a write can be tagged with."""


def sanitise(branch: str) -> str:
    """A branch name as a tag: `rowan/Fix Sync!` -> `rowan-fix-sync`."""
    s = re.sub(r"[^a-z0-9]+", "-", branch.lower()).strip("-")
    if not s or not s[0].isalpha():
        s = ("b-" + s).strip("-")
    return s[:TAG_MAX].rstrip("-")


def _git(*args):
    return subprocess.run(["git", *args], cwd=work_root(), capture_output=True, text=True)


def branch():
    """The checked-out branch; "" when this is not a git repo at all; None when HEAD
    is detached."""
    r = _git("symbolic-ref", "--quiet", "--short", "HEAD")
    if r.returncode == 0:
        return r.stdout.strip()
    if _git("rev-parse", "--git-dir").returncode != 0:
        return ""
    return None


_TAG_CACHE = {}


def branch_tag() -> str:
    """The tag this checkout writes under: "" on main or master (and outside git).

    Raises NoBranch on a detached HEAD, which has no name to tag with, and when another
    local branch sanitises to the same tag, which would let two branches write one part.

    Cached for a second: the screen asks once per task per redraw, and a branch switch
    under it is seen on the next one.
    """
    key = str(work_root())
    hit = _TAG_CACHE.get(key)
    if hit and time.monotonic() - hit[0] < 1.0:
        if isinstance(hit[1], NoBranch):
            raise hit[1]
        return hit[1]
    try:
        tag = _branch_tag()
    except NoBranch as e:
        _TAG_CACHE[key] = (time.monotonic(), e)
        raise
    _TAG_CACHE[key] = (time.monotonic(), tag)
    return tag


def _branch_tag() -> str:
    name = branch()
    if name is None:
        raise NoBranch("HEAD is detached, so there is no branch to tag this write with. "
                       "Check out a branch (git switch -c <name>) and run it again.")
    if name in ("",) + DEFAULT_BRANCHES:
        return ""
    tag = sanitise(name)
    heads = _git("for-each-ref", "--format=%(refname:short)", "refs/heads").stdout.split()
    twins = [b for b in heads if b != name and b not in DEFAULT_BRANCHES and sanitise(b) == tag]
    if twins:
        raise NoBranch("branches %s and %s both sanitise to the tag %r, so they would write "
                       "the same files. Rename one." % (name, ", ".join(twins), tag))
    return tag


def branch_tag_or_none():
    """The tag, or None where there is none: for READERS, which only use it to put this
    branch's own nodes first."""
    try:
        return branch_tag()
    except NoBranch:
        return None


def ignored() -> bool:
    """Whether git ignores the roadmap, so no task file is ever committed.

    Supported, not a fault: `.dfs` is planning, and the tree walk is still in git
    history, one commit per node, found by its subject (`dfs_tree.node_commits`). What
    changes is what can be asked of git: HEAD holds no task file, so `dfs_check` has
    no history to judge an edit against, a node's commit carries only its code (or
    nothing, `--allow-empty`), and the working tree is the only copy of the tree.

    Asked of a task file that does not exist, so a file tracked from before the
    ignore rule was added cannot answer for the new ones, which are what a session
    writes. False outside git.
    """
    probe = "%s/items/W0.md" % STATE_DIR_NAME
    return _git("check-ignore", "-q", probe).returncode == 0


def has_roadmap() -> bool:
    """Whether this directory HAS a roadmap. The check every entry point owes the
    caller: with the work root being the CWD, running from the wrong place is the
    one easy mistake, and its symptom without this is an empty screen or an item
    file written into a repo whose roadmap nobody meant to touch."""
    return roadmap().is_file()


def missing_message() -> str:
    return ("no roadmap here: %s does not exist.\n"
            "The work root is the current directory, so run this from the repo whose\n"
            "roadmap you mean to work, or start one there, from its root:\n"
            "    %s" % (rel(roadmap()), command("init.py")))


def command(script: str, *args: str) -> str:
    """One of the tool's scripts as the reader should type it, from the work root:
    `foo` when the tool is installed, through the flake when it is a store copy
    that `nix run` left on no PATH, and the script itself in a checkout."""
    if str(TOOL_ROOT).startswith("/nix/store/"):
        name = Path(script).stem.replace("_", "-")
        if shutil.which(name):
            return " ".join((name,) + args)
        run = "nix run %s#%s" % (os.environ.get("DFS_FLAKE") or FLAKE, name)
        return " ".join((run, "--") + args) if args else run
    path = rel(SCRIPTS / script)
    return " ".join((("python3 " + path) if script.endswith(".py") else path,) + args)


def rel(p) -> str:
    """A path as a PROMPT should spell it: relative to the CWD a session runs at.

    Absolute when it lies outside the work root, because then there is no relative
    spelling a session could follow — which is the normal case for the TOOL, once
    roadmap-dfs is installed somewhere other than inside the repo it serves.
    """
    p = Path(p).resolve()
    try:
        return str(p.relative_to(work_root()))
    except ValueError:
        return str(p)


def main() -> int:
    if "--require" in sys.argv[1:]:
        if has_roadmap():
            return 0
        print(missing_message(), file=sys.stderr)
        return 3
    if "--sh" not in sys.argv[1:]:
        print(__doc__, file=sys.stderr)
        return 2
    values = [
        ("DFS_TOOL", TOOL_ROOT), ("DFS_SCRIPTS", SCRIPTS), ("DFS_SKILL", SKILL),
        ("DFS_TEMPLATES", TEMPLATES), ("DFS_EPIC", EPIC),
        ("DFS_WORK_ROOT", work_root()), ("DFS_DIR", state()),
        ("DFS_ROADMAP", roadmap()), ("DFS_ITEMS", items()),
        ("DFS_ARTEFACTS", artefacts()), ("DFS_ORDER", order()), ("DFS_RUNS", runs()),
        ("DFS_PLAYWRIGHT_SHELL_FILE", playwright_shell()),
    ]
    for name, value in values:
        print("export %s=%s" % (name, shlex.quote(str(value))))
    for name, value in (("DFS_SKILL_REL", SKILL), ("DFS_EPIC_REL", EPIC),
                        ("DFS_SCRIPTS_REL", SCRIPTS), ("DFS_ROADMAP_REL", roadmap()),
                        ("DFS_ITEMS_REL", items())):
        print("export %s=%s" % (name, shlex.quote(rel(value))))
    print("export DFS_HAS_ROADMAP=%d" % (1 if has_roadmap() else 0))
    print("export DFS_IGNORED=%d" % (1 if ignored() else 0))
    # Empty on the default branch and on a detached HEAD alike; a writer asks
    # `branch_tag()`, which tells the two apart.
    print("export DFS_TAG=%s" % shlex.quote(branch_tag_or_none() or ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
