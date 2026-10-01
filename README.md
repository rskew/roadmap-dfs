# roadmap-dfs

DFS on your roadmap:
- Blockers are raised for your review
- Agent sessions are short and non-interactive, for token and cache efficiency
- A decision tree explains what happened, revert whole sequences

## Run it with nix

```sh
nix run github:rskew/roadmap-dfs#dfs-init                # start a roadmap in the repo you're standing in
nix run github:rskew/roadmap-dfs                         # the screen (dfs-tui), there
nix run github:rskew/roadmap-dfs#dfs-sandbox -- claude   # a container, with the dfs-* commands on its PATH
nix run github:rskew/roadmap-dfs#dfs-sandbox -- kiro     # the same, with kiro-cli
```

Chains run under Claude Code by default; `x` in the screen switches to codex or
kiro-cli, and `dfs-run --codex` / `dfs-run --kiro` do the same from the shell.
kiro-cli needs a login first: `KIRO_API_KEY` in the environment (dfs-sandbox passes
it through), or `kiro-cli login --use-device-flow` once.

One name per command: `scripts/dfs_foo.py` (or `.sh`) is the command `dfs-foo` in
the package and the app `#dfs-foo` in the flake, for every script that runs on its
own. Installed, they are all on PATH; with nothing installed, `dfs-init` writes
hooks and prints next steps that go through the flake (`DFS_FLAKE` names another).
