# roadmap-dfs

A roadmap of tasks, each one a decision tree, and a runner for the short, ephemeral
agent sessions that search it. Each session takes the current node, states a
hypothesis, gathers evidence for and against it, and either confirms it and goes
deeper or refutes it and backs up; it raises only what it cannot decide without the
author, and stops near a measured context checkpoint.

It is a **tool that operates on another repo**. Nothing here belongs to any one
project — the project's own roadmap lives in that project, under `.dfs`.

## Install, start a roadmap, run it

Either as a flake, which puts seven commands on PATH:

    nix profile install github:<owner>/roadmap-dfs     # dfs, dfs-run, dfs-init, dfs-check,
                                                      # dfs-hook, dfs-tree, dfs-artefact-check
    nix run github:<owner>/roadmap-dfs#dfs-init        # or one at a time, without installing

or as a plain checkout anywhere (python3, git and bash are all it needs), running
`<checkout>/scripts/dfs_*.py` and `dfs_run.sh` by path. Either way the agents are
launched through `nix run` unless `CLAUDE_CMD` / `CODEX_CMD` say otherwise.

**Every command runs from the root of the project whose roadmap it works**
(below). To start one there:

    cd ~/src/some-project
    dfs-init                  # or: python3 <checkout>/scripts/dfs_init.py

`dfs_init.py` writes `.dfs/ROADMAP.md` (from `templates/ROADMAP.md`: the law, which
you then make your own), `.dfs/items/`, `.dfs/.gitignore`, and the guard as the
repo's `pre-commit` and `pre-merge-commit` hooks, wherever `core.hooksPath` says
hooks live. It never overwrites: a hook the project already has is left alone and the
lines to add to it are printed. It is idempotent, and hooks in `.git/hooks` belong to
one clone, so run it again in each clone. `--playwright-shell <flake ref>` records
the project's dev shell for the artefact check (below).

A hook finds the tool through `DFS_TOOL` (a checkout's path), then the path
`dfs_init.py` wrote into it (a checkout's, relative when the tool is vendored inside
the repo, and none for a nix store path, which garbage collection deletes), then
`dfs-hook` on PATH. Where none is, a commit touching `.dfs` says it went unjudged
rather than skipping in silence. A hook may also simply be a link to
`scripts/dfs_hook.sh`, which takes its mode from its own name.

Then:

    dfs-run --open            write a task
    dfs-run W1 5              a chain of up to 5 sessions on it
    dfs-run --review W1       answer raises, correct nodes, accept a finished tree
    dfs                       the screen, and the walk

The rest is configuration, all optional:

- `DFS_ARTEFACT_PORT` (3016): where the TUI and the raises expect `.dfs/artefacts`
  to be served, by `python3 -m http.server <port> --directory .dfs/artefacts`.
- `.dfs/playwright-shell` or `DFS_PLAYWRIGHT_SHELL`: the nix dev shell that supplies
  `playwright` for `dfs_artefact_check.sh`, when the project has one whose fonts or
  browsers it wants; otherwise PATH, then `nixpkgs#playwright-test`.
- `CLAUDE_CONFIG_DIR`: where `dfs_metrics.py` finds transcripts (else `~/.claude`).

`nix flake check` runs the test suite.

## The two halves

    roadmap-dfs/                 the tool, installed anywhere
      SKILL.md                   what one work session does, start to stop
      flake.nix                  the commands above, and the test suite as a check
      templates/                 a new roadmap, a new task, a new artefact
      docs/epic-agent-roadmap.md  the reasoning, and the measurements the rules
                                 are derived from
      docs/artefacts.md          how to write a picture for one raise
      scripts/
        dfs_run.sh               a chain of sessions; also --review, --open, --chat
        dfs_tui.py               the screen over all of it, and the walk
        dfs_tree.py              the one place a task file's format means anything,
                                 and the one writer of its Log
        dfs_state.py             status, the walk, and the briefings, derived
        dfs_check.py             the tree's own invariants, against HEAD
        dfs_hook.sh              that check as a git hook runs it
        dfs_init.py              start a roadmap in the current repo
        dfs_context.py           what THIS session has spent, from its transcript
        dfs_metrics.py           what our sessions cost, over the whole corpus
        dfs_limit.py             the one place a usage limit is told from a crash,
                                 and when it lifts
        dfs_artefact_check.sh    render a raise's picture and check it reads
        dfs_paths.py             the one place a path is derived
        dfs_runs.py              the one place "live" means anything
        dfs_order.py             the one place the task order means anything

    <work root>/.dfs/            one project's state, committed with it or gitignored
      ROADMAP.md                 the law: a few paragraphs, read by every session
      items/<id>.md              one task's decision tree and its log, as main/master wrote it
      items/<id>/<tag>.md        the part a branch wrote (see "Branches")
      order.md                   the author's task tree: the order tasks are worked in
      archive/                   the roadmap before 2026-09-25, and each task's moved evidence and log
      artefacts/                 a picture for one raise, served over HTTP
      runs/                      chain logs (gitignored — see .dfs/.gitignore)

## Each task is a decision tree

`.dfs/items/<id>.md` has a `## Goal`, an optional `## Background`, a `## Tree` and a
`## Log`. Every todo and every decision is a node:

    ### W13.4 · <title>
    Parent: W13.3
    Status: open | parked | confirmed | refuted
    Approach: <what the node does and how: the files, the change, the check>
    Hypothesis: <the assumptions the approach rests on, and what would show them wrong>
    Evidence:
    - for: <an observation bearing on an assumption>
    - against: <an observation bearing on an assumption>
    Determination: <what was concluded, and so where the work goes next>

The Approach is where the implementation lives. The Hypothesis and Evidence are only
the reasoning behind it: a sentence or two naming what must be true for the approach
to be the right one, and one line per observation that bears on it. `Plan:`, the
Approach's old name, is still read as it.

Ids number nodes in creation order; the tree is the `Parent:` pointers. A new task is
usually a linear chain, each todo the child of the one before. Backing up is a new
node whose parent is further up, and everything planned under the refuted node is
PRUNED — derived, never written. A determined node is history: `dfs_check.py`
refuses a commit that edits its status, determination, evidence, approach or
hypothesis. Evidence, approaches and hypotheses may MOVE to `.dfs/archive/`, verbatim
(`dfs_tree.py shelve` moves the last two), which is how a task file over its budget
shrinks.

⚠️ **A node ends with a COMMIT, not a session.** Its subject starts with the node's
id (`W13.4: ...`), which is how `dfs_tree.node_commits` finds it — the file cannot
carry a hash, since a commit cannot name itself. A session that stops mid-node
leaves the node's work uncommitted, and the next session on the task picks it up.
That only holds because sessions run SERIALLY, so the runner refuses to start on a
dirty tree unless the last session anywhere was on this task (`--may-start`). A
refuted node is committed too, as the evidence of what was tried, and backing up
reverts its code, not its task file (`revert_recipe` in `dfs_tree.py`: a reverse
3-way apply of the commit's paths outside `.dfs`); the briefing lists refuted or
pruned commits that are still in the code, with that command for each.

A task can be worked on several git branches at once, each pursuing its own
alternative; see "Branches" below.

### A gitignored `.dfs`

`.dfs/` may be gitignored whole, and that is supported: it is planning, and the tree
walk is in git history anyway, one commit per node, found by subject. The tool asks
`dfs_paths.ignored()` (a probe task path through `git check-ignore`; `DFS_IGNORED` in
the shell) and adjusts:

- **The briefing says so**, and the skill's rules for it apply: a node's commit
  stages its code alone, is `--allow-empty` when there is no code (a refuted node
  included), and carries the node's `Parent:`, `Status:` and `Determination:` in its
  body, since git keeps no other copy.
- **The idle check reads `.dfs` from disk.** git sees none of it, so without that a
  session that wrote only its task file would stop the chain as `idle`.
- **`dfs_check` has no HEAD to judge against**, and the hook never fires, since
  nothing under `.dfs` is staged. The shape checks still run on the working tree,
  printed by `dfs_run.sh` after each session; an edited determined node is not
  caught, and the session's own rule is what keeps it.
- **Branches share one copy**: no branch carries `.dfs`, so every part sits in the
  working tree whichever branch is checked out, and a dropped branch leaves its part
  behind rather than taking it along. `dfs_runs.py --left` reports a session's
  `.dfs` writes as `planning`, since git cannot say whether they are still changes.

What it costs is the review trail: a task file's history, and a correction's
before-and-after, are no longer in git, only in the file as it stands.

The **Log** is the only thing more than one party writes, and every entry is keyed by
its timestamp: `session` and `end` (the runner), `raise` (the agent, the critic, the reviewer, the
runner's session limit), `critic` and `review` (the verdicts), `backtrack` (the critic), and
`answer`, `correct`, `accept` (the author's passes). `dfs_tree.py log <task> <kind> [args] < body` is the one way
to append.

**Raise only what cannot be decided without the author**, the system designer or
user: a fact only they have, a preference between outcomes, access the session lacks.
**An issue with the roadmap tooling itself is raised, never fixed in the task**,
including a tooling rule that blocks it: W13 spent eighteen nodes on `roadmap-dfs/`
after its Goal was met, because a finding had nowhere to go but the running task.
Anything a session could reasonably choose, it chooses and writes as the node's
hypothesis; the author reviews the whole tree when the task is done. An answer REOPENS
the task: until a work session ends `ok` after it (or the author accepts the tree), the
task is open, its next session is work, and no review runs, so an answer that meets a
complete tree is acted on rather than reviewed around.

## The critic, the implementation review, and the session limit

All of them are the runner's, counted from the task's Log, and none is left to a session:

- **After every 5 work sessions a critic judges the tree's REASONING, not its code.**
  Handed the law and the nodes determined since it last ran (`--critic-brief`), it asks
  whether each piece of evidence is true and independent, and whether it entails the
  path taken, looking by name for the ways a search goes wrong while every step looks
  fine: **premature branch pruning** (an alternative refuted or parked on thin
  evidence, or never tried), **over-trusting self-generated interpretations** (the
  session's own reading of something offered as an observation), **spurious evidence
  chains** (links that restate each other rather than adding independent support), and
  **false convergence** (sessions agreeing because they share a blind spot), and
  **scope drift** (each node sound while the tree has moved on to work the Goal does
  not ask for, which it raises rather than backtracks, since where that work belongs
  is the author's call). It looks at code only to check whether evidence is true, and
  at which paths the commits touch to check scope.
- **When the tree is complete, an implementation review judges the code** against the
  goal (`--review-brief`): the task's commits, read whole — does the change do what the
  goal says, is it correct, is anything refuted or stale left behind. A task is `done`
  only after it. A finding that shows the Goal is not met (something it asks for
  that nothing delivers, or a fault its user would meet) is a new OPEN NODE in the
  tree, which reopens the task by itself; fixing it earns another review, and the
  author's `accept` takes the code as it is instead. A lesser finding (a wording, a
  stale sentence, a missing test for a guard, tidying) is never a node of its own: it
  rides in one batched node beside a real finding, or in the body of the review's `ok`,
  where the author reads it on accepting. Every issue used to be a node, and since a
  review always finds something smaller, each fix earned a review that found the
  next: W20 ran five rounds in one evening, the last few about the wording of a
  refusal message.
- **After 2 reviews that found issues without the author speaking** (and without an
  `ok` between), the runner raises instead of reviewing a third time
  (`DFS_REVIEW_ROUNDS`). The author accepts the code as it stands, or answers to let
  the rounds start over.
- **What either finds is work, not a question.** Neither fixes anything: the runner
  fingerprints the working tree outside `.dfs/` before and after and fails the chain if
  it moved or no `critic | review · ok | issues` verdict was written. What they write is
  the task file. The reviewer adds open nodes. The critic adds open nodes for what is
  missing (an alternative dropped without a fair try, a check nobody ran) and
  BACKTRACKS where a node's evidence does not carry its determination:
  `backtrack <node>` withdraws that node and everything under it, derived from the log
  as a correction is, and the next session redoes the step beside it with
  `Corrects: <the backtrack's ts>`. It backtracks to the FIRST such node, since
  everything below was built on it. Either raises only for what needs the author.
  Every finding used to be a raise, and the author's passes became answering "ok" to
  bugs.
- **After 10 work sessions since the author last spoke on a task** (an answer, a
  correction or an acceptance), the runner raises for review. Nodes and backtracks are
  deliberately unbounded — small nodes and switching branches are the normal case.

## Planning with the tree

The tree is mostly a PLAN: its branches are the different ways the goal could be
reached. Siblings are alternatives — the one pursued is `open`, the rest `parked` with
their reason — and evidence against the pursued branch is what tells a session to jump
to a parked one. A parked node, and anything planned under it, is dormant: not worked,
not holding the task open, and reopenable.

⚠️ **A WALK FINISHES WHAT IT STARTED**, so `next_item` is not the order's answer
alone: it is the item the LAST RUN was on while that item can still be worked, and the
order's answer otherwise (`dfs_state.continue_last`, over `dfs_state.next_item`). The
order says where the walk goes NEXT, not whether it may abandon where it is — and
without that, an item earlier in the order becoming workable while a chain is mid-tree
(a raise answered, a done item reopened, an item moved up) takes the walk off a tree
with an open node and half-gathered evidence, on the tick after the answer lands.

⚠️ It **subsumes** the uncommitted-work resume it grew out of rather than sitting
beside it: a session cut off by a usage limit leaves its node's work in the tree, and
that work belongs to whichever task had the last session (`may_start`), so naming that
task here is the same answer on a dirty tree as on a clean one. One rule, and no second
reason for the walk to name an item. An item that CANNOT be worked — finished, blocked
by its own raise, or held by an ancestor's raise or a fence — hands the walk straight
back to the order, and on a dirty tree the runner's `dirty` refusal is then what brings
the author, which is right: a raise sits on top of half-done work.

## The task tree, and why it is depth-first

`order.md` is a TREE, and the file's own order is its depth-first preorder — a child
is work that only makes sense under its parent. Indentation is the structure and
nothing else is.

There are two ways to hold work back and they say different things. Nesting is about
ONE branch; a break is about all of them.

⚠️ **An item that RAISES blocks its whole SUBTREE.** A question its parent cannot
answer is one the child cannot either, so a walk that meets a blocked node steps
SIDEWAYS onto the next branch instead of stopping. That is the "dfs" in the name, and
it is derived in `dfs_state.walk`, not left to the screen: every item carries `depth`,
`parent`, `blocked_by` (the ancestor holding it) and `reachable`, and `next_item` is
the first item in preorder that is open with nothing above it blocking.

⚠️ `blocked` and `blocked_by` are DIFFERENT QUESTIONS and are kept apart on purpose:
one says "answer this", the other "answer something above this", and `waiting_on` a
third: "finish something before this". The screen shows an item's own status, `↳W10`
where an ancestor is holding the branch and `»W2` where a fence is — and ⚠️ **a marker
only ever replaces `open`**, which is the whole argument for having them: "open" is
true of a held item and useless, while a `done` item is finished wherever it sits and
a `blocked` one has a question of its own. `row_state` in `dfs_tui.py` is that one
decision, pure and beside the walk's.

⚠️ **A BREAK is a fence across the forest: everything after it waits on the whole
tree before it.** A rule (`---`, on its own line between two root branches) says the
third thing an author knows about their own plan, after "this comes before that" and
"this only makes sense under that": *none of the rest is worth starting until all of
that is finished*. Nesting cannot say it — it is a claim about every branch above at
once, and re-parenting the roadmap into one spine to fake it would make every RAISE
stall the whole tree, which is the stall the depth-first move exists to avoid.

`dfs_state.walk` derives it as `waiting_on` — the first unfinished item on the far
side of the fence — and ⚠️ **not-done covers `blocked` as well as `open`**: a fence
the author drew across the forest is not satisfied by a branch that merely has nobody
asking a question about it, and this is the one place a walk does NOT step sideways.
`reachable` is the AND of the three, and `next_item` still reads nothing else.

⚠️ **A fence is never INSIDE a subtree**, and that is what makes it cheap: it ends
every open branch, so the line after one is always a root, no span can contain one,
and no move can smuggle one into a branch. It is why `b` refuses a nested item —
cutting a parent from its children with a fence would be two claims at once, and
`{` is where the other one is made — and why it refuses the first item, where a fence
would gate nothing while looking as though it gated everything. ⚠️ **Position is the
truth**, including for an item nobody has placed yet: it is written at the bottom,
so it is behind every fence, because the alternative ("unplaced means ungated") would
flip the moment an unrelated `[` rewrote the file with that same item in that same
place — a silent change of meaning from a keypress about something else.

In `dfs_tui.py`: `[`/`]` move the selected item among **its siblings**, `{`/`}` move it
**out of** and **into** a branch, and `b` puts a fence above the selected item or takes
away the one that is there. A fence is drawn — a rule reading `break · waiting on W2`,
or `break · above is done` once it is no longer in anybody's way — and is deliberately
NOT selectable: every other key here needs an item, so a cursor that could land on a
rule would make all of them begin by asking whether it had. `[`/`]` step a branch
**across** a fence one slot at a time, which is the second thing an author does with
one.

`[`/`]` and `{`/`}` carry the whole subtree, always — moving a parent out from under
its children would re-parent work onto whatever happened to be above, which is the
row-versus-identity mistake in a different costume. Up and down deliberately stay
inside the branch, so a key that says nothing about parents never changes one; the
same rule is why `{` refuses to nest an item under whatever is on the other side of a
fence.

## The briefing — what a session is handed

⚠️ **A SESSION IS TOLD ITS STATE; IT DOES NOT GO AND DERIVE IT.** `dfs_run.sh` builds
every prompt from `dfs_state.py --brief <task>`, rebuilt on every run because each run
moves the task file. It carries the law (`ROADMAP.md`) verbatim — never summarised,
since a summary of the law is a second copy of it — then the task's status and current
node, what the author has said since the last finished session, any correction not
yet carried out, the uncommitted work and whose it is, refuted commits still in the
code, the session count against the limit, and the tree's outline. It does NOT carry
the task file itself, which is the session's next read, whole.

⚠️ The old roadmap's briefing existed because sessions walked a half-megabyte
append-only log by eye. That log is archived, and a task file is now 200 to 2,500
tokens rather than up to 16,000; `dfs_check.py` still refuses a commit that grows one
past `HANDOFF_BUDGET`.

## The review — `r`

**The item pane IS the review.** Below the item list, the selected item is shown as
its tree, and **Tab** moves the keys between the two: the list (j/k pick an item, `c`
chats) or the tree (j/k pick a row, and the keys below). Only the half with the keys
draws a highlighted cursor. `r`, and ⏎ on an item that needs review, put the keys in
the tree; esc hands them back. ⚠️ A key means one thing whichever half has the
keys: `c` is chat in both, and correcting is `f`, which was `c` in the tree until
2026-09-28, so the same keypress wrote a correction or opened a chat depending on
where the focus happened to be. At the top of the tree, the task file's Goal (open) and
Background (folded, since it can run to pages); under them the whole tree,
one node to a line with its mark (✓ confirmed, ✗ refuted, ○ open, ‖ parked, · pruned),
the task's own raises at the top and a raise on a node shown on that node's row, in red.
⏎ opens the node under the cursor: its Approach and Hypothesis (from `.dfs/archive/` when a
session moved them there to fit the budget), Evidence, Determination and commits. Space
folds a branch; a folded row says how many open raises it is hiding, since a fold that
hid the one thing you came to answer would be worse than no fold. The review's writes
happen where the node is: `a` answers the raise on this row, `f` corrects this node
(the verdict at the prompt, the directive in `$EDITOR`), `A` accepts a finished tree.
What is typed in the editor is written as typed, minus the `#` lines framing it.
Guarded by `test_dfs_review_tree.py`.

The tree replaced both the item pane's printed outline and the terminal pass as the way
in, because both printed a big tree as a wall, one line per node with the whole
determination on it (W13: 17 nodes, most of them a paragraph), and correcting a node
meant typing an id copied out of the wall.
**`R` is still that pass**, and it is where a raise can be talked through in a chat
before it is answered:

`dfs_run.sh --review W7` is the author's one pass over a task, and spends no session:
the script appends the author's words itself, and a script cannot paraphrase.

1. It shows the tree.
2. It takes the open raises in log order: answer, chat (a session that helps decide
   and writes nothing), skip.
3. It takes **corrections**: a node id, the determination the author says was right
   (confirmed or refuted) and a directive. A correction prunes the node's descendants,
   built on the old determination; a correction to confirmed also prunes the siblings
   made after it, the alternatives taken when it was wrongly abandoned. The task reopens. The next session resumes there, under the node if confirmed and
   beside it if refuted, with a new node carrying `Corrects: <the correction's ts>`.
   Nothing is reverted by the pass itself; the resuming session reverts what the
   pruned nodes committed.
4. On a finished tree, it asks whether to **accept** it. A finished tree nobody has
   accepted shows `rev` on its row.

## The walk — `w`

**`w` turns the tree into work.** It is a behaviour of the running screen, not a
script beside it: the walk needs the same one-second poll, the same liveness check
and the same `next_item` the screen already has, and a second process would be a
second opinion about all three.

It starts a chain on `next_item`, waits, and when the chain ends asks
`walk_decide(stop, moved, next_item)` what to do. That is the entire policy, and it
is a pure function so it can be read and tested without a terminal:

| the chain recorded | the walk |
|---|---|
| `done`, `blocked`, `cap` | run again on whatever `next_item` now says |
| `limit` | hold **until the stated reset** (plus two minutes), then retry the same thing |
| anything else, or nothing | **stop**, naming what it saw |

`done` and `blocked` are both progress — one finishes a branch, the other hands you a
question and frees the walk to take the next branch. `cap` names the same item again,
because there is more to do there. So all three of "carry on with this item", "on to
the next item" and "on to the next branch" are one line of code: ask `next_item`.

⚠️ **Only `limit` waits, and until the reset the provider states** (`dfs_limit.reset_at`;
the flat hour it used to hold started 36 of 124 sessions straight into a 429 over
09-19..25). The hour is only the fallback for a reset it cannot read. A usage limit and a crash are both `rc 1`, so the reason is
RECORDED by `dfs_run.sh` into the run's `meta.json` and given meaning by
`dfs_runs.py` — never grepped out of a console by the walk, because grepping a log
for a marker the reporter does not emit gives zero and reads as green. Everything
else unhappy stops the walk and says so. An autonomous spender that treats a broken
item as a temporary one is one that runs all night on a loop.

⚠️ **And the walk's one retryable fact was itself decided by a grep, until
2026-09-16.** `meta.json` is not grepped, but what went INTO it was: `dfs_run.sh`
matched `hit your (weekly|usage) limit` over the run log. That day a chain on W18 was
answered `You've hit your session limit · resets 3pm (UTC)` with
`api_error_status: 429`. "session" is neither adjective, so the run was recorded
`failed`, and `failed` is not `limit` — the walk turned itself off at 12:37 with two
hours and twenty minutes of its own limit still to run, and nothing to see but
`○ walk off · W18: failed`. `dfs_limit.py` is now the single definition, and it reads
the STATUS the result carries before it reads any sentence: 429 is a fact with a
definition, while the sentence is the provider's to reword. Prose alone convicts only
a run that also errored — `result` is the session's own last message, so a chain
about rate limiting must not read as one that hit a limit. Guarded by
`test_dfs_limit.py`, whose first case is that log.

⚠️ **A chain that moved nothing in the task trees raises on its item, and the walk moves
on**, even when it ended happily; so does a chain the runner stopped as `idle`. That
check is deliberately redundant with the runner's own `idle` guard: the runner refuses
to repeat an empty session *within* a chain, and this refuses to start another *chain*
on an item producing nothing. It used to STOP the walk, which held every other branch
hostage to one item. A raise blocks exactly that item and `next_item` takes the next
branch; if the item is somehow still next after the raise, the walk does stop, since
starting it again is the loop this guards against. A broken machine therefore raises
once per item and ends at "every branch is blocked".

The walk still STOPS on `dirty` (a working tree every next chain refuses; the badge
says to commit it), an interrupt, a chain that recorded nothing, `overbudget` and
`overturns` (the ceiling sits far past any normal overrun, so reaching it is
significant), and `failed`, which is both "the agent exited non-zero" (a nix fetch
error, which every item would hit next) and "a critic or reviewer changed code or gave
no verdict".

"Moved" is asked of what the trees SAY (`dfs_state.content`: every node, field value,
evidence line and agent log entry, as a set), never of a count. `dfs_check` tells a
session over its budget to move settled history into `.dfs/archive/`, so a session that
did real work and obeyed it SHRANK the count: on 2026-09-27 a W13 chain determined two
nodes and added nine evidence lines while its count fell 148 to 143, and the walk turned
itself off blaming the item. A move only removes elements from the set; work adds them.

⚠️ **The budget is in SESSIONS, over the whole walk** — `w` asks for it, and it is
the only bound there is. A per-item cap was the obvious thing to ask for and bounded
nothing: a chain that used one up left the item still open, so `next_item` named it
again and the walk started another chain on it immediately. "Cap 5" meant five
sessions, then five more, for as long as the item stayed open, and a number that reads
like a limit and is not one is worse than no number. Spending is counted across
chains, read off each chain's own `run` (the session it has STARTED, since a session
that began has already cost its context), and the chain's cap is set to whatever the
walk has left — so a chain cannot overshoot the budget by one last session.

⚠️ **Turning the walk off by hand ends its chain after the run in flight.** The
chain's cap is the walk's whole remaining budget, so an off walk that left its chain
alone left it free to spend all of it with nothing watching: on 2026-09-28 the walk
went off at W19's run 3 and the chain carried on to run 5 of 33. `w` now writes the
run the chain is on into its run directory (`dfs_runs.lower_cap`, a `cap` file), and
`dfs_run.sh` reads it between runs, so the session in flight finishes its node's work
and none follows — a chain of one from that point. The file only ever LOWERS a cap.
`K` is still the key that stops the run in flight too.

**`n` pins the selected item as where the walk goes next**, over `next_item`. The
walk's chain on another item is ended after its run in flight the same way (the
`cap` file), and the next chain starts on the pinned item; after that one chain the
pin is gone and `next_item` decides again, which by `continue_last` keeps the walk
on that item while it is open and reachable. `n` on the pinned item takes the pin
away; the header shows it as `● WALK W7 → W9 13/20`. Only the item's own status is
checked, and it is checked when the chain starts rather than when `n` is pressed: an
item that finished or raised in between is dropped rather than started, since a chain
that moves nothing would raise on it. A fence or an ancestor's raise does not refuse
a pin — overriding the order is what it is for. With the walk off, the pin waits for
`w`.

It waits on **any** live work chain, not only its own — two agents writing the same trees at
once is how this roadmap's ids collided in the first place. The budget prompt is also
the confirmation: `K` already asks before stopping one chain because that is an hour
of somebody's subscription, and this starts chains until the budget or the tree runs
out. ⚠️ While it is on, the header **changes shape**: `● WALK W7 13/20 · roadmap · 18
items · …`, at the front, in red over the bar, and the footer's key reads
`w STOP WALK`. Written as one more dot-separated segment in the middle it read
exactly like `18 items`, which is no use to somebody who left it on an hour ago — so
the badge leads the line and is capitalised, legible on a monochrome terminal and out
of the corner of an eye, with the colour as reinforcement rather than the signal. A
message line would not do: that is the last thing that HAPPENED and scrolls away,
while "something is spending money unattended" is a state. Both survive a 60-column
terminal, because the badge is first on the bar and the key is third in the footer.

⚠️ **A walk that STOPPED says so in the same place**, dim rather than red:
`○ walk off · W18: idle`, up until the next walk starts. `walk_off` writes the message
line too and the message line cannot hold it — `poll` overwrites it with `refreshed`
from the same tick, before the screen is drawn — so a walk that stopped was a badge
quietly disappearing and a reason recorded in a field (`walk_note`) that nothing read.
That mattered because of what it was hiding: the walk turned itself off ONE SECOND
after starting its first chain, every time, while the chain ran on for the hour it had
been given. `start_chain` MAKES the run directory and `dfs_run.sh` IDENTIFIES it by
writing `meta.json`, about 110 ms after exec — but `start_chain`'s own reload lists the
directories at about 55 ms, and `dfs_runs.run_dirs` skips a directory that has no meta
to read. So the tick a second later asked "is my chain still on the box?" of a list the
chain could not be in, and answered no. The walk now re-derives that list ITSELF before
it decides anything (`refresh_chains`), and a directory that is made but not yet named
is a start in progress rather than a death until `WALK_START_GRACE` has passed.

⚠️ **The budget prompt does not offer the last number in brackets.** It read
`session budget [20], empty to cancel:`, two keys away from `cap [5]:` where Enter
takes the 5 — so Enter, the natural way to accept a number already on the screen,
cancelled, and said `walk: not started` about a keypress nobody made. Empty still
cancels, because the prompt is the confirmation for an unattended spender; the last
budget is shown as the last one rather than as an offer.

⚠️ **An extension tells you how a file is reached, not a hyphen.** `dfs_run.sh`,
`dfs_tui.py`, `dfs_check.py`, `dfs_context.py`, `dfs_metrics.py` and
`dfs_artefact_check.sh` are run; `dfs_paths.py`, `dfs_state.py`, `dfs_runs.py`,
`dfs_limit.py` and `dfs_order.py` are imported by them (`dfs_limit.py` by a shell
`eval`, which is the same thing through the only door bash has), which is why every name is underscored — Python
cannot import a hyphen, and one convention beats two. Those last four are each the
single definition of one word, so a screen and a runner cannot disagree about what it
means. ⚠️ `dfs_run.sh` the runner and `dfs_runs.py` the run-directory reader are one
letter apart by accident rather than design, so the liveness check matches
`dfs_run.sh` WITH its extension: a pid running the reader must never read as a live
chain.

## Branches

A git branch forks a task the way a parked sibling does, except that both
alternatives are pursued at once: the branch adds its own nodes under the node it
forks from, and when it merges they sit beside the default branch's as alternatives.

**No two branches write the same file**, which is why plain git merges them and there
is no merge driver:

- **Every id a branch creates carries its tag**, the branch name sanitised
  (`dfs_paths.sanitise`: lowercase, runs of anything but `[a-z0-9]` to one `-`, a
  leading letter, at most 32 characters): node `W13.4@rowan-try-b`, task
  `W30@rowan-try-b`. `main` and `master` have no tag, so the default branch's ids are
  plain `W13.4`. Numbers count per tag, one past the highest with that tag. The tag is
  where the id was CREATED, forever; a merge renames nothing.
- **Every node and log entry a branch adds goes in its own part** of the task,
  `items/<task>/<tag>.md` (the default branch's part is `items/<task>.md`), and its
  archive is `archive/items/<task>/<tag>.md` likewise. `dfs_tree.path_for` is this
  branch's part; `dfs_tree.load` reads every part and combines them, logs merged by
  time. `compact` and `shelve` touch only this branch's part.
- **`dfs_check` refuses a node or log entry ADDED to any other part**, and any added on
  a detached HEAD. Editing a node in another part (determining the fork point, say)
  is allowed, and conflicts in git only if both branches edited that node, which is
  a real conflict. The rule is lifted for a merge commit: `pre-merge-commit` passes
  `--merge`, and a conflicted merge finished by hand has `MERGE_HEAD`.
- **The frontier puts this branch's own nodes first**, so on the branch the current
  node is its alternative while the default branch's open node stays open beside it.
- **Refused before a chain starts**: a detached HEAD (no name to tag with), and two
  local branches that sanitise to one tag (`fix/sync`, `fix-sync`).
- **`order.md` is not written by a branch.** A task a branch opens is unplaced, which
  is the end of the order anyway; two branches that each reorder tasks conflict in
  git, which is right, since those are two decisions.

A raise is answered on the branch that raised it, the only place it exists until the
merge. A branch dropped without merging takes its record with it; to keep what it
tried, bring its part across on its own: `git show <branch>:.dfs/items/W13/<tag>.md`.

A merge that `git merge` commits itself is judged BEFORE it lands, by
`scripts/git-hooks/pre-merge-commit`. ⚠️ **A rebase, a cherry-pick or a revert is
not judged at all.** They run no pre- hook, and git ignores the exit status of the
post- hooks they do run, so a check there could only print after the commit exists.
It was built (W13.40 to W13.42) and taken out again (W27.1): a guard that cannot
refuse, for a case nobody had hit, was not worth its three hooks. So a `.dfs` branch
is MERGED, never rebased or cherry-picked.

## ⚠️ The work root is `pwd`

There is no setting and nothing remembered. **Run every command from the repo you
mean to work** — the roadmap it reads and writes is `<cwd>/.dfs`, and running
from the wrong directory reads and writes somebody else's. `dfs_paths.py` is the one
place this is derived and the only file that spells `.dfs`; every entry point refuses
with a named path when the current directory has no roadmap.

    dfs                  <scripts>/dfs_tui.py      the screen over all of it
    dfs-run W4 5         <scripts>/dfs_run.sh W4 5  a chain of up to 5 sessions
    dfs-run --review W4                            one item's raises and assumptions
    dfs-run --open                                 scope a new item
    dfs-check            <scripts>/dfs_check.py    the roadmap's own invariants

## What still reaches into the work repo

The sessions' CWD, which is the point, and one optional borrowing:
`scripts/dfs_artefact_check.sh` takes `playwright` from the work repo's dev shell when
the repo names one (`.dfs/playwright-shell`), since that shell's fonts are the ones
its pictures should be measured in.

⚠️ **Paths in prose are spelled from the WORK root** — `.dfs/items/W4.md` — and the
tool's own scripts as `<scripts>/`, since the tool may be anywhere. The briefing
names that directory (`dfs_paths.rel(SCRIPTS)`), and the runner's prompts are built
through `dfs_paths.rel()`, so both follow an install elsewhere on their own. A new
instruction that spells a path into the tool must do the same.

## Raise artefacts

How a session writes one is `docs/artefacts.md`. Reading one:

Served from the **host**, not from inside the dev container — the repo root is
bind-mounted, so the files are already there:

    python3 -m http.server 3016 --directory .dfs/artefacts     # or DFS_ARTEFACT_PORT

`dfs_run.sh --review <item>` prints a raise in full, so the URL arrives in the
terminal and most terminals make it clickable.

`dfs_tui.py` has the same artefacts on **`v`**, over whichever item is
selected: the ones this item's Log entries name, then everything else in
`.dfs/artefacts/`. Enter opens one in a real browser — `$BROWSER`, else `xdg-open`, else
firefox or chromium — detached, so it never owns the screen. ⚠️ It finds them by
SCANNING the entry prose for a `.html` name, which is the point: the link lives
inside the raise's prose, so there is no artefact field to read and there must
not be one. Two consequences worth knowing: an answered raise's picture is still
listed (the entries are what is scanned, not the open raises), and a name that no
longer resolves is listed as MISSING rather than hidden, a dead link inside an
append-only entry being worth seeing. With nothing on 3016 it opens the `file://`
path instead, which works because these pages are self-contained by rule; with no
display reachable — a terminal inside the dev container — it puts the URL on the
status line rather than forking a browser that fails where nobody sees it.
