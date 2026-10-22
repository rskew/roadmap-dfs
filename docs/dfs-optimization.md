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
