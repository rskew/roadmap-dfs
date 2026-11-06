# W153 · answer tables for the replay tasks

Written from each original task's log before any W153 replay arm ran. Only what the author knew at the raise's
time and a choice among the options the raise itself offered. The later fix, the final code and the rubric are
not used. A replay raise that matches an entry gets that entry's answer, the same in every arm. One that does
not match gets the option the original took, or "proceed on your recommendation", and is logged as flagged.

## W109 (original $7.50, 11 sessions, 1 raise answered, 1 raise for the author's own edits)

| # | Original raise (kind, subject) | Answer, verbatim | Matches a replay raise when |
| --- | --- | --- | --- |
| 1 | Tooling. Two roadmap-tooling faults found while working W109 that do not touch its Goal (non-atomic task-file writes in `tree.py`; a dead run's `meta.json` stays open). Options: (a) make them a new task, (b) take them as extra nodes in W109. Worker recommended (a). | `A` | the raise reports tooling faults outside the Goal and offers "new task" against "extra nodes". Answer: `A: make them a new task of their own and finish W109 without them` |
| 2 | Merge blocked by uncommitted edits of the author's in the main checkout (`scripts/branch.py`, `scripts/test_chain.py`). Options: commit or stash and answer, or leave the branch unmerged. | `Commit and move on` | never: a replay has its own clone with no edits of the author's. If it comes up, flag it and answer "commit and move on". |

Original corrections at nodes: none for W109. The worker's own recommendation at row 1 was the option the author took, so
the pair below cannot tell "the table carries the fix" from "the table agrees with the worker"; it can only show whether
the table's answer leaves the tree no different from a blank "proceed".

## W3 (original $2.78, 7 sessions, 1 loop checkpoint)

| # | Original raise | Answer, verbatim | Matches when |
| --- | --- | --- | --- |
| 1 | Loop checkpoint: 2 implementation reviews found issues and the fixes are in; accept the code or answer to let one more review run. | `Continue` | any checkpoint of the review loop |

Original correction at W3.1 ("I don't have e it, do we store the logs anywhere?") is a correction on a node and is not replayed.

## W30 (original $2.41, 8 sessions, 1 loop checkpoint)

| # | Original raise | Answer, verbatim | Matches when |
| --- | --- | --- | --- |
| 1 | Loop checkpoint after 2 implementation reviews with issues, fixes in. | `Continue` | any checkpoint of the review loop |
