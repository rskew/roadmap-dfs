#!/usr/bin/env sh
# The roadmap guard, as a git hook calls it: `hook.sh pre-commit` or
# `hook.sh pre-merge-commit`. It judges the INDEX (`--staged`), which is what
# lands, and only when the commit touches `.dfs`. It WARNS on what it only warns
# about and exits 1 on a violation, which stops the commit.
#
# A merge that `git merge` commits itself runs pre-merge-commit and not pre-commit,
# so without the second hook a merged `.dfs` would land unjudged. Here HEAD is still
# ours and the index is the merged result, so the merge is checked against ours, and
# `--merge` says it is one: a merge brings in other branches' parts of a task, the one
# time a commit may add to a part that is not this branch's. git has not written
# MERGE_HEAD yet when this hook runs. A rebase, cherry-pick or revert runs neither
# hook and is not judged, which is why a `.dfs` branch is merged, never rebased.
#
# The project's own hook finds the tool and calls this (init.py writes one that
# does), or IS a link to it; what it checks lives here, with the tool, so it moves
# with it.
# Linked in as the hook itself, it takes the mode from its own name, since git passes
# pre-commit and pre-merge-commit no arguments.
self=$(readlink -f "$0" 2>/dev/null || echo "$0")
HERE=$(cd "$(dirname "$self")" && pwd)
mode="${1:-$(basename "$0")}"
cd "$(git rev-parse --show-toplevel)" || exit 0
case "$mode" in
  pre-commit)       base="";     extra="" ;;
  pre-merge-commit) base="HEAD"; extra="--merge" ;;
  *) echo "usage: hook.sh pre-commit | pre-merge-commit" >&2; exit 2 ;;
esac
# shellcheck disable=SC2086  # $base is empty or one word
# A gitignored .dfs is never staged, so a project keeping it as planning never gets here.
staged=$(git diff --cached --name-only --diff-filter=ACMRD $base -- .dfs)
[ -n "$staged" ] || exit 0
# shellcheck disable=SC2086
exec python3 "$HERE/check.py" --warn --staged $extra
