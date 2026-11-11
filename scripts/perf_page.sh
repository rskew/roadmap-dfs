#!/usr/bin/env bash
# Time the web page's client in headless chromium, against a COPY of this work root.
#
#   <scripts>/perf_page.sh [--task W130] [--cpu 4] [--runs 3]
#
# Copies the work root (minus .git) to a temporary directory, serves the copy with
# web.py for the length of the run only, runs perf_page.mjs against it, and stops the
# server. The push step rewrites mtimes in the copy, never in the real roadmap. Needs
# playwright, found as artefact_check.sh finds it (on PATH, else `nix shell`).
set -euo pipefail
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
HERE="$(dirname "$SELF")"
ROOT="${ARTEFACT_WORK_ROOT:-$PWD}"
export ARTEFACT_WORK_ROOT="$ROOT"

if ! command -v playwright >/dev/null 2>&1; then
  [ -z "${PERF_REENTERED:-}" ] || { echo "perf_page: no playwright in nixpkgs#playwright-test" >&2; exit 2; }
  export PERF_REENTERED=1
  exec nix shell nixpkgs#playwright-test -c "$SELF" "$@"
fi
pw="$(command -v playwright)"
node_bin="$(sed -n 's|^exec "\([^"]*\)".*|\1|p' "$pw" | head -1)"
node_path="$(sed -n "s|^NODE_PATH='\([^']*\)'\$NODE_PATH\$|\1|p" "$pw" | head -1)"
browsers="$(sed -n "s|.*PLAYWRIGHT_BROWSERS_PATH=\${PLAYWRIGHT_BROWSERS_PATH-'\([^']*\)'}.*|\1|p" "$pw" | head -1)"

COPY="$(mktemp -d)"
LOG="$COPY.web.log"
trap 'kill "${SRV:-}" 2>/dev/null || true; rm -rf "$COPY" "$LOG"' EXIT
(cd "$ROOT" && tar --exclude=.git -cf - .) | tar -xf - -C "$COPY"
(cd "$COPY" && git init -q && git add -A >/dev/null 2>&1 && git -c user.name=perf -c user.email=perf@localhost commit -qm copy)

TASK=W130
for ((i = 1; i <= $#; i++)); do [ "${!i}" = "--task" ] && { j=$((i + 1)); TASK="${!j}"; }; done
OWN="$(ls "$COPY"/.dfs/items/"$TASK".md)"
OTHER="$(ls "$COPY"/.dfs/items/*.md | grep -v "/$TASK.md" | sed -n 1p)"

(cd "$COPY" && exec python3 -u "$HERE/web.py" --no-trek --host 127.0.0.1 --port 0 >"$LOG" 2>&1) &
SRV=$!
URL=""
for _ in $(seq 1 100); do
  URL="$(sed -n 's/^roadmap web: //p' "$LOG" 2>/dev/null | head -1)"
  [ -n "$URL" ] && break
  sleep 0.1
done
[ -n "$URL" ] || { echo "perf_page: the server did not start:" >&2; cat "$LOG" >&2; exit 1; }
URL="${URL%/}"

NODE_PATH="${node_path}${NODE_PATH:+:$NODE_PATH}" PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH-$browsers}" \
  timeout 600 "$node_bin" "$HERE/perf_page.mjs" "$URL" "$OWN" --other "$OTHER" "$@"
