#!/usr/bin/env python3
"""GitHub mode: a finished item goes to a pull request instead of a local merge.

With `.dfs/github` written (`paths.read_github`, the remote to push to), `branch.merge`
holds a finished item, its branch and worktree kept. The author's Open PR mark is `pr`:

    python3 <scripts>/github.py pr <task>   # from the main checkout: rebase dfs/<task> onto
                                            # the default branch, push it, open its pull
                                            # request with `gh`, and log a `pr` entry with
                                            # the request's url

`gh` must be logged in (`gh auth login`); `DFS_GH` names another command to run as it
(the tests' fake). See README, "GitHub mode".
"""
import os
import shlex
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


def pr_url(t):
    """The url of the task's pull request, from its last `pr` entry; "" when none is open."""
    marks = [e for e in t["log"] if e["kind"] == "pr"]
    return marks[-1]["args"][0] if marks and marks[-1]["args"] else ""


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


def main(argv):
    if len(argv) == 3 and argv[1] == "pr":
        try:
            url = open_pr(argv[2])
        except (GithubError, dfs_branch.BranchError) as e:
            print("dfs_github: %s" % e, file=sys.stderr)
            return 1
        print(url)
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
