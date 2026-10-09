# Roadmap

The law every session here works under. It is read whole at the start of every
session, so it stays short. Each task is a decision tree under `.dfs/items/`; the
order tasks are worked in is `.dfs/order.md`.

## §0 How work is done

Each task is a decision tree, and the tree is for planning: its branches are the
different ways the goal could be reached. Lay the alternatives out as sibling nodes,
pursue one and park the others with the reason. Each node's approach says what it
does; its hypothesis says only what that approach assumes, and what would show the
assumption wrong. As you work, append evidence for and against those assumptions. Evidence against the branch you are on is what tells you
to jump to a parked sibling: refute the branch and unpark the alternative. Evidence
for it lets you confirm the node and go deeper. Add nodes as the work reveals them,
and after each node re-plan what is ahead.

A node ends with a commit whose subject starts with its id. A session may end
mid-node, at its budget or cut off, and the next session picks up the uncommitted
work, finishes the node and commits it. Backing up reverts the refuted node's commits,
unless the evidence says why a change should stay; the commits stay in history as its
evidence.

After every fifth session on a task, a critic judges the tree's reasoning, not its
code: whether the evidence is true and independent, whether it supports the path
taken, and whether the path still serves the task's Goal. When the tree is complete, a reviewer judges the implementation against the
goal. What either finds is work, so it goes in the tree: the reviewer adds an open
node for each finding that shows the Goal is not met, and the critic adds one or
backtracks to the first node whose evidence does not carry its determination, which
withdraws that node and everything below it. A lesser finding (a wording, a stale
sentence, a missing test for a guard) is never a node of its own: it rides in one
node beside a real finding, or in the review's `ok` for the author to read. When two
reviews since the author last spoke have found issues, the next is a raise, not a
third review.

Raise only for what needs the author, the system's designer and user: a fact only
they have, a preference between outcomes, access the session lacks, or a task that
cannot move until another does (the author orders the tasks). A bug, a flaky test, a
false sentence or a missing check is a node, never a raise, with one exception: an
issue with the roadmap tooling itself (roadmap-dfs, the `.dfs` rules, the hooks
that enforce them), including a tooling rule that blocks the task, is raised and not
fixed in the task. It is not the task's Goal, and where it is worked is the author's
call, since the author orders the tasks. Anything you could
reasonably choose, choose, and write it down as the node's hypothesis: the author
reviews the whole tree when the task is done. A raise blocks the task until it is
answered, and an answer reopens it until a work session acts on the answer. Ten
sessions on a task without the author raises for their review. When the author
corrects a node, everything below it is pruned; confirmed, the work resumes under it
and the siblings made after it are pruned too; refuted, it resumes at a sibling.

## §1 Settled law

L1 · The session checkpoint is whatever `<scripts>/dfs_context.py` reports (the tool's
scripts directory, which the briefing names), never a number carried in a session's
head, and never restated beside it.

<Add a rule here when the author settles one that every session must follow: one
numbered paragraph each, L2 onwards, saying the rule and why.>
