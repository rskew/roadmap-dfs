#!/usr/bin/env bash
# Render a roadmap artefact and check it is readable (dfs_artefact_check.mjs).
#
#   <scripts>/dfs_artefact_check.sh .dfs/artefacts/<uuid>.html
#
# Needs `playwright` (the nixpkgs `playwright-test` wrapper), found in this order:
#   1. on PATH;
#   2. in the nix dev shell DFS_PLAYWRIGHT_SHELL names, else the one named by the
#      project's `.dfs/playwright-shell` (one line, e.g. `.#e2e`, relative to the
#      work root), when the project's own shell carries fonts or browsers it wants;
#   3. `nix shell nixpkgs#playwright-test`.
# The wrapper has no `node` on PATH beside it, but it names the node it runs and
# the NODE_PATH it needs, so both are read out of it rather than guessed at or
# pinned to a store path that moves.
#
# ⚠️ TWO ROOTS, and they are different questions (dfs_paths.py): this SCRIPT is
# the tool's, wherever roadmap-dfs was installed, while a dev shell that supplies
# playwright belongs to the repo being worked on — which is the CWD, like everywhere
# else here. Run it from the work root.
set -euo pipefail

SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
TOOL_ROOT="$(dirname "$(dirname "$SELF")")"
# ⚠️ Carried across the re-exec below, not re-read: a dev shell's hook may cd
# elsewhere (shop-app's e2e shell cds two directories down), and every path this
# prints would then come out as `../../.dfs/<path>`.
REPO_ROOT="${ARTEFACT_WORK_ROOT:-$PWD}"
export ARTEFACT_WORK_ROOT="$REPO_ROOT"

# Absolute before any re-exec, for the same reason.
args=()
for a in "$@"; do
  if [ -f "$a" ]; then args+=("$(cd "$(dirname "$a")" && pwd)/$(basename "$a")"); else args+=("$a"); fi
done

if ! command -v playwright >/dev/null 2>&1; then
  if [ -n "${ARTEFACT_CHECK_REENTERED:-}" ]; then
    echo "dfs_artefact_check: no playwright inside ${ARTEFACT_CHECK_REENTERED}" >&2
    exit 2
  fi
  shell="${DFS_PLAYWRIGHT_SHELL:-}"
  if [ -z "$shell" ]; then
    eval "$(cd "$REPO_ROOT" && python3 "$TOOL_ROOT/scripts/dfs_paths.py" --sh)"
    if [ -f "$DFS_PLAYWRIGHT_SHELL_FILE" ]; then
      shell="$(sed -n '/[^[:space:]]/{s/^[[:space:]]*//;s/[[:space:]]*$//;p;q;}' "$DFS_PLAYWRIGHT_SHELL_FILE")"
    fi
  fi
  if [ -n "$shell" ]; then
    # A flake ref relative to the work root, as the project wrote it.
    case "$shell" in
      .\#*) shell="${REPO_ROOT}${shell#.}" ;;
      ./*)  shell="${REPO_ROOT}/${shell#./}" ;;
    esac
    export ARTEFACT_CHECK_REENTERED="nix develop $shell"
    exec nix develop "$shell" -c "$SELF" "${args[@]}"
  fi
  export ARTEFACT_CHECK_REENTERED="nixpkgs#playwright-test"
  exec nix shell nixpkgs#playwright-test -c "$SELF" "${args[@]}"
fi

pw="$(command -v playwright)"
node_bin="$(sed -n 's|^exec "\([^"]*\)".*|\1|p' "$pw" | head -1)"
node_path="$(sed -n "s|^NODE_PATH='\([^']*\)'\$NODE_PATH\$|\1|p" "$pw" | head -1)"
browsers="$(sed -n "s|.*PLAYWRIGHT_BROWSERS_PATH=\${PLAYWRIGHT_BROWSERS_PATH-'\([^']*\)'}.*|\1|p" "$pw" | head -1)"
[ -x "$node_bin" ] || { echo "dfs_artefact_check: could not read node out of $pw" >&2; exit 2; }

ARTEFACT_REPO_ROOT="$REPO_ROOT" \
 NODE_PATH="${node_path}${NODE_PATH:+:$NODE_PATH}" \
PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH-$browsers}" \
  exec "$node_bin" "${TOOL_ROOT}/scripts/dfs_artefact_check.mjs" "${args[@]}"
