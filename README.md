# roadmap-dfs

DFS on your roadmap:
- Blockers are raised for your review
- Agent sessions are short and non-interactive, for token and cache efficiency
- A decision tree explains what happened, revert whole sequences

## Run it with nix

```sh
nix run github:rskew/roadmap-dfs#tui                 # the screen, in the repo you're standing in
nix run github:rskew/roadmap-dfs#sandbox -- claude   # sandbox.sh, with the dfs-* commands on the container's PATH
nix run github:rskew/roadmap-dfs#sandbox -- kiro     # the same, with kiro-cli
```

Chains run under Claude Code by default; `x` in the screen switches to codex or
kiro-cli, and `dfs-run --codex` / `dfs-run --kiro` do the same from the shell.
kiro-cli needs a login first: `KIRO_API_KEY` in the environment (sandbox.sh passes
it through), or `kiro-cli login --use-device-flow` once.

The package puts `dfs` (the screen), `dfs-run`, `dfs-init`, `dfs-hook`, `dfs-tree`
and the rest of `scripts/dfs_*` on PATH, one command per script.
