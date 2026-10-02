#!/usr/bin/env bash

# Run any app (or agent) in a throwaway container against the current repo,
# alongside a Claude Code session running on the host.
#
# The container gets the host's nix store and nix daemon, so `nix develop` and
# flakes work inside it exactly as they do on the host. The repo is bind
# mounted, so edits made on the host are visible immediately.
#
# The daemon is reached through a proxy sidecar, never directly. The container
# runs as the host user, whom the host daemon may trust, and a trusted nix user
# is root-equivalent (unsigned store paths, `--option sandbox false`, arbitrary
# substituters). The sidecar runs `nix daemon --force-untrusted`: it serves the
# container as an untrusted client and forwards only what that allows to the
# host daemon. The real socket is mounted into the sidecar alone.
#
# The agent modes (and shell) start the app in the background first, so the
# agent works against a running backend. A repo declares how in a .container.env
# file at its root (see below); CONTAINER_APP_CMD overrides it ad hoc.
#
# Usage:
#   sandbox shell                  App in background, interactive bash
#   sandbox claude [args...]       App in background, then claude
#   sandbox codex [args...]        App in background, then codex
#   sandbox gemini [args...]       App in background, then gemini
#   sandbox kiro [args...]         App in background, then kiro-cli
#   sandbox opencode [args...]     App in background, then opencode
#   sandbox app                    Just the app, in the foreground
#   sandbox exec <command> [args]  App in background, then any command
#   sandbox run <command> [args]   Any command, app NOT started
#
# Environment:
#   CONTAINER_ENGINE   docker|podman                     (default: docker)
#   CONTAINER_IMAGE    base image                        (default: ubuntu:24.04)
#   CONTAINER_REPO     repo root to mount                (default: git toplevel of cwd)
#   CONTAINER_NAME     container name                    (default: <repo>-dev)
#   CONTAINER_CONFIG   repo config file to read          (default: <repo>/.container.env)
#   CONTAINER_APP_CMD  how to start the app, run via bash -lc. Defaults to
#                      ./scripts/start-dev.sh when that exists in the repo;
#                      if neither is set, no app is started.
#   CONTAINER_APP_READY_PORT  wait for this port to accept connections before
#                      handing over to the agent
#   CONTAINER_APP_READY_TIMEOUT  seconds to wait for that port (default: 300)
#   CONTAINER_PORTS    ports to publish, e.g. "8006 5432" or "8080:80"
#   CONTAINER_MOUNTS   extra mounts, e.g. "/dev/ttyUSB0:/dev/ttyUSB0"
#   CONTAINER_ENV      extra env vars, e.g. "FOO=bar BAZ=qux"
#   CONTAINER_PATH_PREFIX  prepended to the container's PATH, e.g. a store path's
#                      directory of `dfs-<name>` links (the flake's `sandbox` puts the tool's commands there)
#   TERM COLORTERM COLORFGBG NO_COLOR DFS_THEME  passed through when set, so a screen
#                      looks as it does outside the container
#   CONTAINER_SHM_SIZE                                   (default: 1g)
#   CONTAINER_APPARMOR_MODE  default|unconfined          (default: default)
#   KIRO_API_KEY       passed through when set, so kiro-cli runs headless without
#                      a login (otherwise: `kiro-cli login --use-device-flow` once,
#                      on the host or in `sandbox kiro`)
#   opencode           reads its model, provider and endpoint from ~/.config/opencode/
#                      and its login from ~/.local/share/opencode/auth.json; both are
#                      mounted when they exist. A provider key that config reads as
#                      {env:NAME} goes in through CONTAINER_ENV. OPENCODE_CONFIG and
#                      OPENCODE_CONFIG_CONTENT are passed through by name when set.
#                      An endpoint on the host's own localhost is not reachable from
#                      the container — unless it is given by socket:
#   OLLAMA_SOCKET      a unix socket on the host that reaches ollama, e.g. made with
#                        socat UNIX-LISTEN:$XDG_RUNTIME_DIR/ollama.sock,fork,mode=600 TCP:127.0.0.1:11434
#                      It is mounted as /run/ollama.sock, and the container listens on its
#                      own 127.0.0.1:11434 (CONTAINER_OLLAMA_PORT) and forwards to it, so
#                      a config that says http://127.0.0.1:11434/v1 works here and on the
#                      host. Nothing else of the host's network is reachable. Restart the
#                      sandbox if you restart that socat: the mount follows the old file.
#
# Example — put this in <repo>/.container.env:
#   CONTAINER_APP_CMD=scripts/start-dev.sh
#   CONTAINER_PORTS=8006
#   CONTAINER_APP_READY_PORT=8006
# then `sandbox claude` from the repo runs claude with the app already up.
#
# The app's output goes to $HOME/app.log inside the container. In these modes it has
# no terminal (no stdin, no controlling tty, PC_DISABLE_TUI=1 for process-compose),
# because the agent or shell owns it; `app` mode runs it in the foreground, with one.

set -euo pipefail

# The script lives outside any repo, so the repo comes from where it is run.
repo_root() {
  if [[ -n "${CONTAINER_REPO:-}" ]]; then
    (cd "${CONTAINER_REPO}" && pwd)
  elif git rev-parse --show-toplevel >/dev/null 2>&1; then
    git rev-parse --show-toplevel
  else
    pwd
  fi
}

REPO_ROOT="$(repo_root)"
REPO_NAME="$(basename "${REPO_ROOT}")"

# A repo declares its dev setup in .container.env at its root: KEY=value lines
# using the CONTAINER_* names below (e.g. CONTAINER_APP_CMD=scripts/start-dev.sh,
# CONTAINER_PORTS=8006). It is read, not sourced, so it can't run code and
# values need no quoting. Precedence: a value already in the environment wins
# over the file, which wins over the built-in defaults.
CONTAINER_CONFIG="${CONTAINER_CONFIG:-${REPO_ROOT}/.container.env}"
if [[ -f "${CONTAINER_CONFIG}" ]]; then
  while IFS= read -r line || [[ -n "${line}" ]]; do
    line="${line%$'\r'}"
    [[ -z "${line}" || "${line}" == '#'* ]] && continue
    [[ "${line}" == *=* ]] || continue
    key="${line%%=*}"
    key="${key//[[:space:]]/}"
    [[ "${key}" == CONTAINER_* ]] || continue
    # Only apply if the caller didn't already set it in the environment.
    [[ -n "${!key+set}" ]] && continue
    printf -v "${key}" '%s' "${line#*=}"
    export "${key}"
  done < "${CONTAINER_CONFIG}"
fi

CONTAINER_ENGINE="${CONTAINER_ENGINE:-docker}"
CONTAINER_IMAGE="${CONTAINER_IMAGE:-ubuntu:24.04}"
CONTAINER_SHM_SIZE="${CONTAINER_SHM_SIZE:-1g}"
CONTAINER_APPARMOR_MODE="${CONTAINER_APPARMOR_MODE:-default}"
CONTAINER_PORTS="${CONTAINER_PORTS:-}"
CONTAINER_MOUNTS="${CONTAINER_MOUNTS:-}"
CONTAINER_ENV="${CONTAINER_ENV:-}"
CONTAINER_PATH_PREFIX="${CONTAINER_PATH_PREFIX:-}"
CONTAINER_APP_CMD="${CONTAINER_APP_CMD:-}"
CONTAINER_APP_READY_PORT="${CONTAINER_APP_READY_PORT:-}"
CONTAINER_APP_READY_TIMEOUT="${CONTAINER_APP_READY_TIMEOUT:-300}"
# Set by the modes that want the app running alongside them.
CONTAINER_START_APP=0

CONTAINER_NAME="${CONTAINER_NAME:-${REPO_NAME}-dev}"
CONTAINER_HOME="${CONTAINER_HOME:-/tmp/${REPO_NAME}-home}"
CONTAINER_WORKDIR="/workspace/${REPO_NAME}"
CONTAINER_STATE_DIR="${REPO_ROOT}/.container-state"

# If nothing named an app command, fall back to a conventional dev script.
if [[ -z "${CONTAINER_APP_CMD}" && -x "${REPO_ROOT}/scripts/start-dev.sh" ]]; then
  CONTAINER_APP_CMD="scripts/start-dev.sh"
fi

host_nix_bin_dir() {
  dirname "$(command -v nix)"
}

host_ca_bundle() {
  local candidate
  for candidate in \
    /etc/ssl/certs/ca-bundle.crt \
    /etc/ssl/certs/ca-certificates.crt \
    /etc/pki/tls/certs/ca-bundle.crt
  do
    if [[ -f "${candidate}" ]]; then
      printf '%s\n' "${candidate}"
      return
    fi
  done
  echo "Host CA bundle not found in expected locations" >&2
  exit 1
}

host_locale_archive() {
  local candidate
  for candidate in \
    /run/current-system/sw/lib/locale/locale-archive \
    /usr/lib/locale/locale-archive \
    /lib/locale/locale-archive
  do
    if [[ -f "${candidate}" ]]; then
      printf '%s\n' "${candidate}"
      return
    fi
  done
  echo "Host locale archive not found in expected locations" >&2
  exit 1
}

require_prereqs() {
  [[ "$(uname -s)" == "Linux" ]] || { echo "This container workflow is Linux-only" >&2; exit 1; }
  command -v "${CONTAINER_ENGINE}" >/dev/null 2>&1 || { echo "${CONTAINER_ENGINE} not found" >&2; exit 1; }
  command -v nix >/dev/null 2>&1 || { echo "Host nix client not found on PATH" >&2; exit 1; }
  [[ -S /nix/var/nix/daemon-socket/socket ]] || { echo "Nix daemon socket not found at /nix/var/nix/daemon-socket/socket" >&2; exit 1; }
  mkdir -p "${CONTAINER_STATE_DIR}/nix-cache"
}

NIX_PROXY_NAME="${CONTAINER_NAME}-nix"
NIX_PROXY_DIR=""

# Starts the sidecar and sets NIX_PROXY_DIR to the host directory holding the
# socket it serves. Its own nix config is pinned empty: the host daemon trusts
# the sidecar, so any setting it carried would reach the host with that trust.
start_nix_proxy() {
  local nix_bin_dir
  nix_bin_dir="$(host_nix_bin_dir)"
  NIX_PROXY_DIR="$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/${NIX_PROXY_NAME}.XXXXXX")"
  trap stop_nix_proxy EXIT

  "${CONTAINER_ENGINE}" run -d --rm --init \
    --name "${NIX_PROXY_NAME}" \
    --user "$(id -u):$(id -g)" \
    --read-only \
    --tmpfs /tmp \
    --cap-drop ALL \
    --security-opt no-new-privileges \
    --network none \
    -e HOME=/nonexistent \
    -e NIX_CONF_DIR=/nonexistent \
    -e XDG_CONFIG_HOME=/nonexistent \
    -e XDG_CONFIG_DIRS=/nonexistent \
    -e NIX_REMOTE=unix:///host-daemon/socket \
    -e NIX_DAEMON_SOCKET_PATH=/proxy/socket \
    -v /nix/store:/nix/store:ro \
    -v "${nix_bin_dir}:/host-nix-bin:ro" \
    -v /nix/var/nix/daemon-socket/socket:/host-daemon/socket \
    -v "${NIX_PROXY_DIR}:/proxy" \
    "${CONTAINER_IMAGE}" \
    /host-nix-bin/nix daemon \
      --extra-experimental-features "nix-command daemon-trust-override" \
      --force-untrusted \
    >/dev/null

  local _
  for _ in $(seq 1 50); do
    [[ -S "${NIX_PROXY_DIR}/socket" ]] && return
    sleep 0.2
  done
  echo "[container] nix proxy sidecar did not come up; its output:" >&2
  "${CONTAINER_ENGINE}" logs "${NIX_PROXY_NAME}" >&2 || true
  exit 1
}

stop_nix_proxy() {
  "${CONTAINER_ENGINE}" rm -f "${NIX_PROXY_NAME}" >/dev/null 2>&1 || true
  [[ -n "${NIX_PROXY_DIR}" ]] && rm -rf "${NIX_PROXY_DIR}"
}

run_in_container() {
  local ca_bundle locale_archive nix_bin_dir port mount env_pair
  local -a docker_args
  ca_bundle="$(host_ca_bundle)"
  locale_archive="$(host_locale_archive)"
  nix_bin_dir="$(host_nix_bin_dir)"

  start_nix_proxy

  docker_args=(run --rm --init)
  # Only ask for a TTY when there is one, so the script also works when driven
  # from a script, CI, or an agent session.
  if [[ -t 0 ]]; then
    docker_args+=(-it)
  fi

  docker_args+=(
    --name "${CONTAINER_NAME}"
    --user "$(id -u):$(id -g)"
    --workdir "${CONTAINER_WORKDIR}"
    --read-only
    --shm-size "${CONTAINER_SHM_SIZE}"
    --tmpfs /tmp
    --tmpfs /run
    --tmpfs "${CONTAINER_HOME}:uid=$(id -u),gid=$(id -g),mode=700"
    --cap-drop ALL
    -e HOME="${CONTAINER_HOME}"
    -e XDG_CACHE_HOME="${CONTAINER_HOME}/.cache"
    -e XDG_CONFIG_HOME="${CONTAINER_HOME}/.config"
    -e LANG="C.UTF-8"
    -e LC_ALL="C.UTF-8"
    -e LOCALE_ARCHIVE="/usr/lib/locale/locale-archive"
    -e SSL_CERT_FILE="/etc/ssl/certs/host-ca-bundle.crt"
    -e NIX_SSL_CERT_FILE="/etc/ssl/certs/host-ca-bundle.crt"
    -e PATH="${CONTAINER_PATH_PREFIX:+${CONTAINER_PATH_PREFIX}:}/host-nix-bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    -e NIX_CONFIG="experimental-features = nix-command flakes"
    -e CONTAINER_START_APP="${CONTAINER_START_APP}"
    -e CONTAINER_APP_CMD="${CONTAINER_APP_CMD}"
    -e CONTAINER_APP_READY_PORT="${CONTAINER_APP_READY_PORT}"
    -e CONTAINER_APP_READY_TIMEOUT="${CONTAINER_APP_READY_TIMEOUT}"
    -v "${REPO_ROOT}:${CONTAINER_WORKDIR}"
    --tmpfs "${CONTAINER_HOME}/.cache:uid=$(id -u),gid=$(id -g),mode=700"
    -v "${CONTAINER_STATE_DIR}/nix-cache:${CONTAINER_HOME}/.cache/nix"
    -v /nix/store:/nix/store:ro
    -v "${nix_bin_dir}:/host-nix-bin:ro"
    -v "${ca_bundle}:/etc/ssl/certs/host-ca-bundle.crt:ro"
    -v "${locale_archive}:/usr/lib/locale/locale-archive:ro"
    -v "${NIX_PROXY_DIR}/socket:/nix/var/nix/daemon-socket/socket"
  )

  # "8006" publishes 8006:8006; "8080:80" is passed through as given.
  for port in ${CONTAINER_PORTS}; do
    if [[ "${port}" == *:* ]]; then
      docker_args+=(-p "${port}")
    else
      docker_args+=(-p "${port}:${port}")
    fi
  done

  for mount in ${CONTAINER_MOUNTS}; do
    docker_args+=(-v "${mount}")
  done

  for env_pair in ${CONTAINER_ENV}; do
    docker_args+=(-e "${env_pair}")
  done

  # A checkout of roadmap-dfs in the home directory, when there is one. Mounting a
  # missing path would have docker create it, owned by root.
  if [[ -d "${HOME}/roadmap-dfs" ]]; then
    docker_args+=(-v "${HOME}/roadmap-dfs:/roadmap-dfs")
  fi

  # Agent config is only mounted when it exists on the host. kiro-cli keeps its
  # settings and agents in ~/.kiro.
  local cfg
  for cfg in .codex .claude .claude.json .gemini .kiro; do
    if [[ -e "${HOME}/${cfg}" ]]; then
      docker_args+=(-v "${HOME}/${cfg}:${CONTAINER_HOME}/${cfg}")
    fi
  done

  # kiro-cli keeps its login in ~/.local/share/kiro-cli/data.sqlite3, and opencode its
  # in ~/.local/share/opencode (auth.json beside its session database) and its config
  # in ~/.config/opencode. A bind mount that deep would have the engine create
  # ~/.local, ~/.local/share and ~/.config owned by root, and every other tool
  # writing there (claude among them) would fail, so each parent that is needed gets
  # a user-owned tmpfs of its own first. ~/.cache is given one above, for the same
  # reason: opencode keeps its caches there and cannot create them in a root-owned dir.
  local -a deep=() parents=()
  local d
  for d in .local/share/kiro-cli .local/share/opencode .config/opencode; do
    if [[ -d "${HOME}/${d}" ]]; then
      deep+=("${d}")
      case "${d}" in
        .local/*) parents+=(.local .local/share) ;;
        .config/*) parents+=(.config) ;;
      esac
    fi
  done
  for d in $(printf '%s\n' ${parents[@]+"${parents[@]}"} | awk '!seen[$0]++'); do
    docker_args+=(--tmpfs "${CONTAINER_HOME}/${d}:uid=$(id -u),gid=$(id -g),mode=700")
  done
  for d in ${deep[@]+"${deep[@]}"}; do
    docker_args+=(-v "${HOME}/${d}:${CONTAINER_HOME}/${d}")
  done

  # The terminal's own settings, by name. `docker run -t` sets TERM=xterm whatever the
  # host has, which is eight colours and no selection band: the same screen drawn from
  # a plainer palette than outside the container. COLORTERM and COLORFGBG are how a
  # program learns the colours and the light or dark background, and NO_COLOR and
  # DFS_THEME are the author's own say about both. (A TERM the container has no
  # terminfo entry for, e.g. a newer terminal's own, makes curses fall back or fail:
  # set TERM=xterm-256color for the run then.)
  for name in TERM COLORTERM COLORFGBG NO_COLOR DFS_THEME; do
    if [[ -n "${!name:-}" ]]; then
      docker_args+=(-e "${name}")
    fi
  done
  # By name only, so the key's value never lands on a command line.
  if [[ -n "${KIRO_API_KEY:-}" ]]; then
    docker_args+=(-e KIRO_API_KEY)
  fi
  # An ollama on the host, reached by socket rather than by the network: one file is
  # all of the host the container can talk to. The bootstrap listens for it.
  if [[ -n "${OLLAMA_SOCKET:-}" ]]; then
    [[ -S "${OLLAMA_SOCKET}" ]] || { echo "OLLAMA_SOCKET ${OLLAMA_SOCKET} is not a unix socket (is its socat running?)" >&2; exit 1; }
    docker_args+=(-v "${OLLAMA_SOCKET}:/run/ollama.sock" -e "CONTAINER_OLLAMA_PORT=${CONTAINER_OLLAMA_PORT:-11434}")
  fi
  local name
  for name in OPENCODE_CONFIG OPENCODE_CONFIG_CONTENT; do
    if [[ -n "${!name:-}" ]]; then
      docker_args+=(-e "${name}")
    fi
  done

  if [[ "${CONTAINER_APPARMOR_MODE}" == "unconfined" ]]; then
    docker_args+=(--security-opt apparmor=unconfined)
  fi

  docker_args+=(
    "${CONTAINER_IMAGE}"
    bash -lc "${CONTAINER_BOOTSTRAP}" --
    "$@"
  )

  # Not exec'd: the EXIT trap has to outlive the container to stop the sidecar.
  "${CONTAINER_ENGINE}" "${docker_args[@]}"
}

# Runs inside the container. When a mode asked for the app (CONTAINER_START_APP=1)
# and there is a command to run it, start it in the background, optionally wait
# for its port, then hand over to the agent/shell. Single-quoted so nothing is
# expanded on the host; it reads the CONTAINER_* env vars passed with -e.
read -r -d '' CONTAINER_BOOTSTRAP <<'BOOTSTRAP' || true
set -euo pipefail
mkdir -p "$HOME" "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME"

if [[ "${CONTAINER_START_APP:-0}" == "1" && -n "${CONTAINER_APP_CMD:-}" ]]; then
  echo "[container] starting app: ${CONTAINER_APP_CMD}" >&2
  # ⚠️ NOT ON THE TERMINAL. The agent or shell that follows owns it, and an app that
  # also reads keys from it (process-compose's TUI, anything curses) fought it for
  # the foreground, with about one key in five reaching either. So: no stdin, a
  # session of its own (`setsid`: no controlling terminal to open), and
  # process-compose told to run headless, its output going to app.log with the rest.
  # `dfs-sandbox app` still gives an app the terminal, in the foreground.
  export PC_DISABLE_TUI="${PC_DISABLE_TUI:-1}"
  setsid bash -lc "${CONTAINER_APP_CMD}" </dev/null >"$HOME/app.log" 2>&1 &
  app_pid=$!

  if [[ -n "${CONTAINER_APP_READY_PORT:-}" ]]; then
    echo "[container] waiting up to ${CONTAINER_APP_READY_TIMEOUT:-300}s for port ${CONTAINER_APP_READY_PORT}..." >&2
    ready=0
    for _ in $(seq 1 "${CONTAINER_APP_READY_TIMEOUT:-300}"); do
      if ! kill -0 "$app_pid" 2>/dev/null; then
        echo "[container] app exited before opening the port; last output:" >&2
        tail -n 40 "$HOME/app.log" >&2 || true
        break
      fi
      if (exec 3<>"/dev/tcp/127.0.0.1/${CONTAINER_APP_READY_PORT}") 2>/dev/null; then
        exec 3>&- 3<&- || true
        echo "[container] port ${CONTAINER_APP_READY_PORT} is up (app.log at \$HOME/app.log)" >&2
        ready=1
        break
      fi
      sleep 1
    done
    [[ "$ready" == "1" ]] || echo "[container] carrying on without a confirmed port; check \$HOME/app.log" >&2
  fi
fi

# The container's side of OLLAMA_SOCKET: loopback TCP to the mounted socket, so that a
# provider's baseURL can stay http://127.0.0.1:<port>/... as it is on the host.
if [[ -S /run/ollama.sock ]]; then
  port="${CONTAINER_OLLAMA_PORT:-11434}"
  nix shell github:NixOS/nixpkgs#socat -c socat "TCP-LISTEN:${port},bind=127.0.0.1,fork,reuseaddr" UNIX-CONNECT:/run/ollama.sock >"$HOME/ollama-forward.log" 2>&1 &
  fwd_pid=$!
  for _ in $(seq 1 120); do
    kill -0 "$fwd_pid" 2>/dev/null || { echo "[container] ollama forwarder exited; see \$HOME/ollama-forward.log" >&2; break; }
    (exec 3<>"/dev/tcp/127.0.0.1/${port}") 2>/dev/null && { exec 3>&- 3<&- || true; break; }
    sleep 0.5
  done
fi

exec "$@"
BOOTSTRAP

usage() {
  # Print the header comment block (everything after the shebang up to the
  # first non-comment line), stripping the leading "# ".
  awk 'NR>2 && /^#/ { sub(/^# ?/, ""); print; next } NR>2 { exit }' "${BASH_SOURCE[0]}"
}

main() {
  local mode="${1:-shell}"
  if [[ $# -gt 0 ]]; then
    shift
  fi

  case "${mode}" in
    -h|--help|help)
      usage
      exit 0
      ;;
  esac

  require_prereqs

  # Modes that want the app running alongside them opt in here.
  case "${mode}" in
    shell|app|exec|codex|claude|gemini|kiro|opencode)
      CONTAINER_START_APP=1
      ;;
  esac

  if [[ "${CONTAINER_START_APP}" == "1" && -z "${CONTAINER_APP_CMD}" && "${mode}" != "app" ]]; then
    echo "[container] no app to start (no CONTAINER_APP_CMD and no scripts/start-dev.sh)" >&2
  fi

  case "${mode}" in
    shell)
      run_in_container bash
      ;;
    app)
      [[ -n "${CONTAINER_APP_CMD}" ]] || { echo "sandbox app needs CONTAINER_APP_CMD or scripts/start-dev.sh" >&2; exit 1; }
      # Run the app in the foreground instead of backgrounding it.
      CONTAINER_START_APP=0
      run_in_container bash -lc "exec ${CONTAINER_APP_CMD}"
      ;;
    run|exec)
      [[ $# -gt 0 ]] || { echo "sandbox ${mode} requires a command" >&2; exit 1; }
      run_in_container "$@"
      ;;
    codex)
      run_in_container bash -lc 'exec nix shell github:NixOS/nixpkgs#codex -c codex --sandbox danger-full-access "$@"' -- "$@"
      ;;
    claude)
      run_in_container bash -lc 'NIXPKGS_ALLOW_UNFREE=1 exec nix shell --impure github:NixOS/nixpkgs#claude-code -c claude --dangerously-skip-permissions "$@"' -- "$@"
      ;;
    gemini)
      run_in_container bash -lc 'NIXPKGS_ALLOW_UNFREE=1 exec nix shell --impure github:numtide/llm-agents.nix#gemini-cli -c gemini "$@"' -- "$@"
      ;;
    kiro)
      # kiro-cli-unwrapped, not kiro-cli: nixpkgs' kiro-cli is a bwrap FHS env, and
      # bwrap cannot make its namespaces in a container with every capability
      # dropped. The unwrapped binaries are already patched to run from the store.
      run_in_container bash -lc 'NIXPKGS_ALLOW_UNFREE=1 KIRO_NO_AUTO_UPDATE=1 exec nix shell --impure github:NixOS/nixpkgs#kiro-cli-unwrapped -c kiro-cli chat --v3 --trust-all-tools "$@"' -- "$@"
      ;;
    opencode)
      # --auto is its trust-everything flag, as --trust-all-tools is kiro's: the
      # container is the boundary.
      run_in_container bash -lc 'NIXPKGS_ALLOW_UNFREE=1 OPENCODE_DISABLE_AUTOUPDATE=true exec nix shell --impure github:NixOS/nixpkgs#opencode -c opencode --auto "$@"' -- "$@"
      ;;
    *)
      usage
      exit 1
      ;;
  esac
}

main "$@"
