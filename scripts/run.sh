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
# the author is a raise. Neither work sessions without the author nor rounds of
# implementation review have a limit of their own, since some tasks need extensive
# review: the cap below (the walk's budget, for a walk-started chain) is the backstop
# for a loop. An answer reopens the task
# until a work session has acted on it. All of it is counted from the
# task's own Log, where the runner records every session it starts and how it ended;
# none of it is left to a session's judgement.
#
# ⚠️ AN ITEM IS WORKED ON ITS OWN BRANCH, IN ITS OWN WORKTREE (branch.py). The first run
# from the default branch cuts `dfs/<task>` into `.dfs/worktrees/<task>` and every
# session runs there; a passing review (or the author's accept) rebases it onto the
# default branch, merges it back with `--no-ff`, and removes the worktree and the branch.
# See the two blocks below the argument parsing.
#
# ⚠️ A SESSION MAY END MID-NODE, and the next one picks the work up. A node ends with
# a commit, not a session: a usage limit or the context checkpoint leaves the node's
# uncommitted work in the tree, and the briefing tells the next session it is its
# own. That is only true per checkout: a task's own worktree, or the one in-place chain,
# since a parallel walk runs other chains beside it elsewhere. Uncommitted work that is NOT
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
# Usage:  <scripts>/run.sh [--codex|--kiro|--opencode] <task> [cap]   e.g. --codex W4 5
#         <scripts>/run.sh --review <task>   answer raises, correct nodes,
#                                           accept a finished tree
#         <scripts>/run.sh --open [task]     write a new task file
#         <scripts>/run.sh [--codex|--kiro|--opencode] --chat [task|project]
#         <scripts>/run.sh --chat-turn <task|project>   one turn of a chat for the web page
#                          (full permissions, as --chat has): the author's message on
#                          stdin, CHAT_SID to resume, JSON out
#         <scripts>/run.sh --bump-agent     pin the agents to nixpkgs now
#                                           (tui.py does it in the background)
#         <scripts>/run.sh --claude-auth <login|status|logout> [flags]   `claude auth`, from
#                          the claude the runs start (CLAUDE_CMD or the pin): the web
#                          page's sign-in (login.py)
# Env:    CLAUDE_CMD       how claude is launched here — a nix run, not a binary on PATH
#         CODEX_CMD        how codex is launched here — also a nix command by default
#         KIRO_CMD         how kiro-cli is launched here — also a nix command by default,
#                          of kiro-cli-unwrapped (nixpkgs' kiro-cli is a bwrap FHS env,
#                          which cannot start inside sandbox.sh's container)
#         OPENCODE_CMD     how opencode is launched here — also a nix command by default.
#                          Its model and endpoint are NOT set here: opencode reads them
#                          from its own config (~/.config/opencode/opencode.json, or the
#                          project's opencode.json, or $OPENCODE_CONFIG). Set
#                          OPENCODE_CMD="opencode -m provider/model" to override for one run.
#         PLAN_MODEL       the model a claude session that PLANS runs on (default: opus): the
#                          first work session, while the task has no nodes, and every critic
#                          and implementation review
#         IMPLEMENT_MODEL  the model every other claude work session runs on (default: sonnet)
#                          Either set empty passes no --model, so claude's own default
#                          applies. codex, kiro and opencode are not told a model.
#         NIX_AGENT_FLAGS  extra flags for the nix command in those defaults
#                          (default: none)
#         AGENT_NIXPKGS_REV the nixpkgs revision the defaults launch from
#                          (default: the pin in .dfs/runs/agent-nixpkgs.rev)
#         PERMISSION_FLAGS default --dangerously-skip-permissions; with --codex the
#                          default is --dangerously-bypass-approvals-and-sandbox, and
#                          with --kiro it is --trust-all-tools, and with --opencode --auto
#         MAX_TURNS  (default: 100)         PROMPT  (default: the SKILL.md line)
#         CHAT_PROMPT override the opening discussion prompt
#         MAX_CONTEXT (default: 400000, the chain ceiling)
#         BASH_DEFAULT_TIMEOUT_MS (default: 3600000, one hour)  the child's Bash tool timeout when a call names none
#         BASH_MAX_TIMEOUT_MS (default: 21600000, six hours)  the most a child's Bash call may ask for
set -euo pipefail

# ⚠️ TWO ROOTS, and neither is `git rev-parse`: the repo being worked on is the CWD
# (deliberately — the tool serves whichever repo you run it in, so which project this
# is must be a fact about the invocation), while the TOOL is wherever this file was
# installed. `paths.py` is the one place either is derived, and the *_REL
# values are how a prompt spells a path from the session's CWD. NOTHING CDs HERE.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
eval "$(python3 "$HERE/paths.py" --sh)"
python3 "$HERE/paths.py" --require || exit 2

MODE=work
AGENT=claude
args=()
for a in "$@"; do
  case "$a" in
    --review)  MODE=review ;;
    --answer)  MODE=review ;;   # the old name of the same pass
    --open)    MODE=open ;;
    --chat)    MODE=chat ;;
    --chat-turn) MODE=chatturn ;;
    --codex)   AGENT=codex ;;
    --kiro)    AGENT=kiro ;;
    --opencode) AGENT=opencode ;;
    --bump-agent) MODE=bump ;;
    --claude-auth) MODE=auth ;;
    *)         args+=("$a") ;;
  esac
done
ITEM="${args[0]:-}"
CAP="${args[1]:-5}"
[ "$MODE" = review ] || [ "$MODE" = chat ] || [ "$MODE" = chatturn ] || [ "$MODE" = open ] || [ "$MODE" = bump ] || [ "$MODE" = auth ] || [ -n "$ITEM" ] || { echo "usage: $0 [--codex|--kiro|--opencode] <task> [cap]  |  --review <task>  |  --open  |  --claude-auth <login|status|logout>  |  [--codex|--kiro|--opencode] --chat [task|project]" >&2; exit 2; }

# No agent needs to be a binary on PATH: each default launcher is a Nix command
# line, split into an argv. Override the provider's command to use a local checkout, a
# pinned rev, or `echo` to see what would run without starting a session.
export NIXPKGS_ALLOW_UNFREE="${NIXPKGS_ALLOW_UNFREE:-1}"

# ⚠️ THE AGENT IS PINNED TO ONE NIXPKGS REVISION, in .dfs/runs/agent-nixpkgs.rev
# (agent.py owns it). `github:nixos/nixpkgs#x` with no revision is a MUTABLE ref,
# so every launch took whatever the nix cache last resolved it to: this used
# `--offline` to stop the re-resolve, but the cache is shared with `./dev`, which
# re-resolves on purpose (scripts/container.sh), so a `c` in the TUI after a `./dev`
# started a different claude-code than the one before it, after a fresh eval. The pin
# moves only when agent.py --update has BUILT the new one: the TUI runs that in
# the background when it starts, and `--bump-agent` runs it here.
# ⚠️ And NOT `--offline` any more: a revision-locked ref is never re-resolved, so
# going online costs nothing (1.3 s either way, measured) and keeps the substituters,
# which offline turned off, building from source a path that had been collected.
# Read ONCE, here, so a chain keeps one agent even when the pin moves under it.
# `AGENT_NIXPKGS_REV=<rev>` tries another revision for one run. Not read at all when
# the provider's command is given, which is how the tests stub the agent.
if [ "$MODE" = bump ]; then
  exec python3 "$HERE/agent.py" --update
fi
# ⚠️ ONLY THE MODES THAT LAUNCH AN AGENT RESOLVE IT. `--open` and `--review` are the
# author typing, and asking nix which nixpkgs to pin put a network round trip (and, on a
# box with no pin yet or no route out, a hang with nothing on the screen) in front of
# `o` and `R`. A chat `--review` starts runs `--chat`, which resolves for itself.
if { [ "$MODE" = work ] || [ "$MODE" = chat ] || [ "$MODE" = chatturn ] || [ "$MODE" = auth ]; } && [ -z "${AGENT_NIXPKGS_REV:-}" ] && { { [ "$AGENT" = claude ] && [ -z "${CLAUDE_CMD:-}" ]; } || { [ "$AGENT" = codex ] && [ -z "${CODEX_CMD:-}" ]; } || { [ "$AGENT" = kiro ] && [ -z "${KIRO_CMD:-}" ]; } || { [ "$AGENT" = opencode ] && [ -z "${OPENCODE_CMD:-}" ]; }; }; then
  AGENT_NIXPKGS_REV="$(python3 "$HERE/agent.py" --rev)" || exit 2
fi
AGENT_NIXPKGS="github:nixos/nixpkgs/${AGENT_NIXPKGS_REV:-}"
NIX_AGENT_FLAGS="${NIX_AGENT_FLAGS:-}"
# kiro-cli takes its session flags after `chat`, so that word is part of how it is
# started rather than of the command a caller overrides. `--v3` is its next-generation
# agent; it takes the same headless flags (checked against 2.24's `chat --help`).
AGENT_SUB=()
CHAT_PROMPT_FLAG=()     # opencode's TUI takes its opening prompt as a flag, not an argument
if [ "$AGENT" = codex ]; then
  AGENT_CMD_ENV=CODEX_CMD
  AGENT_CMD="${CODEX_CMD:-nix shell $NIX_AGENT_FLAGS $AGENT_NIXPKGS#codex -c codex}"
  PERMISSION_FLAGS="${PERMISSION_FLAGS---dangerously-bypass-approvals-and-sandbox}"
elif [ "$AGENT" = kiro ]; then
  AGENT_CMD_ENV=KIRO_CMD
  AGENT_CMD="${KIRO_CMD:-nix shell $NIX_AGENT_FLAGS --impure $AGENT_NIXPKGS#kiro-cli-unwrapped -c kiro-cli}"
  AGENT_SUB=(chat --v3)
  PERMISSION_FLAGS="${PERMISSION_FLAGS---trust-all-tools}"
elif [ "$AGENT" = opencode ]; then
  # `opencode run` is the headless form, so it is added where a session starts
  # (run_session) and not here: the same word would turn the interactive chat into one.
  AGENT_CMD_ENV=OPENCODE_CMD
  AGENT_CMD="${OPENCODE_CMD:-nix shell $NIX_AGENT_FLAGS $AGENT_NIXPKGS#opencode -c opencode}"
  CHAT_PROMPT_FLAG=(--prompt)
  # `--standalone`: the screen's chat, like every headless turn, runs on a private server. opencode's
  # shared background service (`serve --service`, one fixed port) outlives the terminal and belongs to
  # whichever HOME started it first, so a second HOME's run exits "port already in use" (and a TUI
  # hangs at "Starting background server..."). Under one HOME the shared service works too (the screen,
  # the page's turns and the screen again kept one session; tried with opencode 2.1.1), so this is for
  # isolation: the service keeps the config and environment of whichever process started it (a later
  # client given another model url hung on the first one's), and it is left running after the terminal.
  AGENT_SUB=(--standalone)
  PERMISSION_FLAGS="${PERMISSION_FLAGS---auto}"
else
  AGENT_CMD_ENV=CLAUDE_CMD
  AGENT_CMD="${CLAUDE_CMD:-nix run $NIX_AGENT_FLAGS --impure $AGENT_NIXPKGS#claude-code --}"
  PERMISSION_FLAGS="${PERMISSION_FLAGS---dangerously-skip-permissions}"
fi
read -r -a AGENT_ARGV <<< "$AGENT_CMD"
read -r -a PERMISSION_ARGV <<< "$PERMISSION_FLAGS"
# The sign-in is claude's own `auth` command, from the same claude the runs start, so the
# login it writes (~/.claude) is the one they read. claude alone: the others sign in their own way.
if [ "$MODE" = auth ]; then
  [ "$AGENT" = claude ] || { echo "dfs_run: --claude-auth is for claude only" >&2; exit 2; }
  exec "${AGENT_ARGV[@]}" auth "${args[@]}"
fi
MAX_TURNS="${MAX_TURNS:-100}"          # Fallback only when context usage is unavailable.
# ⚠️ CONTEXT IS WHAT THE COST MODEL IS WRITTEN IN; turns are a proxy that varies 3x.
# The 09-06 overrun grew 532 tokens/turn against a 1,707 median, so 136 of its turns
# weighed about what 42 average ones would. A ceiling on turns cannot see that; a
# ceiling on peak context can, and either one tripping ends the chain.
# Read from context.py's CHAIN_CEILING, not spelled again: one file owns every
# budget number, and a loop that disagreed with the session about the measurement is
# the failure this roadmap keeps writing down. ⚠️ It is NOT `FLAT_TO`, which it was
# until 2026-09-17 — that is the advice a session weighs when deciding to close, and
# this is where an unattended chain stops and fetches a person. The argument for the
# two being one value, and why it was wrong, is on CHAIN_CEILING itself.
MAX_CONTEXT="${MAX_CONTEXT:-$(python3 "$HERE/context.py" --chain-ceiling)}"
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
TREE_PY="$HERE/tree.py"

build_prompt() {  # build_prompt work|critic|review
  local brief ok flag=--brief lead="$PROMPT_LEAD"
  if [ "$1" = critic ]; then flag=--critic-brief; lead="$CRITIC_LEAD"; fi
  if [ "$1" = review ]; then flag=--review-brief; lead="$REVIEW_LEAD"; fi
  brief="$(python3 "$HERE/state.py" "$flag" "$ITEM" 2>"$RUNDIR/brief.err")" && ok=1 || ok=0
  cat "$RUNDIR/brief.err" >&2   # a notice the briefing prints is shown whether or not it succeeds
  if [ "$ok" = 0 ]; then
    echo "dfs_run: state.py $flag $ITEM failed; a session cannot be briefed." >&2
    # recorded, so the walk says why the chain ended rather than that it did not say
    stop_is brief
    meta_set cause="state.py $flag failed: $(tail -n 1 "$RUNDIR/brief.err")"
    return 1
  fi
  printf '%s\n\n%s\n' "$lead" "$brief"
}

if [ "$MODE" = work ] || [ "$MODE" = chat ] || [ "$MODE" = chatturn ]; then
  command -v "${AGENT_ARGV[0]}" >/dev/null || { echo "dfs_run: cannot run '${AGENT_ARGV[0]}' (set $AGENT_CMD_ENV)" >&2; exit 2; }
fi

# A child agent establishes its OWN session identity — claude from the --session-id
# below, codex by assigning one and emitting it in its stream — so nothing about the
# parent's session is legitimately inherited, and all four names are scrubbed rather
# than the other provider's two. The asymmetry that was here covered only the CROSS
# case, which is real (context.py resolves `claude_sid or codex_sid`, so a
# stale CLAUDE_CODE_SESSION_ID beats a live codex id and the worker reads the PARENT's
# transcript) but is the rarer one: this script is normally run FROM a session of the
# same agent it launches, and that inheritance was uncovered. A worker measuring the
# wrong transcript reports the wrong context against its own budget and says nothing.
# If a child turns out not to export its own id, context.py prints that no
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
# kiro-cli and opencode update themselves unless told not to, and a chain's agent is the
# pinned one.
AGENT_ENV=(env -u CLAUDE_CODE_SESSION_ID -u CODEX_SESSION_ID -u CODEX_THREAD_ID
           -u KIRO_SESSION_ID KIRO_NO_AUTO_UPDATE=1
           OPENCODE_DISABLE_AUTOUPDATE=true
           BASH_DEFAULT_TIMEOUT_MS="${BASH_DEFAULT_TIMEOUT_MS:-3600000}"
           BASH_MAX_TIMEOUT_MS="${BASH_MAX_TIMEOUT_MS:-21600000}")

# One headless opencode turn, its `--format json` stream on stdout: the prompt is stdin, the rest
# is `run`'s arguments. opencode 2.1.1's private server (`serve --stdio`) sometimes crashes in Bun
# as it starts ("Bus error"), and the run then ends before any step began: in a `Transport:` `error`, or
# (when the server went before it was ready, 25s to 38s later on a loaded machine) with nothing on stdout and
# "Standalone server exited before reporting readiness" on stderr. The turn is then run again (five runs at most: on a
# loaded machine half the starts crash). A stream that began a step is never run again,
# nor is a turn of a session whose store gained an answer meanwhile: the crashed attempt may have
# stored the message (the page reads a message stored twice once) but not an answer to be asked for twice.
# A store that could not be read (`unreadable`) on either side shows nothing, so the turn is run again.
opencode_turn() {
  local prompt tries=0 out err sid="" answers="" now a
  prompt="$(cat; echo x)"; prompt="${prompt%x}"
  for a in "$@"; do [ "$sid" = "?" ] && sid="$a"; [ "$a" = --session ] && sid="?"; done
  [ "$sid" = "?" ] && sid=""
  [ -z "$sid" ] || answers="$(python3 "$HERE/chat.py" --opencode-answers "$sid")"
  out="$(mktemp)"; err="$(mktemp)"
  # A Stop or a timeout signals the turn's whole process group, this function with it: the files hold
  # the conversation's text, so they go with it. (bash runs a trap once the foreground `run` has ended.)
  trap 'rm -f "$out" "$err"' EXIT
  trap 'exit 143' TERM INT HUP
  while :; do
    printf '%s' "$prompt" | "${AGENT_ENV[@]}" "${AGENT_ARGV[@]}" run --standalone --format json "$@" > "$out" 2> "$err" || true
    tries=$((tries + 1))
    if [ "$tries" -ge 5 ] || grep -q '"type":"step_start"' "$out"; then break; fi
    if ! grep -q '"type":"error".*Transport:' "$out" && { [ -s "$out" ] || ! grep -q 'exited before reporting readiness' "$err"; }; then break; fi
    if [ -n "$sid" ] && now="$(python3 "$HERE/chat.py" --opencode-answers "$sid")" && [ "$now" != "$answers" ] \
       && [ "$now" != unreadable ] && [ "$answers" != unreadable ]; then break; fi
    echo "dfs_run: opencode ended before it began the turn; running it again" >&2
  done
  cat "$err" >&2; cat "$out"; rm -f "$out" "$err"
  trap - EXIT TERM INT HUP
}

# What an item's chat opens with, here and on the web page (--chat-turn's opening): short, in
# this order, then the question. Concise on purpose: the author is often reading it on a phone.
TREE_SUMMARY_ASK="Start with a concise summary, in short lines and no more than you need: (1) the tree, where the work is and what was refuted and why; (2) each assumption it rests on, in plain words, and what would make it wrong; (3) each open raise, what it asks of the author and the options with your recommendation. Say plainly if there are no assumptions or no raises. Then ask what the author wants to talk about."

# A task's files are read where the task is worked: its item worktree's `items/` when it has
# one. The checkout's own copy of the task file is a stale top-level description by then
# (the nodes are committed on `dfs/<task>`), so a chat is pointed at the worktree's.
TASK_ITEMS_REL="$DFS_ITEMS_REL"
if { [ "$MODE" = chat ] || [ "$MODE" = chatturn ]; } && [ -n "$ITEM" ] && [ "$ITEM" != project ] && [ "$ITEM" != new ]; then
  TASK_ITEMS_REL="$(python3 "$HERE/paths.py" --task-items-rel "$ITEM")" || exit 2
  WT_ITEMS_REL="$(python3 "$HERE/paths.py" --task-worktree-items-rel "$ITEM")" || exit 2
  # The session keeps this prompt for good, and the worktree may be cut after it begins, so the
  # prompt also says where to look then.
  WORKTREE_RULE="Where the task is worked is its own worktree: whenever $WT_ITEMS_REL exists it holds the task's current files and the top-level $DFS_ITEMS_REL copy is stale, so check which exists before you rely on what you read earlier."
fi

if [ "$MODE" = chat ]; then
  if [ "$ITEM" = project ] || [ "$ITEM" = new ]; then
    # The project as a whole, not one task: where it stands, what to do next, and new
    # work when that is where the talk goes. `new` is the old name, from when this was
    # only for scoping a new task. Scoping still stops where --open takes over, so a
    # task file is written from the author's words, in front of them.
    default_chat_prompt="Read $DFS_ROADMAP_REL. Discuss the project with the author: its direction, the tasks on the roadmap and how they stand, what to do next, or new work they have in mind. Do not perform work or edit files unless the author explicitly asks. If the talk turns to a NEW task, ask what done looks like and why it is worth doing, then propose the name and the Goal paragraph, and no steps (the author adds those as nodes later). Ask the author before writing the task file; if they say yes, create it with an empty tree (the author can also write it with $DFS_SCRIPTS_REL/run.sh --open). Start by summarising the roadmap in a few lines and asking what the author wants to talk about."
    chat_subject="the project"
  elif [ -n "$ITEM" ]; then
    default_chat_prompt="Read $DFS_ROADMAP_REL and task $ITEM's parts, $TASK_ITEMS_REL/$ITEM.md and every file in $TASK_ITEMS_REL/$ITEM/, whichever exist. $WORKTREE_RULE Discuss task $ITEM with the author. Do not perform the work, edit files or append to the task's log unless the author explicitly changes the task. $TREE_SUMMARY_ASK"
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
  # ⚠️ ONE CONVERSATION PER SCOPE, SHARED WITH THE WEB PAGE. With CHAT_SID (the screen sets it,
  # from chat.py's record) the session is started under that id, or RESUMED when it already has
  # a transcript, so what is said here is on the page and what was said on the page is here.
  # A resumed session needs no opening prompt: it already has one.
  chat_session=()
  if [ "$AGENT" = claude ] && [ -n "${CHAT_SID:-}" ]; then
    if [ -n "${CHAT_RESUME:-}" ]; then
      chat_session=(--resume "$CHAT_SID"); CHAT_PROMPT=""
    else
      chat_session=(--session-id "$CHAT_SID")
    fi
  elif [ "$AGENT" != claude ] && [ -n "${CHAT_SHARE:-}" ]; then
    # codex, opencode and kiro mint their own session ids, so a NEW shared chat cannot be started
    # under one the way claude's is. Its opening turn runs headless first (what the interactive
    # one would have done with the same prompt), the id is recorded for the page, and the chat
    # then RESUMES that session: the opening is in its history and the page and this terminal
    # share it.
    if [ -z "${CHAT_RESUME:-}" ]; then
      echo "dfs_run: $AGENT has no session to resume for $chat_subject; running its opening turn first"
      opening_json="$(mktemp)"
      if [ "$AGENT" = codex ]; then
        printf '%s' "$CHAT_PROMPT" | "${AGENT_ENV[@]}" "${AGENT_ARGV[@]}" exec --json "${PERMISSION_ARGV[@]}" - \
          | python3 "$HERE/chat.py" --codex-result > "$opening_json" || true
      elif [ "$AGENT" = kiro ]; then
        "${AGENT_ENV[@]}" "${AGENT_ARGV[@]}" "${AGENT_SUB[@]}" --output-format stream-json --no-interactive "${PERMISSION_ARGV[@]}" -- "$CHAT_PROMPT" 2>&1 < /dev/null \
          | python3 "$HERE/chat.py" --kiro-result > "$opening_json" || true
      else
        printf '%s' "$CHAT_PROMPT" | opencode_turn "${PERMISSION_ARGV[@]}" \
          | python3 "$HERE/chat.py" --opencode-result > "$opening_json" || true
      fi
      opening_field() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2], ""))' "$opening_json" "$1" 2>/dev/null || true; }
      if [ "$(opening_field is_error)" != False ] || [ -z "$(opening_field session_id)" ]; then
        echo "dfs_run: the opening turn failed: $(opening_field result)" >&2
        rm -f "$opening_json"; exit 1
      fi
      CHAT_SID="$(opening_field session_id)"; rm -f "$opening_json"
      python3 "$HERE/chat.py" --save "$ITEM" "$CHAT_SID" "$AGENT" || exit 1
    fi
    case "$AGENT" in
      codex) chat_session=(resume "$CHAT_SID") ;;
      kiro)  chat_session=(--resume-id "$CHAT_SID") ;;
      *)     chat_session=(--session "$CHAT_SID") ;;
    esac
    CHAT_PROMPT=""
  fi
  echo "dfs_run: opening an interactive $AGENT session for $chat_subject"
  if [ -n "$CHAT_PROMPT" ]; then
    exec "${AGENT_ENV[@]}" "${AGENT_ARGV[@]}" "${AGENT_SUB[@]}" "${chat_session[@]}" "${PERMISSION_ARGV[@]}" "${CHAT_PROMPT_FLAG[@]}" "$CHAT_PROMPT"
  fi
  exec "${AGENT_ENV[@]}" "${AGENT_ARGV[@]}" "${AGENT_SUB[@]}" "${chat_session[@]}" "${PERMISSION_ARGV[@]}"
fi

# One turn of a chat, for the web page: the author's message on stdin, the agent's reply as
# the JSON claude prints on stdout. ⚠️ FULL PERMISSIONS, the same flags as the interactive chat
# above: Edit, Write and Bash all work and nothing is refused or asked about. That is the
# exposure the port already has (the walk it starts runs with permission prompts off), and
# `--no-walk` turns the chat off with it. The turn is headless and one-shot so that a resumed
# turn reads the cached prefix. The first turn gets the framing; a resumed one is just the
# message, the session already has it.
if [ "$MODE" = chatturn ]; then
  [ "$AGENT" = claude ] || [ "$AGENT" = codex ] || [ "$AGENT" = opencode ] || [ "$AGENT" = kiro ] || { echo "dfs_run: a chat from the web page runs under claude, codex, opencode or kiro" >&2; exit 2; }
  message="$(cat)"
  # CHAT_OPENING: the page opened a new chat and nobody has said anything yet, so the turn is
  # the same opening the screen's chat starts with: the summary, unprompted.
  [ -n "$message" ] || [ -n "${CHAT_OPENING:-}" ] || { echo "dfs_run: --chat-turn wants the message on stdin" >&2; exit 2; }
  if [ -n "${CHAT_SID:-}" ] && [ -n "${CHAT_RESUME:-}" ]; then
    prompt="$message"
    session_flag=(--resume "$CHAT_SID")
    [ "$AGENT" = codex ] && session_flag=("$CHAT_SID")      # codex exec resume <id>
    [ "$AGENT" = opencode ] && session_flag=(--session "$CHAT_SID")
    [ "$AGENT" = kiro ] && session_flag=(--resume-id "$CHAT_SID")
  else
    if [ "$ITEM" = project ]; then
      about="the project as a whole: read $DFS_ROADMAP_REL for how it stands"
    else
      about="task $ITEM: read $DFS_ROADMAP_REL, then $TASK_ITEMS_REL/$ITEM.md and every file in $TASK_ITEMS_REL/$ITEM/, whichever exist. $WORKTREE_RULE"
    fi
    framing="You are talking with the author about $about. They are on a phone: answer briefly and plainly, in short paragraphs, and say which node or file you mean."
    if [ -n "${CHAT_OPENING:-}" ] && [ -z "$message" ]; then
      if [ "$ITEM" = project ]; then
        prompt="$framing Start by summarising the roadmap in a few short lines and asking what the author wants to talk about."
      else
        prompt="$framing $TREE_SUMMARY_ASK"
      fi
    else
      prompt="$framing The author writes: $message"
    fi
    session_flag=(--session-id "${CHAT_SID:-$(python3 -c 'import uuid; print(uuid.uuid4())')}")
    [ "$AGENT" != claude ] && session_flag=()     # the others name the session themselves
  fi
  if [ "$AGENT" = codex ]; then
    # The prompt goes in on stdin (`-`): a message that starts with a dash is not a flag, and
    # its length is not an argv's. codex prints a stream of events, read down to the one JSON
    # line chat.py wants; the pipeline's status is the reader's, which fails when the turn did.
    resume_word=(); [ -n "${CHAT_SID:-}" ] && [ -n "${CHAT_RESUME:-}" ] && resume_word=(resume)
    printf '%s' "$prompt" | "${AGENT_ENV[@]}" "${AGENT_ARGV[@]}" exec "${resume_word[@]}" --json "${PERMISSION_ARGV[@]}" "${session_flag[@]}" - \
      | python3 "$HERE/chat.py" --codex-result "${CHAT_SID:-}"
    exit "${PIPESTATUS[1]}"
  fi
  if [ "$AGENT" = opencode ]; then
    # The prompt is on stdin here too: as an argument opencode wraps a message with spaces in
    # quotes, which the page would show; on stdin it is kept as written.
    printf '%s' "$prompt" | opencode_turn "${PERMISSION_ARGV[@]}" "${session_flag[@]}" \
      | python3 "$HERE/chat.py" --opencode-result "${CHAT_SID:-}"
    exit "${PIPESTATUS[1]}"
  fi
  if [ "$AGENT" = kiro ]; then
    # stderr goes into the stream: kiro says a failed login or a limit there, and the reader
    # takes what is not JSON as the error. `--` keeps a message that starts with a dash from
    # being read as a flag.
    "${AGENT_ENV[@]}" "${AGENT_ARGV[@]}" "${AGENT_SUB[@]}" --output-format stream-json --no-interactive "${PERMISSION_ARGV[@]}" \
      "${session_flag[@]}" -- "$prompt" 2>&1 < /dev/null \
      | python3 "$HERE/chat.py" --kiro-result "${CHAT_SID:-}"
    exit "${PIPESTATUS[1]}"
  fi
  exec "${AGENT_ENV[@]}" "${AGENT_ARGV[@]}" -p "$prompt" --output-format json "${session_flag[@]}" \
    "${PERMISSION_ARGV[@]}" < /dev/null
fi

# The caller may name the directory. tui.py does, because it starts a chain in the
# BACKGROUND and has to know where the console log will land before there is a
# process to ask. The root is asked of runs.py rather than spelled here, so the
# writer and the two readers cannot disagree about it.
if [ "$MODE" = work ]; then
  if [ -n "${RUNDIR:-}" ]; then
    mkdir -p "$RUNDIR"
  else
    RUN_ROOT="$(python3 "$HERE/runs.py" --root)"
    mkdir -p "$RUN_ROOT"
    RUNDIR="$(mktemp -d "$RUN_ROOT/dfs_run_XXXXXX")"
  fi
fi
# ⚠️ READ, THEN NOT PASSED ON. `RUNDIR` arrives in the ENVIRONMENT (tui.py sets
# it), so everything this starts inherited it: the agent, and whatever the agent
# runs. On 2026-09-27 a W13 session working on this tooling ran test_live.py,
# whose own `run.sh --codex W1 2` then wrote its meta INTO the live chain's
# directory: item W1, cap 2, agent codex, and a pid that soon died. The walk read
# its own chain as ended, started a second chain on W13 beside the first, and that
# one's critic found the first one's edits and stopped `failed`. A chain's directory
# is the chain's, so the name stops at this process.
export -n RUNDIR 2>/dev/null || true

# What this directory is for, and how far the chain has got — read once a second by
# tui.py, given meaning by runs.py, written ATOMICALLY so a reader never
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
#   blocked     an open raise — the agent's, the critic's or the reviewer's
#   done        every live node confirmed, and the implementation reviewed or accepted
#   cap         the chain ran its full cap with the task still open
#   idle        the repo did not move: the session changed nothing (a critic or
#               reviewer that gave no verdict, twice, with nothing changed either)
#   failed      a session exited non-zero, or a critic or reviewer changed the tree
#               or wrote to the task and still gave no verdict (one that changed
#               nothing at all is carried past, and `idle` twice running)
#   overbudget  a session went past the context ceiling
#   overturns   no context reading, and past the turn ceiling
#   interrupt   Ctrl-C
#   brief       a session could not be briefed (`cause` says why): its task file is
#               missing in the worktree, `state.py` failed, or `branch.py enter` failed
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
# asked of the pid instead (scripts/runs.py). What this adds is the
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
# its share of the budget the walk's live chains have not reserved as its cap, so turning the walk off used to leave
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
  python3 "$HERE/state.py" --sh "$ITEM"
}

# Peak context and response count for a transcript, both from
# scripts/context.py rather than from a copy here. ⚠️ ONE DEFINITION ON
# PURPOSE: a session reads its own budget through that file and this loop enforces a
# ceiling on the same quantity, and a loop that disagreed with the session about what
# its budget said is the failure this roadmap keeps writing down — the screen's own
# first draft of this arithmetic already differed from it by 2 tokens on a 238-response
# transcript. Claude writes one line per content block, so its records are deduped on
# message.id; Codex writes one token_count event per API response. Both count model
# responses rather than JSONL records. A peak of -1 means no reading (no transcript, or
# a stream with no usage in it), which is itself worth seeing, and is what the ceilings
# below read as "unavailable". The turns beside it are then counted without usage
# (-1 only when there is no transcript), since the MAX_TURNS fallback reads them.
transcript_measure() {  # transcript -> "<peak> <turns>"
  python3 "$HERE/context.py" --measure "$1" "$AGENT"
}
transcript_turns() { transcript_measure "$1" | cut -d' ' -f2; }

transcript_path() {  # session id, run log -> the provider's persisted transcript
  # A kiro or opencode session's transcript IS the stream this chain captured
  # (context.py, `_kiro_log`), and here the log is known without searching for it.
  if [ "$AGENT" = kiro ] || [ "$AGENT" = opencode ]; then printf '%s\n' "$2"; return; fi
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
import context as dfs_context
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

# opencode's is the `sessionID` on every event of its `--format json` stream.
opencode_session_id() {
  python3 - "$1" "$HERE" <<'PY'
import json, sys
sys.path.insert(0, sys.argv[2])
import context as dfs_context
recs = []
try:
    with open(sys.argv[1], errors="replace") as fh:
        for n, line in enumerate(fh):
            if n >= 200: break
            try: recs.append(json.loads(line))
            except Exception: continue
except FileNotFoundError:
    pass
print(dfs_context.opencode_session_id(recs))
PY
}

stream_sid() {  # the session id in a codex, kiro or opencode stream so far
  if [ "$AGENT" = kiro ]; then kiro_session_id "$1"
  elif [ "$AGENT" = opencode ]; then opencode_session_id "$1"
  else codex_thread_id "$1"; fi
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
# Codex, Kiro and Opencode assign the id and emit it in their JSONL stream; Claude
# accepts ours up front.
run_session() {
  local prompt="$1" log="$2" session_rc agent_pid
  if [ "$AGENT" = opencode ]; then
    # As kiro, below, and for the same reasons: stderr goes into the log (a config
    # that does not parse, or a provider that cannot be reached, says so there and not
    # in the stream), and the log is the transcript. The prompt is a positional, so it
    # goes after every flag; `--format json` is the stream, and the run's exit status
    # is 1 when it ends in an `error` event.
    SID=""
    meta_set sid=""
    # The author's messages reach it through a plugin that reads the run's inbox after each
    # tool call (inbox.py, opencode_inbox/). ⚠️ `--standalone`: without it the run goes to
    # opencode's shared background service, which loads its config and plugins once, in the
    # environment of whoever started it, so this run's plugin and DFS_INBOX would never be seen.
    # DFS_INBOX is in opencode's environment (the plugin is in its process, and has no command to
    # carry the box in); the other agents' hooks name the box in their commands and get none.
    local opencode_config
    opencode_config="$(python3 "$HERE/inbox.py" --opencode-config)"
    "${AGENT_ENV[@]}" DFS_INBOX="$RUNDIR/inbox" OPENCODE_CONFIG_CONTENT="$opencode_config" \
      "${AGENT_ARGV[@]}" run --standalone --format json "${PERMISSION_ARGV[@]}" \
      "$prompt" > "$log" 2>&1 < /dev/null &
    agent_pid=$!
    stream_watch_sid "$log" "$agent_pid"
    wait "$agent_pid"
    session_rc=$?
    SID="$(stream_sid "$log")"
    meta_set sid="$SID"
  elif [ "$AGENT" = kiro ]; then
    # As codex, below. stderr goes into the log too: kiro reports a failed login or
    # a limit there and not in the stream, and the log is all a finished run leaves.
    # The lines that are not JSON are read as kiro's own (limit.py, tui.py).
    SID=""
    meta_set sid=""
    # The author's messages reach it through a preToolUse hook that holds a tool call back
    # with the message as its result (inbox.py): kiro hands the model the output of no other
    # hook after the start. A hook lives only in a custom agent, found as
    # `.kiro/agents/dfs-inbox.json` in the working directory, which inbox.py writes and keeps
    # out of git status; a message arriving after the last tool call stays waiting.
    python3 "$HERE/inbox.py" --kiro-agent "$PWD" "$RUNDIR/inbox" > /dev/null
    "${AGENT_ENV[@]}" "${AGENT_ARGV[@]}" "${AGENT_SUB[@]}" --output-format stream-json \
      --agent dfs-inbox --no-interactive "${PERMISSION_ARGV[@]}" "$prompt" > "$log" 2>&1 < /dev/null &
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
    # The author's messages reach it as claude's do: two hooks that read the run's inbox
    # (inbox.py), installed by `-c` overrides, one flag to a line. ⚠️ BEFORE `exec`: a `-c`
    # after it replaces every `-c` the launch command gave before it (a model provider
    # among them), and codex then asks api.openai.com.
    local inbox_args=() inbox_arg
    while IFS= read -r inbox_arg; do inbox_args+=("$inbox_arg"); done < <(python3 "$HERE/inbox.py" --codex-args "$RUNDIR/inbox")
    "${AGENT_ENV[@]}" "${AGENT_ARGV[@]}" "${inbox_args[@]}" exec --json "${PERMISSION_ARGV[@]}" "$prompt" \
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
    # first one — by tui.py, which is the only reader.
    meta_set sid="$SID"
    # The author's messages from the screen or the page land in the run's inbox, and
    # these two hooks hand them over: at the next tool call, or as one more turn when
    # the session would stop (inbox.py). Without a message they print nothing.
    local inbox_settings
    inbox_settings="$(python3 "$HERE/inbox.py" --settings "$RUNDIR/inbox")"
    "${AGENT_ENV[@]}" "${AGENT_ARGV[@]}" -p "$prompt" --output-format json --session-id "$SID" \
      --settings "$inbox_settings" "${MODEL_ARGV[@]}" "${PERMISSION_ARGV[@]}" > "$log"
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
# correction prunes everything below the node (and, if confirmed, every sibling made
# after it) unless it only confirms what already stood confirmed; the next session
# resumes from it. A finished tree can then be accepted.
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
import tree as dfs_tree

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
    argv = [str(Path(here) / "run.sh")] + (["--" + agent] if agent != "claude" else []) + ["--chat", task]
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

import paths as dfs_paths
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
    verdict = "confirmed" if v == "c" else "refuted"
    was = dfs_tree.effective(t)[0].get(nid)          # what it stands as NOW, corrections applied
    dfs_tree.append_log(task, "correct", [nid, verdict], body)
    wrote += 1
    say("  -> corrected; %s and %s reopens\n" % (
        "it already stood confirmed, so nothing is pruned" if dfs_tree.keeps(verdict, was)
        else "everything below %s is pruned" % nid, task))

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
# The author's words go in unchanged, under an empty tree: the author adds nodes for
# explicit steps, and the sessions plan, branch and back up from there.
open_mode() {
  python3 - "${ITEM:-}" "$HERE" <<'PY'
import os, re, sys
from pathlib import Path

want, here = sys.argv[1].strip(), sys.argv[2]
sys.path.insert(0, here)
import paths as dfs_paths, tree as dfs_tree
try:
    nxt = dfs_tree.next_task_id()
except dfs_paths.NoBranch as e:
    print("dfs_run: %s" % e, file=sys.stderr); raise SystemExit(2)

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
say("\nYour words go in unchanged. End a multi-line answer with a lone '.'\n")
name = ask("  short name: ")
if not name:
    say("dfs_run: nothing opened."); raise SystemExit(0)
goal = block("  Goal — what done looks like and WHY (lone '.' ends it):")
draft = ask("  Draft, so the walk passes it over until it is marked ready? [y/N]: ").lower().startswith("y")
try:
    task = dfs_tree.create_task(name, goal, task=task, draft=draft)
except ValueError as e:
    say("dfs_run: %s." % e); raise SystemExit(2)
say("\ndfs_run: wrote %s%s." % (dfs_tree.part_path(task, dfs_paths.branch_tag()), ", a draft" if draft else ""))
PY
  python3 "$HERE/check.py" --warn || true
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
import paths as dfs_paths, tree as dfs_tree
try:
    dfs_paths.branch_tag()
except dfs_paths.NoBranch as e:
    print("dfs_run: %s" % e, file=sys.stderr); raise SystemExit(2)
# a task file here that its worktree lacks is said after the worktree is entered, with why
if not dfs_tree.exists(sys.argv[1]) and not (dfs_paths.items() / ("%s.md" % sys.argv[1])).is_file():
    print("dfs_run: no task %s under %s" % (sys.argv[1], dfs_paths.rel(dfs_paths.items())),
          file=sys.stderr); raise SystemExit(2)
# Said, not refused: a roadmap part git will not add is in no commit, and is in the item's
# worktree only through the link to the main checkout's top-level entry that git ignores
# (a file hidden inside a tracked directory never is); this names the rule before a session
# finds one missing.
hidden = dfs_paths.hidden()
if hidden:
    print("dfs_run: " + dfs_paths.hidden_message(hidden), file=sys.stderr)
PY

# ⚠️ AN ITEM IS WORKED ON ITS OWN BRANCH, IN ITS OWN WORKTREE. From the default branch,
# the first run cuts `dfs/<task>` into `.dfs/worktrees/<task>` (branch.py), and every
# session of the chain, work, critic and review, runs there: the author's checkout stays
# on the default branch, and the screen reads the task through the worktree (paths.py).
# The chain's own files stay in the main checkout, where the screen looks: RUNDIR is made
# absolute before the move, and the paths the sessions are handed are derived again from
# the worktree, since they are spelled from the session's CWD. Everything above read
# the main checkout and is done with it.
RUNDIR="$(cd "$RUNDIR" && pwd)"
ITEM_MAIN="$(pwd)"
# ⚠️ THE LOCK IS HELD FOR THE CHAIN'S WHOLE LIFE (fd 9, dropped by the kernel when this
# process dies, however it dies). A screen in another pid namespace cannot tell by the pid
# below whether the chain is alive, and a container that restarted has none of its old
# processes; a free lock on this file is how `runs.close_lost` knows the chain is over.
# Taken before `pid` and `pidns` are recorded, so no record names a chain that holds no lock.
# Only when `flock` exists: a lock file nobody holds would read as a dead chain.
if command -v flock >/dev/null 2>&1; then
  exec 9>"$RUNDIR/lock"
  flock -n 9 || true
fi
meta_set item="$ITEM" cap="$CAP" agent="$AGENT" mode="$MODE" \
         started="$(date -Is)" pid="$$" pidns="$(readlink /proc/$$/ns/pid 2>/dev/null || true)"
trap on_exit EXIT
trap on_interrupt INT TERM
# An `enter` that fails (a BranchError, a traceback) is recorded as the stop, with its cause,
# so the walk does not say the chain ended without recording why.
if ! ITEM_TREE="$(python3 "$HERE/branch.py" enter "$ITEM" 2>"$RUNDIR/enter.err")"; then
  cat "$RUNDIR/enter.err" >&2
  stop_is brief
  meta_set cause="branch.py enter failed: $(tail -n 1 "$RUNDIR/enter.err")"
  exit 2
fi
cat "$RUNDIR/enter.err" >&2   # the notices it prints (a reused branch, a clash) are shown
if [ -n "$ITEM_TREE" ]; then
  cd "$ITEM_TREE"
  eval "$(python3 "$HERE/paths.py" --sh)"
  echo "dfs_run: $ITEM is worked on branch $(git symbolic-ref --short HEAD), in $ITEM_TREE"
fi

# ⚠️ A DONE ITEM IS MERGED, and from the main checkout, which is where the default
# branch is checked out. The runner stops `done` only after a review `ok` (or the author's
# accept), so this is the "passing review merges the branch". The worktree is removed
# with it, so the chain steps back into the main checkout first.
# A merge that cannot go through (branch.py, `_unmerged`) is not a done item: a conflict
# puts an open node back in the tree (exit 3) and the chain goes on and works it, and
# edits of the author's that overlap the merge raise (exit 4). Both return non-zero here
# and the chain steps back into the worktree.
merge_item() {
  [ -n "$ITEM_TREE" ] || return 0
  local rc=0
  cd "$ITEM_MAIN"
  python3 "$HERE/branch.py" merge "$ITEM" || rc=$?
  # Anything but a merge (or nothing to merge) is not done: an exit this does not know
  # (a crash) blocks the chain rather than ending it `done` with the branch unmerged.
  [ "$rc" = 0 ] && return 0
  cd "$ITEM_TREE"
  case "$rc" in 3|4) ;; *) rc=4; echo "dfs_run: the merge of $ITEM failed unexpectedly; see above." ;; esac
  return "$rc"
}

# ⚠️ THE TASK IS READ FROM THE WORKTREE NOW, and the preflight above read the main
# checkout's. The branch has what git tracks, and `branch.enter` moves in the task's
# untracked files; neither reaches a task file an ignore rule hides: on 2026-10-19 a
# project whose `.*` rule hid `.dfs` reached the worktree with only its force-added
# files, and the first briefing failed with "no task file". Said here with its cause,
# and kept as the stop.
if [ -n "$ITEM_TREE" ]; then
  cause="$(python3 - "$ITEM" "$HERE" "$ITEM_MAIN" "$ITEM_TREE" <<'PY'
import subprocess, sys
sys.path.insert(0, sys.argv[2])
import paths as dfs_paths, tree as dfs_tree
task, main, wt = sys.argv[1], sys.argv[3], sys.argv[4]
if not dfs_tree.exists(task):
    rel = "%s/items/%s.md" % (dfs_paths.STATE_DIR_NAME, task)
    rule = subprocess.run(["git", "check-ignore", "-v", "--no-index", rel], cwd=main,
                          capture_output=True, text=True).stdout.split("\t")[0]
    print("the worktree %s has no %s, which the main checkout has: the branch does not track "
          "it and it was not moved in from there%s" % (wt, rel,
                                 ", and the ignore rule %s hides it" % rule if rule else ""))
PY
)"
  if [ -n "$cause" ]; then
    stop_is brief
    meta_set cause="$cause"
    echo "dfs_run: $ITEM cannot be briefed: $cause." >&2
    exit 2
  fi
fi

# ⚠️ AN EMPTY TASK FILE IS NOT A NEW TASK. A process killed while it rewrote the file
# (before writes were atomic) left 0 bytes: on 2026-10-23 W118's worktree held an empty
# W118.md over a finished 96-line tree at the branch HEAD, and `state.py` briefed it as
# "no nodes yet — plan the task". Refused here, with the way back, rather than planned over.
cause="$(python3 - "$ITEM" "$HERE" <<'PY'
import subprocess, sys
sys.path.insert(0, sys.argv[2])
import paths as dfs_paths, tree as dfs_tree
p = dfs_tree.part_path(sys.argv[1])
if p.is_file() and not p.read_text().strip():
    rel = dfs_paths.rel(p)
    head = subprocess.run(["git", "show", "HEAD:./" + rel], capture_output=True, text=True).stdout
    if head.strip():
        print("%s is empty, and the branch HEAD has %d lines of it: restore it with "
              "git checkout -- %s" % (rel, head.count("\n"), rel))
PY
)"
if [ -n "$cause" ]; then
  stop_is brief
  meta_set cause="$cause"
  echo "dfs_run: $ITEM cannot be briefed: $cause." >&2
  exit 2
fi

# ⚠️ THE DIRT HAS AN OWNER: the task whose chain works this checkout (its worktree's own
# task, else the task with the last session, chains taking turns). Work in the tree that is
# not this task's is committed or stashed by this chain's first session, whose briefing lists it.
foreign="$(python3 "$HERE/state.py" --foreign-dirt "$ITEM")"
if [ -n "$foreign" ]; then
  echo "dfs_run: $(printf '%s\n' "$foreign" | wc -l) uncommitted path(s) are not $ITEM's; the first session commits or stashes them."
fi

echo "dfs_run: task $ITEM, cap $CAP, agent $AGENT, logs in $RUNDIR"
if [ "$AGENT" = codex ]; then
  echo "dfs_run: launching  ${AGENT_ARGV[*]} exec --json ${PERMISSION_ARGV[*]} <prompt>"
elif [ "$AGENT" = opencode ]; then
  echo "dfs_run: launching  ${AGENT_ARGV[*]} run --standalone --format json ${PERMISSION_ARGV[*]} <prompt>"
elif [ "$AGENT" = kiro ]; then
  echo "dfs_run: launching  ${AGENT_ARGV[*]} ${AGENT_SUB[*]} --output-format stream-json --agent dfs-inbox --no-interactive ${PERMISSION_ARGV[*]} <prompt>"
else
  echo "dfs_run: launching  ${AGENT_ARGV[*]} -p <prompt> --output-format json --settings <the inbox hooks> --model <opus to plan, sonnet to implement> ${PERMISSION_ARGV[*]}"
fi
total_turns=0
total_cost=0

for i in $(seq 1 "$CAP"); do
  lowered_cap
  if [ "$i" -gt "$CAP" ]; then stop_is cap; echo "dfs_run: CAP before run $i — the cap was lowered to $CAP."; exit 0; fi
  eval "$(dfs_state)"
  case "$status" in
    blocked) stop_is blocked; echo "dfs_run: BLOCKED before run $i — $why. Answer it with --review $ITEM, then run again."; exit 0 ;;
    done)    mrc=0; merge_item || mrc=$?
             if [ "$mrc" = 0 ]; then stop_is done; echo "dfs_run: DONE before run $i — $why."; exit 0; fi
             eval "$(dfs_state)"
             if [ "$mrc" = 4 ]; then stop_is blocked; echo "dfs_run: BLOCKED before run $i — $why. Answer it with --review $ITEM, then run again."; exit 0; fi ;;
  esac
  kind=work
  if [ "$critic_due" = 1 ]; then
    kind=critic
  elif [ "$review_due" = 1 ]; then
    kind=review
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
    fp_before="$(python3 "$HERE/state.py" --fingerprint --outside-dfs)"
    verdicts_before="$(python3 "$HERE/state.py" --verdicts "$ITEM" "$kind")"
  fi
  tree_log session "$kind" "$(basename "$RUNDIR")/run-$i"
  if [ "$kind" = work ]; then fp_before="$(python3 "$HERE/state.py" --fingerprint)"; else fp_tree_before="$(python3 "$HERE/state.py" --fingerprint)"; fi
  # ⚠️ OPUS PLANS, SONNET IMPLEMENTS, and which a session does is a fact of the task, not
  # a judgement: the work session that opens a task with no nodes lays the tree out, and
  # the critic and the review judge it; every other work session builds a node. Only
  # claude is told a model (the others read theirs from their own config).
  MODEL_ARGV=()
  if [ "$AGENT" = claude ]; then
    if [ "$kind" != work ] || [ "${plan:-0}" = 1 ]; then model="${PLAN_MODEL-opus}"; else model="${IMPLEMENT_MODEL-sonnet}"; fi
    [ -z "$model" ] || MODEL_ARGV=(--model "$model")
    echo "   model ${model:-claude default}"
  fi
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
  # A reading below 0 is "unavailable", and the Log says so: `peak 0` and `peak -1` read
  # as measurements, and 13 sessions of 2026-10-28 carried a 0 that measured nothing.
  if [ "${peak:--1}" -lt 0 ]; then peak_shown=unmeasured; else peak_shown="$peak"; fi
  if [ "$AGENT" != claude ]; then
    echo "   exit $rc · $turns turns · peak context $peak_shown · cost unavailable · session ${SID:-unknown}"
  else
    total_cost="$(python3 -c 'import sys; print(round(float(sys.argv[1] or 0)+float(sys.argv[2] or 0),4))' "$total_cost" "${cost:-0}")"
    echo "   exit $rc · $turns turns · peak context $peak_shown · \$${cost:-?} · session $SID"
  fi

  if [ "$rc" -eq 130 ] || [ "$rc" -eq 143 ]; then on_interrupt; fi
  # ⚠️ A usage limit is ASKED OF THE RESULT'S OWN STATUS, not of a sentence — one
  # definition, in limit.py. Defaulted before the eval so a classifier that did
  # not run is never read as "not a limit".
  limit_hit=0; resets=""
  if limit_out="$(python3 "$HERE/limit.py" "$log" "$AGENT")"; then
    eval "$limit_out"; limit_hit="$limit"
  else
    echo "dfs_run: limit.py could not read $log — a usage limit here will be recorded as a failure." >&2
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
    # ⚠️ A critic or a reviewer judges; it does not work, and it must give a verdict.
    # A change outside .dfs/ during its session is NOT a stop: the tree cannot say
    # whether the session or the author (a chat session, an editor) made it, and
    # stopping the walk for the author's own edit cost a finished task its review.
    # It is said on the console and in the end entry, and left uncommitted for the
    # next session to commit as the node's uncommitted work.
    fp_after="$(python3 "$HERE/state.py" --fingerprint --outside-dfs)"
    verdicts_after="$(python3 "$HERE/state.py" --verdicts "$ITEM" "$kind")"
    outside_note=""
    if [ "$fp_after" != "$fp_before" ]; then
      outside_note="; the working tree changed outside .dfs/ during it, left for the next session to commit"
      echo "dfs_run: the $kind ran while the working tree changed outside .dfs/ (the $kind, or the author). Carrying on; the next session commits it."
    fi
    if [ "$verdicts_after" -le "$verdicts_before" ] \
       && [ "$(python3 "$HERE/state.py" --fingerprint)" = "$fp_tree_before" ]; then
      # ⚠️ A SESSION THAT CHANGED NOTHING AT ALL IS NOT A FAILED ONE. It wrote no
      # verdict and touched neither the code nor the task file, so there is nothing
      # to look at, and `failed` stopped the whole walk for it. The chain carries on:
      # the critic's cadence restarts as it does for any critic, and a review that is
      # still due is simply the next run. Silence TWICE RUNNING, with no work session
      # between, is `idle`, which the walk takes as a fact about THIS item: it raises
      # on it and goes on to the next, and a review that can never speak does not
      # loop to the cap. A critic that DID write to the task and still gave no
      # verdict is the case below, and that one is still `failed`.
      silent=$(( ${silent:-0} + 1 ))
      tree_log end "$kind" silent
      if [ "$silent" -ge 2 ]; then
        stop_is idle
        echo "dfs_run: the $kind changed nothing and gave no verdict, twice running. Stopping rather than repeating it."
        exit 1
      fi
      echo "dfs_run: the $kind changed nothing and gave no verdict. Carrying on."
      if [ "$i" -ge "$CAP" ]; then stop_is cap; break; fi
      continue
    fi
    if [ "$verdicts_after" -le "$verdicts_before" ]; then
      tree_log end "$kind" failed
      stop_is failed
      echo "dfs_run: the $kind gave no verdict (no \`$kind\` entry in the log). Stopping."
      exit 1
    fi
    if [ "$kind" = critic ]; then
      # ⚠️ AN `ok` HAS TO BE ONE THE RECORD SUPPORTS. It names the live nodes it checked,
      # and the session shows a read or a command besides the one that logged it
      # (state.py --critic-problem says which is missing). One that does neither is
      # treated as a critic that said nothing: the entry stays in the log, the end
      # entry says why it was not accepted, and two running stop the chain as `idle`.
      problem="$(python3 "$HERE/state.py" --critic-problem "$ITEM" "$tp" "$AGENT" 2>/dev/null | head -1 || true)"
      if [ -n "$problem" ]; then
        # Counted apart from `silent`: a critic runs every few work sessions, so two
        # refusals are never back to back. Two with no accepted verdict between them
        # is a critic that cannot be made to check, and that is `idle` for this item.
        refused=$(( ${refused:-0} + 1 ))
        tree_log end "$kind" unjustified "$problem"
        echo "dfs_run: the critic's ok was not accepted: $problem."
        if [ "$refused" -ge 2 ]; then
          stop_is idle
          echo "dfs_run: the critic's ok was refused twice with none accepted between. Stopping rather than repeating it."
          exit 1
        fi
        if [ "$i" -ge "$CAP" ]; then stop_is cap; break; fi
        continue
      fi
    fi
    silent=0
    [ "$kind" = critic ] && refused=0
    tree_log end "$kind" ok "peak $peak_shown$outside_note"
  else
    fp_after="$(python3 "$HERE/state.py" --fingerprint)"
    if [ "$fp_after" = "$fp_before" ]; then
      tree_log end work idle
      stop_is idle
      echo "dfs_run: the repo did not move — the session changed nothing. Stopping rather than repeating it."
      exit 1
    fi
    tree_log end work ok "peak $peak_shown"
    silent=0
  fi
  # What the run did to the task, as one outline.
  python3 "$TREE_PY" show "$ITEM" | sed 's/^/   /'
  # A gitignored .dfs is never staged, so the pre-commit guard never judges it: ask
  # the working tree instead, as a warning, since there is no HEAD to hold it to.
  if [ "$DFS_IGNORED" = 1 ]; then python3 "$HERE/check.py" --warn | sed 's/^/   /' || true; fi

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
    done)    mrc=0; merge_item || mrc=$?
             if [ "$mrc" = 0 ]; then stop_is done; echo; echo "dfs_run: DONE after run $i — $why."; break; fi
             eval "$(dfs_state)"
             if [ "$mrc" = 4 ]; then stop_is blocked; echo; echo "dfs_run: BLOCKED after run $i — $why."; break; fi
             echo "dfs_run: the merge did not go through; the item is open again — $why." ;;
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
