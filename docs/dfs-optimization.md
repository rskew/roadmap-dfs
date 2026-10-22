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
| Task files and archive | `<repo>/.dfs/items/*.md`, `.dfs/archive/items/*.md` | roadmap-dfs: 101 tasks, 497 nodes; logs with 288 work, 216 review and 23 critic sessions | The tree as decided: node statuses, hypotheses, evidence, reviews ok/issues, critic ok/issues, author answers | Time or cost per node; anything the session tried and did not write down |
| Git history | each repo | roadmap-dfs: 507 commits, 436 of them node commits, 2026-09-30 to 2026-10-21 | The code each node changed, in order, and the state of the repo before any task began: the starting point a replay needs | The other three projects: only their transcripts are on this box |
| Old versions of the tool | `/nix/store/*-roadmap-dfs-<rev>` | 39 store paths | A past version of the skill, law and scripts to run a replay under, for a before/after comparison | Versions already garbage-collected |

Two facts from the tally already bear on where to look:

- **The tree is not used to back up.** Of roadmap-dfs's 497 nodes, 423 are confirmed,
  64 parked, 10 open or started, and none refuted. Every task walked a single line;
  the parked siblings were never taken up.
- **Review is close to half the sessions.** 216 review sessions against 288 work
  sessions; 88 of the 235 recorded verdicts found issues, each reopening the task for
  more work and another review.

Only roadmap-dfs can be replayed from this box, since replay needs the repo at a
task's starting commit. The other three projects contribute to the static analysis
through their transcripts alone.

## 2. Where the work goes

`scripts/history.py` reads every transcript into one record per session (kind, task,
tokens, tool calls, commits, refused commits, verdicts) and one per task, and prints
the tables below. Dollars are the runner's own `total_cost_usd` where it recorded one,
and otherwise tokens priced at the same ratios (input 1, cache read 0.1, cache write 2,
output 5), which match the runner's figure to within 0.1% at the median and 40% at the
90th percentile over 566 sessions it priced.

Run on 2026-10-22 over 2,018 transcripts, 1,919 of them dfs sessions:

| project | tasks | sessions per task, median / p90 | $ per task, median / p90 | review issues per task | raises |
|---|---|---|---|---|---|
| chirpstack-docker | 106 | 4 / 19 | 2.6 / 21.0 | 0.67 | 46 |
| house | 20 | 4 / 22 | 3.6 / 24.0 | 0.75 | 3 |
| roadmap-dfs | 99 | 3 / 13 | 2.0 / 9.4 | 0.74 | 10 |
| shop-app | 22 | 6 / 30 | 7.4 / 50.3 | 1.95 | 22 |

What it shows, most important first:

1. **A finished tree is often not finished.** 41.5% of all dfs spend is on a task after
   one of its reviews has already said issues. Of the 81 findings roadmap-dfs's reviews
   wrote, 32 are bugs the work session did not test for (an interrupted merge, a
   regression at one chain, a drag on a short screen), 19 are unmarked assumptions and
   25 are lesser wording. The worst case, shop-app W13, had 18 reviews and every one
   found issues. Quality, measured as review rounds with issues, is the largest cost
   as well as the largest failure.
2. **The tree does not branch in practice.** Of roadmap-dfs's 497 nodes none is
   refuted, and across all four projects' transcripts a session wrote `Status: refuted`
   about 35 times against about 2,350 `Status: confirmed`. Work proceeds as a confirmed
   line; the parked siblings are never taken up. Either the work rarely meets evidence
   against it, or sessions do not look for it: the 32 untested bugs suggest the second.
3. **Review and critic are 29% of spend** (review 22.5%, critic 6.3%, work 71.2%).
   Reviews are cheap per session (median $0.29 to $0.96) and are where the bugs
   above were caught, so they are not the place to cut.
4. **The guard refuses a commit in 11% of sessions** (214 of 1,919, 283 refusals).
   The reasons most seen are a task file over its size budget (~144), a file:line
   coordinate in a task file (~200 across files) and a node naming a Parent that does
   not exist (~72). Each costs a retry, not a failure.
5. **Re-reading is not a cost.** 128 of 2,964 `Read` calls repeat an earlier slice.

Not measured yet: whether a task's work was later fixed by another task (needs commit
subjects matched across tasks), and the author's time, which no transcript records.

## 3. Replaying a task

`scripts/replay.py` runs a finished roadmap-dfs task again from where it began and scores
the run with the code that scored the original (`history.session()`, `tasks_of()`).

    python3 <scripts>/replay.py plan   W55                     # starting point and the original's numbers
    python3 <scripts>/replay.py replay W55 /tmp/dfs-replay-W55-a [--cap N] [--tool DIR | --tool-ref REV] [--law FILE]

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

### Proved on W55

W55 ("Codex limits reached until reset time, time not parsed properly") is small: 5
sessions and $1.30 originally, with a first review that found issues. Replayed on
2026-10-22 under the same tool as this session:

| | original | replay |
|---|---|---|
| sessions (work / review / critic) | 5 (3/2/0) | 6 (3/3/0) |
| dollars | 1.30 | 3.48 |
| node commits | 4 | 7 |
| first review | issues | issues |
| reviews with issues / rounds | 1 / 2 | 2 / 3 |
| spent after a review said issues | 0.47 | 1.66 |
| chain stopped on | | DONE, review passed, merged |

The replay planned one node, was found wanting by its first review, which added two
nodes, and by its second, which added two more, then passed. It reproduced the original's
shape (the first finished tree failed review) at 2.7 times the cost.

What the proof does not show, and what a comparison has to allow for:

- **One replay is one sample.** The same task cost 1.3 or 3.5 dollars and took one or two
  failing reviews; a change to the system can only be judged over several replays of each
  arm (control and changed), compared on the first review's pass rate before cost.
- **The replay's box is not the original's.** W55's original evidence was codex session
  logs in `~/.codex/sessions`, which the scratch clone does not carry; the replay's first
  session ran `grep -r /` for them and sat in it for 53 minutes until the search was
  killed by hand. Tasks whose evidence is outside the repo make poor replay candidates,
  and a replay should be given a timeout on a single Bash call.
- **The tool is the current one**, not the original's, so the control arm is the current
  tool; the original column is the history, not a control.

## 4. What to try, and how to find out

### The measure

Judged on a replay, a change succeeds if more first finished trees **pass their first
review**, at a cost no higher than the control's. That is the one number the history
supports as the thing to move: over the 234 tasks that had a review, the first review
found issues in 70 (30%); 87 tasks (37%) had at least one issues round and spent 69% of
all dfs dollars; 27 tasks (12%) had three or more and spent 42%. The cost is a long
tail of review loops, not a typical task. Cost per task, rounds with issues and refused
commits are read beside it, and a reviewer that passes more often by finding less is
caught by the next section's check, not by this number.

### Candidates

Ordered by (share of spend the evidence puts behind it) over (cost and risk of trying
it). Each names the file it changes and the replay that tests it.

| # | Change | Evidence | Where | Test replay |
|---|---|---|---|---|
| 1 | **Break it before finishing.** The last node of a tree is a node that tries to break the work: for each Goal clause, a check that would fail were the clause not met, and the cases the work's own tests skip (interrupted, empty, repeated, a short screen). The tree may not be declared complete with that node absent. | 32 of 81 review findings are bugs the work did not test for; 41.7% of spend follows an issues review; 35 refuted nodes against 2,350 confirmed says sessions rarely look for evidence against their own work. | `SKILL.md` "Ending a node" | W55 and the other small roadmap-dfs tasks whose first review cited an untested case (shop-app and chirpstack-docker cannot be replayed from this box) |
| 2 | **Name assumptions as they are made.** A pass before commit that lists every choice the Goal did not settle and writes each as a Hypothesis or says there is none. | 19 of 81 findings are unmarked assumptions. | `SKILL.md` "A node" | any task whose original review cited an unmarked assumption (find with `history.py --json`, filter on the finding's class) |
| 3 | **One review finds all of it.** The reviewer lists every finding it can ground before stopping, ranked, instead of the first few. A task that needs five review rounds usually has more than five problems at round one. | 44 tasks needed two or more issue rounds; shop-app W13: 18 reviews, all with issues. Cuts rounds, not work, if the second finding was already there at round one; no evidence yet that it was. | the review prompt (`run.sh`) | a task with 3+ rounds: compare findings of round 1 against the union of later rounds in the task log before spending a replay |
| 4 | **Stop refusals at the source.** Show the guard's three most common refusals (task file over budget, a file:line coordinate, a Parent that does not exist) in the skill, and let `tree.py` report the first two before the commit. | 214 of 1,919 sessions hit a refusal, about 283 refusals; each costs a retry. | `SKILL.md`, `tree.py` | any task, scored on `refused commits` alone; the smallest effect, cheapest to try |
| 5 | **Cap a review loop.** After three issues rounds the task raises for the author instead of continuing. | 12% of tasks hold 42% of spend. Does not improve quality, it limits the loss, and hands the author the tail. | `state.py` | none: read from history by counting what the cap would have saved |

Not candidates, from the evidence: cutting reviews (they are 22.6% of spend and where
the bugs were caught), re-read avoidance (128 of 2,971 reads), and encouraging branching
as an end in itself (candidate 1 is the version of it with a purpose).

### The loop

    mine → choose → change → replay control and changed → compare → keep or drop → mine

1. **Mine.** `python3 <scripts>/history.py --json /tmp/dfs-history.json` produces the
   dataset. Pick the failure class and the tasks it appears in.
2. **Choose** one candidate and a replayable set: roadmap-dfs tasks that are small
   (original at most $3), whose evidence lies in the repo (not `~/.codex`, not the
   network), and whose original first review found issues, so the control has
   something to move. Take at least four. `replay.py plan TASK` prints the starting
   point and the original's numbers.
3. **Change** the tool, on a branch of this repo, one candidate at a time: the changed
   arm is that revision (`--tool-ref REV`), the control is the revision it branched from.
   A change to the law goes as `--law FILE`, with the same revision for the tool.
4. **Replay** each task under each arm **three times**: 4 tasks × 2 arms × 3 = 24 replays,
   at the W55 figures roughly $30 to $85. Run them one at a time, since each is a chain
   in its own scratch clone and the limits are the account's. Each replay is bounded:
   `--cap` sessions, and `--bash-minutes M` (default 15) is the most one Bash call may
   run, with twice that as the most a call may ask for, so a runaway search ends in
   minutes, where W55's replay sat 53 minutes in `grep -r /`. A call that hits the bound
   shows in the transcript as a timeout; count those as a result (the task needed
   something the scratch clone lacks) and drop the task from the set if it recurs.
5. **Compare** the arms per task, never pooled: first-review pass rate (out of 3),
   median dollars, refused commits, replays that stopped BLOCKED or on the cap. Then
   read the reviews of the changed arm's passes: a pass counts only if the review's
   `ok` names what it checked. A candidate is **kept** if it passes the first review in
   at least two more replays of the twelve than the control does, and no task gets worse
   by more than one replay of three; with this sample a smaller difference is noise (one
   task replayed to 1.3 and 3.5 dollars). **Dropped** otherwise, with the numbers in the
   candidate's row.
6. **Keep** means merged into the tool and recorded here (the row, the arms, the
   figures) and the control for the next candidate is now the changed tool. Candidates
   are tested one at a time for that reason: two changes in one arm cannot be told apart.

What the loop cannot see: whether a change helps on projects other than roadmap-dfs
(only its history can be replayed); the author's time; and drift in the model between
the control run and the changed run, which is why the arms are interleaved, control then
changed, task by task, in one sitting, and not run as two batches on different days.

### Next

Candidate 3 can be answered from the task logs with no replay, so it goes first: for the
44 tasks with two or more issue rounds, whether the later rounds' findings were
already present in the tree at round one. Then candidate 1 on four tasks, the first real
run of the loop.
