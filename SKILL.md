---
name: roadmap-session
description: Run one short, ephemeral work session on one task's decision tree in .dfs/items — plan the ways to the goal as branches, pursue one, let evidence say when to jump to another, commit each node, and stop near the context checkpoint. Opt-in; deliberately NOT in the skill tree.
allowed-tools: Read Edit Write Bash Grep Glob
---

# One session, on a budget

The law is `.dfs/ROADMAP.md` §0, carried verbatim in your briefing. This file is how a
session carries it out.

## Start

1. Your **briefing** is in the prompt: the law, the task's status and current node, what
   the author has said since the last finished session, and any uncommitted work. It is
   derived by `dfs_state.py`, the same code the runner stops on, so do not re-derive it.
   It names the tool's scripts directory: `<scripts>` in this file means that
   directory, wherever roadmap-dfs is installed.
2. Read the task file, `.dfs/items/<task>.md`, whole: `## Goal`, `## Background` if
   present, `## Tree`, `## Log`.
3. **The author first, then the critic.** An answer to a raise, a correction at a
   node, or a critic's backtrack is the first work; the briefing lists each with what
   it prunes. After a correction, resume with a node carrying
   `Corrects: <the correction's ts>`: under the corrected node if the author said
   confirmed, or at a sibling if refuted (unpark one, or add a new alternative). A
   backtrack withdraws the node itself as well: redo its step beside it, under its
   parent, with `Corrects: <the backtrack's ts>`.
4. **Uncommitted work the briefing says is yours** is the current node's, left by a
   session that stopped mid-node. Review it and carry on with that node.
5. **Open nodes the critic or reviewer added** carry only an Approach: what is wrong
   and what would settle it. Take them up like any open node, writing the Hypothesis as
   you start.

## A node

```
### W13.4 · <title>
Parent: W13.3
Status: open | parked | confirmed | refuted
Approach: <what the node does and how: the files, the change, the check>
Hypothesis: <the assumptions the approach rests on, and what would show them wrong>
Evidence:
- for: <an observation that bears on an assumption, and what it shows>
- against: <the same>
Determination: <what you concluded, and so where the work goes next>
Corrects: <only on a node carrying out a correction or backtrack: its ts>
```

- Ids are `<task>.<n>`, n one past the highest in the file. The tree is the `Parent:`
  pointers: a chain of steps names the one before; alternatives name the same parent.
  No `Parent:` means a child of the task.
- **On a branch other than main or master**, your briefing names the tag and the one
  part you write (`.dfs/items/<task>/<tag>.md`) and the next id (`W13.1@<tag>`). Every
  node you add and every log entry goes there; read every other part of the task too,
  since the task is all of them. You may edit a node in another part to determine it,
  never add to one. Commit subjects start with the tagged id (`W13.1@<tag>: <what it did>`).
- **Each field has one job.** The Approach is the implementation: what the node
  does, where, and the check that will show it worked. The Hypothesis is not a second
  description of the work; it is the assumptions the Approach rests on, the facts
  about the code or the problem that, if false, make this the wrong branch
  ("`sync()` is the only writer of `cursor`", not "adding the guard fixes the race").
  The Evidence is the observations that bear on those assumptions, one line each: what
  was run or read, what it showed, and which way it cuts. What you built, the steps
  you took, what you tried first and every test you ran belong in the Approach and the
  commit, not here; only the check that the Approach named, and its result, is a line
  of evidence.
- **A node's Approach and Hypothesis are concise, clear and stand alone.** A session,
  the critic and the TUI each read one node without the rest of the tree, so say the thing
  itself: the file, function, fault and check by name. "Fix what W13.3 found", "as in
  W13.2" or "the other case" sends the reader off to find what the writer already
  knew. Refer to another node only beside the substance, never in place of it, and
  keep it short: one or two sentences, not the history of how you got there. Keep the
  Evidence as short: a node with ten evidence lines is usually a log of the work.
- `parked` is an alternative not being pursued: its Determination says why it is not
  first. It is not determined, so it may be edited and reopened, and neither it nor
  what is planned under it holds the task open.
- A node is **live** unless it is pruned: below a refuted node, or cut off by a
  correction or backtrack.
- **Write the hypothesis when you start the node**, before the work: what must be
  true for this approach to be the right one, and what would show it is not. A node
  with no hypothesis cannot be refuted, and the critic will say so.
- **Evidence is what you saw, not what you believe**: the command and what it
  printed, the test and whether it went red, cited in a line, not pasted. Try to
  refute your own hypothesis. The critic looks for a node confirmed on reasoning
  alone, your own reading of something offered as an observation, several pieces of
  evidence that are really one, a hypothesis that restates the approach instead of
  naming what it assumes, and an alternative parked or refuted without a fair try.
- Add nodes freely and keep them small. After each node, re-plan the open nodes ahead:
  edit, add or refute them. Backing up is normal.
- **A determined node is history.** Once confirmed or refuted and committed, it is not
  edited; `dfs_check.py` refuses it. Later understanding goes in a new node. Its
  evidence lines may MOVE, verbatim, into `.dfs/archive/items/<task>.md` (leaving
  `Evidence: archived.`), which is how a task file over its budget shrinks; append
  them at the archive's END under a `**<id> Evidence**:` heading, since nothing
  already there may move, and stage the archive in the same commit. Its Approach and
  Hypothesis may move too, by `dfs_tree.py shelve <task> [<ids>]`, which appends
  each verbatim to the archive's end as `**<id> Approach**: <text>` and leaves
  `Approach: archived.`; `dfs_tree.load` and the TUI put the words back, and the
  critic finds them in the archive with the evidence. (`Plan:`, the Approach's old
  name, is still read as it.) Status, Parent and
  Determination never move.
- **The log only grows, but its past may move.** `dfs_tree.py compact <task>` moves
  every entry older than the author's last answer, correction or accept, verbatim, to
  the archive's end under `**Log**:`. Every count is read through `dfs_tree.load`,
  which merges them back, so nothing the briefing says changes. Stage both files.

## Ending a node

- **Confirmed**: commit, with a subject that starts with the node's id —
  `W13.4: <what the node established>` — then carry on beneath it.
- **Refuted**: commit it too; the commit is the evidence of what was tried. Then jump:
  unpark the best sibling (or add one) and, as that node's first change, revert the refuted
  node's commits, newest first, unless the evidence says why a change should stay (then
  its Determination says `<sha> stays`). Not with `git revert`: a node's commit carries
  its task file, and reverting that takes the determined node's text out, which the
  guard refuses. The briefing lists refuted or pruned commits still in the code, each
  with the command that reverts its code alone; commit that under your node with
  `This reverts commit <sha>.` in the body, so the briefing stops listing it.
- Stage the node's paths by name, never `git add -A`, and read the diff first. The
  task file goes in the same commit, unless `.dfs/` is gitignored (below).
- The tree is **complete** when no live node is open. Make sure the last node says the
  Goal is met, and how you know. A reviewer then judges the implementation; each
  finding that shows the Goal is not met arrives as a new open node (lesser ones
  batched into one), and the next session fixes it. After two reviews that did not
  settle, the runner raises to the author rather than reviewing again.

## When `.dfs/` is gitignored

Supported: `.dfs/` is planning, and git history shows the walk. Your briefing says
when it is. Then:

- **Every node still ends in a commit** whose subject starts with its id. It stages
  the code alone, since there is no task file to stage; a node that changed no
  tracked file, a refuted one included, is `git commit --allow-empty`, because the
  commit IS the node's record in git. Never `git add -f` a `.dfs` path.
- **The body carries the node's `Parent:`, `Status:` and `Determination:` lines
  verbatim**, so the walk reads from `git log` alone.
- The rules on the task file are unchanged: a determined node is history, the log
  only grows, archive moves stay verbatim. Only nothing checks them against HEAD, so
  keep them yourself; "stage the archive" and "stage both files" have nothing to
  stage.
- Branches still write only their own part; the parts all sit side by side in the
  one working tree, since no branch carries them.

## Raising

Raise only on the grounds in §0; anything else is a node. An issue with the roadmap
tooling itself (roadmap-dfs, the `.dfs` rules, their hooks), including one that
blocks your task, is raised and not fixed in the task.

```
python3 <scripts>/dfs_tree.py log <task> raise <node> <<'EOF'
<the decision, answerable without opening the code: the options, what each commits
the work to, what you would do, and the strongest case against it>
EOF
```

**When the decision turns on how parts of the system are wired together**, so the
author would otherwise have to rebuild the picture in their head from prose, write
an artefact for it first: one self-contained HTML page in `.dfs/artefacts/`, checked
with `<scripts>/dfs_artefact_check.sh` and its screenshot looked at, then linked
from the raise as `http://localhost:<port>/<uuid>.html`. How to write one is
`<scripts>/../docs/artefacts.md`; read it before starting one. The raise must still
be answerable with the artefact gone, so the options and your recommendation stay
written out in the raise itself. A raise about anything else (a budget, a tooling
bug, a choice between two wordings) needs no artefact.

Then stop. A raise blocks the task until it is answered.

## The budget

Run `python3 <scripts>/dfs_context.py` after each node: it reports your peak
context against the checkpoint (§1 L1) and says when to wrap up. A session may walk
several nodes. To wrap up, commit a node that is finished; for one that is not, write
its evidence so far into the task file and stop. The next session picks up the
uncommitted work.

## You get ONE turn, and a long command runs in the FOREGROUND

It is `claude -p`: when you end your turn the process exits. A background command, a
`Monitor`, or "I'll report when it finishes" is a promise to a turn that never comes.
Run the long thing in the foreground in one Bash call with a `timeout` sized to it; the
chain sets the Bash default to one hour. Keep each step under that hour even though the
tool allows six: past an hour the prompt cache expires and the next request pays to
rewrite the whole context. If a job cannot fit, record in the node what is running and
where its output lands, and stop.

## Shell discipline

Every result stays in context for good. Read a slice, never a whole file twice
(`sed -n 'a,bp'`, `grep -c`, `--stat`, `| head`). Prefer one command that answers the
question to three that circle it.
