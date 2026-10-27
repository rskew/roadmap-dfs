#!/usr/bin/env python3
"""GitHub mode: a finished item goes to a pull request instead of a local merge.

With `.dfs/github` written (`paths.read_github`, the remote to push to), `branch.merge`
holds a finished item, its branch and worktree kept. The author's Open PR mark is `pr`:

    python3 <scripts>/github.py pr <task>   # from the main checkout: rebase dfs/<task> onto
                                            # the default branch, push it, open its pull
                                            # request with `gh`, and log a `pr` entry with
                                            # the request's url
    python3 <scripts>/github.py watch [<task>]  # each new comment or review on an open pull
                                                # request becomes an open node

`gh` must be logged in (`gh auth login`); `DFS_GH` names another command to run as it
(the tests' fake). See README, "GitHub mode".
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import branch as dfs_branch  # noqa: E402
import paths as dfs_paths    # noqa: E402
import tree as dfs_tree      # noqa: E402


class GithubError(Exception):
    pass


def gh(*args, cwd=None):
    """Run `gh` (or `DFS_GH`) and return its stdout; GithubError with its message when it fails."""
    cmd = shlex.split(os.environ.get("DFS_GH") or "gh") + list(args)
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    except OSError as e:
        raise GithubError("gh could not run (%s); install the GitHub CLI and `gh auth login`" % e)
    if r.returncode != 0:
        raise GithubError("gh %s failed: %s" % (args[0] if args else "",
                                                " ".join((r.stderr or r.stdout).split())[:400]))
    return r.stdout


pr_url = dfs_tree.pr_url


@dfs_branch.one_at_a_time
def open_pr(task):
    """Open the pull request of a finished item GitHub mode holds; returns its url. The branch
    is rebased onto the default branch first (a conflict reopens the item with a node, as a
    merge's does, and is a GithubError here), then pushed to the remote `.dfs/github` names
    and handed to `gh pr create` with the merge commit's details as its body."""
    remote = dfs_paths.read_github()
    if not remote:
        raise GithubError("GitHub mode is off: write the remote to push to into %s"
                          % dfs_paths.rel(dfs_paths.github_file()))
    t = dfs_tree.load(task)
    if pr_url(t):
        raise GithubError("%s already has its pull request, %s" % (task, pr_url(t)))
    status, why = dfs_tree.status_of(t)
    if status != "done":
        raise GithubError("%s is not finished (%s): a pull request is opened for a finished task"
                          % (task, why))
    name, wt = dfs_paths.item_branch(task), dfs_paths.worktree(task)
    default = dfs_paths.branch()
    if wt is None or default not in dfs_paths.DEFAULT_BRANCHES:
        raise GithubError("%s has no worktree held for it, or this checkout is not on the "
                          "default branch (%s)" % (task, default))
    if dfs_paths.ignored():
        raise GithubError("the roadmap is gitignored, so the branch holds no task file to push "
                          "beside the code; GitHub mode needs `.dfs` committed")
    dfs_branch._commit_task_files(task, wt, "the log and edits left uncommitted")
    if not dfs_branch._rebase(task, wt, name):
        raise GithubError("%s could not be rebased onto %s; its tree has a node to settle that"
                          % (name, default))
    r = dfs_branch._git("push", "--force-with-lease", "-u", remote, name, cwd=wt)
    if r.returncode != 0:
        raise GithubError("git push %s %s failed: %s" % (remote, name, " ".join(dfs_branch.out_of(r).split())[:400]))
    title = "%s: %s" % (task, t["title"].lstrip("# ").split(" · ", 1)[-1])
    out = gh("pr", "create", "--base", default, "--head", name, "--title", title,
             "--body", dfs_branch._task_details(task), cwd=wt)
    url = next((w for w in out.split() if w.startswith("http")), "")
    if not url:
        raise GithubError("gh pr create printed no url: %s" % " ".join(out.split())[:200])
    dfs_tree.append_log(task, "pr", [url])
    return url


def _pull(url):
    """The pull request at `url` as (state, comments): its state (OPEN, MERGED or CLOSED), and
    every comment on it that asks something of the work, oldest first, as (kind, id, who, text,
    where, link): its conversation comments and reviews with a body (`gh pr view`) and its line
    comments (`gh api .../pulls/<n>/comments`)."""
    m = re.fullmatch(r"https://[^/]+/([^/]+)/([^/]+)/pull/(\d+)/?", url)
    if not m:
        raise GithubError("%s is not a pull request's url" % url)
    view = json.loads(gh("pr", "view", url, "--json", "comments,reviews,state") or "{}")
    lines = json.loads(gh("api", "repos/%s/%s/pulls/%s/comments" % m.groups()) or "[]")
    who = lambda c, key="author": "@" + ((c.get(key) or {}).get("login") or "someone")
    out = [("comment", c.get("id"), who(c), c.get("body"), "", c.get("url") or url)
           for c in view.get("comments") or []]
    out += [("review", c.get("id"), who(c), c.get("body"), "", url) for c in view.get("reviews") or []]
    out += [("line", c.get("id"), who(c, "user"), c.get("body"),
             "%s:%s" % (c.get("path"), c.get("line") or c.get("original_line") or "?"),
             c.get("html_url") or url) for c in lines]
    return view.get("state") or "OPEN", [c for c in out if c[1] and (c[3] or "").strip()]


def _pr_marks(t):
    return [e["args"] for e in t["log"] if e["kind"] == "pr"]


def _raise_once(task, tag, text):
    """Raise `text` unless the task already holds an unanswered raise carrying `tag`, which a
    watch repeating every few minutes would otherwise log again."""
    t = dfs_tree.load(task)
    for e in reversed(t["log"]):
        if e["kind"] == "answer":
            break
        if e["kind"] == "raise" and tag in e.get("body", ""):
            return
    dfs_tree.append_log(task, "raise", [], "%s\n(%s)" % (text, tag))


def closed(task, url):
    """A pull request closed without merging is the author's to settle: log it once as
    `pr <url> closed` and raise, asking whether to reopen it or drop the task."""
    if _pr_marks(dfs_tree.load(task))[-1:] == [[url, "closed"]]:
        return
    dfs_tree.append_log(task, "pr", [url, "closed"])
    dfs_tree.append_log(task, "raise", [], "The pull request %s was closed on GitHub without being "
                        "merged, so %s's branch is not in the default branch. Reopen the pull "
                        "request on GitHub and the watch carries on with it, or tell me to drop "
                        "the task (its worktree and branch are left as they are until then)."
                        % (url, task))


@dfs_branch.one_at_a_time
def land(task, url):
    """The pull request of `task` was merged on GitHub: bring the default branch up to it, then
    let go of the worktree and `dfs/<task>` and log `pr <url> merged`, so the task is done and
    merged. Fetches the remote and fast-forwards the default branch; that needs the checkout on
    it with no tracked change and no merge or rebase of the author's in progress, else it is a
    raise asking them to pull (once). The task's files are carried from the worktree, which holds
    the log entries no commit does, and committed on the default branch. A branch holding
    commits that never reached the pull request, or a worktree holding changes beyond the task's
    files, is kept as `dfs-merged/<task>`, not deleted."""
    remote = dfs_paths.read_github()
    name, wt, default = dfs_paths.item_branch(task), dfs_paths.worktree(task), dfs_paths.branch()
    tag = "merged PR " + url

    def pull_by_hand(why):
        _raise_once(task, tag, "The pull request %s was merged on GitHub, but %s was not brought "
                    "up to it: %s. Run `git pull --ff-only %s %s` in the main checkout and the "
                    "next watch lands %s." % (url, default, why, remote, default, task))

    if default not in dfs_paths.DEFAULT_BRANCHES:
        return pull_by_hand("the checkout is on %s" % default)
    if dfs_branch._in_progress():
        return pull_by_hand("a %s of yours is unfinished in the checkout" % dfs_branch._in_progress())
    if dfs_branch._git("status", "--porcelain", "--untracked-files=no").stdout.strip():
        return pull_by_hand("the checkout has uncommitted changes to tracked files")
    r = dfs_branch._git("fetch", remote)
    if r.returncode != 0:
        raise GithubError("git fetch %s failed: %s" % (remote, " ".join(dfs_branch.out_of(r).split())[:400]))
    r = dfs_branch._git("merge", "--ff-only", "%s/%s" % (remote, default))
    if r.returncode != 0:
        return pull_by_hand("it cannot be fast-forwarded to %s/%s (%s)" % (
            remote, default, " ".join(dfs_branch.out_of(r).split())[:200]))
    kept = ""
    if wt is not None:
        mine = dfs_branch.task_paths(task)
        stray = [l[3:] for l in dfs_branch._git("status", "--porcelain", cwd=wt).stdout.splitlines()
                 if not any(l[3:].strip('"') == p or l[3:].startswith(p.rstrip("/") + "/") for p in mine)]
        gone = dfs_branch._git("rev-list", name, "--not", default, "refs/remotes/%s/%s" % (remote, name))
        for p in dfs_branch._tracked_task_paths(task, wt):
            src, dst = wt / p, dfs_paths.work_root() / p
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        keep = bool(stray) or gone.returncode != 0 or bool(gone.stdout.split())
        if keep:
            dfs_branch.keep_leftover_work(task, "merged")
            kept = "; %s holds work the pull request lacks, kept as dfs-merged/%s" % (name, task)
        steps = [("its worktree", dfs_branch._git("worktree", "remove", "--force", str(wt))),
                 ("its branch", dfs_branch._git("branch", "-m", name, "dfs-merged/" + task) if keep
                  else dfs_branch._git("branch", "-D", name))]
        for what, r in steps:   # not `release`, which takes the lock `land` holds
            if r.returncode != 0:
                print("dfs_github: %s merged, but %s could not be removed: %s"
                      % (task, what, " ".join(dfs_branch.out_of(r).split())), file=sys.stderr)
    dfs_tree.append_log(task, "pr", [url, "merged"])
    try:
        dfs_branch._commit_task_files(task, dfs_paths.work_root(), "the pull request is merged")
    except dfs_branch.BranchError as e:
        print("dfs_github: %s: the log is left uncommitted: %s" % (task, e), file=sys.stderr)
    print("dfs_github: %s: %s merged on GitHub; %s is at %s/%s%s" % (task, url, default, remote, default, kept))


def watch(task):
    """Turn each comment on `task`'s pull request that the tree does not hold yet into an open
    node, at the root as a review's finding is, which reopens the task for the walk. A comment
    is known by the `(github <kind> <id>)` its node's Approach ends with. Returns the new ids.
    A pull request merged on GitHub is landed locally (`land`) and one closed unmerged is a
    raise (`closed`), and add nothing."""
    t = dfs_tree.load(task)
    url = pr_url(t)
    if not url or dfs_tree.pr_merged(t):
        return []
    state, comments = _pull(url)
    if state == "MERGED":
        land(task, url)
        return []
    if state == "CLOSED":
        closed(task, url)
        return []
    if _pr_marks(t)[-1:] == [[url, "closed"]]:
        dfs_tree.append_log(task, "pr", [url])   # reopened on GitHub: watched as before
    known = " ".join(nd["fields"].get("Approach", "") for nd in t["nodes"])
    added = []
    for kind, cid, who, text, where, link in comments:
        mark = "(github %s %s)" % (kind, cid)
        if mark in known:
            continue
        gist = " ".join(text.split())
        added.append(dfs_tree.add_node(
            task, "PR %s from %s: %s" % ("review" if kind == "review" else "comment", who,
                                         gist[:60] + ("…" if len(gist) > 60 else "")),
            approach="%s commented on the pull request%s (%s): %s Act on it as a reviewer's "
                     "finding: change the work, or raise if only the author can settle it. %s"
                     % (who, " at " + where if where else "", link, gist, mark)))
    return added


def watched():
    """The tasks GitHub mode watches: each with a `pr` entry whose pull request is not merged."""
    if not dfs_paths.read_github():
        return []
    out = []
    for task in dfs_tree.task_ids():
        t = dfs_tree.load(task)
        if pr_url(t) and not dfs_tree.pr_merged(t):
            out.append(task)
    return out


def main(argv):
    if len(argv) == 3 and argv[1] == "pr":
        try:
            url = open_pr(argv[2])
        except (GithubError, dfs_branch.BranchError) as e:
            print("dfs_github: %s" % e, file=sys.stderr)
            return 1
        print(url)
        return 0
    if len(argv) in (2, 3) and argv[1] == "watch":
        rc = 0
        for task in argv[2:] or watched():
            try:
                for nid in watch(task):
                    print("dfs_github: %s: %s from its pull request" % (task, nid))
            except (GithubError, ValueError) as e:
                print("dfs_github: %s: %s" % (task, e), file=sys.stderr)
                rc = 1
        return rc
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
