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
                                               # is done: rebase the branch onto the default
                                               # branch, merge, drop the worktree

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


# `branch.py merge`'s exit status: 0 the item is settled (merged, or nothing to merge), 3 it
# was not merged and the tree has an open node again, 4 it is blocked on a raise.
# Every result that is not a merge is 3 or 4, so a chain never takes an unmerged item for done.
MERGE_EXIT = {"merged": 0, "none": 0, "dirty": 3, "conflict": 3, "guard": 3, "refused": 4, "blocked": 4}


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


def carry_named_attachments(task, wt):
    """Move the files the task's text names in this checkout's `.dfs/artefacts` into `wt`,
    as `tree.carry_attachments` does for words written once the worktree exists: an
    attachment named in a task before its first run is untracked here, and `carry_uncommitted`
    moves only the task's own files. Returns the names moved."""
    import tree as dfs_tree
    texts = []
    for p in task_paths(task):
        base = wt / p
        for f in ([base] if base.is_file() else sorted(base.rglob("*.md")) if base.is_dir() else []):
            texts.append(f.read_text())
    return dfs_tree.carry_attachments(task, "\n".join(texts))


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
    carry_named_attachments(task, wt)
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


def _reopen_with(task, wt, title, approach, what):
    """Put the item back in work with an open node, which earns another review. The node
    is committed onto the branch unless the commit itself is what is refused."""
    import tree as dfs_tree
    dfs_tree.add_node(task, title, approach=approach)
    try:
        _commit_task_files(task, wt, what)
    except BranchError:
        pass


def _reopen(task, wt, name, files):
    """Put the item back in work: an open node asks for the branch to be rebased onto the
    default branch and the conflicts resolved there."""
    default = dfs_paths.branch()
    _reopen_with(task, wt, "Rebase %s onto %s and resolve the conflicts" % (name, default),
                 "%s could not be rebased onto %s: %s conflict. In this worktree run "
                 "`git rebase --autostash %s` (the autostash keeps the task's uncommitted log "
                 "entries), resolve the conflicts commit by commit, keep both sides' intent, "
                 "run the tests, and `git rebase --continue`."
                 % (name, default, ", ".join(files) or "files", default),
                 "the rebase onto %s conflicted" % default)


def _raise_blocked(task, text):
    """A merge only the author can settle: a raise, and the chain stops blocked."""
    import tree as dfs_tree
    dfs_tree.append_log(task, "raise", [], text)


def raise_unmerged(task, error):
    """The merge of a finished item could not start (`BranchError`: the checkout is on another
    branch): a raise, so the screen and the web page show it and the task is not done."""
    _raise_blocked(task, "%s is finished and reviewed, but it was not merged: %s. Put the "
                         "checkout right and run the chain again, or tell me to leave the "
                         "branch unmerged." % (task, error))


def _rebase(task, wt, name):
    """Replay the branch's commits on the default branch, in the worktree, so the merge
    that follows is a plain `--no-ff` of a branch that starts at the default branch's tip.
    A rebase that conflicts is aborted and reopens the item (`_reopen`); returns whether
    the branch is now on the default branch."""
    default = dfs_paths.branch()
    if _git("merge-base", "--is-ancestor", default, name).returncode == 0:
        return True
    r = _git(*_identity(), "rebase", default, cwd=wt)
    if r.returncode == 0:
        return True
    files = _git("diff", "--name-only", "--diff-filter=U", cwd=wt).stdout.split()
    _git("rebase", "--abort", cwd=wt)
    _reopen(task, wt, name, files)
    return False


def out_of(r):
    return r.stderr + r.stdout


def _gist(text, limit=400):
    """A hook's or git's output on one line, without the guard's own "--warn" aside, cut at
    a word and marked when cut."""
    one = " ".join(l.strip() for l in text.splitlines() if "--warn covers" not in l)
    one = " ".join(one.split())
    return one if len(one) <= limit else one[:limit].rsplit(" ", 1)[0] + " …"


def _unmerged(task, wt, name, r):
    """A merge git did not complete leaves the default branch as it was, and the item
    open. A conflict is work (the default branch moved after the rebase): the merge is
    aborted and the item reopened as `_reopen` does. A merge git refused before starting,
    because the author's uncommitted or staged edits here are in its way, is theirs to settle: a raise
    naming the paths."""
    import tree as dfs_tree
    default = dfs_paths.branch()
    if _git("rev-parse", "-q", "--verify", "MERGE_HEAD").returncode == 0:
        files = _git("diff", "--name-only", "--diff-filter=U").stdout.split()
        _git("merge", "--abort")
        if files:
            _reopen(task, wt, name, files)
            return "conflict"
        # Nothing conflicts: a hook (the roadmap's guard) refused to commit the merge.
        _reopen_with(task, wt, "Fix what refused the merge of %s into %s" % (name, default),
                     "git merged %s into %s without a conflict, but refused to commit the merge: %s "
                     "Change the branch so the merge is accepted (the guard's message says what "
                     "to fix), and commit it." % (name, default, _gist(out_of(r))),
                     "a hook refused the merge into %s" % default)
        return "guard"
    out = r.stderr + r.stdout
    # git lists the paths indented, by a tab when the edits are in the working tree and by
    # spaces when they are staged.
    paths = [l.strip() for l in out.splitlines() if l[:1] in ("\t", " ") and l.strip()]
    _raise_blocked(task, (
        "%s is finished and reviewed, but git would not merge %s into %s: %s\n"
        "Your uncommitted edits in %s stop the merge: %s.\n"
        "Options: commit or stash those edits and answer this, after which the chain works the item "
        "again and merges it when the next review passes, or tell me to leave the branch unmerged. "
        "I would commit them.") % (
            task, name, default, " ".join(out.split())[:200], dfs_paths.work_root(),
            ", ".join(paths) or "see the message above"))
    return "refused"


def merge(task):
    """Merge `dfs/<task>` into the default branch, from the main checkout, and remove the
    worktree and the branch. Local only: nothing is pushed.

    First the task's own files left uncommitted in the worktree (the runner's log entries,
    an accept) are committed onto the branch, since the merge carries only commits; then the
    branch is rebased onto the default branch (`_rebase`). Returns
    "none" when the task has no worktree, "merged", "dirty" when the worktree holds
    uncommitted work that is not the task's (left in place, nothing merged; an open node
    asks for it to be committed or dropped), "conflict" when the branch cannot be rebased
    onto the default branch, which has moved (an open node is added), "guard" when a hook
    refused a clean merge or the task's files (an open node names its message) or "refused"
    when the author's edits overlap (a raise is logged); see `_unmerged`.
    """
    wt = dfs_paths.worktree(task)
    if wt is None:
        return "none"
    name = dfs_paths.item_branch(task)
    if dfs_paths.branch() not in dfs_paths.DEFAULT_BRANCHES:
        raise BranchError("%s is merged from the default branch; this checkout is on %s" %
                          (name, dfs_paths.branch()))
    try:
        _commit_task_files(task, wt, "the log and edits left uncommitted")
    except BranchError as e:
        _reopen_with(task, wt, "Fix what refuses the commit of %s's files on %s" % (task, name),
                     "The runner could not commit the task's own files on %s: %s" % (name, e),
                     "the node asking for the refused commit to be fixed")
        return "guard"
    stray = [l[3:] for l in _git("status", "--porcelain", cwd=wt).stdout.splitlines()]
    if stray:
        _reopen_with(task, wt, "Commit or drop the uncommitted changes in the %s worktree" % name,
                     "%s is finished and reviewed, but its worktree %s holds uncommitted changes that are "
                     "not the task's: %s. Commit the ones that belong to the work (with a node's id in the "
                     "subject) and restore or delete the rest, so the branch can be merged."
                     % (name, dfs_paths.rel(wt), ", ".join(stray[:12])),
                     "uncommitted changes stopped the merge")
        return "dirty"
    if not _rebase(task, wt, name):
        return "conflict"
    r = _git(*_identity(), "merge", "--no-ff", "-m", "%s: merge the item's branch %s" % (task, name), name)
    if r.returncode != 0:
        return _unmerged(task, wt, name, r)
    for what, r in (("its worktree could not be removed", _git("worktree", "remove", "--force", str(wt))),
                    ("its branch could not be deleted", _git("branch", "-d", name))):
        if r.returncode != 0:
            print("dfs_branch: %s merged, but %s: %s" % (task, what, " ".join(out_of(r).split())),
                  file=sys.stderr)
    return "merged"


def keep_leftover_work(task):
    """Keep what the task's worktree holds uncommitted before it is removed (`release`'s
    `--force` would drop it): the untracked files in its `.dfs/artefacts`, the author's
    attachments and the pages a session made, are copied back to this checkout's, where an
    unworked task keeps them, and everything else is committed onto `dfs/<task>`, which
    `release` then keeps as `dfs-deleted/<task>`. BranchError when git refuses the commit,
    so the delete can be refused with the worktree untouched."""
    wt = dfs_paths.worktree(task)
    if not wt:
        return
    art = "%s/artefacts" % dfs_paths.STATE_DIR_NAME
    r = _git("status", "--porcelain", "-z", "--untracked-files=all", "--", art, cwd=wt)
    for entry in r.stdout.split("\0"):
        if entry.startswith("?? "):
            src, dst = wt / entry[3:], dfs_paths.work_root() / entry[3:]
            if not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
    _git("add", "-A", cwd=wt)
    if _git("diff", "--cached", "--quiet", cwd=wt).returncode != 0:
        r = _git(*_identity(), "commit", "-q", "-m",
                 "%s: work left in its worktree when the task was deleted" % task, cwd=wt)
        if r.returncode != 0:
            raise BranchError("what %s's worktree holds could not be kept on %s: %s" %
                              (task, dfs_paths.item_branch(task), out_of(r).strip()))


def release(task):
    """Let go of a deleted task's worktree and branch: remove the worktree (its task files
    have been moved out, and `keep_leftover_work` has kept the rest) and rename
    `dfs/<task>` to `dfs-deleted/<task>` rather than delete it, so the commits it holds stay
    reachable. A failure is said on stderr."""
    wt, name = dfs_paths.worktree(task), dfs_paths.item_branch(task)
    steps = []
    if wt:
        steps.append(("its worktree could not be removed", _git("worktree", "remove", "--force", str(wt))))
    if _has_branch(name):
        steps.append(("its branch could not be renamed", _git("branch", "-m", name, "dfs-deleted/" + task)))
    for what, r in steps:
        if r.returncode != 0:
            print("dfs_branch: %s released, but %s: %s" % (task, what, " ".join(out_of(r).split())),
                  file=sys.stderr)


def main(argv):
    if len(argv) == 3 and argv[1] == "merge":
        try:
            result = merge(argv[2])
        except BranchError as e:
            # Only the author can settle it (the checkout is on another branch): a raise.
            print("dfs_branch: %s" % e, file=sys.stderr)
            raise_unmerged(argv[2], e)
            return MERGE_EXIT["blocked"]
        print("dfs_branch: %s: %s" % (argv[2], {
            "merged": "dfs/%s merged into %s%s" % (argv[2], dfs_paths.branch(),
                      ", its worktree removed" if dfs_paths.worktree(argv[2]) is None else ""),
            "dirty": "not merged: its worktree holds uncommitted work that is not the task's, so a node asks for it to be committed or dropped",
            "guard": "not merged: a hook refused the commit, so a node asks for what it names to be fixed",
            "conflict": "not merged: it conflicts with %s, so a node asks for the branch to be rebased onto it" %
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
