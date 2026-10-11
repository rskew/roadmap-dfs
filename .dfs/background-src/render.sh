#!/usr/bin/env bash
# bash render.sh <in.svg> <out.png>: draw an svg with headless chromium (render.mjs).
# Needs the nixpkgs `playwright-test` wrapper, which names the node and NODE_PATH it runs; both are read out of it.
set -euo pipefail
if ! command -v playwright >/dev/null 2>&1; then exec nix shell nixpkgs#playwright-test -c "$(readlink -f "$0")" "$@"; fi
pw="$(command -v playwright)"
node_bin="$(sed -n 's|^exec "\([^"]*\)".*|\1|p' "$pw" | head -1)"
node_path="$(sed -n "s|^NODE_PATH='\([^']*\)'\$NODE_PATH\$|\1|p" "$pw" | head -1)"
browsers="$(sed -n "s|.*PLAYWRIGHT_BROWSERS_PATH=\${PLAYWRIGHT_BROWSERS_PATH-'\([^']*\)'}.*|\1|p" "$pw" | head -1)"
# the wrapper's text is parsed, so say so plainly if its format has changed rather than render with nothing
for v in node_bin node_path browsers; do [ -n "${!v}" ] || { echo "render.sh: could not read $v out of $pw; the playwright-test wrapper's format changed" >&2; exit 1; }; done
NODE_PATH="${node_path}${NODE_PATH:+:$NODE_PATH}" PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH-$browsers}" \
  exec "$node_bin" "$(dirname "$(readlink -f "$0")")/render.mjs" "$(readlink -f "$1")" "$(readlink -f -m "$2")"
