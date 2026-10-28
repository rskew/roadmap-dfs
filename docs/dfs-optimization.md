# Optimizing dfs from its own history

How to make roadmap-dfs better using the projects it has already run: read what past
sessions did (static analysis), then change the system and re-run old tasks from
their starting point to see whether the change helps (replay).

## 1. What the past recorded

Surveyed 2026-10-22 on the box the chains run on. Four projects have been walked:
roadmap-dfs itself, chirpstack-docker, house and shop-app.

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
| farm-app | 2 | 8 / 9 | 7.5 / 7.8 | 0.00 | 0 |
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
   would class them. The most reviewed task, roadmap-dfs W75, had 23 reviews and 16 of them
   found issues (`history.py`, "Most expensive tasks"). Quality, measured as review rounds with issues, is the largest cost
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

`prepare`, `run` and `score` are the same three steps one at a time. What it does:

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

The last two rows are from `replay.py score` (below): the three replays were not sealed
from the box. Their DEST sits inside the live checkout (`/workspace/roadmap-dfs/.dfs/runs/replays/`,
the one mount here that runs scripts), and sessions run as the same user, so the live
`.dfs/runs`, `.dfs/items`, `.dfs/worktrees` and `~/.codex` were one directory up.

What the proof does not show, and what a comparison has to allow for:

- **One replay is one sample.** The same task, under one system, cost 1.95, 2.77 and 3.44
  dollars (1.30 originally) and its first review passed twice in three; a change to the
  system can only be judged over several replays of each arm (control and changed),
  compared on the first review's pass rate before cost.
- **The replay's box is the original's, with the answer on it.** `seal` empties the clone
  of the original's later work, but not the box: a replay's sessions run as the same user,
  and the live checkout's `.dfs/runs` (the original chain's run logs, with its sessions'
  tool output), `.dfs/items` (the original `W55.md`), `.dfs/worktrees`, the codex session
  logs in `~/.codex/sessions` and every earlier session's transcript under `~/.claude` are
  readable. `replay.py score` now counts the tool calls that name them from outside DEST
  (DEST, its exported tool and the tool it runs under are cut out first) and the original's
  node titles and Determinations that a tool result printed and the replay did not write
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
- **What the box must hide, and what it can.** For a clean replay the sessions would see
  the clone, the tool and the account's credentials and nothing else: no live checkout,
  no `~/.codex`, no other `~/.claude/projects`. This box cannot do it: a mount namespace
  (`unshare -Urm`) is refused ("Operation not permitted"), `/tmp`, `/dev/shm` and the home
  do not run scripts, so DEST cannot move out of the live checkout, and the original's run
  logs, transcripts and task file are on the same disk. A box that can (a container that
  mounts only the clone, `sandbox.sh run`, or a copy of the repo without `.dfs/runs`) is
  the fix, and until a replay runs in one its pass rate is an upper bound on a clean
  replay's, with `score`'s two rows printed beside it. Tasks whose evidence lies outside
  the repo make poor candidates for the same reason, and a replay should be given a
  timeout on a single Bash call.
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
says only that the changed-arm path runs end to end and that the session reads the
changed text; it says nothing about whether any change helps. `replay.py score` now
refuses a DEST that is not a prepared replay of that task, and a replay whose transcripts
hold no session of the task, where it used to print a table of dashes.

## 4. What to try, and how to find out

### The measure

Judged on a replay, a change succeeds if more first finished trees **pass their first
review**, at a cost no higher than the control's. That is the one number the history
supports as the thing to move. `history.py` prints these counts in its "Share of all
dfs spend" block (run of 2026-10-28, the same run as section 2): of 276 tasks that had a
review, the first review found issues in 84 (30%); 104 tasks (38%) had at least one
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
| 1 | **Break it before finishing.** The last node of a tree is a node that tries to break the work: for each Goal clause, a check that would fail were the clause not met, and the cases the work's own tests skip (interrupted, empty, repeated, a short screen). The tree may not be declared complete with that node absent. | 40.9% of spend follows an issues review; 101 of 207 review finding lines are defects, but only 2 of them say a test or check is absent. Of the 23 defects whose review says it reproduced the fault (`docs/dfs-break-it-cases.tsv`; one reading by one session, so a reader can disagree row by row), 7 are in the cases this node names (4 repeated, 2 interrupted, 1 empty, none a short screen), 6 more only if "interrupted" is stretched to a failure path or a race, and 10 are not (an unusual starting state, which includes a repo with no commit yet, two checkouts out of step, a different branch). So 7 of the 101 defects are shown to be ones this node would have looked for, 13 on the widest reading; the other 78 do not say they were reproduced and cannot be placed, so 7 is a floor. Whether a repo with no commit yet is "empty" or "an unusual starting state" is a reading (row 8 of the file, classed with the other unusual starting state, row 1); read as empty it is 8 and 14. 13 refuted nodes and 8 backtracks against 535 confirmed is consistent with sessions rarely looking for evidence against their own work, which it does not show. The rank above 2 is kept because neither is stronger on this evidence (2's 28 lines repeat across rounds) and the order is not moved by it. | `SKILL.md` "Ending a node" | W55 and the other small roadmap-dfs tasks whose first review cited an untested case (shop-app and chirpstack-docker cannot be replayed from this box) |
| 2 | **Name assumptions as they are made.** A pass before commit that lists every choice the Goal did not settle and writes each as a Hypothesis or says there is none. | 28 of 207 review finding lines are unmarked assumptions (some repeat across rounds). | `SKILL.md` "A node" | any task whose original review cited an unmarked assumption (find with `audit.py findings`, rows of class `assumption` in `docs/dfs-review-findings.tsv`) |
| 3 | **One review finds all of it.** The reviewer lists every finding it can ground before stopping, ranked, instead of the first few. | Weak, and tested only on roadmap-dfs: of 43 defect lines in a second or later issues review (21 tasks; 26 have two or more issues reviews), 3 name code no commit since the earlier review touched or came near, 7 name code that review could not have seen, 20 name code the fixes changed (either could hold), 13 name no code the check can place (`docs/dfs-rounds.tsv`, `audit.py rounds`). The premise, that most later findings were there at round one, is not shown. | the review prompt (`run.sh`) | only worth a replay if a reviewer given the same tree can be shown to find the 3 to 23 present ones; the three are in W75, W88 and W91 |
| 4 | **Stop refusals at the source.** Show the guard's three most common refusals (task file over budget, a file:line coordinate, a Parent that does not exist) in the skill, and let `tree.py` report the first two before the commit. | 236 of 2,043 sessions hit a refusal, 304 refusals; each costs a retry. | `SKILL.md`, `tree.py` | any task, scored on `refused commits` alone; the smallest effect, cheapest to try |
| 5 | **Cap a review loop.** After three issues rounds the task raises for the author instead of continuing. | 30 of 276 reviewed tasks (11%) had three or more issues rounds and hold 34.3% of all dfs dollars (`history.py`); the cap saves only what is spent after the third round, less than that. Does not improve quality, it limits the loss, and hands the author the tail. | `state.py` | none: read from history by counting what the cap would have saved |

Not candidates, from the evidence: cutting reviews (they are 27.0% of spend and where
the bugs were caught), re-read avoidance (150 of 3,458 reads), and encouraging branching
as an end in itself (candidate 1 is the version of it with a purpose).

### The loop

    mine → choose → change → replay control and changed → compare → keep or drop → mine

1. **Mine.** `python3 <scripts>/history.py --json /tmp/dfs-history.json` produces the
   dataset. Pick the failure class and the tasks it appears in.
2. **Choose** one candidate and a replayable set: roadmap-dfs tasks that are small
   (original at most $3), whose evidence lies in the repo (not `~/.codex`, not the
   network; every task's original run logs and task file are on this box, so the replay's
   `score` rows for outside reach are read against that), and whose original first review found issues, so the control has
   something to move. Take at least four. `replay.py plan TASK` prints the starting
   point and the original's numbers.
3. **Change** the tool, on a branch of this repo, one candidate at a time: the changed
   arm is that revision (`--tool-ref REV`), the control is the revision it branched from.
   A change to the law goes as `--law FILE`, with the same revision for the tool.
4. **Replay** each task under each arm **three times**: 4 tasks × 2 arms × 3 = 24 replays,
   at the W55 figures ($1.95 to $3.44 a replay) roughly $47 to $83. Run them one at a
   time: each is a chain in its own scratch clone, and the three W55 replays run side by
   side used up the account's usage limit in about half an hour and were stopped for 2h20m. Each replay is bounded:
   `--cap` sessions, and `--bash-minutes M` (default 15) is the most one Bash call may
   run, with twice that as the most a call may ask for, so a runaway search ends in
   minutes, where W55's replay sat 53 minutes in `grep -r /`. A call that hits the bound
   shows in the transcript as a timeout; count those as a result (the task needed
   something the scratch clone lacks) and drop the task from the set if it recurs.
   The box is not hidden from the replay (section 3): `score` prints each replay's
   calls naming the live checkout and the original's conclusions a result showed, and
   those two rows go in the comparison.
5. **Compare** the arms per task, never pooled: first-review pass rate (out of 3),
   median dollars, refused commits, replays that stopped BLOCKED or on the cap. Then
   read the reviews of the changed arm's passes: a pass counts only if the review's
   `ok` names what it checked (the `ok` of two guarded W55 replays still listed three
   and four defects each). A candidate is **kept** if it passes the first review in at
   least **four** more replays of the twelve than the control does, and no task gets
   worse by more than one replay of three, **on the pass rates of all replays and again on
   only those whose `score` shows none of the original's conclusions in a result** (a
   margin that holds only when the exposed replays are counted is the box's, not the
   change's). **Dropped** otherwise, with the numbers in the candidate's row.

   *Why four.* Assume each replay passes its first review with probability p, alone
   and independently, p between 0.5 and 0.7 (the history fails 30% of first reviews; the
   three guarded W55 replays passed 2 of 3, an upper bound on a clean replay's rate for the reason in section 3). Two arms of the same tool then differ by
   the margin by chance this often (exact sums over every outcome, recomputed by the page named below):

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
   first candidate, and if that happens, the margin and the 24 replays are not enough.
6. **Keep** means merged into the tool and recorded here (the row, the arms, the
   figures) and the control for the next candidate is now the changed tool. Candidates
   are tested one at a time for that reason: two changes in one arm cannot be told apart.

What the loop cannot see: whether a change helps on projects other than roadmap-dfs
(only its history can be replayed); the author's time; and drift in the model between
the control run and the changed run, which is why the arms are interleaved, control then
changed, task by task, in one sitting, and not run as two batches on different days.

### Next

Candidate 3 was answered from the task logs, with no replay (row 3 above): the present
evidence does not support it, so no replay budget goes to it first. Limits of that
check: it places a finding by the names it points at (backticks, snake_case, `name()`,
file names such as `branch.py`) against the code before and the commits after the earlier
review; a fix that touches code near a name, or the file a file name points at, makes the
line `maybe-introduced`; 13 of 43 lines cannot be placed; and it reads roadmap-dfs only
(the other projects' repos are not on this box). What a misplacement could do to the
result: "most later findings were there at round one" needs at least 22 of 43 lines
present, and the 3 present and 13 unplaced lines together are 16, so no misreading of
the unplaced lines reaches it; the 20 `maybe-introduced` lines could, and are why the
result is "not shown" and not "shown false".

The tasks to replay are not yet chosen. `python3 <scripts>/replay.py candidates` lists
what step 2's rule picks from the history (original at most $3, first review said
issues, a node commit to start before): W22, W11, W10, W5, W76, W3, W36 and W40, with
W55 (the task of section 3) dropped because its evidence was outside the repo. The
rule's last clause, evidence inside the repo, is not decidable from the history; reading
each task file for it, and taking four, is the first step. Then
run the control (the current tool) twice on those four tasks, to see whether two
control arms differ by four or more of twelve; then candidate 1 on the same four tasks,
the first real run of the loop (its changed-arm path ran once, in section 3). It needs `claude` (from nixpkgs) and a DEST that can run
scripts (inside the repo's ignored `.dfs/runs` works on the box used here).
