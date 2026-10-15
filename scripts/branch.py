#!/usr/bin/env python3
"""Where an item is worked: its own git branch, in its own worktree.

A work chain on a task cuts `dfs/<task>` from the default branch into a worktree at
`.dfs/worktrees/<task>` and runs every session there, so the author's checkout stays
on the default branch, with the screen and their own edits undisturbed. The branch
writes plain ids and merges back as written (paths.py, "an item's branch and its
worktree").

    python3 <scripts>/branch.py enter <task>   # print the worktree to work in, "" to
                                               # stay where we are
    python3 <scripts>/branch.py merge <task>   # from the main checkout, when the item
                                               # is done: merge, drop the worktree

`enter` cuts the branch and the worktree on the task's first run and reuses them
after. It leaves the chain where it is, printing nothing, when the roadmap is
gitignored (no task file is committed, so there is nothing for a branch to carry),
when the checkout is not on the default branch (a tagged branch writes its own part;
an item branch is already the place), and outside git.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths as dfs_paths  # noqa: E402


class BranchError(Exception):
    pass


# `branch.py merge`'s exit status: 0 the item is settled (merged, or nothing to merge), 1 it
# could not be merged and nothing changed, 3 the tree has an open node again, 4 it is blocked.
MERGE_EXIT = {"merged": 0, "none": 0, "dirty": 1, "conflict": 3, "refused": 4}


def _git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd or dfs_paths.work_root(),
                          capture_output=True, text=True)


def _has_branch(name):
    return _git("rev-parse", "--verify", "--quiet", "refs/heads/" + name).returncode == 0


def task_paths(task):
    """The files that are a task's own, as repo-relative paths: its file, its parts and
    its archive."""
    s = dfs_paths.STATE_DIR_NAME
    return ["%s/items/%s.md" % (s, task), "%s/items/%s" % (s, task),
            "%s/archive/items/%s.md" % (s, task)]


def _dirty(paths):
    """[(status, path)] for each uncommitted file under `paths`, untracked ones listed one
    by one."""
    r = _git("status", "--porcelain", "-z", "--untracked-files=all", "--", *paths)
    out = []
    for entry in r.stdout.split("\0"):
        if len(entry) > 3:
            out.append((entry[:2], entry[3:]))
    return out


def carry_uncommitted(task, wt):
    """Move the task's uncommitted files from this checkout into `wt`, and put them back
    to HEAD here: the task's state is whatever the author last left (a raise answered, a
    task just written from the screen), and the branch's first session commits it.
    Returns the paths moved."""
    root = dfs_paths.work_root()
    moved = []
    for st, p in _dirty(task_paths(task)):
        src, dst = root / p, wt / p
        if "D" in st:
            if dst.exists():
                dst.unlink()
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        if st == "??":
            src.unlink()
        elif _git("cat-file", "-e", "HEAD:" + p).returncode == 0:
            _git("checkout", "HEAD", "--", p)
        else:
            _git("rm", "-qf", "--cached", "--", p)
            if src.exists():
                src.unlink()
        moved.append(p)
    return moved


def default_branch():
    for name in dfs_paths.DEFAULT_BRANCHES:
        if _has_branch(name):
            return name
    return None


def keep_worktrees_untracked():
    """Make git ignore `.dfs/worktrees`, in `.git/info/exclude` when no ignore file does:
    a roadmap started before worktrees has no `worktrees/` line in `.dfs/.gitignore`, and
    this is local, so it dirties nothing the author would commit."""
    probe = "%s/worktrees/x" % dfs_paths.STATE_DIR_NAME
    if _git("check-ignore", "-q", probe).returncode == 0:
        return
    common = _git("rev-parse", "--git-common-dir").stdout.strip()
    exclude = (dfs_paths.work_root() / common / "info" / "exclude")
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with open(exclude, "a") as fh:
        fh.write("/%s/worktrees/\n" % dfs_paths.STATE_DIR_NAME)


def enter(task):
    """The worktree `task` is worked in, cut on the first call; None to work where we are."""
    if dfs_paths.ignored() or dfs_paths.branch() not in dfs_paths.DEFAULT_BRANCHES:
        return None
    wt = dfs_paths.worktrees() / task
    name = dfs_paths.item_branch(task)
    if dfs_paths.worktree(task):
        return wt
    fresh = not _has_branch(name)
    keep_worktrees_untracked()
    wt.parent.mkdir(parents=True, exist_ok=True)
    args = ["worktree", "add", str(wt)] + (["-b", name, "HEAD"] if fresh else [name])
    r = _git(*args)
    if r.returncode != 0:
        raise BranchError("cannot work %s in a worktree on %s: %s" % (task, name, r.stderr.strip()))
    if fresh:
        moved = carry_uncommitted(task, wt)
        if moved:
            print("dfs_branch: %d uncommitted path(s) of %s moved into %s" %
                  (len(moved), task, dfs_paths.rel(wt)), file=sys.stderr)
    else:
        print("dfs_branch: %s existed; reused, and %s's uncommitted edits here are left as they are" %
              (name, task), file=sys.stderr)
    return wt


def _identity():
    """`-c` flags for a commit made where git has no identity configured."""
    if _git("config", "user.email").stdout.strip():
        return []
    return ["-c", "user.name=roadmap-dfs", "-c", "user.email=roadmap-dfs@localhost"]


def _commit_task_files(task, wt, what):
    """Commit the task's own files left uncommitted in its worktree onto its branch."""
    _git("add", "--", *[p for p in task_paths(task) if (wt / p).exists()], cwd=wt)
    if _git("diff", "--cached", "--quiet", cwd=wt).returncode != 0:
        r = _git(*_identity(), "commit", "-q", "-m", "%s: %s" % (task, what), cwd=wt)
        if r.returncode != 0:
            raise BranchError("cannot commit %s's files on %s: %s" %
                              (task, dfs_paths.item_branch(task), (r.stderr or r.stdout).strip()))


def _unmerged(task, wt, name, r):
    """A merge git did not complete leaves the default branch as it was, and the item
    open. A conflict is work: the merge is aborted and an open node asks for the default
    branch to be merged into the item's branch and resolved there, which earns another
    review. A merge git refused before starting, because the author's uncommitted edits
    here overlap it, is theirs to settle: a raise naming the paths."""
    import tree as dfs_tree
    default = dfs_paths.branch()
    if _git("rev-parse", "-q", "--verify", "MERGE_HEAD").returncode == 0:
        files = _git("diff", "--name-only", "--diff-filter=U").stdout.split()
        _git("merge", "--abort")
        dfs_tree.add_node(task, "Merge %s into %s and resolve the conflicts" % (default, name),
                          approach="%s could not merge into %s: %s conflict. In this worktree run `git merge %s`, "
                                   "resolve the conflicts, keep both sides' intent, run the tests, and commit."
                                   % (name, default, ", ".join(files) or "files", default))
        _commit_task_files(task, wt, "the merge into %s conflicted" % default)
        return "conflict"
    out = r.stderr + r.stdout
    paths = [l.strip() for l in out.splitlines() if l.startswith("\t")]
    dfs_tree.append_log(task, "raise", [], (
        "%s is finished and reviewed, but git would not merge %s into %s: %s\n"
        "Your uncommitted edits in %s overlap the merge: %s.\n"
        "Options: commit or stash those edits and run the chain again (it merges first), or tell me "
        "to leave the branch unmerged. I would commit them.") % (
            task, name, default, " ".join(out.split())[:200], dfs_paths.work_root(),
            ", ".join(paths) or "see the message above"))
    return "refused"


def merge(task):
    """Merge `dfs/<task>` into the default branch, from the main checkout, and remove the
    worktree and the branch. Local only: nothing is pushed.

    First the task's own files left uncommitted in the worktree (the runner's log entries,
    an accept) are committed onto the branch, since the merge carries only commits. Returns
    "none" when the task has no worktree, "merged", "dirty" when the worktree holds
    uncommitted work that is not the task's (left in place, nothing merged), "conflict"
    when the default branch moved under it (an open node is added) or "refused" when the
    author's edits overlap (a raise is logged); see `_unmerged`.
    """
    wt = dfs_paths.worktree(task)
    if wt is None:
        return "none"
    name = dfs_paths.item_branch(task)
    if dfs_paths.branch() not in dfs_paths.DEFAULT_BRANCHES:
        raise BranchError("%s is merged from the default branch; this checkout is on %s" %
                          (name, dfs_paths.branch()))
    _commit_task_files(task, wt, "the log and edits left uncommitted")
    if _git("status", "--porcelain", cwd=wt).stdout.strip():
        return "dirty"
    r = _git(*_identity(), "merge", "--no-ff", "-m", "%s: merge the item's branch %s" % (task, name), name)
    if r.returncode != 0:
        return _unmerged(task, wt, name, r)
    _git("worktree", "remove", "--force", str(wt))
    _git("branch", "-d", name)
    return "merged"


def main(argv):
    if len(argv) == 3 and argv[1] == "merge":
        try:
            result = merge(argv[2])
        except BranchError as e:
            print("dfs_branch: %s" % e, file=sys.stderr)
            return 1
        print("dfs_branch: %s: %s" % (argv[2], {
            "merged": "dfs/%s merged into %s, its worktree removed" % (argv[2], dfs_paths.branch()),
            "dirty": "not merged: its worktree holds uncommitted work that is not the task's",
            "conflict": "not merged: it conflicts with %s, so a node asks for that merge on the branch" %
                        dfs_paths.branch(),
            "refused": "not merged: git would overwrite uncommitted edits here, raised for the author",
            "none": "no worktree, nothing to merge"}[result]), file=sys.stderr)
        return MERGE_EXIT[result]
    if len(argv) == 3 and argv[1] == "enter":
        try:
            wt = enter(argv[2])
        except BranchError as e:
            print("dfs_branch: %s" % e, file=sys.stderr)
            return 2
        print(os.fspath(wt) if wt else "")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
