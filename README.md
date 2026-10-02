# roadmap-dfs

DFS on your roadmap:
- Blockers are raised for your review
- Agent sessions are short and non-interactive, for token and cache efficiency
- A decision tree explains what happened, revert whole sequences

## Run it with nix

```sh
nix run github:rskew/roadmap-dfs#init                    # start a roadmap in the repo you're standing in
nix run github:rskew/roadmap-dfs                         # the screen (tui), there
nix run github:rskew/roadmap-dfs#sandbox -- claude       # a container, with the tool's commands on its PATH
nix run github:rskew/roadmap-dfs#sandbox -- kiro         # the same, with kiro-cli
nix run github:rskew/roadmap-dfs#sandbox -- opencode     # the same, with opencode
```

Chains run under Claude Code by default; `x` in the screen switches to codex,
kiro-cli or opencode (and the screen remembers the choice next time), and
`run --codex` / `run --kiro` / `run --opencode` do the same from the shell.
kiro-cli runs its `--v3` agent, and needs a login first: `KIRO_API_KEY` in the
environment (`sandbox` passes it through), or `kiro-cli login --use-device-flow` once.

opencode is not told which model to use: it reads its own config, so set the default
once there. `~/.config/opencode/opencode.json` (or an `opencode.json` in the project,
or the file `OPENCODE_CONFIG` names) takes `"model": "provider/model"`, and a provider's
endpoint under `provider.<id>.options.baseURL` (an OpenAI-compatible one uses
`"npm": "@ai-sdk/openai-compatible"` and lists its `models`). Credentials come from
`opencode auth login` (kept in `~/.local/share/opencode/auth.json`), or an environment
variable the config reads as `"apiKey": "{env:NAME}"`. With no config at all it falls
back to its own free hosted models. One run can override with
`OPENCODE_CMD="opencode -m provider/model" run --opencode ...`. `sandbox` mounts
that config and login, and passes `OPENCODE_CONFIG` and `OPENCODE_CONFIG_CONTENT`
through; a key the config reads with `{env:NAME}` goes in through `CONTAINER_ENV`.

The container cannot reach the host's localhost, so an ollama there is given to it as a
unix socket, which is all of the host it can talk to:

```sh
socat UNIX-LISTEN:$XDG_RUNTIME_DIR/ollama.sock,fork,mode=600 TCP:127.0.0.1:11434 &
OLLAMA_SOCKET=$XDG_RUNTIME_DIR/ollama.sock nix run github:rskew/roadmap-dfs#sandbox -- opencode
```

The container listens on its own `127.0.0.1:11434` and forwards to the socket, so the
same `baseURL: http://127.0.0.1:11434/v1` works on the host and in the sandbox
(`CONTAINER_OLLAMA_PORT` changes the port).

One name per command: `scripts/foo.py` (or `.sh`) is the command `foo` in
the package and the app `#foo` in the flake, for every script that runs on its
own (`init`, `run`, `tui`, `sandbox`, …). Installed, they are all on PATH; with nothing installed, `init` writes
hooks and prints next steps that go through the flake (`DFS_FLAKE` names another).
