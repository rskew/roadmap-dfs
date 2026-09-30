# roadmap-dfs

DFS on your roadmap:
- Blockers are raised for your review
- Agent sessions are short and non-interactive, for token and cache efficiency
- A decision tree explains what happened, revert whole sequences

## Run it with nix

```sh
nix run github:rskew/roadmap-dfs#tui                 # the screen, in the repo you're standing in
nix run github:rskew/roadmap-dfs#sandbox -- claude   # sandbox.sh, with the dfs-* commands on the container's PATH
```

The package puts `dfs` (the screen), `dfs-run`, `dfs-init`, `dfs-hook`, `dfs-tree`
and the rest of `scripts/dfs_*` on PATH, one command per script.
