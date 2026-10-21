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
after. A gitignored roadmap is in no commit, so the worktree is given links to the main
checkout's ignored planning files (`share_ignored_state`) and both read the one copy. It
leaves the chain where it is, printing nothing, when the checkout is not on the default
branch (a tagged branch writes its own part; an item branch is already the place), when no
commit exists yet, and outside git.
"""
import fcntl
import functools
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


def _git_bytes(*args, cwd=None):
    """`git`'s stdout as the bytes it wrote, for comparing a file's content exactly."""
    return subprocess.run(["git", *args], cwd=cwd or dfs_paths.work_root(),
                          capture_output=True).stdout


# ⚠️ ONE CHAIN AT A TIME ON THE MAIN CHECKOUT. A walk may run several chains at once
# (`walk.py`, `parallel`), each in its own worktree, but cutting a worktree, merging a
# finished branch and removing it all write the ONE main checkout and repository: two
# merges at once meet on `index.lock`, and the loser's failed `git merge` reads in
# `_unmerged` as the author's edits being in the way, a raise for nothing. So each runs
# under an exclusive lock in the git common dir, which every worktree shares, and a second
# chain waits for the first to finish there. Its rebase is inside the lock too, so it is
# made onto the default branch as the first chain's merge left it.
LOCK_NAME = "dfs-branch.lock"


def one_at_a_time(fn):
    """Run `fn` holding `LOCK_NAME`, waiting for any other holder; outside git, unlocked."""
    @functools.wraps(fn)
    def locked(*args, **kwargs):
        r = _git("rev-parse", "--git-common-dir")
        if r.returncode != 0:
            return fn(*args, **kwargs)
        path = Path(r.stdout.strip())
        if not path.is_absolute():
            path = dfs_paths.work_root() / path
        with open(path / LOCK_NAME, "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                return fn(*args, **kwargs)
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
    return locked


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
        if dst.parent.resolve() == src.parent.resolve():
            continue   # the worktree reaches it through a link: it is the one copy, already shared
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


def _exclude(line):
    """Append `line` to `.git/info/exclude`: git's local ignore list, which dirties nothing
    the author would commit."""
    common = _git("rev-parse", "--git-common-dir").stdout.strip()
    exclude = (dfs_paths.work_root() / common / "info" / "exclude")
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with open(exclude, "a") as fh:
        fh.write(line + "\n")


def keep_worktrees_untracked():
    """Make git ignore `.dfs/worktrees`, in `.git/info/exclude` when no ignore file does:
    a roadmap started before worktrees has no `worktrees/` line in `.dfs/.gitignore`."""
    probe = "%s/worktrees/x" % dfs_paths.STATE_DIR_NAME
    if _git("check-ignore", "-q", probe).returncode != 0:
        _exclude("/%s/worktrees/" % dfs_paths.STATE_DIR_NAME)


SHARED = ("items", "archive", "artefacts")   # the `.dfs` directories a session writes into


def _ignored_dir(rel, cwd=None):
    """Whether git ignores the directory `rel`, though it need not exist (a trailing slash
    makes the path a directory to `check-ignore`)."""
    return _git("check-ignore", "-q", rel + "/", cwd=cwd).returncode == 0


def _move_into(src, dst):
    """Move the files of the directory `src` into `dst`, keeping both when a file is in each:
    the one from `src` lands beside the other as `<name>.from-worktree`, named on stderr.
    `src` is left empty."""
    for f in sorted(src.rglob("*")):
        if f.is_dir() and not f.is_symlink():
            continue
        target = dst / f.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            target = target.with_name(target.name + ".from-worktree")
            print("dfs_branch: %s exists here too; the worktree's copy is kept as %s" %
                  (dfs_paths.rel(target.with_name(target.name[:-len(".from-worktree")])),
                   dfs_paths.rel(target)), file=sys.stderr)
        shutil.move(str(f), str(target))
    shutil.rmtree(src)


def _move_file(src, dst):
    """Move the file `src` to `dst`, or beside it as `<name>.from-worktree` (named on
    stderr) when `dst` exists."""
    if dst.exists() or dst.is_symlink():
        keep = dst.with_name(dst.name + ".from-worktree")
        print("dfs_branch: %s exists here too; the worktree's copy is kept as %s" %
              (dfs_paths.rel(dst), dfs_paths.rel(keep)), file=sys.stderr)
        dst = keep
    shutil.move(str(src), str(dst))


def rescue_ignored_state(wt):
    """Bring back to this checkout what the worktree `wt` holds for real under a `.dfs` entry
    git ignores: such files are in no commit, so `git worktree remove --force` would erase
    them, and they are the task's own (a first `.dfs/archive` from `tree.py shelve`, a page in
    a first `.dfs/artefacts`). Each real directory moves into this checkout's `.dfs` (merged
    with what is there) and is left linked to it; a real ignored file at the top moves across
    as it is. Returns the names moved."""
    s = dfs_paths.STATE_DIR_NAME
    here, there = dfs_paths.state(), wt / s
    moved = []
    if not there.is_dir():
        return moved
    for entry in sorted(there.iterdir()):
        if entry.name == "worktrees" or entry.is_symlink():
            continue
        if not entry.is_dir():
            if _git("check-ignore", "-q", "%s/%s" % (s, entry.name)).returncode == 0:
                here.mkdir(exist_ok=True)
                _move_file(entry, here / entry.name)
                os.symlink(os.path.relpath(here / entry.name, there), entry)
                moved.append(entry.name)
            continue
        if not _ignored_dir("%s/%s" % (s, entry.name)):
            continue
        here.mkdir(exist_ok=True)
        dest = here / entry.name
        dest.mkdir(exist_ok=True)
        _move_into(entry, dest)
        os.symlink(os.path.relpath(dest, there), entry)
        moved.append(entry.name)
    return moved


def _carry_branch_edits(wt, rel):
    """Before the worktree's own copy of the `.dfs` entry `rel` gives way to a link, bring the
    branch's changes to its tracked files into this checkout, where the link will read them: a
    worktree begun before the entry was shared worked its tracked task file for real, and the
    branch holds (or the worktree has edited) a newer version than this checkout's. A file
    this checkout also changed since the branch began cannot be settled by copying. Returns
    those paths; the entry is then left unshared."""
    root = dfs_paths.work_root()
    base = _git("merge-base", "HEAD", _git("rev-parse", "HEAD").stdout.strip(), cwd=wt).stdout.strip()
    if not base:
        return []
    tracked = [p for p in _git("ls-files", "-z", "--", rel, cwd=wt).stdout.split("\0") if p]
    clashes = []
    for p in tracked:
        mine = wt / p
        if not mine.is_file() or mine.is_symlink():
            continue
        if _git("diff", "--quiet", base, "--", p, cwd=wt).returncode == 0:
            continue   # the branch has not changed it, committed or not
        was = _git_bytes("show", "%s:%s" % (base, p), cwd=wt)
        now, theirs = mine.read_bytes(), root / p
        if theirs.is_file() and theirs.read_bytes() not in (now, was):
            clashes.append(p)
            continue
        if not theirs.is_file() or theirs.read_bytes() != now:
            theirs.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(mine, theirs)
        if _git("diff", "--quiet", "--", p, cwd=wt).returncode != 0:
            _git("checkout", "--", p, cwd=wt)   # the edit now lives in the shared copy
    return clashes


def _hide_tracked(wt, rel, src):
    """Clear the worktree's own copy of the `.dfs` entry `rel`, which git checked out because
    it tracks part of it though the rules hide it, so a link to `src` can take its place:
    its tracked paths are marked `--skip-worktree` (a link git cannot look beyond leaves them
    unchanged, not deleted), unedited ones are deleted, and what is left (untracked files, an
    edited copy) moves to `src` as `_move_into` does."""
    dest = wt / rel
    tracked = [p for p in _git("ls-files", "-z", "--", rel, cwd=wt).stdout.split("\0") if p]
    edited = {p for p in _git("diff", "--name-only", "-z", "--", rel, cwd=wt).stdout.split("\0") if p}
    if tracked:
        _git("update-index", "--skip-worktree", "--", *tracked, cwd=wt)
    for p in tracked:
        if p not in edited and (wt / p).is_file():
            (wt / p).unlink()
    if dest.is_dir():
        _move_into(dest, src)
    elif dest.exists():
        _move_file(dest, src)


def put_back_tracked(wt):
    """Undo `_hide_tracked` before git rewrites the worktree: a rebase refuses to update a
    `--skip-worktree` path it finds through a link ("would be overwritten by checkout") when
    the default branch changed it. The links over hidden paths go, and the paths are checked
    out from HEAD again. Returns the names of the `.dfs` entries that held any (none when
    there were none); `share_ignored_state` hides them again."""
    s = dfs_paths.STATE_DIR_NAME
    hidden = [e[2:] for e in _git("ls-files", "-v", "-z", "--", s, cwd=wt).stdout.split("\0")
              if e.startswith("S ")]
    if not hidden:
        return []
    tops = sorted({"/".join(p.split("/")[:2]) for p in hidden})
    for top in tops:
        if (wt / top).is_symlink():
            (wt / top).unlink()
    _git("update-index", "--no-skip-worktree", "--", *hidden, cwd=wt)
    _git("checkout", "--", *hidden, cwd=wt)
    return [t.split("/")[1] for t in tops]


def share_ignored_state(wt, again=None):
    """Give the worktree the planning files git does not carry. A gitignored roadmap is in
    no commit, so the branch's checkout has none of it: each entry of this checkout's
    `.dfs` that git ignores and `wt` lacks (`items`, `archive`, `artefacts`, `runs` …, never
    `worktrees`) is linked into the worktree's `.dfs`, so a session there reads and writes
    the one copy the screen and the web page read. The directories a session writes into
    (`SHARED`) are made here first when git ignores them, so the first `.dfs/archive` or page
    is written through the link, not into the worktree, which is removed at the merge. A real
    directory the worktree already holds under an ignored name is moved back first
    (`rescue_ignored_state`). `again`, the entries `put_back_tracked` unlinked around a
    rebase, limits it to those and links them without `_carry_branch_edits`: they were
    shared before, so this checkout's copy is the live one and is not to be weighed against
    the rebased branch. Returns the names linked."""
    s = dfs_paths.STATE_DIR_NAME
    here, there = dfs_paths.state(), wt / s
    linked = []
    if not here.is_dir():
        return linked
    there.mkdir(exist_ok=True)
    rescue_ignored_state(wt)
    for name in SHARED:
        if not (here / name).exists() and _ignored_dir("%s/%s" % (s, name)):
            (here / name).mkdir()
    for entry in sorted(here.iterdir()):
        dest = there / entry.name
        rel = "%s/%s" % (s, entry.name)
        if entry.name == "worktrees" or dest.is_symlink():
            continue
        if again is not None and entry.name not in again:
            continue
        # `--no-index`: an entry the rules hide is shared even when git tracks part of it
        # (a blanket `.*` over files force-added once), or the branch's stale copy is read
        if _git("check-ignore", "-q", "--no-index", rel).returncode != 0:
            continue
        if dest.exists():
            clashes = [] if again is not None else _carry_branch_edits(wt, rel)
            if clashes:
                print("dfs_branch: %s is changed both on %s and here (%s); it is not shared, and "
                      "the worktree keeps its own copy" % (rel, dfs_paths.item_branch(wt.name),
                      ", ".join(clashes)), file=sys.stderr)
                continue
            _hide_tracked(wt, rel, entry)
        os.symlink(os.path.relpath(entry, there), dest)
        if _git("check-ignore", "-q", rel, cwd=wt).returncode != 0:
            _exclude("/" + rel)   # `items/` matches a directory, and a link is not one
        linked.append(entry.name)
    return linked


def catch_up(name, wt):
    """Bring a branch with no commits of its own up to the default branch. A branch cut
    before the task's file was committed (a first attempt that died after the cut) is
    reused as it was, and a session in it finds no task file. Only a fast-forward, only
    from the default branch, and a refusal (edits in the way) leaves it as it was.
    Returns whether it moved."""
    if dfs_paths.branch() not in dfs_paths.DEFAULT_BRANCHES:
        return False
    if _git("merge-base", "--is-ancestor", name, "HEAD").returncode != 0:
        return False
    head = _git("rev-parse", "HEAD").stdout.strip()   # not "HEAD" in `wt`: that is the branch
    if _git("rev-parse", name).stdout.strip() == head:
        return False
    # Like `_rebase`: git refuses to update a `--skip-worktree` path it finds through a link,
    # and replaces a link with a real directory when the default branch added a file under it
    hidden = put_back_tracked(wt)
    state = wt / dfs_paths.STATE_DIR_NAME
    links = [e.name for e in state.iterdir() if e.is_symlink()] if state.is_dir() else []
    moved = _git("merge", "--ff-only", "-q", head, cwd=wt).returncode == 0
    again = hidden + [n for n in links if n not in hidden and not (state / n).is_symlink()]
    if again:
        share_ignored_state(wt, again=again)
    return moved


@one_at_a_time
def enter(task):
    """The worktree `task` is worked in, cut on the first call; None to work where we are:
    the checkout is off the default branch, or no commit exists yet for a branch to start
    from. A task that already has a worktree is worked in it wherever the checkout is, since
    the task's files are read from there; the merge then raises off the default branch
    (`merge`) rather than the task ending done unmerged. A gitignored roadmap is worked in a
    worktree too, with its planning files shared (`share_ignored_state`)."""
    wt = dfs_paths.worktrees() / task
    name = dfs_paths.item_branch(task)
    ignored = dfs_paths.ignored()
    if dfs_paths.worktree(task):
        if ignored:
            share_ignored_state(wt)
        catch_up(name, wt)
        return wt
    if not dfs_paths.gets_worktree(task):
        return None
    fresh = not _has_branch(name)
    _git("worktree", "prune")   # a worktree deleted by hand stays registered, and blocks the add
    keep_worktrees_untracked()
    wt.parent.mkdir(parents=True, exist_ok=True)
    args = ["worktree", "add", str(wt)] + (["-b", name, "HEAD"] if fresh else [name])
    r = _git(*args)
    if r.returncode != 0:
        raise BranchError("cannot work %s in a worktree on %s: %s" % (task, name, r.stderr.strip()))
    if ignored:
        share_ignored_state(wt)
    if fresh:
        moved = carry_uncommitted(task, wt)
        if moved:
            print("dfs_branch: %d uncommitted path(s) of %s moved into %s" %
                  (len(moved), task, dfs_paths.rel(wt)), file=sys.stderr)
    else:
        print("dfs_branch: %s existed; reused, and %s's uncommitted edits here are left as they are" %
              (name, task), file=sys.stderr)
        if catch_up(name, wt):
            print("dfs_branch: %s had no commits of its own; moved up to %s" %
                  (name, dfs_paths.branch()), file=sys.stderr)
    carry_named_attachments(task, wt)
    return wt


def _restore(task):
    """The worktree of a branch whose directory was removed by hand: `git worktree prune`
    drops the stale registration and the branch is checked out again, so the merge sees its
    commits (what the directory held uncommitted is gone). None when there is no such branch."""
    name = dfs_paths.item_branch(task)
    if not _has_branch(name):
        return None
    wt = dfs_paths.worktrees() / task
    _git("worktree", "prune")
    r = _git("worktree", "add", str(wt), name)
    if r.returncode != 0:
        raise BranchError("%s exists but its worktree is gone and cannot be restored: %s" %
                          (name, " ".join(out_of(r).split())))
    print("dfs_branch: %s's worktree was missing; %s checked out again" % (task, name), file=sys.stderr)
    return wt


def _identity():
    """`-c` flags for a commit made where git has no identity configured."""
    if _git("config", "user.email").stdout.strip():
        return []
    return ["-c", "user.name=roadmap-dfs", "-c", "user.email=roadmap-dfs@localhost"]


def _tracked_task_paths(task, wt):
    """The task's own paths that exist in `wt` and that git does not ignore: the ones the
    branch carries. A path beyond a link (the shared, ignored planning files) is not one."""
    out = []
    for p in task_paths(task):
        if not (wt / p).exists():
            continue
        if any(q.is_symlink() for q in [wt / p, *(wt / p).parents] if wt in q.parents):
            continue
        if _git("check-ignore", "-q", p, cwd=wt).returncode != 0:
            out.append(p)
    return out


def _commit_task_files(task, wt, what):
    """Commit the task's own files left uncommitted in its worktree onto its branch. Those
    git ignores (a gitignored roadmap, whole or in part) are shared, not the branch's.
    Only those paths: `--only` builds the commit from HEAD and them, so whatever else is
    staged in the worktree (another task's file) is neither committed nor judged by the
    guard here, and stays for `merge` to name as uncommitted."""
    paths = _tracked_task_paths(task, wt)
    if not paths:
        return
    _git("add", "--", *paths, cwd=wt)
    if _git("diff", "--cached", "--quiet", "HEAD", "--", *paths, cwd=wt).returncode != 0:
        r = _git(*_identity(), "commit", "-q", "--only", "-m", "%s: %s" % (task, what),
                 "--", *paths, cwd=wt)
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


def _in_progress():
    """The git operation the author has begun and not finished in the main checkout (a merge
    that conflicted, a cherry-pick, a revert, a rebase), or "". `_unmerged` takes a MERGE_HEAD
    for the merge it started, so none of these may be there when `merge` begins. A rebase of
    the checked-out branch detaches HEAD, so `merge` asks this before it asks which branch
    the checkout is on."""
    for what, marker in (("merge", "MERGE_HEAD"), ("cherry-pick", "CHERRY_PICK_HEAD"),
                         ("revert", "REVERT_HEAD"), ("rebase", "rebase-merge"),
                         ("rebase", "rebase-apply")):
        path = Path(_git("rev-parse", "--git-path", marker).stdout.strip())
        if not path.is_absolute():
            path = dfs_paths.work_root() / path
        if path.exists():
            return what
    return ""


def _rebase(task, wt, name):
    """Replay the branch's commits on the default branch, in the worktree, so the merge
    that follows is a plain `--no-ff` of a branch that starts at the default branch's tip.
    A rebase that conflicts is aborted and reopens the item (`_reopen`); returns whether
    the branch is now on the default branch."""
    default = dfs_paths.branch()
    if _git("merge-base", "--is-ancestor", default, name).returncode == 0:
        return True
    for marker in ("rebase-merge", "rebase-apply"):
        path = Path(_git("rev-parse", "--git-path", marker, cwd=wt).stdout.strip())
        if (path if path.is_absolute() else wt / path).exists():
            # A session left its own rebase unfinished: aborting it would throw its
            # resolutions away, and there are no conflicts of ours to name.
            _reopen_with(task, wt, "Finish or abort the rebase left unfinished in the %s worktree" % name,
                         "%s is finished and reviewed, but a rebase is unfinished in its worktree %s. "
                         "Run `git rebase --continue` (or `--abort`), and rebase it onto %s if it "
                         "is not yet on it." % (name, dfs_paths.rel(wt), default),
                         "a rebase was left unfinished in the worktree")
            return False
    hidden = put_back_tracked(wt)
    r = _git(*_identity(), "rebase", default, cwd=wt)
    files = []
    if r.returncode != 0:
        files = _git("diff", "--name-only", "--diff-filter=U", cwd=wt).stdout.split()
        _git("rebase", "--abort", cwd=wt)
    if hidden:
        share_ignored_state(wt, again=hidden)
    if r.returncode == 0:
        return True
    _reopen(task, wt, name, files)
    return False


def out_of(r):
    return r.stderr + r.stdout


def _gist(text, limit=400):
    """A hook's or git's output on one line, without the guard's own "--warn" aside, cut at
    a word and marked when cut. A coordinate the guard quotes loses its line number
    (`a.py:12` is `a.py`): the gist goes into the task file, where the guard warns of one.
    The guard's notes (`dfs_check: note — ...`, one per over-budget task file in the index,
    printed before the faults) are dropped with their indented lines: they are about other
    tasks and would fill the gist before the fault that refused the commit."""
    import check as dfs_check
    lines, noting = [], False
    for l in text.splitlines():
        head, mark, _ = l.partition("dfs_check: note")
        if mark:
            noting = True
            if head.strip():
                lines.append(head)
        elif noting and l[:1].isspace():
            pass
        else:
            noting = False
            lines.append(l)
    one = " ".join(l.strip() for l in lines if "--warn covers" not in l)
    one = dfs_check.COORD.sub(lambda m: m.group(0).rsplit(":", 1)[0], " ".join(one.split()))
    return one if len(one) <= limit else one[:limit].rsplit(" ", 1)[0] + " …"


def _unmerged(task, wt, name, r, aside=None):
    """A merge git did not complete leaves the default branch as it was, and the item
    open. A conflict is work (the default branch moved after the rebase): the merge is
    aborted and the item reopened as `_reopen` does. A merge git refused before starting,
    because the author's uncommitted or staged edits here are in its way, is theirs to settle: a raise
    naming the paths. The shared edits `aside` (`_set_aside_shared_edits`) are put back before
    anything is written into the task file, which they may be: the raise or node would land on
    the restored copy and be overwritten by the put-back."""
    import tree as dfs_tree
    default = dfs_paths.branch()
    if _git("rev-parse", "-q", "--verify", "MERGE_HEAD").returncode == 0:
        files = _git("diff", "--name-only", "--diff-filter=U").stdout.split()
        _git("merge", "--abort")
        _put_back_shared_edits(aside or {})
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
    _put_back_shared_edits(aside or {})
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


def _set_aside_shared_edits(name, wt, aside=None):
    """The `.dfs` files the branch `name` changed and that this checkout holds edited, where
    the edit was made on the branch's version: the worktree `wt` reaches the file through a
    link to this checkout's copy (`share_ignored_state`), so the sessions edited it while the
    branch, begun before it was shared, committed it too, and the merge would refuse to
    overwrite the edit. Each is saved and restored to HEAD; `_put_back_shared_edits` writes
    them back. Any other edit, the author's own, is left for git to refuse and `_unmerged` to
    raise: putting it back would silently undo the branch's change in the working copy.
    Fills and returns {path: bytes}: the caller passes `aside` in so that its `finally` holds
    each edit from the moment it is read, before the reset that follows."""
    s = dfs_paths.STATE_DIR_NAME
    root = dfs_paths.work_root()
    changed = [p for p in _git("diff", "--name-only", "-z", "HEAD", name, "--", s).stdout.split("\0") if p]
    aside = {} if aside is None else aside
    for st, p in (_dirty(changed) if changed else []):
        if st == "??" or "D" in st:
            continue
        if not ((wt / p).exists() and (wt / p).samefile(root / p)):
            continue
        aside[p] = (root / p).read_bytes()
        _git("checkout", "HEAD", "--", p)
    return aside


def _put_back_shared_edits(aside):
    """Write the saved edits back, each once: an entry leaves `aside` as it is written, so a
    second call (the `finally` in `merge` after `_unmerged` has put them back and written its
    raise) does not overwrite what was written since."""
    while aside:
        p, data = aside.popitem()
        (dfs_paths.work_root() / p).write_bytes(data)


@one_at_a_time
def merge(task):
    """Merge `dfs/<task>` into the default branch, from the main checkout, and remove the
    worktree and the branch. Local only: nothing is pushed.

    First the task's own files left uncommitted in the worktree (the runner's log entries,
    an accept) are committed onto the branch, since the merge carries only commits; then the
    branch is rebased onto the default branch (`_rebase`). Returns
    "none" when the task has no branch (one whose worktree was removed by hand is checked out
    again, `_restore`), "merged", "dirty" when the worktree holds
    uncommitted work that is not the task's (left in place, nothing merged; an open node
    asks for it to be committed or dropped), "conflict" when the branch cannot be rebased
    onto the default branch, which has moved (an open node is added), "guard" when a hook
    refused a clean merge or the task's files (an open node names its message) or "refused"
    when the author's edits overlap (a raise is logged); see `_unmerged`. A merge, cherry-pick,
    revert or rebase the author has left unfinished in the checkout is a `BranchError` before
    anything is touched (it is theirs, and `_unmerged` would abort it), and it is met before the
    off-branch check, since a rebase detaches HEAD; so is a checkout on another branch or on a
    detached HEAD.
    """
    name = dfs_paths.item_branch(task)
    wt = dfs_paths.worktree(task)
    if wt is None and not _has_branch(name):
        return "none"
    if wt is None and dfs_paths.branch() == name:
        if _git("rev-parse", "--git-dir").stdout != _git("rev-parse", "--git-common-dir").stdout:
            return "none"   # run inside a linked worktree, which has nothing to merge from here
        raise BranchError("%s is merged from the default branch; this checkout has %s itself "
                          "checked out, with no worktree for it" % (name, name))
    started = _in_progress()
    if started:
        raise BranchError("the checkout has its own %s unfinished, which a merge would take for "
                          "its own and abort" % started)
    here = dfs_paths.branch()
    if here is None:
        raise BranchError("%s is merged from the default branch; this checkout has a detached HEAD"
                          % name)
    if here not in dfs_paths.DEFAULT_BRANCHES:
        raise BranchError("%s is merged from the default branch; this checkout is on %s" %
                          (name, here))
    wt = wt or _restore(task)
    try:
        _commit_task_files(task, wt, "the log and edits left uncommitted")
    except BranchError as e:
        _reopen_with(task, wt, "Fix what refuses the commit of %s's files on %s" % (task, name),
                     "The runner's commit of the task's own files was refused: %s Fix what the "
                     "message names (`check.py --staged` in the worktree prints the guard's "
                     "whole message) and commit the task's files." % _gist(str(e)),
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
    aside = {}
    try:
        _set_aside_shared_edits(name, wt, aside)
        r = _git(*_identity(), "merge", "--no-ff", "-m", "%s: merge the item's branch %s" % (task, name), name)
        if r.returncode != 0:
            return _unmerged(task, wt, name, r, aside)
    finally:
        # Every way out of the window puts the edits back, an interrupt (Stop chain's SIGINT
        # reaches this process) included: they have no other copy.
        _put_back_shared_edits(aside)
    if dfs_paths.ignored():
        rescue_ignored_state(wt)
    for what, r in (("its worktree could not be removed", _git("worktree", "remove", "--force", str(wt))),
                    ("its branch could not be deleted", _git("branch", "-d", name))):
        if r.returncode != 0:
            print("dfs_branch: %s merged, but %s: %s" % (task, what, " ".join(out_of(r).split())),
                  file=sys.stderr)
    return "merged"


def keep_leftover_work(task, verb="deleted"):
    """Keep what the task's worktree holds uncommitted before it is removed (`release`'s
    `--force` would drop it): the untracked files in its `.dfs/artefacts`, the author's
    attachments and the pages a session made, are copied back to this checkout's, where an
    unworked task keeps them, and everything else is committed onto `dfs/<task>`, which
    `release` then keeps as `dfs-<verb>/<task>`. BranchError when git refuses the commit,
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
                 "%s: work left in its worktree when the task was %s" % (task, verb), cwd=wt)
        if r.returncode != 0:
            raise BranchError("what %s's worktree holds could not be kept on %s: %s" %
                              (task, dfs_paths.item_branch(task), out_of(r).strip()))


@one_at_a_time
def release(task, verb="deleted"):
    """Let go of the worktree and branch of a task that was `verb` ("deleted" or "archived"):
    remove the worktree (its task files have been moved out and `keep_leftover_work` has
    committed the rest, so `--force` drops only the moved files' entries) and rename
    `dfs/<task>` to `dfs-<verb>/<task>` rather than delete it, so the commits it holds stay
    reachable. A failure is said on stderr."""
    wt, name = dfs_paths.worktree(task), dfs_paths.item_branch(task)
    steps = []
    if wt:
        if dfs_paths.ignored():
            rescue_ignored_state(wt)
        steps.append(("its worktree could not be removed", _git("worktree", "remove", "--force", str(wt))))
    if _has_branch(name):
        steps.append(("its branch could not be renamed", _git("branch", "-m", name, "dfs-%s/%s" % (verb, task))))
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
            "none": "no branch, nothing to merge"}[result]), file=sys.stderr)
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
