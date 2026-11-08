#!/bin/sh
# Build the two tool directories a W153 replay arm runs under, from this repo's HEAD:
#   $1/control  the tool as committed (what a control arm runs)
#   $1/pooled   the same plus docs/dfs-w153-pooled-tool.diff (E1, E2, E6, E7 pooled)
# Usage: sh docs/dfs-w153-build-tool.sh DIR     (DIR on the repo's mount; replay.py prepare --tool DIR/pooled)
set -e
out=$1; [ -n "$out" ] || { echo "usage: $0 DIR" >&2; exit 2; }
here=$(cd "$(dirname "$0")/.." && pwd)
rm -rf "$out/control" "$out/pooled"; mkdir -p "$out/control" "$out/pooled"
for d in control pooled; do git -C "$here" archive HEAD scripts SKILL.md templates flake.nix README.md | tar -x -C "$out/$d"; done
(cd "$out/pooled" && patch -p1 -s < "$here/docs/dfs-w153-pooled-tool.diff")
echo "built $out/control and $out/pooled"
