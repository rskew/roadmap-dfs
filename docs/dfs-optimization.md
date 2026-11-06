# Optimizing dfs from its own history

How to make roadmap-dfs better using the projects it has already run: read what past
sessions did (static analysis), then change the system and re-run old tasks from
their starting point to see whether the change helps (replay).

## 1. What the past recorded

Surveyed 2026-10-22 on the box the chains run on. Four projects have been walked:
roadmap-dfs itself, chirpstack-docker, house and shop-app. `optimisation-data/farm-app` is chirpstack-docker's
repo (its flake describes the ChirpStack stack and Farm Operator): 27 of its 31 task files are tasks whose
sessions were recorded under `/workspace/chirpstack-docker`, and only 3 tasks were recorded under
`/workspace/farm-app`, and no worktree task id is in both. The `farm-app` and `chirpstack-docker` rows below are one repo's
sessions under its two names; their sessions are distinct transcripts, so no total counts one twice, but
"every project" is one fewer than the rows say.

| Source | Where | Size | Answers | Cannot answer |
|---|---|---|---|---|
| Session transcripts | `$HOME/.claude/projects/<cwd>/<sid>.jsonl`, one directory per checkout and per worktree | ~1.4 GB, ~2,000 files: chirpstack-docker 832, roadmap-dfs 612, shop-app 402, house 151 | Every turn: tokens (cache read, cache write, output), timestamps, each tool call and its result, the prompt the session got (so its kind: work, review, critic, chat), every refused commit and the guard's message | Which node a session served, except by reading its `tree.py start` and commit commands; nothing about the outcome after the session ended |
| Runner directories | `<repo>/.dfs/runs/dfs_run_*/` (`meta.json`, `run-N.json`, `console.log`), plus `activity.log` | roadmap-dfs only: 206 chains on 99 items since 2026-10-08 | Per session: item, kind, peak context, turns, stop reason, exit code, and `claude -p`'s own `total_cost_usd`, duration and usage; `sid` joins it to its transcript | Chains before 2026-10-08; the other projects' runs (their checkouts are not on this box) |
| Task files and archive | `<repo>/.dfs/items/*.md`, `.dfs/archive/items/*.md` | roadmap-dfs: 101 tasks, 497 nodes on the 2026-10-22 survey (123 and 659 by the recount below); logs with 288 work, 216 review and 23 critic sessions | The tree as decided: node statuses, hypotheses, evidence, reviews ok/issues, critic ok/issues, author answers | Time or cost per node; anything the session tried and did not write down |
| Git history | each repo | roadmap-dfs: 507 commits, 436 of them node commits, 2026-09-30 to 2026-10-21 | The code each node changed, in order, and the state of the repo before any task began: the starting point a replay needs | The other three projects: only their transcripts are on this box |
| Old versions of the tool | `/nix/store/*-roadmap-dfs-<rev>` | 39 store paths | A past version of the skill, law and scripts to run a replay under, for a before/after comparison | Versions already garbage-collected |

Two facts from the task files already bear on where to look (`python3 <scripts>/audit.py nodes`
and `findings`, run 2026-10-28 over the 123 tasks and 659 nodes of the roadmap-dfs checkout; the
other three projects' task files are not on this box, so these counts are roadmap-dfs's alone):

- **The tree is rarely used to back up, though not never.** As written, 3 of 659 nodes have
  `Status: refuted` and 82 are parked. Read as the screen reads them, with the author's
  corrections and the critic's backtracks applied, 13 nodes are refuted (3 by their own
  status, 10 by an author ruling), 8 critic backtracks withdraw 41 nodes, and 70 stay
  parked. A final state cannot say how many parked nodes were later taken up, since
  they then stop being parked.
- **Reviews send the work back often.** The task logs hold 116 review verdicts that say
  issues (`audit.py findings`); section 4 gives the first-review rate (30%).

## 2. Where the work goes

`scripts/history.py` reads every transcript into one record per session (kind, task,
tokens, tool calls, commits, refused commits, verdicts) and one per task, and prints
the tables below. Dollars are the runner's own `total_cost_usd` where it recorded one,
and otherwise tokens priced at the same ratios (input 1, cache read 0.1, cache write 2,
output 5), which, fitted on one half of the 689 runner-priced sessions and measured on the other,
miss the runner's figure by 0.0% at the median (the runner's price is linear in these
token counts) and 25% at the 90th percentile (`audit.py cost`). Only roadmap-dfs has
runner prices: 34% of dfs sessions are priced, and the dollars of the other three projects
are roadmap-dfs's rate applied to their tokens, which no held-out check reaches.

Run on 2026-10-28 over 2,129 transcripts, 2,043 of them dfs sessions (every figure
in this section and in section 4 is from this one run):

| project | tasks | sessions per task, median / p90 | $ per task, median / p90 | review issues per task | raises |
|---|---|---|---|---|---|
| chirpstack-docker | 131 | 4 / 18 | 2.6 / 18.5 | 0.70 | 48 |
| farm-app | 2 | 8 / 9 | 7.5 / 7.8 | 0.00 | 0 | (chirpstack-docker's repo, recorded after it was renamed)
| house | 22 | 4 / 20 | 2.9 / 24.0 | 0.77 | 4 |
| roadmap-dfs | 115 | 4 / 13 | 2.1 / 9.4 | 0.80 | 13 |
| shop-app | 16 | 6 / 28 | 7.1 / 35.4 | 1.12 | 5 |

What it shows, most important first:

1. **A finished tree is often not finished.** 40.9% of all dfs spend is on a task after
   one of its reviews has already said issues (40.7% counted in weighted tokens, so the
   share does not depend on the price). 71% of that dollar figure is estimated from
   tokens, all of it in the three projects with no runner prices; roadmap-dfs alone,
   where the runner priced all of it, gives 41.2%. The reviews of roadmap-dfs said issues 116 times, and those
   reviews wrote 207 finding lines (`docs/dfs-review-findings.tsv`, one row each with its
   class): 101 defects, 78 batches of lesser findings and 28 assumptions the review left for
   the author (some repeat across rounds, as "still open"). Of the defects, 23 say the
   review reproduced the fault and 2 say a test or check is absent. Whether the work's
   own tests missed a defect is not recorded in a review line, so "bugs the work did
   not test for" is not a measured class; the classes come from patterns in
   `audit.py` (`finding_rows`), and 30 sampled rows were each classed as a reader
   would class them. The most expensive task and the one with most issues rounds, roadmap-dfs W75, had 23
   reviews and 16 of them found issues (`history.py`, "Most expensive tasks"; chirpstack-docker
   W108 also had 23 reviews, 3 with issues). Quality, measured as review rounds with issues, is the largest cost
   as well as the largest failure.
2. **The tree backs up rarely.** Section 1's recount: 13 nodes refuted once corrections
   are applied and 8 backtracks, against 535 confirmed. That is the tree used to back
   up on 19 of 123 tasks. A node refuted by its own evidence is rare (3 nodes), while the
   author's rulings and the critic's backtracks catch more, which is consistent with
   sessions not looking hard for evidence against their own work but does not show it. The earlier count of `Status: refuted` strings in
   transcripts is dropped: it counted every re-read of a task file and the skill's own
   text.
3. **Review and critic are 34% of spend by dollars** (review 27.0%, critic 6.6%, work
   66.4%), and 28% in weighted tokens (review 23.0%, critic 5.3%, work 71.7%).
   Reviews are cheap per session (median $0.42 to $1.01) and are where the defects
   above were caught, so they are not the place to cut.
4. **The guard refuses a commit in 12% of sessions** (236 of 2,043, 304 refusals).
   `history.py` counts the refusals by what the refused commit's output lists (a refusal
   lists every fault it found, so a refusal counts under each reason): a file:line
   coordinate in a task file 198, a task file over its size budget 36, a node naming a
   Parent that does not exist 28, a Hypothesis naming no artefact 9, anything else 22.
   Each costs a retry, not a failure.
5. **Re-reading is not a cost.** 150 of 3,458 `Read` calls repeat an earlier slice.

Not measured yet: whether a task's work was later fixed by another task (needs commit
subjects matched across tasks), and the author's time, which no transcript records.

## 3. Replaying a task

`scripts/replay.py` runs a finished roadmap-dfs task again from where it began and scores
the run with the code that scored the original (`history.session()`, `tasks_of()`).

    python3 <scripts>/replay.py plan   W55                     # starting point and the original's numbers
    python3 <scripts>/replay.py replay W55 <exec-capable dir>/dfs-replay-W55-a [--cap N] [--tool DIR | --tool-ref REV] [--law FILE]

`prepare`, `run` and `score` are the same three steps one at a time. Run the `replay.py` of the
live checkout (`python3 scripts/replay.py` from the main checkout, `/workspace/roadmap-dfs` here; `replay.py`
itself takes that path from `main_checkout()`), not the one in the installed
package a session's briefing names: the package is built from whatever commit was installed and can
be older than the repo. An old one has no `--agent`, so `replay` fails with "too many values to
unpack", and its `run.sh` call has no `--kiro`, so it runs claude whatever `.dfs/runs/agent` says.
Before using an installed one, check that `replay.py` with no arguments lists `--agent` and `--shell`.
What it does:

- **The starting point** is the commit before the task's first node commit, with one
  change: the task file is cut back to what the author wrote (title, Goal, Background),
  since the Tree, Log and Summary are what the sessions added. It is committed as
  `replay: W55 as the author left it`, after `init.py` has brought the hooks and
  `.gitignore` up to the tool's, so the first session finds a clean tree and the task's
  first node is the next commit.
- **The system that changes** is the tool (`--tool DIR`, or `--tool-ref REV` to export a
  revision of the repo itself: SKILL.md, the scripts and the templates all come from it)
  and optionally the law (`--law FILE` over `.dfs/ROADMAP.md`). With neither, the
  replay runs the current tool under the law the original had.
- **The chain** is the real one: `run.sh <task> <cap>` in the scratch clone, which cuts
  `dfs/<task>`, runs work, review and critic sessions, and merges on a passing review.
  The default cap is twice the original's sessions (at least 6). Raises are not
  answered: a chain that stops on one scores as BLOCKED.
- **The score** is a table of the original beside the replay: sessions by kind, dollars,
  nodes, whether the **first review** passed, review rounds with issues, critic issues,
  refused commits, spend after a review said issues, and why the chain stopped. The
  replay's transcripts live in a directory named `…-dfs-replay-…`, which `history.py`
  leaves out of the history it mines.

### Replayed on W55

W55 ("Codex limits reached until reset time, time not parsed properly") is small: 5
sessions and $1.30 originally, with a first review that found issues. It was replayed
three times on 2026-10-28 (`dfs-replay-W55-a`, `-b`, `-c`) under the current tool, on a
mount that runs scripts, so the dfs hooks ran (the clones hold `pre-commit` and
`pre-merge-commit`); in the replays' transcripts no commit failed for a skipped hook or an
unknown identity, and none ran `grep -r /`. All three stopped on the account's usage
limit part-way (a work session in `a`, a work session in `b`, a review in `c`) and were
resumed with `replay.py run` once it lifted, 2h20m later; the cut-off sessions are in the
cost.

| | original | a | b | c |
|---|---|---|---|---|
| sessions (work / review / critic) | 5 (3/2/0) | 3 (2/1/0) | 5 (3/2/0) | 3 (1/2/0) |
| dollars | 1.30 | 1.95 | 3.44 | 2.77 |
| of which cut off by the limit | | 1.12 | 0.20 | 0.54 |
| node commits | 4 | 1 | 4 | 1 |
| first completed review | issues | ok | issues | ok |
| reviews with issues / rounds | 1 / 2 | 0 / 1 | 2 / 3 | 0 / 1 |
| spent after a review said issues | 0.47 | 0.00 | 1.06 | 0.00 |
| chain stopped on | | DONE, merged | DONE, merged | DONE, merged |
| tool calls naming the live checkout (sessions of the replay's total) | | 8 (1 of 3) | 22 (3 of 5) | 5 (1 of 3) |
| original's nodes shown in a tool result | | 1 | 3 | 0 |

Two of the three first trees passed review, one failed it twice (as the original failed
once), and the cost ran from 1.5 to 2.6 times the original's: so the harness has not shown
that it reproduces the failure it is meant to move, only that the first review's verdict
varies between replays of one task. The `ok` reviews of `a` and `c` each still list
defects (`c` names a day-boundary hold of 24 hours that `b`'s review made a Fix node of),
so which side of `ok` a tree falls on is partly the reviewer's call.

The last two rows are from `replay.py score` (below): these three replays predate the
sandbox ("Replayed under the sandbox", below) and were not sealed from the box. Their DEST sits inside the live checkout (`/workspace/roadmap-dfs/.dfs/runs/replays/`,
the one mount here that runs scripts), and sessions run as the same user, so the live
`.dfs/runs`, `.dfs/items`, `.dfs/worktrees` and `~/.codex` were one directory up.

What the proof does not show, and what a comparison has to allow for:

- **One replay is one sample.** The same task, under one system, cost 1.95, 2.77 and 3.44
  dollars (1.30 originally) and its first review passed twice in three; a change to the
  system can only be judged over several replays of each arm (control and changed),
  compared on the delivered tree's rubric score, then on credits (step 5 of the loop; this bullet
  was written when the first review's pass rate was the measure).
- **The replays `a`, `b`, `c` and `arm1` ran with the original's evidence on the box.** `seal` empties the clone
  of the original's later work, but not the box: a replay's sessions run as the same user,
  and the live checkout's `.dfs/runs` (the original chain's run logs, with its sessions'
  tool output), `.dfs/items` (the original `W55.md`), `.dfs/worktrees`, the codex session
  logs in `~/.codex/sessions` and every earlier session's transcript under `~/.claude` are
  readable. `replay.py score` now counts the tool calls that name them from outside DEST
  (DEST, its exported tool and the tool it runs under, meaning that tool's `SKILL.md`, `scripts`, `templates` and `docs` but not its `dfs-*` files, are cut out first; a tool that is the live checkout still counts a read of its `.dfs/` or of its `dfs-*` docs) and the original's
  node titles, Determinations and review and critic findings that a tool result printed and the replay did not write
  itself. Both are floors: a path built at run time (`cd ..`, a variable) is not seen, and
  a result that shows the answer is not a session that used it. Run on the four W55
  replays, the sessions that reached out were 1 of 3 (`a`), 3 of 5 (`b`), 1 of 3 (`c`)
  and 4 of 9 (`arm1`), 8, 22, 5 and 32 calls. All four read the live `.dfs/runs`, searching
  its run logs and `activity.log` for the "usage limit" and "Try again at" lines the task is
  about; `b` and `arm1` also searched `~/.codex/sessions`; `b` also read the test of
  another task's live worktree (`.dfs/worktrees/W122/scripts/test_web.py`), ran a recursive
  `grep` over `/workspace` and `/tmp`, and a glob over the live `scripts/*.py`,
  `.dfs/worktrees/*/scripts/*.py` and `.dfs/items/*.md` that hit the 900-second bound and
  returned nothing to the session. A tool result printed the original's own Determinations
  (the fix, in words) in `a` (1 of the original's 6 lines), `b` (3) and `arm1` (3), from the
  original chain's run logs (`dfs_run_53uiza6t`), not from the items directory; `c` saw none.
  So the earlier text, "evidence the scratch clone does not carry", was wrong for these
  runs. What it does to the figures: the two replays whose first review passed are `a`
  (one of the original's lines shown) and `c` (none), and the one that failed, `b`, was
  shown three, so the answer being on the box did not make these replays pass; but the
  2 of 3 cannot be called a clean replay's pass rate either, and is an upper bound on
  one. The earlier unguarded replay ran `grep -r /` for the evidence and sat in it for
  53 minutes; none of these four did (searched in their transcripts), and a session in
  `a` spent 28 minutes in tool calls.
- **From here a replay runs in a sandbox that hides the box.** `replay.py run` and `replay`
  start the chain through proot (nix: `pkgs.proot`, no privileges; chroot, bwrap and user
  namespaces are refused on this box). The sessions see an empty root holding the store
  paths the chain runs from (`store_closure`: the agent, proot, bash, git, python and the
  sessions' other `PATH` entries and everything they refer to, about 140 paths bound one by
  one; not `/nix` whole, which holds 107,000 entries on this box, 4,166 of them flake
  `*-source` copies of repos made when nix built them, 18 of those the original W55 task
  file with its Determinations), `/proc`, `/dev`, the network files of `/etc`, DEST at its own path, the tool's own
  entries (`SKILL.md`, `scripts`, `templates`; never the tool as a whole, which
  when it is the live checkout holds its `.dfs/`), a `docs` of the box's own holding every
  doc of the tool's except the files named `dfs-*` (the tool's `docs` carries
  `dfs-review-findings.tsv`, `dfs-rounds.tsv`, `dfs-break-it-cases.tsv` and this document, which name every task's
  review findings, the original W55's among them; a tool from the store is bound whole for
  its `bin` and `libexec`, and the box's `docs` is laid over it). A changed tool that adds a
  doc its `SKILL.md` points at therefore finds it; `launch` warns when a `SKILL.md` or
  template points at a hidden `dfs-*` file, which that arm's sessions would not find (a
  tool that needs one is named differently). The reading this rests on: no doc but
  `dfs-*` names other tasks' findings. It is wrong if one does, and a read of it would
  show as the original's conclusions in a result. The box also has a fresh home (`DEST-box/home`, at
  `/home/replay`) with a copy of the sign-in and `settings.json` and nothing else: no
  `~/.claude/projects`, no `~/.codex`, no history. Their environment is rebuilt (no
  `DFS_*`, no `XDG_*`). Of the live checkout they see the path down to DEST and nothing
  beside it. `SandboxTest` runs a real proot and finds the live runs, the tool's own
  `.dfs/`, a `DFS_RUNS` in the host environment and every `*-source` directory of the store
  all absent (red when the tool's `.dfs` is bound, the environment passes through or the
  store is bound whole); `claude` runs under it with network and
  sign-in, and the chain (`run.sh`) runs to its first session. DEST stays inside the live
  checkout's ignored `.dfs/runs`, the one mount here that runs scripts (git skips a hook
  elsewhere).
  What it does not do. proot hides paths and is not a security boundary: it intercepts
  system calls and does not isolate the process, so an escape that never names a live path
  would go unseen. The detector is `score`'s rows, and they see only tool calls that name
  a live path: "calls naming a live path, answered nothing" (the sandbox returned nothing
  or an error: an attempt, which a task whose text sends the sessions to the run logs makes
  as a matter of course) and "answered output" (which a reader opens: a command can name a
  live path and print only what its in-sandbox part found), and "original's nodes shown in
  a result". A call that names a `*-source` directory or the store as a whole
  (`ls /nix/store`, `grep -r x /nix/store/*-source/`) counts as a call naming a live path too
  (`STORE_LOOK`), since that is where a flake source of the repo would be, and so does a call
  that names the replayed repo's public remote (`remote_paths`: its `origin` as `git clone`,
  `curl`, `gh` or a web fetch would write it, and `raw.githubusercontent.com/owner/name`). A replay with any call answered with live output, or any of the original's
  conclusions shown, is void: it is dropped from the count and a Fix node is opened to
  close the hole. The sandbox copies the sign-in, so after the access token's hours the
  replay's `claude` refreshes its own copy; whether that signs out the author's login is
  not checked here, and the fix is to sign in again.
  The network is open and is not narrowed. The agents need their API, and `/etc/hosts` or a
  proxy could stop a session reaching `github.com`, but not an IP literal, a mirror or a
  search tool that runs on the agent's side of the API (`WebSearch`), and the original's
  sessions had GitHub (a kiro replay read the codex repo's issues on it). So the sandbox does
  not hide a public remote: when the replayed repo is public, the original's task file, review
  findings and fixed code are one `git clone` away. Nothing in the clone points there (`seal`
  removes `origin`), and the row above counts a call that names it, but a fetch that spells
  the repo another way would go unseen. All six stored W55 replays (`a`, `b`, `c`, `arm1`,
  `p1`, `k1`) were scanned, calls and results, for `rskew`, `raw.githubusercontent.com/rskew`
  and `github.com/rskew`: none names it (the other `github.com` lines are search results about
  ollama and the codex repo), and `score` on each prints the same rows as before. A task is
  replayable only if its answer is not on the public remote or a fetch of it would show in
  those rows; W55's is on the remote, so its replays carry that exposure, measured here as nil.
  The agent is the configured one. A replay runs under the agent the screen is set to
  (`.dfs/runs/agent`: claude, codex, kiro or opencode; `replay.py --agent A` overrides it,
  and `prepare` records it in `.git/replay.json`), because the loop's evidence should come
  from the system the author runs. The sandbox gets that agent's package at the pin, its
  command in `CLAUDE_CMD`/`CODEX_CMD`/`KIRO_CMD`/`OPENCODE_CMD`, and its sign-in only. For
  kiro that is a copy of `data.sqlite3` with its conversation and history tables emptied,
  `settings/cli.json`, and the engine kiro unpacks into `~/.local/share/kiro-cli` (node,
  bun, the TUI and the agent server, about 160 MB, program and not data), plus the host's
  `/usr/lib`, `/usr/lib64` and `/lib64` bound in, since that `node` wants the host's dynamic
  loader. A one-line `kiro-cli chat` answered inside the sandbox. `score` and `replay` read a kiro
  replay's run logs (`.dfs/runs/dfs_run_*/run-N.json`, ACP events, one file per session, with
  the CLI's own `[INFO]` lines between them: `history.stream_session`, `acp_calls`). They carry
  no prompt, timestamps or tokens, so a session's kind comes from the task file's log
  (`session · work · dfs_run_X/run-N`; a replay's run directory has no `console.log`), its cost is
  credits (`promptTurnSummaries`, one summary per finished turn, none for a session cut off,
  counted as "unpriced"), and its size is the context window's highest percentage. The score
  table prints credits on their own row and `n/a` in the dollar rows, never a credit in a dollar
  column, and the original's dollars are not comparable to them. The first-review verdict is
  read the same way, but the keep rule does not use it (it takes the rubric score, then credits). The breach rows read the same calls (a kiro call is `execute`,
  `read`, `search` or `edit`, with its `rawInput` and the text it printed). Commits and nodes are
  counted by the same pattern as claude's, which misses `git commit -am` and `-q -am` and can
  match the word inside a script a call writes, so `main`'s `W<n>.<m>:` commits are the check
  for nodes. codex and opencode replays can be run but not scored: codex's rollouts and
  opencode's logs are other shapes, and no replay under either has been run here to read one
  against. A kiro
  replay refreshing its copied token may sign the author out of the live one, as for claude.
  The kiro box rests on choices made on one install (kiro-cli 2.31.1) and not tested on a
  second: that the history, conversations and conversations_v2 tables are all of `data.sqlite3`
  that holds earlier sessions (table names are guesses at kiro's schema; a table of earlier
  sessions under another name would come along, so list the copy's tables with a row count
  before trusting a new kiro version), that the engine files listed are program and not data,
  that binding the host's `/usr/lib`, `/usr/lib64` and `/lib64` for kiro alone shows the sessions
  libraries and no data of the author's, and that `.dfs/runs/agent` is where the screen keeps its
  choice. A re-used box (`make_box` on a DEST that already has one) keeps the database it was
  given the first time. Scoring covers claude and kiro; the node that added kiro's scoring set
  out to cover codex and opencode too and stopped at those two, which is the author's call.
  A task whose Goal sends its sessions to evidence outside the repo gets nothing from the
  box now and has to work from the repo, so it is a poorer replay of that task, not a leak.

### Replayed under the sandbox

`dfs-replay-W55-p1` (2026-10-28, the store's tool, claude 2.1.308, run once the account's
five-hour limit had reset): 2 sessions (1 work, 1 review), $2.36 against $1.30, 1 node
commit, first review `ok`, merged. `score` prints 0 of the original's nodes shown in a
result and 7 calls naming a live path (5 before the store counted): 2 answered nothing (greps of `/workspace/roadmap-dfs/.dfs`
and `~/.codex` for the usage-limit logs, which the task is about) and 5 answered with
output, which were read in the box's transcripts: lines from the clone's own
`scripts/test_limit.py`; `worktrees`, the path down to DEST and a `which: command not found`
(an error result, exit 127, that an earlier version of `score` counted as nothing); and
`replays` (the path down to DEST) with a listing of the nix store. Two more calls count since the
store became a path to look for: `ls -d /nix/store/*codex*` (the codex package names), and a
`find /nix/store -name "*source*"` that listed three `*-source` names, and a
`grep -rl format_retry_timestamp /nix/store/*-source/` that ran into the 900 s bound and was
stopped with no output read. That last call is the reason the store is no longer bound whole:
`p1` ran with the whole store visible and searched its flake sources, where the original's task
file is, and held because that grep timed out. Nothing of the live checkout in any result. So the
sandbox held on this replay, which ran under the old binds, is one sample and counts as a
first-review pass of one.

`dfs-replay-W55-k1` (2026-10-28, the store's tool, kiro-cli 2.31.1 under the sandbox, DEST in
the worktree's ignored `.dfs/runs/replays`): 2 sessions (1 work, 1 review) in about five
minutes, 5.7 credits (4.56 and 1.13; the original's $1.30 is another unit), 1 node commit
(W55.1, as `main` holds), first review `ok`, merged, chain stopped on DONE. `score` prints
0 calls naming a live path and 0 of the original's nodes shown. I checked that by reading the
sessions' calls: every path they named outside DEST was the tool in the store (`SKILL.md`,
`context.py`, `tree.py`) and the sandbox answered `context.py` with "no transcript". The
sandbox held on this replay under the new binds, and it counts as a first-review pass of one,
on a sample of one: the arm is kiro, so its pass rate is the kiro system's, not claude's.
A second kiro replay, `dfs-replay-W55-k2` (same setup, run from `replay.py replay W55 … --agent
kiro`), finished in 3 sessions (2 work, 1 review) and 5.6 credits, 2 node commits, first review
`ok`, merged, DONE, and `score` printed 0 calls naming a live path and 0 conclusions shown. It
shows the kiro path still runs end to end; two passes of two are not a pass rate.

- **The starting point keeps only the title, Goal and Background.** Any nodes the author
  wrote into the task before work began are dropped, so a task that began with
  author-written nodes is not replayed from its real start; none of the candidate tasks
  is known to have had any, which is unchecked.
- **The tool is the current one**, not the original's, so the control arm is the current
  tool; the original column is the history, not a control.

### Run once under a changed tool (the changed-arm path)

The three replays above all ran the current tool, so the path that makes an arm differ
(`--tool-ref REV`: `export_tool`, then `prepare` and `run` using the exported tool) had
never run. It is tested now (`ChangedToolTest` in `scripts/test_replay.py`: the tool is
the exported revision and not the checkout, the clone's `pre-commit` names it, an earlier
revision exports its own text, an unknown revision fails instead of falling back to the
current tool, and `run` launches `run.sh` from it with `DFS_TOOL` set; the tests go red
when `export_tool` archives the wrong revision or `prepare` ignores the exported tool).
It was also run once: a branch of this repo (`w97-arm-1`) differing from the tool of that
day in one line of `SKILL.md`, a marker (`Arm marker W97-ARM-1: ...`) that changes nothing
about how a session works, replayed on W55 as `dfs-replay-W55-arm1`.

- The five work sessions were told to follow `dfs-replay-W55-arm1-tool/SKILL.md`, and the
  marker is in the Read or `cat` result of each of them (5 of the 9 sessions; the review
  and critic sessions are briefed by the runner and do not read the skill).
- The clone's `pre-commit` names the exported tool; no commit failed for a skipped hook or
  an unknown identity, and none ran `grep -r /` (searched in its transcripts).
- `replay.py score` read the replay's transcripts: 9 sessions (5 work, 3 review, 1
  critic), $4.82 against $1.30, 5 commits on `main` whose subject starts `W55.<n>` (4 distinct nodes, W55.4 twice; the original's 4 counts the same way), first review
  `issues` (2 reviews with issues of 3), merged. The $4.82 includes a session cut off when
  the shell call running the chain hit its 30-minute limit; the chain was resumed with
  `replay.py run`, as for the usage limit above.

- It was not sealed from the box either: 32 calls in 4 of its 9 sessions named the live
  checkout (`.dfs/runs`) or `~/.codex`, and a tool result printed three of the original's
  Determinations (see "The replay's box" above).

That is one replay of one arm, and the marker changes nothing a review would judge, so it
says only that the changed-arm path ran end to end once (a reading: one run under one
marker is taken as enough for that) and that the session reads the changed text; it says nothing about whether any change helps. `replay.py score` now
refuses a DEST that is not a prepared replay of that task, and a replay whose transcripts
hold no session of the task, where it used to print a table of dashes.

## 4. What to try, and how to find out

### The measure

Judged on a replay, a change succeeds if the delivered tree meets more of the task's Goal clauses
than the same-sitting control's (a rubric per task, a blind judge, a tie margin of 1.5 clauses; step 5),
and, where quality ties, at no more than 70% of the control's credits (a pair whose credits are only a
cut-off's lower bound is unresolved, not same). The first review's verdict is
not the measure, since it is the dfs reviewer grading its own system. The history is still where
the spend is found: `history.py` prints these counts in its "Share of all
dfs spend" block (run of 2026-10-28, the same run as section 2): of 276 tasks that had a
review, the first review found issues in 84 (30%; the run read verdicts from the
commands that wrote them and missed a verdict set through a shell variable, and the
fixed reading of 2026-10-29 gives 106 of 290, 37%); 104 tasks (38%) had at least one
issues round and hold 72.7% of all dfs dollars; 30 tasks (11%) had three or more and hold
34.3%. A task's dollars are all its work, review and critic sessions, so the 72.7% is a
share of the spend on those tasks, not of the spend after their first issues review (that
is the 40.9% of section 2). The cost is a long tail of review loops, not a typical task.
An earlier draft of this paragraph said 234 tasks, 87 and 69%, 27 and 42%; no script
printed them and they do not reproduce, so they are dropped. Cost per task, rounds with issues and refused
commits are read beside it, and a reviewer that passes more often by finding less is
caught by the next section's check, not by this number.

### Candidates

Ordered by (share of spend the evidence puts behind it) over (cost and risk of trying
it). Each names the file it changes and the replay that tests it.

| # | Change | Evidence | Where | Test replay |
|---|---|---|---|---|
| 1 | **Break it before finishing.** The last node of a tree is a node that tries to break the work: for each Goal clause, a check that would fail were the clause not met, and the cases the work's own tests skip (interrupted, empty, repeated, a short screen). The tree may not be declared complete with that node absent. | 40.9% of spend follows an issues review; 101 of 207 review finding lines are defects, but only 2 of them say a test or check is absent. Redone in W130 over the three checkouts (the section "Re-counted on the three checkouts" below): of the 40 defects whose review says it reproduced the fault (`docs/dfs-break-it-cases.tsv`; one reading by one session, so a reader can disagree row by row; roadmap-dfs alone gave 23 on 2026-10-28), 11 are in the cases this node names (5 repeated, 5 interrupted, 1 empty, none a short screen), 10 more only if "interrupted" is stretched to a failure path, a race or a stale state, and 19 are not. So 11 of the 307 defect lines are shown to be ones this node would have looked for, 21 on the widest reading; the other 267 do not say they were reproduced and cannot be placed, so 11 is a floor. 13 refuted nodes and 8 backtracks against 535 confirmed is consistent with sessions rarely looking for evidence against their own work, which it does not show. The rank above 2 is kept because neither is stronger on this evidence (2's 28 lines repeat across rounds) and the order is not moved by it. | `SKILL.md` "Ending a node" | W55 and the other small roadmap-dfs tasks whose first review cited an untested case (no changed arm has run on shop-app or farm-app: with `--shell shop-app-e2e` the replay box holds nix, node, playwright, chromium, Postgres and the backend, and one shop-app control of W32 ran; one farm-app control of W132 ran under kiro and ranks nothing; see "Next") |
| 2 | **Name assumptions as they are made.** A pass before commit that lists every choice the Goal did not settle and writes each as a Hypothesis or says there is none. | 28 of 207 review finding lines are unmarked assumptions (some repeat across rounds). | `SKILL.md` "A node" | any task whose original review cited an unmarked assumption (find with `audit.py findings`, rows of class `assumption` in `docs/dfs-review-findings.tsv`) |
| 3 | **One review finds all of it.** The reviewer lists every finding it can ground before stopping, ranked, instead of the first few. | Weak, and tested only on roadmap-dfs: of 43 defect lines in a second or later issues review (21 tasks; 26 have two or more issues reviews), 3 name code no commit since the earlier review touched or came near, 7 name code that review could not have seen, 20 name code the fixes changed (either could hold), 13 name no code the check can place (`docs/dfs-rounds.tsv`, `audit.py rounds`). The premise, that most later findings were there at round one, is not shown. | the review prompt (`run.sh`) | only worth a replay if a reviewer given the same tree can be shown to find the 3 to 23 present ones; the three are in W75, W88 and W91; the one pair run on a task above $3.5 (W17, with candidate 6 beside it) read better on quality at about four times the credits, see "Candidates 3 and 6 were then run" below |
| 4 | **Stop refusals at the source.** Show the guard's three most common refusals (task file over budget, a file:line coordinate, a Parent that does not exist) in the skill, and let `tree.py` report the first two before the commit. | 236 of 2,043 sessions hit a refusal, 304 refusals; each costs a retry. | `SKILL.md`, `tree.py` | any task, scored on `refused commits` alone; bounded below about 3% of spend (the coordinate refusal, 7 of 480 sessions since 22 October, already fell after the guard's wording changed), too small for a before and after count to show, so parked |
| 5 | **Cap a review loop.** After three issues rounds the task raises for the author instead of continuing. | 30 of 276 reviewed tasks (11%) had three or more issues rounds and hold 34.3% of all dfs dollars (`history.py`); the cap saves only what is spent after the third round, less than that. Does not improve quality, it limits the loss, and hands the author the tail. Counted in W130.47 (`docs/dfs-cap-bound.py`, output `docs/dfs-cap-bound.txt`, 292 reviewed tasks over every checkout): 36 tasks reach a third issues review and 11.9% of all units (12.0% of dollars) fall after it: 21.2% of roadmap-dfs's, 8.6% of chirpstack-docker's, 7.4% of house's and 7.7% of shop-app's. One task, W75 (16 issues reviews), is 21.8 of the 44.5 million units after the cap in roadmap-dfs; without it the figure is about 9% of all units. What the cut rounds found, in the 9 rounds after the third that the review files cover (roadmap-dfs only, 12 tasks): 11 of 23 finding lines are defects (7 reproduced), 8 minor, 4 assumptions; of the 11 defects 5 name a fault the previous fix made or left. So the tail is not mostly stale sentences, and a raise stops the task until the author answers. Not made: parked, because the saving rests on the author preferring to decide at round three, which is theirs to say (the Hypothesis page of W130.47). | `state.py` | none: read from history by counting what the cap would have saved (done) |

Not candidates, from the evidence: cutting reviews (they are 27.0% of spend and where
the bugs were caught), re-read avoidance (150 of 3,458 reads), and encouraging branching
as an end in itself (candidate 1 is the version of it with a purpose).

### Re-counted on the three checkouts (W130)

The 2026-10-28 survey could read roadmap-dfs's task files only. This recount adds the
task files and transcripts of shop-app (66 task files, 27 with a review) and farm-app (23
with a review) from `optimisation-data/`. Every number is printed by `python3
docs/dfs-class-counts.py` from `docs/dfs-class-lines.tsv` (output: `docs/dfs-class-counts.txt`;
the history behind it: `docs/dfs-history-w130.txt`). The task-file counts cover roadmap-dfs, shop-app and farm-app; the history file reads every transcript under `$HOME/.claude/projects`, so it also has chirpstack-docker and house rows. Candidates 1 to 3 change what a review
finds and are each a predicate on a review's finding lines, counted the same three ways. A line
is *blocking* if it is a defect or an unmarked assumption (the lesser batch, class `minor`,
comes with `ok` reviews too). Of 207 reviews that said issues (68 of them first reviews):

| candidate | blocking lines it matches (of 358) | issues reviews with such a line (of 207) | reviews with no other blocking line (of 207) | first reviews with no other (of 68) |
|---|---|---|---|---|
| 1 break it, the node's own words (repeat, again, re-run, interrupt, empty, short screen) | 30 (8%) | 29 (14%) | 3 (1%) | 1 |
| 1 break it, plus concurrent, race, stale, crash, kill, cut off | 54 (15%) | 49 (24%) | 11 (5%) | 2 |
| 2 assumptions | 51 (14%) | 49 (24%) | 0 | 0 |
| 3 one review finds all (a later round's defect in code unchanged since the round before) | 9 (3%) | 9 (4%) | 5 (2%) | 0 |

The last two columns say how many rounds that class alone started: with every line of the
class gone, the review would have had nothing blocking left. None of the three started more than 11 of the 207
rounds. Assumption lines are 14% of blocking lines and never the only ones, so naming them
better would not have turned one review in the 207. Candidate 1's predicate is a word list, so
it is set against the same words in lines that are not defects (minor and assumption lines):
the narrow list is in 10% of defect lines (30 of 307) and in 10% of the others (19 of 197), the
wide list in 18% and 21%. The words do not pick out defects in the three checkouts together or in roadmap-dfs
(10% and 9%); the small checkouts split (shop-app 6% against 0 of 26, farm-app 36% against 20%
on 22 and 35 lines), so a word count says nothing about how many defects a break-it node reaches.
The one reading that does is by hand, of the defects whose review says it reproduced the
fault (`docs/dfs-break-it-cases.tsv`, 40 rows now, 23 before): 11 are in the cases the node
names (5 repeated, 5 interrupted, 1 empty), 10 only by stretching a case to a race, a stale
state or a failure path, 19 are not. That is 11 of the 307 defect lines (4%) known to be
in the node's cases and 21 of 307 (7%) on the widest reading; the other 267 defects do not say
they were reproduced, so these are floors. The new checkouts add little to it: shop-app's
reviews say they reproduced 1 of 155 defects, farm-app's 8 of 22 (7 are one task's
re-send logic, none in the narrow cases).

Candidates 4 and 5 do not change a review and are counted in the history (2,111 sessions,
738 million weighted-token units, `--history-report`): 8% of sessions since 22 October had
a refused commit, 1% for the file:line coordinate (14% from 19 to 22 October, 9% before), and
the sessions that had one hold 16% of the units, an upper bound since a refusal costs a retry
inside a session. The coordinate's drop followed the guard's wording change on 19 October by
three days; whether the wording caused it is not shown. Units after the third review that
said issues are 12% of all, after the first 43% (history.py gives 42.9% in dollars), and the
first review said issues on 34% of roadmap-dfs's tasks with a transcript (39 of 114), 47% of
shop-app's (7 of 15, a small base) and 36% of chirpstack-docker's (49 of 135). These rates
and the 12% are higher than the first count of this section (29%, 43%, 30%, 11%, 40%),
which read a review's verdict from the command that wrote it and missed a verdict written
through a shell variable (`S=…/tree.py`, then `python3 $S log W22 review issues`); the
reading was fixed in W130.15 and the counts of the task files, which candidates 1 to 3
use, did not change. By units, then,
candidate 5's bound (12% of all units) is larger than what candidate 1's class alone started
(0.4% narrow, 3% wide, of the units of the 131 tasks with a transcript; candidate 3 1%,
candidate 2 none); the order of the table does not follow spend, and rests on candidate 1 changing the quality of the delivered tree or its credits (the
measure a replay moves, step 5) while 5 only limits the loss and cannot be tested by one.

What this does to the rank: the recount leaves candidate 1 first among the three that change a
review where the document had it, on the one reading that separates them (the rounds a class
alone started: 3 to 11 for 1, 5 for 3, 0 for 2), and shows that none of them started more than 11 of the 207
rounds alone. It does not show candidate 1 stronger than 2 or 3 by any word count.

### The loop

    mine → choose → change → replay control and changed → compare → keep or drop → mine

1. **Mine.** `python3 <scripts>/history.py --json /tmp/dfs-history.json` produces the
   dataset. Pick the failure class and the tasks it appears in.
2. **Choose** one candidate and a replayable set: roadmap-dfs tasks that are small
   (original at most $3), whose evidence lies in the repo (not `~/.codex`, not the
   network: the sandbox hides the checkout, the home and the store but not the public remote
   or the web, so a task whose answer is on the remote shows in the breach rows or not at all;
   and a task that sends its sessions to the original's run logs can only be guessed at, as
   W55's is), and whose original first
   review found issues, which marks a task the reviewer found wanting (whether a control tree falls
   short of the task's Goal is read from the rubric, not from the review). Take at least four. `replay.py plan TASK` prints the starting
   point and the original's numbers.
3. **Change** the tool, on a branch of this repo, one candidate at a time: the changed
   arm is that revision (`--tool-ref REV`), the control is the revision it branched from.
   A change to the law goes as `--law FILE`, with the same revision for the tool.
4. **Replay in pairs.** A pair is one task replayed twice in one sitting, control then
   changed (W5, W36, W22, W11, W5 in that order, as many pairs as step 5 asks for, at most
   five). Under kiro a replay cost 5.9 to 23.4 credits (mean 12.8), so a pair is about 26
   credits and five pairs about 130. Run them one at a
   time: each is a chain in its own scratch clone, and the three W55 replays run side by
   side used up the account's usage limit in about half an hour and were stopped for 2h20m. Each replay is bounded:
   `--cap` sessions, and `--bash-minutes M` (default 15) is the most one Bash call may
   run, with twice that as the most a call may ask for, so a runaway search ends in
   minutes, where W55's replay sat 53 minutes in `grep -r /`. A call that hits the bound
   shows in the transcript as a timeout; count those as a result (the task needed
   something the scratch clone lacks) and drop the task from the set if it recurs.
   A replay runs only through `replay.py`, which starts it inside the proot sandbox of
   section 3, so it sees its own clone and a fresh home and not the live checkout; there
   is no way to run a candidate outside it. `score` prints three breach rows for each
   replay (calls naming a live path that were answered nothing, that were answered
   output, and the original's conclusions a result showed), and they go in the
   comparison as a check that the sandbox held. They see only tool calls that name a live
   path, and proot hides paths without being a security boundary, so an escape that never
   names the path would go unseen. A replay with a call answered with live output (read
   by hand) or a conclusion shown is void: it is dropped from the count, the arm is run
   again, and a Fix node is opened to close the hole.
5. **Compare** the changed replay with the control replay of each pair, and judge the pair
   **better**, **same** or **worse** on two tiers, quality first (W130.26 to W130.28). Both arms
   end with the review passed, and the dfs reviewer grades its own system, so neither the review's
   verdict nor the count of issues reviews is the measure.
   *Tier 1, quality.* Each replay's delivered tree is scored against a rubric of the task's Goal
   clauses (`docs/dfs-rubrics/<task>.md`, one per task, written before any arm ran; rubric only, no
   tests) by a separate kiro session with a fixed prompt (`docs/dfs-rubrics/judge-prompt.md`,
   `docs/dfs-quality-judge.py`) that sees a blind snapshot (no `.dfs`, log, task file or arm name)
   and must cite file and line for every clause. A tree's score is the mean of two judgings, in
   clauses met. The pair is *better* (or *worse*) if the changed tree's score is above (below) the
   control's by more than **1.5 clauses**, the margin sized from the judge's own spread: scoring the
   20 replay trees twice gave 165 of 175 clause verdicts the same (94.3%), 14 of 20 trees the same
   score, a spread of 0.71 clauses for one judging and so 1.5 for a difference of two two-judging
   means. *Tier 2, credits (kiro records credits per session, not token counts).* When quality ties,
   the pair is *better* at no more than 70% of the control's credits, *worse* at 1.43 times or more,
   otherwise *same*; a cheaper tree of lower quality is *worse*, by tier 1. A cut-off replay's credits
   are a lower bound, so a pair in which credits decide and the bound cannot settle the verdict is
   *unresolved* (`docs/dfs-quality-pairs.py` prints it so): it is run again, unless no cost it could
   have changes how the candidate ends, and so is a pair void under step 4. The tie test is made on
   whole clause counts (the two judgings' sums differ by more than 3), not on a float mean, so a
   difference of exactly 1.5 clauses is a tie. Two limits on the cost and the blindness of tier 1:
   the judge's own cost is unknown (kiro-cli prints no credits for a judge session), so a pair's credits
   are the replays' alone; and the judge is blind by its prompt and its snapshot, not by a sandbox: it
   has read, glob and grep tools that can open any path, and the scores file keeps only its answer lines,
   so a read outside the snapshot could not be seen afterwards.
   A candidate is **kept** if it is better in **3 of 3** pairs. If not, and the first three
   were two better and one same, run two more pairs: kept if **4 of 5** are better and none
   is worse. Anything else is **dropped** (after three pairs with fewer than two better,
   4 of 5 cannot happen, so stop there). Dropped is "not shown", not "shown useless": the
   rule keeps only a clear win. Read the reviews of the changed replays that count as
   better too: a pass counts only if the review's `ok` names what it checked.

   *The two-tier rule (W130.28, redone by W130.30), the keep rate.* Over the pairs of two different
   control replays of one task (both orders, `docs/dfs-quality-pairs.py`) a change that does nothing
   reads 27% better, 48% same and 25% worse, and the sequence below keeps it **2.8%** of the time
   (the first-review rule with a 30% credits margin: 6.8%). 12% of those pairs are *unresolved*,
   because one control replay of W36 was cut off and its credits are only a bound; read as costs they
   give the figure above, and if every unresolved pair went one way the keep rate would be 1.6% to
   6.5%. A change that cuts credits to 0.7 of the control's is kept 24.2% of the time (14.9% to 33.6%)
   and one that halves them 60.0% (42.0% to 67.9%), so a modest gain is still returned as "not
   shown". Tier 1 decides only 2 of the 48 ordered control pairs (W11-c1a and W11-c2a,
   8.0 against 6.0 of 8 clauses, in both orders), and the lower tree, W11-c2a, lost one clause on a
   reading of "items" (see below) and two that the judge scored differently in its two judgings; the
   other control trees sit within the 1.5-clause margin of each other (means of 7 to 8 of 8 for W11
   without it, 6.5 to 7 of 8 for W22, 7.5 to 9 of 9 for W36, 6 to 7 of 10 for W5). So the rubric
   separates candidate from control where a whole feature is missing (W5's lists and fences), and
   otherwise credits decide. W22 C6 (printed warnings) and W5 C7 (terminal markers) are unmet in all ten
   trees each and separate nothing. The paragraph below is the earlier rule's reasoning and is kept as
   history.

   *Why pairs and this margin (W130.9, figures corrected by W130.12).* The first-review pass
   rate was the earlier measure, and it is coarse: counted from the replays' own task files
   (`docs/dfs-replay-verdicts.py`) the control passed it 7 times in 12 (W22 2, W11 3, W36 2,
   W5 0). `replay.py score` printed 9 of 12, because it reads a review's verdict from the
   command that wrote it, and twice (W22 and W5, the third control replay of each) the review
   had set `S=…/tree.py` on one line and run `python3 $S log W22 review issues` on the next,
   which the pattern does not match; the task file is the record. A pass count over twelve
   cannot separate a change from the control's own spread (a task's pass rate runs from 0 of 3
   to 3 of 3 across the four), and credits to a reviewed finish already include rework after
   an issues review, but one control replay of a task differs from another by up to 2.0
   times (W5 12.2 to 23.4, W22 5.9 to 11.2), so a pair is judged by the review count first
   and by credits only past a margin. The margin was chosen on the 15 control replays: every
   pair of two different replays of one task is a pair with no change, and how often the rule
   keeps those is its false keep rate.

   | margin on credits | no-change pair: better / same / worse | no-change kept | kept if the change costs 30% less | 50% less |
   |---|---|---|---|---|
   | 20% | 42% / 17% / 42% | 8.7% | 24% | 48% |
   | **30%** | 37% / 25% / 37% | **6.8%** | 20% | 32% |
   | 40% | 23% / 54% / 23% | 1.7% | 12% | 27% |
   | 50% | 21% / 58% / 21% | 1.2% | 8% | 20% |

   30% keeps a change that does nothing about one time in fifteen and a change that cuts
   credits by half one time in three; 20% keeps a no-op nearly one in eleven; 40% keeps a no-op
   one time in sixty and half-price one time in four. Power is low on purpose: three pairs with
   this much noise can only show a large effect, and a smaller one goes back as "not shown".
   The figures are printed by `python3 docs/dfs-replay-verdicts.py <replays> --pairs`
   (output: `docs/dfs-replay-verdicts.txt`); the page `.dfs/artefacts/8e49a572-ecd0-40de-b489-efaccd8b2798.html`
   holds the earlier figures (two replays counted as passed that the task files say failed).
   Hypothesis of W130.9; it is wrong
   if the control's pair-to-pair spread in a later sitting is wider than these 15 replays
   show (a no-op is then better more often than 7%), or if the pairs of one sitting share
   their noise (the pairs are then not independent). Cost: three pairs about 78 credits,
   five about 130, against about 310 an arm for the twelve-replay rule it replaces.

   *Why four (the first rule, replaced by W130.9).* Assume each replay passes its first review with probability p, alone
   and independently, p between 0.5 and 0.7 (the history fails 30% of first reviews; the
   three W55 replays before the sandbox passed 2 of 3, an upper bound on a sandboxed replay's rate, and the one sandboxed replay passed). Two arms of the same tool then differ by
   the margin by chance this often (exact sums over every outcome, recomputed by `python3 <scripts>/chance.py` and by the page named below):

   | margin (of 12) | kept by chance, p=0.5 | p=0.7 | kept if p goes 0.5 to 0.9 | 0.5 to 0.75 |
   |---|---|---|---|---|
   | 2 | 25.2% | 24.1% | 93.9% | 72.0% |
   | 3 | 14.8% | 12.9% | 86.8% | 58.0% |
   | 4 | 7.4% | 5.8% | 74.2% | 41.5% |
   | 5 | 3.2% | 2.1% | 56.5% | 25.8% |

   A margin of two keeps a change that does nothing one time in four, so the earlier
   rule was no test; four keeps it about one in sixteen and still finds a change that
   lifts p from 0.5 to 0.9 three times in four, but misses a modest gain (0.5 to 0.75:
   41%), so a candidate that is dropped here is "not shown", not "shown useless". The
   assumption is a Hypothesis of W97.10 (page
   `.dfs/artefacts/e2c464a2-0f7a-4d68-b5fe-a0860ca3f587.html`). It is wrong if replays
   of one task are alike (a task that passes 3 of 3 or fails 3 of 3 under both arms
   carries no information and the true noise is above the table's), or if the control
   replayed twice differs by four or more of twelve: run the control twice before the
   first candidate, and if that happens, the margin and the replays are not enough.
6. **Keep** means merged into the tool and recorded here (the row, the arms, the
   figures) and the control for the next candidate is now the changed tool. Candidates
   are tested one at a time for that reason: two changes in one arm cannot be told apart.

What the loop cannot see: whether a change helps on projects other than roadmap-dfs
(only its history can be replayed); the author's time; and drift in the model between
the control run and the changed run, which is why the arms are interleaved, control then
changed, task by task, in one sitting, and not run as two batches on different days.

A replay is not the history it was chosen from (W130.12). The four tasks were picked because
the original's first review said issues, and all four did (4 of 4); the originals were run by
claude, and every replay here runs under kiro, a different agent whose cost is in credits and
whose reviewer is not the one that wrote those verdicts. The control's first review, counted
from the replays' task files, said issues in 5 of 12 replays (W22 1 of 3, W11 0, W36 1, W5 3),
and in 1 of 3 more in the sitting of the second control (W5), so the failure that selected
the tasks recurs in under half of the control replays and not at all in W11. A candidate
aimed at a class mined from the history can only be tested on a task whose control replays
fail in that class, and the measure (rubric quality, then credits) cannot show a change aimed at a class
that does not occur; `docs/dfs-replay-verdicts.txt` lists each replay's verdicts.

Read against candidate 1's class (W130.13, `docs/dfs-review-class-reading.tsv`, one row per issues
review, my reading of its first finding line): of the four originals' first reviews one is in the
node's narrow cases (W36's Stop on a chat, an interruption) and of the control's seven issues
reviews one is (W36's late interrupt leaving a stale flag); the other six are a place where a Goal
clause is still not met (3: W22, W5 twice), or an input kind the code mishandled (3: W5's lists,
nested markup and refused picture). The two issues reviews of the changed arm (W22, W5) were of the
first kind, and in those two trees the "Break it" node did run the per-clause check for it (W22: a
grep for caps, an AST walk for shouted words; W5: a read of each place that prints task prose) and
missed what the review then found; in W11 the node found three faults and the review passed. W36,
the one task whose control fails in the narrow cases, was not run under the changed arm. So the
first three pairs of the first-review rule say the node does not reliably finish the per-clause search, and say nothing
about its interrupted, repeated and empty cases.

### Candidate 1 under the loop (W130): dropped, not shown

*Re-judged by the two-tier rule (W130.28, redone by W130.30; the text below used the first-review
rule).* Against its same-sitting control candidate 1 reads:

- **W5 better** (9.0 against 6.5 of 10 clauses; credits 27.1 against 15.9). The clauses that differ in both judgings are
  C4 (bulleted and numbered lines drawn as lists) and C5 (fenced code drawn as a preformatted block);
  the changed tree has a renderer for both (`scripts/richtext.py`), the control's `rich()` in
  `index.html` has neither, and both judgings agree on each. They are Goal clauses ("rich text") that
  either arm could have met (C8, plain text not mangled, also differs, in one judging). Against the mean of the four controls (6, 7, 7, 6.5) it is also better.
- **W11 better by the rule, and not carried by the trees** (8.0 against 6.0 of 8; credits 13.2 against
  9.8). The control it is paired with, W11-c2a, does have an edit control on the page (an "Edit goal"
  button, `index.html:460`); it lost C1 because the rubric reads "items" as the task's name and Goal text
  and that button edits the Goal only. Its two other lost clauses, C4 and C5, are the ones the judge
  scored differently in its two judgings (met then not met); on the first judging alone the pair is 8
  against 7. The other three W11 controls scored 8, 7 and 7, so against the mean of the four (7.0) the
  gain is 1.0 clause, inside the margin, and credits (13.2 against 11.8, 1.12 times) make it *same*.
  Nothing in it shows a gain from the node.
- **W36 same** (9.0 against 9.0 of 9; 25.4 against 22.9 credits, 1.11 times).
- **W22 unresolved** (7.0 against 7.0 of 8; the changed arm was cut off at 14.4 credits against 11.2,
  so its ratio is at least 1.29 and it is worse if the true cost is 16.0 or more). Against the mean of
  the four controls (8.9 credits) the bound alone gives 1.62 times, worse. It was not run again,
  because no cost could make it better, which is all the sequence needs.

In the order of step 4 (W5, W36, W22, W11) the first three pairs are better, same and unresolved:
fewer than two better, and 4 of 5 cannot happen, so the candidate is **not kept** (three better, or
two better and one same, is needed). The verdict "not shown" stands, reached on a tie test and
a W11 pair that hold, and not for the reason given below, since the issues reviews that made W22 and
W5 "worse" are no longer the measure. The one gain that carries is W5's: two Goal clauses (lists,
fences) that one control tree left out and the node's tree had, not the edge-case clauses a break-it
node would favour. It needs both: the rubric's reading of "Render rich text" is an assumption (the
starting chat renderer already draws fences but not lists), and with either clause dropped the pair is a
tie of exactly 1.5 clauses that credits (1.70 times) call worse. The verdict does not turn on it, since
a W5 that is worse leaves none of the first three better, and a W5 that is better leaves one.

*The table and the paragraphs below are the first-review rule's reading (the rule the author refuted, kept
as history); the figures "worse, better, worse" in them are not the measure.* Run under kiro in the
sandbox, each pair in one sitting, control then changed. The changed arm
is the store tool with one bullet added to `SKILL.md` "Ending a node" (the text of row 1: the last
node tries to break the work, and a tree whose last node is not that is not complete), as a copy
of the store tool and not `--tool-ref`, because the repo's `SKILL.md` is older than the store tool
the control ran. Verdicts are the task files' (`docs/dfs-replay-verdicts.txt`), credits are
`replay.py score`'s, and a pair is judged by step 5 (margin 30%):

| pair | control: first review, credits | changed: first review, credits | pair |
|---|---|---|---|
| W5 | issues, 15.9 | issues, 27.1 | worse (1.7 times the credits) |
| W36 | issues, 22.9 | ok, 25.4 | **better** (one issues review fewer) |
| W22 | ok, 11.2 | issues, 14.4 and one unpriced cut-off | worse (one more issues review) |
| W11 | ok, 9.8 | ok, 13.2 | same (1.35 times, under 1.43) |

Taken in step 4's order (W5, W36, W22) the first three pairs are worse, better, worse, so fewer
than two are better and the rule drops the candidate; all four give one better. It is **not
shown**, and not merged. A node titled "Break it" came before the first review in all four changed
trees, so the change was followed. What the reading of the reviews (W130.13) adds: in W22 and W5,
where the control's failures are a Goal clause unmet in some place, the node ran the per-clause
check and still missed what the review found, and W22's control failed its first review once in four
replays, so that pair is as much noise as a loss; in W36, the task whose control fails in
the node's narrow cases (the control's review: interrupting the first turn of a chat loses the
conversation), the node listed "interrupt an opening turn" and "interrupt, then send", the
changed tree's review passed, and it noted that path as handled. That is one pair: it says the
interrupted half of the node can reach the failure that half names, and the per-clause half did not
stop the misses it is meant for. A candidate that kept only the narrow cases would be a new
candidate, not tested here. The W36 pair's scores print 0 calls naming a live path and 0 of the
original's nodes shown, for both. The W22, W11 and W5 changed replays printed 1, 3 and 6 calls
answered with output; the earlier reading of them found the changed tool's own directory, which
lies inside the live checkout (not re-read here). The W36 pair cost 48.3 credits.

### Next

Candidate 3 was answered from the task logs, with no replay (row 3 above): the present
evidence does not support it, so it was not the first candidate to get a replay. The W17 pair below later ran its
walk-every-clause line together with a re-run-the-checks line and read better on quality. Limits of that
check: it places a finding by the names it points at (backticks, snake_case, `name()`,
file names such as `branch.py`) against the code before and the commits after the earlier
review; a fix that touches code near a name, or the file a file name points at, makes the
line `maybe-introduced`; 13 of 43 lines cannot be placed; and it reads roadmap-dfs only
(the other projects' repos are not on this box). What a misplacement could do to the
result: "most later findings were there at round one" needs at least 22 of 43 lines
present, and the 3 present and 13 unplaced lines together are 16, so no misreading of
the unplaced lines reaches it; the 20 `maybe-introduced` lines could, and are why the
result is "not shown" and not "shown false".

Correction to the two bounds that follow (candidates 3 and 6 on the replays, "one round in sixteen").
That count is true of the replays and does not carry to the loop. `replay.py candidates` picks tasks
by the original run's total cost ($3, or $3.5 here), and a task that went through several issues
rounds is not cheap, so the cut keeps the one-round tasks. In the history (`docs/dfs-round-bound-by-size.py`,
292 reviewed tasks) 3 of the 181 tasks that cost $3.5 or less have a second issues review (2%, close to the
replays' 1 of 16), against 58 of the 111 dearer ones (52%); the four replayed originals each had one issues
review. The spend after a second issues review is $399 of $1,719 (23.2%) and 99% of it is on the dearer
tasks, which no replay covers. So these two candidates are not shown worthless: the most a change that
makes round two pass could save is about 23% of spend, and by the fix-caused reading below (14 of 37
later rounds made of such lines alone) a crude 9%. Only a pair on a task above $3.5 can test it.

Candidate 3 was bounded a second time on the 20 kiro replays and on W130's own log
(`docs/dfs-round-one-bound.tsv`). A reviewer that finds everything in round one can only remove a
second or later issues round, and 1 of the 16 control replays has one (W5, a missed place that was
there at round one); W130 has two such rounds, one present at round one and one a regression of the
earlier fix. So a pair cannot show fewer rounds or 30% fewer credits for it, and every pair pays for
the longer review. What is left is quality: 12 of the 20 trees pass their last review with a rubric
clause unmet in both judgings, 7 of them outside the two clauses (W22 C6, W5 C7) that all ten trees
of their task miss (five trees each, both judgings). Whether a reviewer told to walk the Goal's clauses finds those is the premise a
cheaper check than a pair tests: the same review run on the finished trees.

That check was run (`docs/dfs-review-only.py`, `docs/dfs-review-walk.tsv`). The round note of
`review_brief` in a copy of the tool asks the review to split the Goal into clauses, say where each is
delivered and what was run, try the empty, repeated and interrupted cases, and report every miss in
one verdict (`docs/dfs-review-walk-tool.diff`). One walk review and one unmodified review (the control)
each ran once under kiro, in the sandbox, on a copy of each of 11 finished trees: the 7 that miss a
rubric clause outside W22 C6 and W5 C7 (12 clauses) and 4 that meet every clause; the reviewer saw no
rubric. The walk review said issues on 11 of 11 trees, the control on 6 of 11, and on the 4 trees that
meet every clause the walk review said issues 4 of 4 times and the control 3 of 4, so a second review
of a finished tree is rarely quiet whichever it is (W36-b4a's repeated Stop typing a second Esc, found
by both, comes with a reproduction). It named 6 of the 12 unmet clauses and the control 3 (one reader's reading of
the added lines), 5 of the 6 as lines in a "Minor findings" node and one (W11-c2a, the task name cannot
be edited) as a Fix node, at 44.9 credits against 30.2 (1.49 times). The most it could add to any
tree beyond the control review is two clauses (W5-c1a), under the 1.5-clause margin for every other
tree, W36's one unmet clause was named by neither review, and W22's only misses are the clause every tree misses; so in step
4's order (W5, W36, W22) a changed arm cannot be better on two of the first three, and the credits
of an added round are certain. Not shown, no pair run, nothing merged. What this does not settle:
one run of each review, so a review's own spread is unmeasured; the reviews' findings that no
rubric clause covers (the reproduced ones above) are real faults the quality tier cannot credit.

A sixth candidate, "after fixing a finding, re-run the node's checks and search the repo and docs for
every place that states what you changed" (a line in `SKILL.md` "Ending a node" and in the round
note of `review_brief`), was bounded the same way (`docs/dfs-fix-regressions.tsv`, one reader's
reading of the first 200 characters of each line, so a reader can flip a row). Of the 43 defect lines
in a second or later issues review of roadmap-dfs, 9 name a fault the previous fix made (a claim it
made untrue, a test it added that is flaky, behaviour that worked before it) and 9 a place or case the
fix left (the same fault through another path); 25 are neither. A later round could vanish only if
every line in it is one of those 18: 14 of the 37 rounds, 6 of them with from-fix lines alone, and
only the re-run half is aimed at the 9. In the replays 1 of the 16 control replays has a second issues
round (W5, the same kind of fault left in other places), and W130 has two, one from the previous fix.
On the replays a pair would have one round in sixteen to remove, but see the correction above: the
replays are the cheap tasks, so this says nothing of the dearer ones. Not shown, no pair run, nothing merged.

Candidates 3 and 6 were then run as one bundle on a task above $3.5 (W130.48): W17, "How to end chat
sessions so we don't leak resources on the sever" (the original: $4.49, 7 sessions, 3 reviews, 2 with
issues; it names no evidence outside the repo). The changed tool is the current one with the two
lines of `docs/dfs-w17-changed-tool.diff` in `state.py`: the walk-every-clause note in the review brief
and the re-run-the-checks line in the work brief; `run.sh` and the other scripts are the control's. The
rubric (`docs/dfs-rubrics/W17.md`, 10 clauses) was written first, from the Goal and the starting code.
Two controls and one changed arm ran under kiro (`dfs-replay-W17-k1c` and `-k2c`, `-k1w`), the changed arm
cut off at its third resume. Scores are in `docs/dfs-w17-scores.jsonl`, two judgings of each tree.

| arm | sessions | issues reviews | credits | merged | rubric (two judgings) |
|---|---|---|---|---|---|
| control 1 | 4 | 1 of 2 | 24.0 | yes | 5, 5 of 10 |
| control 2 | 4 | 1 of 2 | 20.3 | yes | 7, 6 of 10 |
| changed | 18 (7 + 5 + 3 + 3 in four runs of the chain; the first two were cut off mid-session and the third by a tool call that timed out) | 6 of 6, and 2 of its 2 critic sessions said issues | 94.7 or more (2 sessions unpriced) | no: the critic's last backtrack was open at the cap | 8, 9 of 10 |

Under step 5 the pair is better on tier 1: 8.5 against 5.0 (difference 3.5) and against 6.5 (difference
2.0), both above 1.5 clauses, and both of the control's missing clauses C4 (a screen stopped by SIGTERM
or SIGHUP closes its chats) and C6 (a page turn running when the server ends is ended) are unmet in all four
judgings of the controls and met in both of the changed tree's. C1 and C10 (a hangup-ignoring agent is
killed, and tested) are unmet in both judgings of control 1 and met in the changed tree (C1 is also unmet in
one judging of control 2); C2 (the agent's helpers that left its group) is unmet in both judgings of the
changed tree and of control 1 and met in control 2's; C7 (the idle limit can be set) is met by control 1,
unmet in both judgings of control 2 and unmet in one of the changed tree's. Credits are 3.9 to 4.7 times the
controls', which tier 2 does not weigh when quality differs. The sandbox held: `replay.py score` flags 0 calls
for each control and 6 for the changed arm, all answered with output, and the six read the changed arm's tool
copy (`dfs-tool-walk`) or the clone's own worktree, none the live checkout's items (listed in
`docs/dfs-reach-w17-w132.txt`; an earlier figure of 90 flagged calls was not reproduced). `replay.py score` once read
control 2's issues reviews as 3 of 4 against its task file's 1 of 2, because `history.verdicts` counted two
`review issues` commands that `tree.py` refused or that failed; it now counts a verdict only when its command
did not fail and `tree.py` printed no `dfs_tree:` refusal, and score and task file both say 1 of 2 for both
controls. The changed arm's 6 of 6 has no refused command among them (its task file in the replay folder is
a stub with no log, so the count rests on the run logs).

What it says of the two candidates' premises: neither removed a round or cut credits. A review told to walk
every clause did not report them all at round one: it found the page turn outliving the server (C6) in
round one, but SIGTERM (C4) only in round four, the 11th session; both controls' second reviews said ok
with C4 and C6 unmet. The extra rounds are where the quality came from; the Hypothesis of that comparison, which assumed
they did not, is answered against by its own evidence, so the pair cannot say whether the
walk wording found more or any longer loop would have: that needs the control given the same number of
review rounds, not run. It is one task and one changed arm, so it keeps nothing (step 5 needs three pairs, and
W17 is not in step 4's order), and a dearer tree that scores better counts as better under the rule as written,
at four times the credits: whether that is a gain is the author's to say. The chain was stopped at the
cap with the critic's backtrack open and the tree judged as it stood at the last commit.

Candidate 4 was bounded on the history again, with the refusal reasons split by period (`history.py
--json`, 2,126 dfs sessions, 29 September to 31 October). The file:line coordinate refusal, 113 of
1,393 sessions up to 19 October, was 7 of 480 from 22 October. The two reasons the candidate would
add (task file over its size budget, a Parent that does not exist) were 15 of those 480 sessions
(3%), and the sessions that hit either hold 3.1% of the units in that period (3.4% over all of it),
which is an upper bound because the retry is part of a session. So the candidate's whole remaining
prize is about 3% of spend at most and in practice a fraction of it, too small for a before and after count
to show: 15 sessions become perhaps 8 in nine days. Not worth a change to `tree.py` and `SKILL.md`
that every session then reads; parked with that figure.

The loop has been run once from a box with kiro (the first real run of it). Its replays were
W22, W11, W5 and W36, the four tasks of `replay.py candidates` whose files name no evidence outside
the repo; the control ran three times on each and candidate 1 once on W22, W11 and W5 and
once on W36, judged by pairs (step 5), with no change kept. The shop-app tasks are listed by `python3 <scripts>/replay.py candidates --repo optimisation-data/shop-app
--recorded-as /workspace/shop-app --limit 3.5` (W32 and W14; `score` and `replay` take the same `--recorded-as`).
W32 was prepared: `replay.py prepare W32 DEST --repo optimisation-data/shop-app` makes a clean clone at 1da34d2f with the Goal as its whole task file; no replay was run. By default the sandbox binds only the store closure of its programs, so
`nix`, `node`, `playwright` and `chromium` are absent in it. `replay.py prepare W32 DEST --shell shop-app-e2e` (also on `replay`) gives the box
a nix store of its own (`seed_store`: the sandbox's closure, nix, the flake's `shop-app-e2e` dev shell and the flake's inputs, copied
from the host store, about 53 s and 4.5 GB per box) bound as `/nix/store` and `/nix/var`. Inside it `nix develop .#shop-app-e2e` finds
`playwright`, `psql` and `java`, and chromium starts: a trivial spec passes and `pricing-detail.spec.js` runs as far as its global setup,
which fails because no Postgres listens on 5442 (the backend on 3006 is not up either). With `--also shop-app,devShells.x86_64-linux.db,db-create-views`
the box store also holds the backend and the database tools, and `sh docs/dfs-shop-app-e2e-up.sh DEST APP pricing-detail.spec.js`
(APP is the store path of `.#shop-app` on the host) starts Postgres on 5442 from an empty directory, builds the schema, starts the
backend on 3006 and runs the spec in the sandbox: 25 of 25 passed, once with the steps run by hand and once by the script from an empty database dir. The schema needs the
script's order, not `base_schema.sql` plus `migrate.sh`: the baseline lacks `context` on two note tables that migrations 0004 and 0020 read,
and the views and the migrations each need what the other creates. The backend is the host-built binary serving the clone's `static/`; a
change to `Main.hs` needs a rebuild that was not tried inside the box (nix there cannot download: it reports no CA certificate), and
`static/vendor` (the wasm views) is not built. One control replay of W32 was run under kiro (W130.43; the first attempt, 9.3 credits, raised at once because `e2e-up.sh` was not where the session worked, a harness fault, and is not counted): `e2e-up.sh` is committed to the clone with a Background note in the task file (`sh e2e-up.sh "$PWD" auto pricing-detail.spec.js`), so the session started the database and backend itself and ran the specs. It took 2 sessions and 25.9 credits (the original: 4 sessions, $3.04, first review issues), the first review said ok, and its new spec `pricing-register-line-live.spec.js` is red on the starting commit and green after. Against `docs/dfs-rubrics/W32.md` (written first; `docs/dfs-w32-scores.jsonl`) the judge gave 7 and 8 of 9: C4 (the header mark and the mismatch-only list) unmet in both judgings, C7 (a queued or offline change) unmet in one. That is one tree, with no changed arm, so it ranks no candidate: it shows only that a shop-app control passes its first review while a surface the Goal names is left unmet, the same shape as W22 and W5. farm-app: the sessions of `optimisation-data/farm-app` (the autofarm2 repo) are recorded under
`/workspace/chirpstack-docker`, not `/workspace/farm-app`, so `candidates --repo optimisation-data/farm-app` printed 0 tasks until it
was given `--recorded-as /workspace/chirpstack-docker` (the fault W130.18 fixed for shop-app); with it, `--limit 100` lists 8 tasks
(W132 $6.25, W133 $8.44, W142 $8.53, W121, W122, W118, W120, W127) and `plan W132` prints the starting commit c757d4ea and the
original's figures (5 sessions, first review issues). The checks run on the host: `nix build .#test-farm-operator` is in the store and
`FARM_OPERATOR_SRC=farm-operator test-farm-operator -k map` ran 97 tests there (95 passed, 1 failed on a menu focus assertion, 1 skipped); whether it runs
inside the replay box is not yet tried.
farm-app W132 was replayed once as a control under kiro (W130.46), with `replay.py prepare W132 DEST --repo optimisation-data/farm-app
--agent kiro --shell default --also test-farm-operator` (DEST under the writable mount, as /tmp is noexec) and a Background note in the
clone's task file saying how to run the check. The check runs in the box: `nix build --no-link --print-out-paths .#test-farm-operator`
(1m42s, from the box store) and then `FARM_OPERATOR_SRC=$PWD/farm-operator P/bin/test-farm-operator -k map` ran 79 tests at the starting
commit, 77 passed, 1 failed (`test_the_zone_card_counts_down_its_time_left_without_an_event`), 1 skipped. The chain stopped at its cap of
10 sessions (6 work, 3 review, 1 critic) with the task open, at 62.4 credits (the original: 5 sessions, $6.25); the first review said issues,
and so did the next two, 3 of 3. Their findings were a scope narrowing and a design choice stated without a Hypothesis (W132.5, W132.7) and one
defect, Escape pressed mid-drag letting the box reappear (W132.9, an interrupted case); the critic's finding was a missing test that the
editor is left alone. Several late sessions wrote that the map tests could not be built offline, which the critic's own node contradicts (58 and 60
map tests ran green offline in its node and the last session). Scored against `docs/dfs-rubrics/W132.md` (written first; `docs/dfs-w132-scores.jsonl`) the judge gave 11 of 12 in
both judgings, C1 unmet (a label's box keeps its second line's height when that line is empty). Of 10 calls the score flags as naming a live path with
output (listed in `docs/dfs-reach-w17-w132.txt`), they are `ls /nix/store | grep ...` of the box's own store plus a few `which`, `S=` and `E=` calls and a `grep` of `map.js`, all inside the clone or the box's store; none shows the live checkout, and no node of the original appeared in any
result. One tree, no changed arm and a task that was not merged: it ranks no candidate.
Candidates 3 and 6 were run once on W17, a task above $3.5, with two controls: better on quality (8.5 against 5.0 and
6.5 of 10), 18 sessions and at least 94.7 credits against 4 sessions and 20.3 and 24.0, the task not finished at the cap; kept
by nothing, since one task is one pair. What is next, besides a second pair on a dear task, is candidate 2 (naming assumptions), the only one not yet bounded or run, as a pair against a
same-sitting control, on the tasks whose control trees fall short of the rubric in the clauses the
candidate targets (the kiro control's first review said issues in 5 of 12 replays, none in W11; the
rubric scores of the control trees are in `docs/dfs-quality-scores.jsonl`).

A SKILL.md with its unenforced and unactionable lines removed (`docs/dfs-skill-trim.diff`, 20.6 KB to 18.6 KB:
the "claude -p" and `Monitor` wording, "one sentence" for a Determination and "one or two sentences" for a
node's wording, the worktree section cut to what a worker acts on, "never `git add -A`", the re-plan sentence
the law already holds, and the "Shell discipline" section, a rule about keeping context small that is neither obvious nor
unenforceable) was run as one pair on W36 under kiro, control then trimmed in one sitting. It is not shown as a bundle: one pair cannot say whether the
1.5 times came from the removals or from dropping that section, and a trim that keeps "Shell discipline" was not tried. `check.py`, `tree.py` and `state.py` enforce none of the removed lines; the lines kept
are the ones they do enforce (Hypothesis with its page, a standalone Approach, archive moves) and the "ONE turn"
rule `run.sh` records as measured. Quality tied (control 8 of 9 clauses in both judgings, missing C5, a stop
with no turn running; trimmed 9 of 9), credits did not: 26.7 against 17.8, 1.50 times, so the pair is worse by
step 5, and the trimmed review said issues where the control's said ok. One pair worse means 3 of 3 cannot
happen and the first three pairs cannot be two better and one same, so it is dropped as not shown, with no
further pair; scores in `docs/dfs-skill-trim-scores.jsonl`. One replay each cannot tell a cost of 1.5 times from
the control's own spread (W36 control replays ran 11.5 to 17.0 credits, changed arms 22.9 and 25.4).

A replay needs the configured agent's binary (claude, codex, kiro-cli or opencode) and `proot`
(both from nixpkgs at the agent pin; `REPLAY_CLAUDE_CODE`, `REPLAY_KIRO_CLI`, `REPLAY_CODEX`,
`REPLAY_OPENCODE` and `REPLAY_PROOT` name others) and a DEST that can run scripts
(inside the repo's ignored `.dfs/runs` works on the box used here).

### Seven changes weighed by the author's time (W153)

W153 took seven changes to the hypothesis workflow and judged each on three tiers in this order:
quality of the delivered tree, the author's time, credits. The order, the margins and the verdict
rule are in `docs/dfs-w153-measures.md`; the order and the tie-break between tiers are my choice and
were stated there as a Hypothesis, not seen by the author. Margins: quality 1.5 clauses (the judge's
spread), words 60 a tree, counts one a tree, credits 2 times. A change whose evidence is inside the
margin is called inconclusive, never adopt or drop. Every count reads roadmap-dfs, shop-app and
farm-app as at d595c44a (1,519 nodes, 124 raises, 25 backtracks); today's files have grown
(1,708 nodes), and the scripts in `docs/dfs-w153-*.py` rerun each count.

What each kind of evidence can say here. Accounting (reading task files) shows what a change would
have removed, not whether the tree got worse without it. The judges did not work: the text-only E2
judge flagged 36 of 50 judgings of backtracked nodes and 53 of 82 of unchallenged ones (Fisher p
0.43), and the judge that reads the repository flagged 2 of 12 backtracks and 0 of 12 controls (p
0.239, 10 missed; 7 of the 12 critics' faults came from re-running something, which a read-only judge
cannot do). Replay evidence is one task. On W17 the three controls met 5.0, 7.5 and 8.0 of 10
clauses (a spread of 3, twice the judge's 1.5) and the pooled arm (E1, E2, E6, E7) met 6.5, so a gap
under about 3 clauses is invisible, and no raise happened in any kept arm, so E4 and E5 had no case.
The driver-answers assumption was not tested: the W109 and W3 pairs (table answers against a blank
"proceed") never used a table answer, because the table arm raised nothing. W3's 2.5-clause gap
(7 of 8 against 4 or 5) came from one flagged raise and a different design choice, not from the
answers.

| change | evidence | quality | author time | credits | verdict |
|---|---|---|---|---|---|
| E1 no page for a Hypothesis, `assumed` | accounting; W17 pooled arm | pooled arm inside the controls' range (one page-less Hypothesis) | 131 of 376 pages would not be made; about 8 fewer `Wrong if` words a task (inside 60); the files cannot show the author opened any page (12 of 18 rulings were on a page-bearing node, 6 on none) | about 1.9k to 2.8k output tokens a task, small | inconclusive |
| E2 gate at determination | judge test; W17 pooled arm | no judging evidence (judges did not separate backtracks from controls) | not measured | not measured | inconclusive |
| E4 checkpoint shows churn and a default | accounting | not measured, and not measurable by replay: the review-count and ten-session checkpoints were removed on 15 and 20 Oct 2026, the worker never read them, and a replay driver answers both arms alike | all 36 raises stay; the author answered stop 2, continue 29, steer 5; a default of stop would have been overridden 23 times of 24 where it fires; 15 of the 29 continues ran on to four or more issues reviews, all on five tasks, and neither the no-fall rule nor a caused-by-the-previous-fix reading separated those tasks from the rest | none | inconclusive |
| E5 tooling fault filed as a draft | accounting | not measured | removes 17 of 26 tooling raises if "blocks the Goal" means a fix was needed to finish (A), 8 if it means the commit was refused (B); 9 of the 17 need a new permission to go around a refusing hook; pool-wide 0.07 raises a task (A) or 0.03 (B), inside one, but 1.55 and 1.6 in the 11 and 5 tasks that held one | none | inconclusive |
| E6 word budget on a Determination | accounting; W17 pooled arm | none of the pooled arm's Determinations was over 80 words, so the budget never bit | 80 words refuses 365 of 1,483 and cuts 52 words a task (inside 60); of 36 long ones read in full 1 has its verdict first past word 80 and 13 a hand-off (revert, keep, who does what) | none | inconclusive |
| E7 minor findings in the review's `ok` | accounting; W17 pooled arm | no review round was owed to a minor node (169 reviews); at least 10 of 484 minor lines (2.1%) came back as the same defect and 29 (6.0%) as a related one | adds 42 words a task over all tasks (inside 60), 137 in the 73 tasks that have a minor node | none | inconclusive |
| E8 blind reviewer | review-only run on 23 trees, hand-matched | headline: 2 unmet clauses caught by the informed reviewer, 4 by the blind one (p 0.625); headline or minor line 5 against 4 (p 1.000); the pool is 35 targets with power 59% at 60% against 30% catch rates | not measured | not measured | inconclusive |

Reading the table. No change was shown to hurt the delivered tree and none was shown to help it:
every difference is inside the noise of the evidence that exists, so nothing is adopted or dropped
on this round. What would settle each: E1, E6 and E7 need three or more paired tasks that raise
hypotheses and minor findings (the W17 spread alone is 3 clauses); E5 needs a replayed task
that meets a tooling fault, which W17 did not, and E4 needs the author's checkpoint back first (parked); E8 needs about 50 targets and
the blind reviewer run with the git history closed (it ran `git log`, `git show` or `git diff` in
all 20 first trees, and its output held 12 of 78 Determinations and 4 Summaries in 5 trees); E2
needs a judge that can run the node's check, since most critic backtracks rested on a rerun.
The E6 hand-off finding is the one to watch if a budget is tried: a verdict is rarely lost, a
hand-off often. The 8 tooling raises E5 can remove without a new permission are the part of E5
that costs the law nothing.

Tooling faults found on the way were filed as draft tasks and not fixed in W153:
`artefact_check.sh` reports every page blank when fontconfig has no default config (W156, verified
again here on the shipped template: `FAIL blank` without a config, `clean` with one),
`tree.py shelve` leaving the task file over its budget on a commit that grows it (W157),
`tree.py start` lost when the task file is restored from git (W158), and `replay.py run` leaving the
killed run's runner alive when a wrapper `timeout` ends the command (W159). W157 to W159 were seen
by earlier sessions of W153 and not re-run when filed.

Scripts and outputs: `docs/dfs-w153-mine.py`, `-e1.py`, `-e4.py`, `-e5.py`, `-e6.py`, `-e7.py`,
`-e8-test.py`, `-e8-hand.py`, `-judge.py`, `-judge-repo.py`, `-build-tool.sh`, with their `.txt` and
`.tsv` beside them, the answer tables in `docs/dfs-w153-answers.md` and the W17 scores in
`docs/dfs-w153-w17-scores.jsonl`.
