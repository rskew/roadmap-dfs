# W153 · worked cases: where a change's mechanism showed up in one real or replayed tree

The counts in `docs/dfs-optimization.md` are too small to show a gap. This file reads single cases instead.
A case can show that a change *can* work and how. It cannot show how often, or that the gains outweigh
what the change costs elsewhere. No case here moves a verdict in the table.

How the cases were chosen: by reading, from the files this task already holds (the E4, E5, E6 and E8
tables, the 25 critic backtracks in `docs/dfs-w153-judge-cases.jsonl`, the W17 replay trees in
`.dfs/runs/replays`). I looked for the clearest instance of each change's mechanism and for the clearest
case against it. They were not drawn at random, so a favourable case is expected to exist. The first
sentence of each case says whether the change was *in effect* (a replay ran with it) or *counterfactual*
(read from history: what the change would have done).

## E2 · gate at determination — in effect (W17 pooled arm) and counterfactual (critic backtracks)

In effect. In the W17 replay with the pooled tool (`dfs-replay-W153-W17-P1`), 7 of its 9 Determinations
state a before-and-after observation (a probe test red before, green after; "the probe listed `apart`
alone, where it listed `apart` and `deaf` before"; "the new test went red" on the old lock). In the
controls the same wording is in 2 of 5, 4 of 8 and 3 of 10 (one control has no Determination).
The count is a regex for red, before, and "would have", read on one pooled arm, and the arm's workers
were told the gate. It shows the gate changes how a Determination is written. It does not show a better
tree: the arm met 6.5 of 10 clauses against the controls' 5.0, 7.5 and 8.0.

Counterfactual. Of the 25 critic backtracks, I read 13 whose reason is that the check could not have
come out differently or did not observe the claimed thing. The clearest:
- W68.5: the Determination rested on the `reorderpending` flow. The critic ran the same flow on the parent
  commit and it passed there too, so nothing in the suite would go red if the fix were removed.
- W32.32: the regression test patches `outboxPut`, but the code now reaches `putCoalescedOperation`. The
  forced-order variable stayed null and the block was skipped, so the green result observed ordinary
  completion and neither claimed order.
- W21.15 and W14.14: the verdict was decided by the harness's own label, or by the definition of the
  measurement being tested.

These are the gate's question in the critic's words. Each cost a critic round and a redo. Against it: the other 12 of
the 25 are other faults (a grep that missed a phrase split over a line, a figure that did not reproduce, a
check run with the wrong scripts). W67.3's grep was an observation that could have differed and was just
wrong, so a gate would not have caught it. And `docs/dfs-w153-judge*.txt` found that a judge reading the
node text separated these nodes from sound ones poorly (2 of 12 flagged when it could read the repository),
so whether the worker, who has the run to hand, answers the question honestly is still untested.

## E8 · blind reviewer — in effect (two trees, both with no Determination or Summary in the blind output)

- W22-c1c (task: lowercase all the all-caps text). The worker's Summary says the Goal was fully met. The
  informed reviewer, given that Summary, returned `ok`. The blind reviewer returned `issues` with a fix node
  naming `scripts/web.py` (~l.844 and ~l.848 print `INSTALL` and `START AGENTS`) and `scripts/metrics.py`
  (`OPTIMAL SESSION LENGTH`). That is rubric clause C6, unmet.
- W17-k2c (task: no leaks when a chat ends). The blind reviewer found that a TERM or HUP to the screen or
  the web server skips all closing, so chats and turns leak (clauses C4 and C6). The informed reviewer
  raised a Hypothesis about a different change, did not name C4, and named C6 only in a minor line (a send
  after `close_all` starts a turn nobody kills).

Against it, from the same table: W11-c2a/C1 (the item's title cannot be edited) was named by the informed
reviewer only, and the informed reviewer found W22-c1b's capitals and W17-k2c's `kill_group` reuse that the
blind one did not. Over the 35 targets they are level (see the E8 row). The two cases above show the
mechanism the change relies on: a reviewer handed the worker's "done" can stop at it.

## E5 · tooling fault filed as a draft — counterfactual

- W109.5 (roadmap-dfs, raised 2026-10-24, answered 15.1 hours later). The worker said two tooling faults
  (non-atomic `tree.py` writes, a dead run's `meta.json` left open) did not touch W109's Goal, which was
  met, that it had closed the node that carried them, that W109 was otherwise ready for review, and
  recommended making them a task of their own. The author answered with exactly that option. Under E5 the
  worker would have filed the draft and gone to review: 15.1 hours of waiting and one author answer saved.
- The E5 table (`docs/dfs-w153-e5.tsv`) files W113.1, W113.9, W113.10 and W130.4 under the same answer
  (draft; waits of 3.8, 5.7, 1.4 and 1.8 hours). I read only W109.5's text for this file; W113.9 and W113.10
  had no refusal and were answered with a change to the law. With W109.5 that is 5 of the 17 raises E5
  removes on the Goal's own test, 27.8 hours of a blocked task.

Against it: 9 of the 17 removable raises need a permission the law does not give (going round a refusing
hook, usually with `--no-verify`). E5 is only a saving for the 8 that need no new permission, and on the
current tool 14 of the 17 are fixed or cannot occur (W153.49), so W109.5's kind is the case that has gone.
W75.60 (the task-file size guard refused every W75 commit, 74.8 hours) is the case where a draft would not
have helped: the guard blocks the arm that drafts as well. This very task hit that guard in W153.42.

## E4 · churn evidence at the checkpoint — counterfactual, and a case against its default

W32 (shop-app) was asked "2 implementation reviews found issues, accept or continue" seven times between
14 and 17 October with the same wording, and the author answered continue each time (16 issues reviews
followed). The raise text carried nothing about the findings: not that W32.24 and W32.28 each extended the
fix to one more operation, nor that W32.46 was test-timing hardening that its own session said "does not
advance W32's Goal". Churn evidence would have put those in the one line the author reads. That is the
information gap the change fills.

It is also the case against a default of stop: the reviews the continues let run led to W32.51, where accepting the tree
would have knowingly reported a successful user edit as failed and rolled it back. The author's habit of
continuing (29 of 36) is right at least here. The change keeps the author's decision, so this shows a gap in what
the author sees and not a harm from the recommended default being stop. It does not show that the churn lines would
have changed an answer.

## E6 · word budget — counterfactual

W39.18 (shop-app) has a 373-word Determination whose first word is the verdict ("Confirmed for checks 1, 2, 3
and 4") and whose rest is the evidence of four checks, a commit-message's worth, and a correction to two parked
nodes. The skill already says that belongs in the Evidence and the commit. A budget would have made the
worker put it there and left the author reading the verdict and one line.

Against it: W130.11's Determination (150 words) puts its verdict past word 100 and ends with "9a42daa71a is
reverted ... 8a1225829a stays", the revert-or-keep a Determination is meant to carry. An 80-word
budget would cut that tail, not the evidence before it. Of 36 long Determinations read in full, 13 had a
hand-off first past word 80 and 1 a verdict (see the E6 row). The case for E6 is a long Determination of
the W39.18 kind; the case against is the W130.11 kind.

## E1 · no page for a Hypothesis — in effect (W17 pooled arm) and history

In effect. In the W17 replays the controls that wrote a Hypothesis wrote a page for each: 12,971, 14,138,
14,817 and 10,890 bytes (one control wrote none). The pooled
arm's one Hypothesis (a helper an agent starts is part of the chat; wrong if the author wants a long-lived
server to outlive it) has none. About 3,000 output tokens a page by bytes over four. The arm was in the
controls' range on clauses and in the middle of their range on credits (20.1 against 16.4 to 30.9), so the
saved page is not visible in credits.

History. 6 of the author's 18 rulings on a hypothesis were on a node with no page, and each was a
few words: "Web page" (W1.1), "Both" (W4.1), a question that redirected the design (W17.2: "can't we just
sigterm the chat and it will stop all child processes?"). So the author rules from the text of the Hypothesis, and a
page is not a precondition. Against it: 12 of 18 rulings were on a page-bearing node, and the files cannot
say whether the author opened the page.

## E7 · minor findings in the review's `ok` — no case found that shows it effective

Nodes whose whole content is a wrapped docstring or a closed file (W75.69; W87.11) took a node and a commit
each and would have been a line in an `ok`. That is the saving, and it is small. I found no case where a
line left in an `ok` was shown to be enough, and the opposite is easy to find: the `uncommitted()` /
`unpushed()` reading of the main checkout came back as a minor line in W75.12, W75.15 and W75.18, though
each sat in a minor node. That does not show E7 worse, since the node form failed there too. E7 stays
without a favourable case.

## What the cases add

E2 and E8 have a mechanism that shows in a real tree, and E5 has five raises whose answer was the
change itself, on a tool since fixed. E1 and E6 have favourable cases and clear counter-cases. E4's case
shows a gap in what the author sees and a case against a default of stop. E7 has none. No case shows a
net effect: that needs the paired arms the other rows say they lack.
