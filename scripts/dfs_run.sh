#!/usr/bin/env bash
# Run sessions on one task, one after another, until a FACT says stop.
#
# The loop lives outside the sessions on purpose: whether the chain ends is a fact
# about the task file, the repo and the transcript, never another judgement to spend a
# session making. Every stop below is read off one of those:
#
#   an open raise in the task's Log   -> blocked, wake the author
#   every live node confirmed          -> done
#   the repo did not move              -> idle: the session changed nothing; stop
#                                         rather than repeat it
#   a session past the context ceiling -> stop, the budget is not holding
#   a usage limit                      -> limit; the one stop worth waiting out
#   otherwise                          -> go again, up to the cap
#
# ⚠️ AND THREE SESSIONS AND A STOP THE RUNNER DECIDES ITSELF. After every CRITIC_EVERY
# work sessions a CRITIC judges the tree's reasoning — is the evidence true and
# independent, does it entail the path taken — and not the code. Once a tree is
# complete, an IMPLEMENTATION REVIEW judges the code against the goal. What either
# finds is work, put in the tree: the reviewer adds open nodes, the critic adds them
# or backtracks to where the reasoning stopped holding. Only a decision that needs
# the author is a raise. And after SESSION_LIMIT work sessions since the author last
# spoke on the task, the runner raises for their review, and so it does when
# REVIEW_ROUNDS implementation reviews in a row found issues without the author
# having spoken, rather than reviewing the fixes to the fixes. An answer reopens the task
# until a work session has acted on it. All of it is counted from the
# task's own Log, where the runner records every session it starts and how it ended;
# none of it is left to a session's judgement.
#
# ⚠️ A SESSION MAY END MID-NODE, and the next one picks the work up. A node ends with
# a commit, not a session: a usage limit or the context checkpoint leaves the node's
# uncommitted work in the tree, and the briefing tells the next session it is its
# own. That is only true because sessions run serially. Uncommitted work that is NOT
# this task's (another task's last session, or the author's) does not stop a chain:
# its first session is told to commit or stash it before touching its node, and that
# first session is always a work session (see `foreign`, below).
#
# The author's side spends no session: --review takes a task's open raises, then any
# corrections to its tree, and appends the author's words itself — a script cannot
# paraphrase. --open writes a new task file the same way.
#
# ⚠️ RUN IT FROM THE WORK ROOT. The repo whose roadmap this works is the CURRENT
# DIRECTORY — there is no setting and nothing remembered — and it is `<cwd>/.dfs`.
#
# Usage:  <scripts>/dfs_run.sh [--codex|--kiro] <task> [cap]   e.g. --codex W4 5
#         <scripts>/dfs_run.sh --review <task>   answer raises, correct nodes,
#                                           accept a finished tree
#         <scripts>/dfs_run.sh --open [task]     write a new task file
#         <scripts>/dfs_run.sh [--codex|--kiro] --chat [task|new]
#         <scripts>/dfs_run.sh --bump-agent     pin the agents to nixpkgs now
#                                           (dfs_tui.py does it in the background)
# Env:    CLAUDE_CMD       how claude is launched here — a nix run, not a binary on PATH
#         CODEX_CMD        how codex is launched here — also a nix command by default
#         KIRO_CMD         how kiro-cli is launched here — also a nix command by default,
#                          of kiro-cli-unwrapped (nixpkgs' kiro-cli is a bwrap FHS env,
#                          which cannot start inside dfs_sandbox.sh's container)
#         NIX_AGENT_FLAGS  extra flags for the nix command in those defaults
#                          (default: none)
#         AGENT_NIXPKGS_REV the nixpkgs revision the defaults launch from
#                          (default: the pin in .dfs/runs/agent-nixpkgs.rev)
#         PERMISSION_FLAGS default --dangerously-skip-permissions; with --codex the
#                          default is --dangerously-bypass-approvals-and-sandbox, and
#                          with --kiro it is --trust-all-tools
#         MAX_TURNS  (default: 100)         PROMPT  (default: the SKILL.md line)
#         CHAT_PROMPT override the opening discussion prompt
#         MAX_CONTEXT (default: 400000, the chain ceiling)
#         BASH_DEFAULT_TIMEOUT_MS (default: 3600000, one hour)  the child's Bash tool timeout when a call names none
#         BASH_MAX_TIMEOUT_MS (default: 21600000, six hours)  the most a child's Bash call may ask for
set -euo pipefail

# ⚠️ TWO ROOTS, and neither is `git rev-parse`: the repo being worked on is the CWD
# (deliberately — the tool serves whichever repo you run it in, so which project this
# is must be a fact about the invocation), while the TOOL is wherever this file was
# installed. `dfs_paths.py` is the one place either is derived, and the *_REL
# values are how a prompt spells a path from the session's CWD. NOTHING CDs HERE.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
eval "$(python3 "$HERE/dfs_paths.py" --sh)"
python3 "$HERE/dfs_paths.py" --require || exit 2

MODE=work
AGENT=claude
args=()
for a in "$@"; do
  case "$a" in
    --review)  MODE=review ;;
    --answer)  MODE=review ;;   # the old name of the same pass
    --open)    MODE=open ;;
    --chat)    MODE=chat ;;
    --codex)   AGENT=codex ;;
    --kiro)    AGENT=kiro ;;
    --bump-agent) MODE=bump ;;
    *)         args+=("$a") ;;
  esac
done
ITEM="${args[0]:-}"
CAP="${args[1]:-5}"
[ "$MODE" = review ] || [ "$MODE" = chat ] || [ "$MODE" = open ] || [ "$MODE" = bump ] || [ -n "$ITEM" ] || { echo "usage: $0 [--codex|--kiro] <task> [cap]  |  --review <task>  |  --open  |  [--codex|--kiro] --chat [task|new]" >&2; exit 2; }

# No agent needs to be a binary on PATH: each default launcher is a Nix command
# line, split into an argv. Override the provider's command to use a local checkout, a
# pinned rev, or `echo` to see what would run without starting a session.
export NIXPKGS_ALLOW_UNFREE="${NIXPKGS_ALLOW_UNFREE:-1}"

# ⚠️ THE AGENT IS PINNED TO ONE NIXPKGS REVISION, in .dfs/runs/agent-nixpkgs.rev
# (dfs_agent.py owns it). `github:nixos/nixpkgs#x` with no revision is a MUTABLE ref,
# so every launch took whatever the nix cache last resolved it to: this used
# `--offline` to stop the re-resolve, but the cache is shared with `./dev`, which
# re-resolves on purpose (scripts/container.sh), so a `c` in the TUI after a `./dev`
# started a different claude-code than the one before it, after a fresh eval. The pin
# moves only when dfs_agent.py --update has BUILT the new one: the TUI runs that in
# the background when it starts, and `--bump-agent` runs it here.
# ⚠️ And NOT `--offline` any more: a revision-locked ref is never re-resolved, so
# going online costs nothing (1.3 s either way, measured) and keeps the substituters,
# which offline turned off, building from source a path that had been collected.
# Read ONCE, here, so a chain keeps one agent even when the pin moves under it.
# `AGENT_NIXPKGS_REV=<rev>` tries another revision for one run. Not read at all when
# the provider's command is given, which is how the tests stub the agent.
if [ "$MODE" = bump ]; then
  exec python3 "$HERE/dfs_agent.py" --update
fi
if [ -z "${AGENT_NIXPKGS_REV:-}" ] && { { [ "$AGENT" = claude ] && [ -z "${CLAUDE_CMD:-}" ]; } || { [ "$AGENT" = codex ] && [ -z "${CODEX_CMD:-}" ]; } || { [ "$AGENT" = kiro ] && [ -z "${KIRO_CMD:-}" ]; }; }; then
  AGENT_NIXPKGS_REV="$(python3 "$HERE/dfs_agent.py" --rev)" || exit 2
fi
AGENT_NIXPKGS="github:nixos/nixpkgs/${AGENT_NIXPKGS_REV:-}"
NIX_AGENT_FLAGS="${NIX_AGENT_FLAGS:-}"
# kiro-cli takes its session flags after `chat`, so that word is part of how it is
# started rather than of the command a caller overrides.
AGENT_SUB=()
if [ "$AGENT" = codex ]; then
  AGENT_CMD_ENV=CODEX_CMD
  AGENT_CMD="${CODEX_CMD:-nix shell $NIX_AGENT_FLAGS $AGENT_NIXPKGS#codex -c codex}"
  PERMISSION_FLAGS="${PERMISSION_FLAGS---dangerously-bypass-approvals-and-sandbox}"
elif [ "$AGENT" = kiro ]; then
  AGENT_CMD_ENV=KIRO_CMD
  AGENT_CMD="${KIRO_CMD:-nix shell $NIX_AGENT_FLAGS --impure $AGENT_NIXPKGS#kiro-cli-unwrapped -c kiro-cli}"
  AGENT_SUB=(chat)
  PERMISSION_FLAGS="${PERMISSION_FLAGS---trust-all-tools}"
else
  AGENT_CMD_ENV=CLAUDE_CMD
  AGENT_CMD="${CLAUDE_CMD:-nix run $NIX_AGENT_FLAGS --impure $AGENT_NIXPKGS#claude-code --}"
  PERMISSION_FLAGS="${PERMISSION_FLAGS---dangerously-skip-permissions}"
fi
read -r -a AGENT_ARGV <<< "$AGENT_CMD"
read -r -a PERMISSION_ARGV <<< "$PERMISSION_FLAGS"
MAX_TURNS="${MAX_TURNS:-100}"          # Fallback only when context usage is unavailable.
# ⚠️ CONTEXT IS WHAT THE COST MODEL IS WRITTEN IN; turns are a proxy that varies 3x.
# The 09-06 overrun grew 532 tokens/turn against a 1,707 median, so 136 of its turns
# weighed about what 42 average ones would. A ceiling on turns cannot see that; a
# ceiling on peak context can, and either one tripping ends the chain.
# Read from dfs_context.py's CHAIN_CEILING, not spelled again: one file owns every
# budget number, and a loop that disagreed with the session about the measurement is
# the failure this roadmap keeps writing down. ⚠️ It is NOT `FLAT_TO`, which it was
# until 2026-09-17 — that is the advice a session weighs when deciding to close, and
# this is where an unattended chain stops and fetches a person. The argument for the
# two being one value, and why it was wrong, is on CHAIN_CEILING itself.
MAX_CONTEXT="${MAX_CONTEXT:-$(python3 "$HERE/dfs_context.py" --chain-ceiling)}"
# ⚠️ THE SESSION IS HANDED ITS STATE; IT DOES NOT GO AND DERIVE IT. The briefing
# carries the law verbatim and what the task's tree and log say — the same derivation
# this script reads its stop conditions from — and is REBUILT ON EVERY RUN, because
# each run moves the task file.
PROMPT_LEAD="${PROMPT:-Follow $DFS_SKILL_REL. Work task $ITEM. Everything below is your \
briefing: the roadmap's law verbatim, then what $ITEM's tree and log say, derived for you.}"
CRITIC_LEAD="You are the critic for task $ITEM. Everything below is your briefing: the \
roadmap's law verbatim, then what to judge and how to record it."
REVIEW_LEAD="You are the implementation reviewer for task $ITEM. Everything below is your \
briefing: the roadmap's law verbatim, then what to review and how to record it."
TREE_PY="$HERE/dfs_tree.py"

build_prompt() {  # build_prompt work|critic|review
  local brief flag=--brief lead="$PROMPT_LEAD"
  if [ "$1" = critic ]; then flag=--critic-brief; lead="$CRITIC_LEAD"; fi
  if [ "$1" = review ]; then flag=--review-brief; lead="$REVIEW_LEAD"; fi
  if ! brief="$(python3 "$HERE/dfs_state.py" "$flag" "$ITEM")"; then
    echo "dfs_run: dfs_state.py $flag $ITEM failed; a session cannot be briefed." >&2
    return 1
  fi
  printf '%s\n\n%s\n' "$lead" "$brief"
}

if [ "$MODE" = work ] || [ "$MODE" = chat ]; then
  command -v "${AGENT_ARGV[0]}" >/dev/null || { echo "dfs_run: cannot run '${AGENT_ARGV[0]}' (set $AGENT_CMD_ENV)" >&2; exit 2; }
fi

# A child agent establishes its OWN session identity — claude from the --session-id
# below, codex by assigning one and emitting it in its stream — so nothing about the
# parent's session is legitimately inherited, and all four names are scrubbed rather
# than the other provider's two. The asymmetry that was here covered only the CROSS
# case, which is real (dfs_context.py resolves `claude_sid or codex_sid`, so a
# stale CLAUDE_CODE_SESSION_ID beats a live codex id and the worker reads the PARENT's
# transcript) but is the rarer one: this script is normally run FROM a session of the
# same agent it launches, and that inheritance was uncovered. A worker measuring the
# wrong transcript reports the wrong context against its own budget and says nothing.
# If a child turns out not to export its own id, dfs_context.py prints that no
# session id is set and exits 2 — loud, where reading the wrong transcript is silent.
#
# ⚠️ AND THE BASH TOOL'S TIMEOUTS ARE SET FOR LONG STEPS. Unset, a call that names no
# timeout gets 120s, so a session that wrote `timeout 290 playwright test <spec>` and
# passed no tool timeout had the tool give up first: the command was moved to the
# background and the session spent turns polling an output file for a result it had
# sized to wait for. Measured over 63 roadmap sessions (2026-09-12 to 2026-09-14): 172
# calls wrapped a shell timeout over 120s with no tool timeout, 21 commands were moved to
# the background, 15 of them at exactly 120s. The default was checked on its own in a
# headless session: the same 12s command ran in the foreground unset and was backgrounded
# at 5s with it set to 5000. These sessions use the one-hour prompt cache, so the default
# is the hour AGENTS.md gives as the ceiling for one step, and a call may ask for up to six
# hours. Nothing past ten minutes has been exercised against the maximum yet.
# ⚠️ AND THE SESSION IS TOLD SO, in SKILL.md's "You get ONE turn" — which is the half
# that was missing until 2026-09-18. A budget nobody reads buys nothing: two chains
# died with the runner's hour available and unused, each having backgrounded the e2e
# suite and ended its turn expecting to be woken. Keep the two in step.
# kiro-cli updates itself unless told not to, and a chain's agent is the pinned one.
AGENT_ENV=(env -u CLAUDE_CODE_SESSION_ID -u CODEX_SESSION_ID -u CODEX_THREAD_ID
           -u KIRO_SESSION_ID KIRO_NO_AUTO_UPDATE=1
           BASH_DEFAULT_TIMEOUT_MS="${BASH_DEFAULT_TIMEOUT_MS:-3600000}"
           BASH_MAX_TIMEOUT_MS="${BASH_MAX_TIMEOUT_MS:-21600000}")

if [ "$MODE" = chat ]; then
  if [ "$ITEM" = new ]; then
    # Scoping work that does not exist yet. It stops where --open takes over, so the
    # task file is written from the author's words, in front of them.
    default_chat_prompt="Read $DFS_ROADMAP_REL. The author wants to scope a NEW task. Ask what done looks like and why it is worth doing, then propose the Goal paragraph and a first linear chain of todo nodes, each one line. Do not create the task file and do not start work — the author writes it with $DFS_SCRIPTS_REL/dfs_run.sh --open."
    chat_subject="a new task"
  elif [ -n "$ITEM" ]; then
    default_chat_prompt="Read $DFS_ROADMAP_REL and task $ITEM's parts, $DFS_ITEMS_REL/$ITEM.md and every file in $DFS_ITEMS_REL/$ITEM/, whichever exist. Discuss task $ITEM with the author. Do not perform the work, edit files or append to the task's log unless the author explicitly changes the task. Start by summarising the tree: where the work is, what was refuted and why, and the next question worth discussing."
    chat_subject="task $ITEM"
  else
    default_chat_prompt="Read $DFS_EPIC_REL and $DFS_ROADMAP_REL. Discuss the roadmap tool with the author. Do not perform work or edit files unless the author explicitly changes the task. Start by summarising its current goal, open decisions, and the next question worth discussing."
    chat_subject="the roadmap tool"
  fi
  CHAT_PROMPT="${CHAT_PROMPT:-$default_chat_prompt}"
  # The permission flags are the same ones the unattended modes pass, and for a reason
  # that survives being attended: a chat session reads the roadmap and the task file
  # before it can say anything, so without them the first thing the author sees is a
  # prompt to allow a read they already asked for. PERMISSION_FLAGS= (set and empty,
  # which is why the defaults above test for unset rather than for empty) passes none
  # and sits through the prompts.
  echo "dfs_run: opening an interactive $AGENT session for $chat_subject"
  exec "${AGENT_ENV[@]}" "${AGENT_ARGV[@]}" "${AGENT_SUB[@]}" "${PERMISSION_ARGV[@]}" "$CHAT_PROMPT"
fi

# The caller may name the directory. dfs_tui.py does, because it starts a chain in the
# BACKGROUND and has to know where the console log will land before there is a
# process to ask. The root is asked of dfs_runs.py rather than spelled here, so the
# writer and the two readers cannot disagree about it.
if [ "$MODE" = work ]; then
  if [ -n "${RUNDIR:-}" ]; then
    mkdir -p "$RUNDIR"
  else
    RUN_ROOT="$(python3 "$HERE/dfs_runs.py" --root)"
    mkdir -p "$RUN_ROOT"
    RUNDIR="$(mktemp -d "$RUN_ROOT/dfs_run_XXXXXX")"
  fi
fi
# ⚠️ READ, THEN NOT PASSED ON. `RUNDIR` arrives in the ENVIRONMENT (dfs_tui.py sets
# it), so everything this starts inherited it: the agent, and whatever the agent
# runs. On 2026-09-27 a W13 session working on this tooling ran test_dfs_live.py,
# whose own `dfs_run.sh --codex W1 2` then wrote its meta INTO the live chain's
# directory: item W1, cap 2, agent codex, and a pid that soon died. The walk read
# its own chain as ended, started a second chain on W13 beside the first, and that
# one's critic found the first one's edits and stopped `failed`. A chain's directory
# is the chain's, so the name stops at this process.
export -n RUNDIR 2>/dev/null || true

# What this directory is for, and how far the chain has got — read once a second by
# dfs_tui.py, given meaning by dfs_runs.py, written ATOMICALLY so a reader never
# catches a truncated file.
meta_set() {  # meta_set key=value ...
  python3 - "$RUNDIR/meta.json" "$@" <<'META'
import json, os, sys
path, pairs = sys.argv[1], sys.argv[2:]
try:
    with open(path) as fh:
        meta = json.load(fh)
    if not isinstance(meta, dict):
        meta = {}
except Exception:
    meta = {}
for pair in pairs:
    key, _, value = pair.partition("=")
    meta[key] = value
tmp = path + ".tmp"
with open(tmp, "w") as fh:
    json.dump(meta, fh)
os.replace(tmp, path)
META
}


# ⚠️ WHY THE CHAIN STOPPED, RECORDED RATHER THAN PRINTED, so a caller deciding what
# to do next — the TUI's walk — never greps for a sentence.
#
#   limit       a usage limit; the only reason a caller should WAIT and retry
#   blocked     an open raise — the agent's, the critic's, the reviewer's, or the session limit's
#   done        every live node confirmed, and the implementation reviewed or accepted
#   cap         the chain ran its full cap with the task still open
#   idle        the repo did not move: the session changed nothing
#   failed      a session exited non-zero, or a critic or reviewer changed the tree
#               or gave no verdict
#   overbudget  a session went past the context ceiling
#   overturns   no context reading, and past the turn ceiling
#   interrupt   Ctrl-C
stop_is() {
  # ⚠️ Spelled out rather than `${2:+resets="$2"}`: that expansion word-splits a
  # reset time like "resets 3pm" into two argv entries.
  if [ -n "${2:-}" ]; then meta_set stop="$1" resets="$2"; else meta_set stop="$1"; fi
}

# One line in the task's Log. The runner writes `session` before a session starts and
# `end` after it stops, so a session killed outright is a `session` with no `end`,
# and every count the stops use is read from the file rather than kept here.
tree_log() {  # tree_log <kind> [args...]  (body on stdin, or none)
  python3 "$TREE_PY" log "$ITEM" "$@" < /dev/null > /dev/null
}

IN_SESSION=""
# The finish line, recorded by the process itself. ⚠️ Its ABSENCE is not "still
# running" — a chain killed outright never writes one — which is why liveness is
# asked of the pid instead (scripts/dfs_runs.py). What this adds is the
# difference between a chain that ENDED and one that DIED, and the exit code that
# says which. ⚠️ `rc` is captured on the first line: a `$?` written beside a
# `$(date)` is the date's exit status, every time.
on_exit() {
  rc=$?
  meta_set finished="$(date -Is)" rc="$rc"
}

# Ctrl-C must end the CHAIN, not just the run. A claude that handles the interrupt
# itself and exits 0 looks exactly like a session that finished, so this trap is what
# makes one Ctrl-C mean "stop".
on_interrupt() {
  stop_is interrupt
  [ -n "$IN_SESSION" ] && tree_log end "$IN_SESSION" interrupt || true
  echo
  echo "dfs_run: interrupted. Stopping the chain. The node's uncommitted work stays in the tree for the next session."
  exit 130
}

# ⚠️ THE CAP CAN BE LOWERED FROM OUTSIDE, BETWEEN RUNS. A walk-started chain is given
# the walk's whole remaining budget as its cap, so turning the walk off used to leave
# a chain that could run thirty more sessions with nothing watching it (W19,
# 2026-09-28: the walk went off at run 3 and the chain carried on to run 5 of 33).
# The TUI writes the run it is on into `$RUNDIR/cap` (`dfs_runs.lower_cap`), and this
# reads it between runs, so the session in flight finishes and no further one starts.
# Only ever LOWERS: a stray file cannot buy a chain more sessions than it was given.
lowered_cap() {
  local f="$RUNDIR/cap" n
  [ -f "$f" ] || return 0
  n="$(tr -dc 0-9 < "$f")"
  if [ -n "$n" ] && [ "$n" -lt "$CAP" ]; then
    CAP="$n"
    meta_set cap="$CAP"
    echo "dfs_run: cap lowered to $CAP from outside."
  fi
}

dfs_state() {
  python3 "$HERE/dfs_state.py" --sh "$ITEM"
}

# Peak context and response count for a transcript, both from
# scripts/dfs_context.py rather than from a copy here. ⚠️ ONE DEFINITION ON
# PURPOSE: a session reads its own budget through that file and this loop enforces a
# ceiling on the same quantity, and a loop that disagreed with the session about what
# its budget said is the failure this roadmap keeps writing down — the screen's own
# first draft of this arithmetic already differed from it by 2 tokens on a 238-response
# transcript. Claude writes one line per content block, so its records are deduped on
# message.id; Codex writes one token_count event per API response. Both count model
# responses rather than JSONL records. -1 means no transcript, which is itself worth
# seeing, and is what the ceilings below read as "unavailable".
transcript_measure() {  # transcript -> "<peak> <turns>"
  python3 "$HERE/dfs_context.py" --measure "$1" "$AGENT"
}
transcript_turns() { transcript_measure "$1" | cut -d' ' -f2; }

transcript_path() {  # session id, run log -> the provider's persisted transcript
  # A kiro session's transcript IS the stream this chain captured (dfs_context.py,
  # `_kiro_log`), and here the log is known without searching for it.
  if [ "$AGENT" = kiro ]; then printf '%s\n' "$2"; return; fi
  python3 - "$1" "$AGENT" <<'PY'
import glob, os, re, sys
sid, agent = sys.argv[1:]
if agent == "codex":
    root = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
    found = glob.glob(os.path.join(root, "sessions", "**", "rollout-*-" + sid + ".jsonl"), recursive=True)
    print(found[0] if found else "")
else:
    proj = re.sub(r"[^A-Za-z0-9]", "-", os.getcwd())
    print(os.path.join(os.path.expanduser("~/.claude/projects"), proj, sid + ".jsonl"))
PY
}

result_field() { python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d.get(sys.argv[2], ""))' "$1" "$2" 2>/dev/null || true; }

codex_thread_id() {
  python3 - "$1" <<'PY'
import json, sys
try:
    for line in open(sys.argv[1], errors="replace"):
        try: rec = json.loads(line)
        except Exception: continue
        if rec.get("type") == "thread.started":
            print(rec.get("thread_id", ""))
            break
except FileNotFoundError:
    pass
PY
}

# The id of a codex session that has STARTED but not finished, recorded so the
# screen can read its transcript while it is being written.
#
# ⚠️ IT IS THE FIRST LINE OF THE STREAM, so this costs one poll in the normal case —
# `{"type":"thread.started","thread_id":"<the id>"}` is emitted before the first turn.
# The loop is bounded rather than open-ended because a codex that dies at launch emits
# nothing at all, and the value is re-read after `wait` either way, so a miss here only
# means the live pane waits for the run to end as it always did.
#
# ⚠️ THE STALE VALUE IS CLEARED FIRST, and that is half the point. `meta.json` holds
# ONE sid — the session the chain is in RIGHT NOW — so leaving run 1's id there
# through the whole of run 2 did not merely withhold the live turns, it showed the
# PREVIOUS session's finished transcript under a heading saying `agent · session
# 01a0b959`, which is the shape of wrongness nobody checks: a pane full of plausible
# turns that stopped moving.
#
# kiro's stream is read the same way: it mints its own id too, and names it as ACP's
# `sessionId` in its first events.
kiro_session_id() {
  python3 - "$1" "$HERE" <<'PY'
import json, sys
sys.path.insert(0, sys.argv[2])
import dfs_context
recs = []
try:
    with open(sys.argv[1], errors="replace") as fh:
        for n, line in enumerate(fh):
            if n >= 200: break
            try: recs.append(json.loads(line))
            except Exception: continue
except FileNotFoundError:
    pass
print(dfs_context.kiro_session_id(recs))
PY
}

stream_sid() {  # the session id in a codex or kiro stream so far
  if [ "$AGENT" = kiro ]; then kiro_session_id "$1"; else codex_thread_id "$1"; fi
}

CODEX_SID_TRIES="${CODEX_SID_TRIES:-120}"
stream_watch_sid() {  # stream_watch_sid <log> <pid>
  local log="$1" pid="$2" sid="" i=0
  while [ "$i" -lt "$CODEX_SID_TRIES" ] && kill -0 "$pid" 2>/dev/null; do
    sid="$(stream_sid "$log")"
    [ -n "$sid" ] && break
    i=$(( i + 1 ))
    sleep 0.5
  done
  [ -n "$sid" ] || return 0
  SID="$sid"
  meta_set sid="$SID"
}

# Start one fresh non-interactive session and leave its provider session id in SID.
# Codex and Kiro assign the id and emit it in their JSONL stream; Claude accepts ours
# up front.
run_session() {
  local prompt="$1" log="$2" session_rc agent_pid
  if [ "$AGENT" = kiro ]; then
    # As codex, below. stderr goes into the log too: kiro reports a failed login or
    # a limit there and not in the stream, and the log is all a finished run leaves.
    # The lines that are not JSON are read as kiro's own (dfs_limit.py, dfs_tui.py).
    SID=""
    meta_set sid=""
    "${AGENT_ENV[@]}" "${AGENT_ARGV[@]}" "${AGENT_SUB[@]}" --output-format stream-json \
      --no-interactive "${PERMISSION_ARGV[@]}" "$prompt" > "$log" 2>&1 < /dev/null &
    agent_pid=$!
    stream_watch_sid "$log" "$agent_pid"
    wait "$agent_pid"
    session_rc=$?
    SID="$(stream_sid "$log")"
    meta_set sid="$SID"
  elif [ "$AGENT" = codex ]; then
    # ⚠️ BACKGROUNDED SO THE ID CAN BE READ WHILE THE RUN IS GOING — there is nothing
    # else to wait on, since codex mints the id itself and the chain is otherwise
    # blocked in the agent for the whole session. `< /dev/null` is explicit rather
    # than inherited: a background command in a shell without job control gets
    # /dev/null anyway, and codex reads stdin when it is not a terminal, so saying it
    # here keeps a foreground launch and a TUI-launched one reading the same thing.
    SID=""
    meta_set sid=""
    "${AGENT_ENV[@]}" "${AGENT_ARGV[@]}" exec --json "${PERMISSION_ARGV[@]}" "$prompt" \
      > "$log" < /dev/null &
    agent_pid=$!
    stream_watch_sid "$log" "$agent_pid"
    wait "$agent_pid"
    session_rc=$?
    SID="$(codex_thread_id "$log")"
    meta_set sid="$SID"
  else
    SID="$(python3 -c 'import uuid; print(uuid.uuid4())')"
    # ⚠️ RECORDED BEFORE THE SESSION STARTS, and that is the whole point. `-p
    # --output-format json` writes ONE object at exit, so the log in this directory
    # is empty for the entire run and the session id inside it arrives only once
    # there is nothing left to watch. The provider writes its transcript as the
    # session goes, and we CHOSE the id, so the live turns are readable from the
    # first one — by dfs_tui.py, which is the only reader.
    meta_set sid="$SID"
    "${AGENT_ENV[@]}" "${AGENT_ARGV[@]}" -p "$prompt" --output-format json --session-id "$SID" "${PERMISSION_ARGV[@]}" > "$log"
    session_rc=$?
  fi
  return "$session_rc"
}

session_cost() {
  if [ "$AGENT" != claude ]; then
    printf '%s' ""
  else
    result_field "$1" total_cost_usd
  fi
}

# ── the author's pass over one task ──────────────────────────────────────────────
# Transcription throughout — the words are the author's — so this pass spends no
# session of its own. It shows the tree, takes the open raises in log order, then any
# corrections: a node whose determination the author overrides, with a directive. A
# correction prunes everything below the node and every sibling made after it, and
# the next session resumes from it. A finished tree can then be accepted.
review_mode() {
  if [ -z "$ITEM" ]; then
    echo "dfs_run: --review takes a task: $0 --review W7." >&2
    exit 2
  fi
  python3 - "$ITEM" "$HERE" "$AGENT" <<'PY'
import os, subprocess, sys
from pathlib import Path

task, here, agent = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, here)
import dfs_tree

tty_in, tty_out = open("/dev/tty", "r"), open("/dev/tty", "w")
def say(*a): print(*a, file=tty_out, flush=True)
def ask(prompt):
    tty_out.write(prompt); tty_out.flush()
    return (tty_in.readline() or "").strip()
def prose(label):
    """A block of the author's own words, ended by a lone '.' — empty means skip."""
    say(label)
    lines = []
    while True:
        ln = tty_in.readline()
        if ln == "" or ln.rstrip("\n") == ".": break
        lines.append(ln.rstrip("\n"))
    return "\n".join(lines).strip()

def chat(prompt):
    argv = [str(Path(here) / "dfs_run.sh")] + (["--" + agent] if agent != "claude" else []) + ["--chat", task]
    rc = subprocess.call(argv, stdin=tty_in, stdout=tty_out, stderr=tty_out,
                         env=dict(os.environ, CHAT_PROMPT=prompt))
    if rc != 0:
        say("  the chat session exited %d." % rc)

def show_node(t, nid):
    nd = dfs_tree.by_id(t).get(nid)
    if nd is None:
        say("  (no node %s)" % nid); return None
    say("  " + "\n  ".join(nd["raw"]))
    return nd

import dfs_paths
rel = ", ".join(dfs_paths.rel(p) for p in dfs_tree.part_files(task))
t = dfs_tree.load(task)
status, why = dfs_tree.status_of(t)
say("%s · %s — %s: %s\n" % (task, t["name"], status, why))
say("\n".join(dfs_tree.outline(t)) or "(no nodes yet)")
say("")
wrote = 0

for r in dfs_tree.open_raises(t):
    say("─" * 78)
    node = r["args"][0] if r["args"] else ""
    say("RAISE %s%s" % (r["ts"], (" on " + node) if node else " (the task)"))
    if node:
        show_node(t, node)
        say("")
    say(r["body"])
    say("")
    while True:
        v = (ask("  [a]nswer / [c]hat / [s]kip / [q]uit: ") or "s")[:1].lower()
        if v == "c":
            chat("Read %s. The author is answering the raise logged at %s%s. Help them "
                 "decide: what the node's evidence shows, what each answer commits the work "
                 "to. Do not edit any file and do not start work; the author types the "
                 "answer when this session ends." % (rel, r["ts"], (" on " + node) if node else ""))
            continue
        break
    if v == "q":
        break
    if v != "a":
        say("  skipped — still open.\n"); continue
    body = prose("  answer (a lone '.' ends it, empty skips):")
    if not body:
        say("  skipped — still open.\n"); continue
    dfs_tree.append_log(task, "answer", [r["ts"]], body)
    wrote += 1
    say("  -> answered\n")

say("─" * 78)
while True:
    nid = ask("correct a node — its id, or empty to finish: ")
    if not nid:
        break
    if not nid.startswith(task + "."):
        nid = "%s.%s" % (task, nid.lstrip("."))
    t = dfs_tree.load(task)
    if show_node(t, nid) is None:
        continue
    v = ""
    while v not in ("c", "r", "s"):
        v = (ask("  the right determination: [c]onfirmed (continue below it) / "
                 "[r]efuted (back up from it) / [s]kip: ") or "s")[:1].lower()
    if v == "s":
        continue
    body = prose("  directive — what the work should do from here (a lone '.' ends it):")
    if not body:
        say("  empty directive, skipped.\n"); continue
    dfs_tree.append_log(task, "correct", [nid, "confirmed" if v == "c" else "refuted"], body)
    wrote += 1
    say("  -> corrected; everything below %s is pruned and %s reopens\n" % (nid, task))

t = dfs_tree.load(task)
status, why = dfs_tree.status_of(t)
if status == "done" and not dfs_tree.accepted(t):
    if (ask("accept %s's tree as finished? [y/N]: " % task) or "n")[:1].lower() == "y":
        dfs_tree.append_log(task, "accept")
        wrote += 1
        say("  -> accepted")
say("dfs_run: %s — %s: %s%s" % (task, status, why,
                                 "" if wrote else " (nothing written)"))
PY
}

# ── writing a new task (--open) ──────────────────────────────────────────────────
# The author's words go in unchanged. The todo lines become a LINEAR chain of open
# nodes, each the child of the one before, which is the shape a task usually starts
# in; the sessions add nodes, branch and back up from there.
open_mode() {
  python3 - "${ITEM:-}" "$HERE" <<'PY'
import os, re, sys
from pathlib import Path

want, here = sys.argv[1].strip(), sys.argv[2]
sys.path.insert(0, here)
import dfs_paths, dfs_tree
try:
    tag = dfs_paths.branch_tag()
except dfs_paths.NoBranch as e:
    print("dfs_run: %s" % e, file=sys.stderr); raise SystemExit(2)
# One past the highest W on ANY branch this checkout can see, tagged with this one's.
nums = [int(m.group(1)) for m in (re.fullmatch(r"W(\d+)(?:@.*)?", t) for t in dfs_tree.task_ids()) if m]
suffix = "@" + tag if tag else ""
nxt = "W%d%s" % (max(nums, default=0) + 1, suffix)

tty_in, tty_out = open("/dev/tty", "r"), open("/dev/tty", "w")
def say(*a): print(*a, file=tty_out, flush=True)
def ask(prompt):
    tty_out.write(prompt); tty_out.flush()
    return (tty_in.readline() or "").strip()
def block(label):
    say(label)
    lines = []
    while True:
        ln = tty_in.readline()
        if ln == "" or ln.rstrip("\n") == ".": break
        lines.append(ln.rstrip("\n"))
    return "\n".join(lines).strip()

task = want or ask("  task id [%s]: " % nxt) or nxt
if re.fullmatch(r"[A-Za-z]+\d+", task):
    task += suffix
if not re.fullmatch(r"[A-Za-z]+\d+%s" % re.escape(suffix), task):
    say("dfs_run: a task id is letters then a number (W25)%s, not %r."
        % (", tagged %s on this branch" % suffix if suffix else "", task)); raise SystemExit(2)
path = dfs_tree.part_path(task, tag)
if dfs_tree.exists(task):
    say("dfs_run: %s already exists. Pick another id." % task); raise SystemExit(2)
path.parent.mkdir(parents=True, exist_ok=True)

say("\nYour words go in unchanged. End a multi-line answer with a lone '.'\n")
name = ask("  short name: ")
if not name:
    say("dfs_run: nothing opened."); raise SystemExit(0)
goal = block("  Goal — what done looks like and WHY (lone '.' ends it):")
say("  Todos — one per line, in order; each becomes a node (lone '.' ends it, none is fine):")
todos = []
while True:
    ln = tty_in.readline()
    if ln == "" or ln.rstrip("\n") == ".": break
    if ln.strip(): todos.append(ln.strip().lstrip("-[ ]x").strip())

body = ["# %s · %s" % (task, name), "", "## Goal", "", goal or name, "", "## Tree", ""]
for n, todo in enumerate(todos, 1):
    body += ["### %s · %s" % (dfs_tree.node_id(task, n, tag), todo)]
    if n > 1:
        body += ["Parent: %s" % dfs_tree.node_id(task, n - 1, tag)]
    body += ["Status: open", ""]
body += ["## Log", ""]
path.write_text("\n".join(body).rstrip() + "\n")
say("\ndfs_run: wrote %s — %d node%s." % (path, len(todos), "" if len(todos) == 1 else "s"))
PY
  python3 "$HERE/dfs_check.py" --warn || true
}

if [ "$MODE" = open ]; then
  open_mode
  exit 0
fi

if [ "$MODE" = review ]; then
  review_mode
  exit 0
fi

# A work chain writes this branch's part, so it needs a branch to tag with, and a task.
python3 - "$ITEM" "$HERE" <<'PY' || exit 2
import sys
sys.path.insert(0, sys.argv[2])
import dfs_paths, dfs_tree
try:
    dfs_paths.branch_tag()
except dfs_paths.NoBranch as e:
    print("dfs_run: %s" % e, file=sys.stderr); raise SystemExit(2)
if not dfs_tree.exists(sys.argv[1]):
    print("dfs_run: no task %s under %s" % (sys.argv[1], dfs_paths.rel(dfs_paths.items())),
          file=sys.stderr); raise SystemExit(2)
PY

meta_set item="$ITEM" cap="$CAP" agent="$AGENT" mode="$MODE" \
         started="$(date -Is)" pid="$$"
trap on_exit EXIT
trap on_interrupt INT TERM

# ⚠️ SERIAL, SO THE DIRT HAS AN OWNER. Work in the tree that is not this task's is
# committed or stashed by this chain's first session, whose briefing lists it.
foreign="$(python3 "$HERE/dfs_state.py" --foreign-dirt "$ITEM")"
if [ -n "$foreign" ]; then
  echo "dfs_run: $(printf '%s\n' "$foreign" | wc -l) uncommitted path(s) are not $ITEM's; the first session commits or stashes them."
fi

echo "dfs_run: task $ITEM, cap $CAP, agent $AGENT, logs in $RUNDIR"
if [ "$AGENT" = codex ]; then
  echo "dfs_run: launching  ${AGENT_ARGV[*]} exec --json ${PERMISSION_ARGV[*]} <prompt>"
elif [ "$AGENT" = kiro ]; then
  echo "dfs_run: launching  ${AGENT_ARGV[*]} ${AGENT_SUB[*]} --output-format stream-json --no-interactive ${PERMISSION_ARGV[*]} <prompt>"
else
  echo "dfs_run: launching  ${AGENT_ARGV[*]} -p <prompt> --output-format json ${PERMISSION_ARGV[*]}"
fi
total_turns=0
total_cost=0

for i in $(seq 1 "$CAP"); do
  lowered_cap
  if [ "$i" -gt "$CAP" ]; then stop_is cap; echo "dfs_run: CAP before run $i — the cap was lowered to $CAP."; exit 0; fi
  eval "$(dfs_state)"
  case "$status" in
    blocked) stop_is blocked; echo "dfs_run: BLOCKED before run $i — $why. Answer it with --review $ITEM, then run again."; exit 0 ;;
    done)    stop_is done; echo "dfs_run: DONE before run $i — $why."; exit 0 ;;
  esac
  kind=work
  if [ "$critic_due" = 1 ]; then
    kind=critic
  elif [ "$review_due" = 1 ] && [ "$review_limit" = 1 ]; then
    printf '%s\n' "$review_rounds implementation reviews of $ITEM since you last spoke on it found issues, and the fixes for them are in. Rather than a round $(( review_rounds + 1 )), review the tree ($DFS_SCRIPTS_REL/dfs_run.sh --review $ITEM): accept the code as it stands, or answer this to let one more review run." \
      | python3 "$TREE_PY" log "$ITEM" raise > /dev/null
    stop_is blocked
    echo "dfs_run: BLOCKED before run $i — $review_rounds review rounds found issues without settling; raised for review."
    exit 0
  elif [ "$review_due" = 1 ]; then
    kind=review
  elif [ "$limit" = 1 ]; then
    printf '%s\n' "$sessions work sessions on $ITEM since you last spoke on it. Review the tree ($DFS_SCRIPTS_REL/dfs_run.sh --review $ITEM): answer this to let it carry on, or correct the node where it went wrong." \
      | python3 "$TREE_PY" log "$ITEM" raise > /dev/null
    stop_is blocked
    echo "dfs_run: BLOCKED before run $i — $sessions work sessions since the author last spoke; raised for review."
    exit 0
  fi
  # ⚠️ Foreign dirt goes to a WORK session first: a critic or reviewer may not change
  # code, and once either had run on this task the dirt would read as its own. The
  # critic or review stays due, so it is the next run.
  if [ -n "$foreign" ]; then
    kind=work
    why="uncommitted work that is not $ITEM's, to commit or stash first"
    foreign=""
  fi

  echo
  echo "── run $i/$CAP · $kind ($why) ──"
  meta_set run="$i" status="$status" kind="$kind"
  log="$RUNDIR/run-$i.json"
  prompt="$(build_prompt "$kind")" || exit 2
  if [ "$kind" != work ]; then
    fp_before="$(python3 "$HERE/dfs_state.py" --fingerprint --outside-dfs)"
    verdicts_before="$(python3 "$HERE/dfs_state.py" --verdicts "$ITEM" "$kind")"
  fi
  tree_log session "$kind" "$(basename "$RUNDIR")/run-$i"
  if [ "$kind" = work ]; then fp_before="$(python3 "$HERE/dfs_state.py" --fingerprint)"; fi
  IN_SESSION="$kind"
  set +e
  run_session "$prompt" "$log"
  rc=$?
  set -e

  cost="$(session_cost "$log")"
  tp="$(transcript_path "$SID" "$log")"
  read -r peak turns <<< "$(transcript_measure "$tp")"
  total_turns=$(( total_turns + (turns > 0 ? turns : 0) ))
  meta_set peak="$peak" turns="$turns"
  if [ "$AGENT" != claude ]; then
    echo "   exit $rc · $turns turns · peak context $peak · cost unavailable · session ${SID:-unknown}"
  else
    total_cost="$(python3 -c 'import sys; print(round(float(sys.argv[1] or 0)+float(sys.argv[2] or 0),4))' "$total_cost" "${cost:-0}")"
    echo "   exit $rc · $turns turns · peak context $peak · \$${cost:-?} · session $SID"
  fi

  if [ "$rc" -eq 130 ] || [ "$rc" -eq 143 ]; then on_interrupt; fi
  # ⚠️ A usage limit is ASKED OF THE RESULT'S OWN STATUS, not of a sentence — one
  # definition, in dfs_limit.py. Defaulted before the eval so a classifier that did
  # not run is never read as "not a limit".
  limit_hit=0; resets=""
  if limit_out="$(python3 "$HERE/dfs_limit.py" "$log" "$AGENT")"; then
    eval "$limit_out"; limit_hit="$limit"
  else
    echo "dfs_run: dfs_limit.py could not read $log — a usage limit here will be recorded as a failure." >&2
  fi
  IN_SESSION=""
  if [ "$limit_hit" = 1 ]; then
    tree_log end "$kind" limit
    stop_is limit "$resets"
    echo "dfs_run: the run hit a usage limit. Stopping; the next session picks up the node's uncommitted work."
    [ -n "$resets" ] && echo "$resets"
    exit 1
  fi
  if [ "$rc" -ne 0 ]; then
    tree_log end "$kind" failed
    stop_is failed
    echo "dfs_run: the run failed (exit $rc). Output in $log."
    sed -n '1,20p' "$log"
    exit 1
  fi

  if [ "$kind" != work ]; then
    # ⚠️ A critic or a reviewer judges; it does not work. Anything it changed outside
    # .dfs/ is it doing the work it was meant to judge, and it must give a verdict.
    fp_after="$(python3 "$HERE/dfs_state.py" --fingerprint --outside-dfs)"
    verdicts_after="$(python3 "$HERE/dfs_state.py" --verdicts "$ITEM" "$kind")"
    if [ "$fp_after" != "$fp_before" ]; then
      tree_log end "$kind" failed
      stop_is failed
      echo "dfs_run: the $kind changed the working tree outside .dfs/. Stopping — look at what it did before anything else runs."
      exit 1
    fi
    if [ "$verdicts_after" -le "$verdicts_before" ]; then
      tree_log end "$kind" failed
      stop_is failed
      echo "dfs_run: the $kind gave no verdict (no \`$kind\` entry in the log). Stopping."
      exit 1
    fi
    tree_log end "$kind" ok "peak $peak"
  else
    fp_after="$(python3 "$HERE/dfs_state.py" --fingerprint)"
    if [ "$fp_after" = "$fp_before" ]; then
      tree_log end work idle
      stop_is idle
      echo "dfs_run: the repo did not move — the session changed nothing. Stopping rather than repeating it."
      exit 1
    fi
    tree_log end work ok "peak $peak"
  fi
  # What the run did to the task, as one outline.
  python3 "$TREE_PY" show "$ITEM" | sed 's/^/   /'
  # A gitignored .dfs is never staged, so the pre-commit guard never judges it: ask
  # the working tree instead, as a warning, since there is no HEAD to hold it to.
  if [ "$DFS_IGNORED" = 1 ]; then python3 "$HERE/dfs_check.py" --warn | sed 's/^/   /' || true; fi

  if [ "${peak:--1}" -gt "$MAX_CONTEXT" ]; then
    stop_is overbudget
    echo "dfs_run: that session reached $peak peak context, past the $MAX_CONTEXT chain ceiling. Stopping — the budget is not holding, and that is worth knowing before more spend."
    exit 1
  fi
  if [ "${peak:--1}" -lt 0 ] && [ "$turns" -gt "$MAX_TURNS" ]; then
    stop_is overturns
    echo "dfs_run: context usage was unavailable and that session reached $turns turns, past the $MAX_TURNS fallback ceiling. Stopping."
    exit 1
  fi
  eval "$(dfs_state)"
  case "$status" in
    blocked) stop_is blocked; echo; echo "dfs_run: BLOCKED after run $i — $why."; break ;;
    done)    stop_is done;    echo; echo "dfs_run: DONE after run $i — $why."; break ;;
  esac
  lowered_cap
  if [ "$i" -ge "$CAP" ]; then stop_is cap; break; fi
done

echo
if [ "$AGENT" != claude ]; then
  echo "dfs_run: $total_turns assistant turns, cost unavailable, over ${i} run(s). Task $ITEM: $status — $why"
else
  echo "dfs_run: $total_turns assistant turns, \$$total_cost, over ${i} run(s). Task $ITEM: $status — $why"
fi
