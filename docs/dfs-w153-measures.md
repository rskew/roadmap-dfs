# W153 · measures, margins and what this box can run

Fixed before any W153 count. Nothing below was read off a result.

## What this box can run (checked 2026-11-06, redone after the critic's backtrack)

The first check here said proot cannot start (seccomp). That was wrong: the cause was `/tmp`, which is
`tmpfs ... noexec` on this box (`mount`), and proot refuses a temporary directory with no execute
permission. `replay.py` sets `PROOT_TMP_DIR` to `<DEST>/box/tmp`, so a DEST on an executable mount works.
The repo's mount (`/workspace/roadmap-dfs`, zfs) is executable; `$HOME` and `/tmp` are not.

| Need | Result | How checked |
| --- | --- | --- |
| Agent for judges and review-only runs | `kiro-cli` 2.35.1 only, logged in (IAM Identity Center). No `claude`, no `codex`. | `which`; `kiro-cli whoami`; `kiro-cli chat --no-interactive "Reply with the single word: ok"` printed `ok` |
| `replay.py` itself | runs | `plan W17`, `candidates` print |
| Replay sandbox | **runs**, with DEST on the repo's mount (here `.dfs/runs/dfs-replay-w153-probe`, gitignored). kiro-cli runs inside proot. The Seccomp: 2 line and the refused `unshare -r` do not stop it. | `replay.py prepare W54 DEST --agent kiro` (0.4 s), then `replay.py run DEST W54 --cap 2 --agent kiro`: 2 sessions, exit 0 both, 23 turns, 6 min wall, W54.1 confirmed; `replay.py score` read 3.8 credits |
| Replay sandbox with DEST in `/tmp` or under `$HOME` | **does not start** (proot: temporary directory has no execution permission) | the critic's run with the default `/tmp`; `mount` |
| `docs/dfs-review-only.py` (E8) | Same sandbox call (`sandbox_argv`, `PROOT_TMP_DIR` in `<copy>/box/tmp`), so it runs when REPLAYS_DIR is on the repo's mount (`.dfs/runs/replays` is). Not itself run in this node. | read the script; same `make_box` and `sandbox_argv` as `replay.py run` |
| Artefact check | runs only with a font config. Without one it reports `blank` even for the unmodified `_TEMPLATE.html` (Fontconfig error: no default config). With `FONTCONFIG_FILE` naming the DejaVu directory it is clean. | ran it on the template and on this task's page |
| History data | `/workspace/roadmap-dfs/.dfs/{items,runs,archive}` and `optimisation-data/{shop-app,farm-app}` are in the main checkout, read-only for this task. They are not in the W153 worktree (`optimisation-data` is gitignored, `.dfs/runs` is untracked). The old scratch scripts are still in `/tmp/dfsmine`. | `ls` |
| Replay trees | 20 in `.dfs/runs/replays` (W11, W22, W36, W5, each 5 arms), 4 in `optimisation-data` (W132-k1, W17-k1c, W17-k1w, W17-k2c) | `ls` |

Limits that did show: a replay session is slow (6 minutes for two cheap sessions, so a dear task is
hours, in the foreground, under one hour a command), and kiro reports credits per session (3.8 for the
two above; a dear replay is 20 to 95 or more). No sign-in problem inside the box: kiro-cli answered.

What that decides:

- Accounting nodes (E1, E4, E5, E6, E7): can run. They read task files.
- Judging nodes (E2 judge, E8 reviewer): can run with `kiro-cli chat --no-interactive`; E8's review-only run can run too.
- Replay nodes (answer-table pair, E1/E2/E6/E7 arms, E4/E5 where reached): can run, bounded by wall time and credits.
- The count of nodes in the three projects is now 154 + 66 + 31 tasks and 1,064 + 435 + 209 = 1,708 nodes (`audit.load_tasks`), against the 1,519 of the 2026-11-05 pass. The history grew. Counts are reproduced from today's files and the gap is reported, not forced.

## Order of the tiers

1. Quality of the delivered tree: rubric clauses met (replay trees, via the rubric in `docs/dfs-rubrics`), or the judge's score.
2. The author's time.
3. Credits.

Each tier is reported on its own. They are not added into one score.

## Author-time tier

Counted from the task file and log through `dfs_tree.load` (so archived words count):

- Raises the author must answer, in two columns: **loop checkpoints** (body matches `since you last spoke|implementation reviews? of|the review loop|walk stopped|runner` in its first 300 characters) and **tooling faults** (body matches `roadmap tooling|tooling|check.py|tree.py|run.sh|state.py|the guard|hook|dfs_run|supervisor`). Anything else is a substantive decision. These are the regexes of `/tmp/dfsmine/author.py`, kept as they were so the Background counts are comparable. A hand reading replaces the regex where a node says so.
- Pages the author would open: Hypotheses that carry an `.dfs/artefacts/<uuid>.html` page. Those whose `Wrong if` only the author can answer are counted apart.
- Words the author reads: the Summary, every Determination, and every Hypothesis (whitespace-separated words).

## Margins (from Background, fixed here)

- Quality: the judge's spread, 1.5 clauses (0.71 clauses of 8 to 10 on repeat judging). Under about 15% of the clauses is inside the noise.
- Words: under 60 words a tree is a tie (control SD 30 to 60).
- Counts: one hypothesis, one page, one raise or one minor-findings node a tree is a tie.
- Credits: controls for one task vary 1.9 to 2.2 times, so a gap under 2 times is a tie.
- E2 judge: if it misses more than half of the 25 critic backtracks, E2 judging is parked.
- E8: a gap inside 1.5 clauses is a tie.

## Verdict rule (my choice, stated as a Hypothesis in the node)

- Quality worse than the margin: drop, whatever it saves.
- Else author time worse than the margin: inconclusive at best.
- Else a real gain in quality or author time: adopt.
- Else credits better than the margin: adopt on credits alone.
- Else: inconclusive. A change whose evidence is inside the noise is called inconclusive, never adopt or drop.

A change that rests on accounting alone says "accounting" beside its verdict: accounting shows what a change would have removed, not whether the tree got worse without it.
