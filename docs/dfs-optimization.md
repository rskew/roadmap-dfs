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
