# roadmap-dfs and AI-DLC

A comparison of this tool with [awslabs/aidlc-workflows](https://github.com/awslabs/aidlc-workflows)
(AI-DLC). It was made by reading both; nothing was run.

**What was read.** AI-DLC at commit `c2d8477` of `main` (release 2.11.0): `README.md`, `AGENTS.md`,
`docs/guide/` chapters 00, 04, 06, 07, 09, 10, 11, workflow-profiles and the glossary,
`docs/harness-engineering/06-sensors.md` and `docs/reference/02-plane-architecture.md`. roadmap-dfs at
`efbe2a9`: `README.md`, `SKILL.md`, `templates/ROADMAP.md` and the headers of `scripts/check.py`, `hook.sh`,
`run.sh`, `branch.py` and `walk.py`.
**Not read:** AI-DLC's specification PDF, the rest of `docs/reference/`, the agents' knowledge files, its
tests and its source beyond the file list. Where a claim about AI-DLC rests on a file, the file is named in
brackets, relative to that repo.

## What each is for

**AI-DLC** is a software factory for one person driving an agent through a delivery lifecycle: intent, then
spec and plan, then code, then deployment. "The person drives it, and it delivers their project"
[`AGENTS.md`]. The unit is an *intent* run through a *scope* (a fixed route through up to 33 *stages* in 5
phases) with 14 agent personas, and the person approves at a gate after every stage
[`docs/guide/04-phases-and-stages.md`, `docs/guide/00-introduction.md`]. Its answer to "AI coding loses
context as projects grow" is structure the engine enforces: one deterministic engine decides which stage is
next and the chat session carries it out [`README.md`, `docs/guide/00-introduction.md`].

**roadmap-dfs** is a to-do list for agents: each task is a decision tree whose branches are different ways to
reach the goal, worked by short non-interactive sessions that pursue one branch, record evidence, and jump to
a sibling when the evidence refutes the branch [`templates/ROADMAP.md` §0, `README.md`]. The person is not in
the loop while a session runs; they are asked only for what needs them (a raise), and otherwise review the
finished tree, where every assumption was declared with what would show it wrong.

So the two are not rivals for the same job. AI-DLC is a *method for delivering software with a person
steering live*. roadmap-dfs is a *method for letting an agent work unattended on a backlog and keeping its
reasoning auditable*. They overlap in the middle, where an agent takes a piece of work from idea to code.

## Side by side

| Axis | AI-DLC | roadmap-dfs |
|---|---|---|
| **Unit of work** | An *intent*: one run of a lifecycle in a *space* [`docs/guide/03-spaces-and-intents.md`] | A *task*: one file, `.dfs/items/W<n>.md`, with a Goal, a Tree and a Log |
| **How it is planned** | Chosen from a catalogue: 11 scopes (Classic 18 of 33 stages, Express 10, Enterprise 33…), or a plan the composer agent proposes from the request; depth and test strategy set per work [`docs/guide/workflow-profiles.md`] | No catalogue. The agent writes the tree for the task: sibling nodes are the alternatives, one is pursued, the rest parked with a reason; the tree is re-planned after every node |
| **What a step is** | A stage with declared inputs, outputs and a lead agent; stages run in lifecycle order, jumps mark skipped stages `[S]` [`docs/guide/04-…`, `docs/guide/10-…`] | A node: an Approach, optionally a Hypothesis and Evidence, a Determination (confirmed or refuted), and a commit whose subject starts with the node id |
| **Where the human is** | In the session. A gate after every stage (Approve / Request Changes), a questions file in three modes (Guide me, Edit file, Chat), Plan Approval before code generation; in Construction the person may let routine checkpoints pass [`docs/guide/07-interaction-modes.md`] | Outside it. Sessions are non-interactive. A choice is made by the agent and written as a falsifiable Hypothesis with a page the author can operate; a raise is only for a fact, a preference between outcomes, access, or an ordering dependency; the author's ruling prunes or confirms a subtree [`templates/ROADMAP.md`, `SKILL.md`] |
| **Default when unsure** | Ask once, in plain words, with a recommendation [`AGENTS.md`] | Choose, and declare the assumption; "anything you could reasonably choose, choose" |
| **A session** | One long conversation; the *conductor* asks the engine for the next step, does it and reports back. Resume reads the state file [`docs/guide/00-…`, `docs/guide/11-…`] | One turn of `claude -p`, ended near a context checkpoint. The briefing is derived by `state.py`; unfinished work is left uncommitted for the next session; a chain runs until a raise, a finished tree or its budget of sessions [`SKILL.md`, `scripts/run.sh`] |
| **State** | `aidlc-state.md` per intent: scope, depth, six-state stage checkboxes, current stage, resume point; the engine rewrites it [`docs/guide/10-state-and-audit.md`] | The task file's Tree is the state; there is no separate state file. Status is derived from nodes and log entries |
| **Audit** | A 119-event taxonomy written as Markdown shards by tools and hooks (stage, gate, question, guard, sensor, subagent events…) [`docs/guide/10-…`] | The append-only Log and git history: one commit per node. A determined node cannot be edited; later understanding is a new node |
| **Checks on the work** | *Sensors*: deterministic checks (required sections, upstream coverage, claim sources, traceability, linter, type-check), advisory unless the team makes one block at a gate; verification gates at phase boundaries; 2 reviewer agents on stages that declare one [`docs/harness-engineering/06-sensors.md`, `docs/guide/06-agents.md`] | A *critic* every fifth session judges the tree's reasoning (is the evidence true, independent, and does it support the path?); a *reviewer* judges the finished implementation against the Goal; findings become nodes. Tests are part of nodes' Approach, not a separate mechanism |
| **Enforcement** | 17 hooks plus an engine: fences (plan approval, review freeze, state transition, reviewer read scope, human presence) under a Guard Policy of strict / relaxed / off; a gate needs a real message from the person [`AGENTS.md`, `docs/guide/glossary.md`] | A git pre-commit and pre-merge-commit hook runs `check.py` and refuses commits that break the tree's invariants: edited determined nodes, edited log, new `Ask:`, a Hypothesis without an interactive artefact, an oversize task file [`scripts/check.py`, `scripts/hook.sh`] |
| **What guards protect** | The workers staying on the spec and plan the person last approved; "never stand between the person and what the person asks for" [`AGENTS.md`] | The record: that what the author later reads is what was reasoned at the time |
| **Learning** | The *learning loop*: a stage diary (`memory.md`), candidates surfaced at the gate, kept ones written into layered rules `org → team → project → phase` [`docs/guide/09-…`] | The author settles a rule into the law (§1, L1…); the critic can backtrack a node. Nothing is learned automatically |
| **Agents** | 14 personas ("small mob, broad agents"); most stages run inline in the conductor's own context, four dispatch subagents; agents never call each other [`docs/guide/00-…`, `docs/guide/06-…`] | One agent per session, no personas; separate prompts for work, critic and review. Plan sessions and implement sessions can run on different models (`PLAN_MODEL`, `IMPLEMENT_MODEL` in `scripts/run.sh`) |
| **Harnesses** | One authored core packaged into per-harness trees: Claude Code, Kiro CLI, Kiro IDE, Codex CLI, Cursor, opencode, GitHub Copilot; a native `aidlc` binary installs them [`README.md`] | One skill file and a runner that launches claude (default), codex, kiro or opencode (`run.sh --codex`…); packaged as a nix flake |
| **Many things at once** | Spaces and intents; team mode where each person claims a Unit in their own checkout; parallel Unit batches (`swarm`); git worktrees for Units [`docs/guide/04-…`, `docs/guide/10-…`] | One branch and worktree per task (`dfs/<task>`, merged `--no-ff` when the review passes); `order.md` orders tasks as a tree with breaks; an item that raises blocks only its subtree [`README.md`, `.dfs/order.md`] |
| **Surfaces** | The chat, a statusline, `aidlc status` / `doctor`, plain-Markdown records | A terminal screen, a phone-sized web page (installable), and chats; the author answers raises and Hypotheses on the page |

## What they share

- **Records in the repo, in plain files.** Both keep their working state in text under version control, so a
  fresh session (or a person) can pick the work up from disk, not from a conversation.
- **A tool for the bookkeeping, the model for the judgement.** AI-DLC's first tenets are "Tools for
  determinism. LLM for knowledge. Human for judgement" [`AGENTS.md`]; roadmap-dfs derives the briefing and
  the stop conditions in `state.py`, "the same code the runner stops on".
- **Hooks that refuse.** Each stops an action rather than asking the agent to behave.
- **The human decides what only they can.** They differ on *when* and *how often*.

## Where they differ in kind

1. **Prescribed route against derived route.** AI-DLC knows the stations in advance and the work is to pass
   through them well; the value is in the catalogue (stages, agents, knowledge, rules). roadmap-dfs has no
   catalogue; the value is in the discipline of the tree: alternatives written down, assumptions stated
   with what would refute them, backing up when the evidence says so. AI-DLC can go back ("Redo" or "Jump")
   but nothing in the chapters read treats a refuted branch as a concept.
2. **Approval against review.** AI-DLC's human approves *each stage's output before the next starts*.
   roadmap-dfs's author reviews *the whole tree after the agent has finished*, and can prune any node. That
   is cheaper in attention and far riskier if an early assumption is wrong, which is why every assumption is
   named and has a page to rule on it.
3. **What the audit audits.** AI-DLC's trail records what happened and who approved it (119 event types,
   with the person's words kept whole). roadmap-dfs's record is of *why*: the evidence for and against each
   assumption, and the critic checks that evidence for independence.
4. **Interactive against unattended.** AI-DLC supports unattended runs only as a declared exception
   (`AIDLC_UNATTENDED=1`) [`docs/guide/07-interaction-modes.md`]. For roadmap-dfs it is the only mode, and
   cost control (short sessions, a context checkpoint, cache-aware limits) is part of the design.
5. **Scope of the lifecycle.** AI-DLC reaches from market research and mockups to deployment pipelines and
   feedback loops. roadmap-dfs says nothing about what a task's nodes should contain; it is a planner and a
   record, not a delivery process.

## What each has that the other does not

**In AI-DLC, not in roadmap-dfs**
- A delivery lifecycle with domain knowledge built in: agent personas, per-agent knowledge, stage
  definitions, and team knowledge indexed from documents.
- Profiles that size the ceremony to the work (Express at 10 of 33 stages, Enterprise at all 33).
- Sensors: deterministic content checks on the *work product* (headings present, upstream artefacts cited).
  roadmap-dfs checks the *shape of its own record*, not the code or documents a node produces.
- A learning loop that turns a correction into a standing rule.
- Many-harness packaging, with a determinism check on the build and a native installer.
- Team features: spaces, Unit claims, pinned merge-back.
- A rule that nothing shown to the person is engine jargon (`AGENTS.md`, "What the person reads").

**In roadmap-dfs, not in AI-DLC**
- A refutable plan: nodes with a Hypothesis, a `Wrong if`, evidence for and against, and a jump to a parked
  sibling when refuted, with the refuted code reverted and kept in history.
- A critic that reviews the reasoning rather than the output, and backtracks to the first node whose
  evidence does not carry its determination.
- Immutability: a determined node, and the log, cannot be rewritten, and the commit guard enforces it.
- Unattended operation as the design centre: budgeted sessions, a derived briefing, usage-limit holds, a
  phone page to answer on.
- Raises only for things that need the author, with tooling faults kept out of the task.

## Ideas that could carry over

These are suggestions only; nothing here has been decided or built.

*From AI-DLC to roadmap-dfs*
1. **Learned rules.** The critic and the author's corrections already produce reusable findings; AI-DLC's
   diary-and-confirm loop is a model for proposing them as candidate law (§1) for the author to accept.
2. **Sensors on node output.** `check.py` could be joined by checks on what a node *produced* (a
   Determination that answers its Approach, a screenshot where the node changes what the user sees), run at
   commit, as AI-DLC runs them at a gate.
3. **Sized trees.** AI-DLC's profiles suggest that a bugfix and a feature want differently shaped trees; a
   few starting shapes could save the first planning session.
4. **A shared vocabulary rule.** The "never show engine words" rule maps onto what the author reads on the
   page.

*From roadmap-dfs to AI-DLC*
1. **Hypotheses at the points it already asks.** AI-DLC's questions are the person's decisions; a defaulted
   one could be recorded with what would show it wrong, so unanswered questions do not stall unattended runs.
2. **A critic stage** over a whole intent's decisions (not artefacts), at intervals.
3. **Immutable history for approved stages**, so that a change after approval is a new record, not an edit.

## When to use which

Use AI-DLC to build a product with a person steering and a team's conventions encoded as stages and rules.
Use roadmap-dfs to let an agent work through a backlog while the author is elsewhere, and to find out
afterwards *why* it chose what it chose. Nothing stops a roadmap-dfs task from being "run AI-DLC's express
scope on this", with the tree recording what that run assumed.

## Limits of this comparison

It is a reading, not a measurement. It says nothing about output quality, token cost, time to a working
feature or how often each stops for the human; comparing those means running both on the same piece of work,
which needs AI-DLC installed from its release script and paid model time on two runs. AI-DLC's counts (33
stages, 14 agents, 119 events) are as its documents state them, and were not recounted from source.
