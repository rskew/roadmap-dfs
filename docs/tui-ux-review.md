# TUI review: can a driver/reviewer understand a tree?

Review run 2026-10-06 against `main` at `b64666d`. The question: looking only at the node
tree in `tui.py`, can the author tell what happened, what rests on an assumption, whether
the evidence holds, and whether each node makes sense on its own?

## Verdict

The screen is well made for **navigating** (footer, `?`, folding, guides, raises that cannot
be folded away, a summary on top of finished work). It is weak at the thing the tool is
for: **reviewing reasoning**. Four causes, in order of damage:

1. **The tree is a list of titles; the story is behind a keypress per node.** Nothing
   skims. The one-line-per-node digest that carries the story (title + determination)
   exists in `tree.py show` and in `R`, but not in the screen the author actually sits in.
2. **The part of an assumption the author must answer is the part that gets cut.** The
   preview keeps two lines of the Hypothesis, and the "wrong if…" clause is at the end.
3. **The author's own decisions disappear or contradict.** An answered raise vanishes from
   the tree; a corrected node reads `✗ refuted` over `Determination: Confirmed: …`, with
   the author's reason only in log pane `3`.
4. **What gets flagged for the author depends on what the agent volunteers, and agents
   volunteer little.** 3 of 28 nodes carried a Hypothesis; real caveats ended up in
   Summary prose and "Next, …" sentences, where the ⚑ pass never looks.

Descriptions do stand alone when an agent writes them (0 of 28 back-references). They stop
doing so only where nothing enforces it (see §3).

## 1. How this was done, and what it can't tell you

- An example project (`gateway`: auth → rate limit → cache → upstream, one thread per
  request) with `.dfs` initialised and the hooks installed.
- Nine tasks. W1–W8 were opened exactly as `run.sh --open` writes them (author's Goal,
  linear chain of title-only nodes). **W1–W4, W6–W8 were worked by subagents handed the real
  `state.py --brief` briefing and `SKILL.md`**, in clones, with the real `check.py` guard.
  Then real reviewer and critic briefings (`--review-brief`, `--critic-brief`) were run the
  same way, and second work sessions carried out a reviewer's nodes, an author's
  correction (W3), an author's answer to a raise (W7), and an open-ended design (W8).
- The author's side was done **in the TUI** under tmux (answer `a`, correct `f`, add `i`,
  accept `A`, search, `?`, `m`, `3`), at 150×45, 120×50, 100×30 and 80×24.
- **W9 is hand-written by me** (11 nodes: a refuted branch, a parked
  alternative, a critic raise, evidence-heavy hypotheses, and three nodes of the
  "Fix what W9.6 found" kind). The agents never produced those shapes, so I wrote them to
  see how the screen handles them. Treat what W9 shows as layout findings, not as a claim
  about what agents write.
- Fixture: `docs/tui-ux-review/items/` (copy into any git repo's `.dfs/items/` after
  `init`, then run the screen).

Limits. The sessions are subagents, not `claude -p` with a real 100k-token context; each
ran for about a minute and the trees came out mostly linear. Real long-running tasks will be
messier, so the quality findings below are probably *optimistic*. Not exercised: live
chains, `L`/`l` transcripts, the walk, `c` chat, `o` open, light terminals. The sandbox also
pins a raw `dfs_agent: --update failed: {"message":…}` JSON blob on the message line from
start-up, which says nothing and was not a TUI fault.

## 2. Findings

### 2.1 Skimming the tree (what happened?)

`W3` after a correction and redo, as the author lands on it:

```
   ▾ W3.1 ✓ Reproduce the stampede with threads
   │ ▾ W3.2 ✗ Share one computation per key
   │ │   W3.3 · Check unrelated keys are not blocked
   │ ▾ W3.4 ✓ Revert the per-key-lock code
   │ │ ▾ W3.5 ✓ One computation per key, record dropped when it ends
   │ │ │   W3.6 ✓ Waiters share a failed computation
```

You can tell the shape and which nodes passed. You cannot tell what any of them found.
`tree.py show W3` gives, per node, `title — determination`, which reads as a story:
"W9.5 [refuted] … Refuted: the stores can disagree and the import cannot repair it." That
digest is the single most useful view in the tool and the screen doesn't have it.
Reading W9 fully took one `⏎` per node; at 80×24 one opened node fills the pane.

**Fix:** a skim mode (`s`, or on by default for determined nodes): first sentence of
`Determination` dim under each row. Open stays for the rest.

### 2.2 Assumptions: found, but cut off where it matters

- ⚑ + amber + a two-line preview is the right idea. It's the only thing in the tree that
  makes a node stand out, and it worked on every flagged node.
- **The preview drops the falsifier.** `tui.py:3387-3392` keeps `said[:2]`. The skill
  asks for "the assumption, and what would show it wrong", so "Wrong if …" is the tail.
  Cut before it:
  - W2.2 (agent-written): `…so raising by default would take their …` — the next sentence
    is "Wrong if the author wants hard failure for everyone", i.e. the actual question for
    the author. (W1.2, also agent-written, just fit in two lines at 120 columns, but is cut
    at 150, where the activity column narrows the tree.)
  - W9.2 (hand-written, so this is layout evidence only) at 120 columns: the second line ends `Wrong if p99 exceeds 5 ms, or if` and the
    ` …` that should say "there's more" is itself clipped off the edge, so there is no hint
    of truncation. What's lost is "…or if the Goal's 'without losing any live session' is
    read as surviving power loss", the whole decision.
- Opened, the node is fully unstyled (below), so the Hypothesis loses its amber exactly
  when you read it.
- **The accept prompt names ids, not assumptions**: `Assumptions to check at W2.2 (⚑)`.
  Accepting is "the moment they stop being looked at" (the code's own words). The prompt
  could quote each assumption's falsifier, and the reviewer's remarks (§2.5).
- **Nothing walks you through them.** No key jumps to the next ⚑ / raise / open node. `/`
  searches node text and doesn't find "assumption". On the list, `rev N` is documented
  (`review_cell`) as counting "open raises and the standing assumptions", but `state.py:143`
  sets `standing` to `["tree"]` for any finished unaccepted tree. So W2 (one assumption) and
  W4 (none) both read `rev 1`, `m` lists both, and a task with a decision in it can't be told
  from one that only needs a tick.
- **There is no cross-task view.** With nine tasks the author's real pre-accept job is
  "show me every open assumption I haven't looked at", and the only way is to enter each.

**Fix:** show the whole Hypothesis (it's ≤3 lines in practice) or split on "wrong if" and
render that clause as its own dim line; fix the clipped ellipsis; `n`/`N` next ⚑/raise; make
`rev` count assumptions; an "assumptions" pane across tasks; quote them in the accept prompt.

### 2.3 Hypotheses and evidence: parseable, but undifferentiated

Opened node (W9.2, 120 columns):

```
   │ │   Hypothesis: WAL with synchronous=NORMAL keeps p99 lookup under 5 ms at 8 concurrent writers and loses
   │ │       at most the last few commits … Wrong if p99 exceeds 5 ms, or if the Goal's "without losing any live
   │ │       session" is read as surviving power loss.
   │ │   Evidence: for: WAL, 8 writers, 8 readers, 30 s: lookup p99 1.8 ms, 41k writes/s.
   │ │   Evidence: for: DELETE mode, same load: p99 22 ms, readers blocked behind writers.
   │ │   Evidence: against: the Goal says "without losing any live session"; I read that as process crashes …
   │ │   Evidence: against: bench ran on tmpfs, so fsync cost is not represented.
```

- The colour dump shows every line in the default colour (only the guide glyphs are grey).
  `for`/`against` differ by one word that you have to read. The label `Evidence:` repeats
  per line. Nothing separates the claim from the observations.
- **Questions to the author hide inside evidence.** "…which the author may not mean" is
  evidence line 4 of 5 here, and W2.2's agent put "a stderr warning can still be missed"
  in `against`. The format has nowhere to say "this is a question for you", and the
  author's attention is spent on the second-most-skimmable line.
- Evidence quality in the real runs (8 evidence lines in 28 nodes): W1.2's `against:
  none found` is filler; its `for` is the check itself, and its test churns ids slower than
  the refill window, so it doesn't bear on the stated risk ("churn within one refill window").
  W8.1's `against` line is about the *alternative* (rate/2 split), not the hypothesis, and
  its spike ran "outside the repo", so the reader can't re-run the evidence. The tool can't
  see any of this, and nothing in the screen prompts the question.
- The refuted node (W9.5) reads well opened: hypothesis, two `against` lines that kill it,
  one `for`, then determination. This is the format working.

**Fix:** label style: dim label, one `Evidence` header, `+`/`−` glyphs coloured green/red;
hypothesis amber when open; render "wrong if" as its own line; consider an `Ask:` field (or
a prefix the skill reserves) rendered red-ish like a raise, so a question isn't an evidence
line.

### 2.4 Do descriptions stand alone?

Measured on the 28 agent-written nodes:

| | |
|---|---|
| Approach contains another node id | **0 / 28** |
| Determination contains another node id | 3 / 28 |
| Determination starts "Confirmed:" / "Refuted:" (repeats the status mark) | 6 |
| Determination contains "next" (hand-off sentence) | 14 |
| Has a Hypothesis | **3** |
| Parked or alternative sibling nodes | **0** |

- Agent text is good where the briefing is explicit. The **reviewer-added nodes (W1.4,
  W6.4) are the best prose in the sample**: file, function, how it was checked, what it
  printed, what would settle it. They are what the author wants to read.
- Back-references appeared only where nothing stops them. In hand-written W9 the open
  nodes read `Fix what W9.6 found` / `See W9.6.` / `The other case`. They pass `check.py`
  (the guard doesn't lint the standalone rule), the row shows only the title, and opening
  it shows `Approach: See W9.6.`. `/W9.6` does jump there (good), but the node also doesn't
  show its own `Parent:`, so you can't tell why it relates (it was a child of W9.8, not
  W9.6). Late-session agents are exactly who writes this and I could not reproduce them with
  fresh subagents; a lint is cheap insurance.
- Titles on author-opened nodes are bare titles (`W7.3 · Apply them in handle`); opened they
  show `status: open` and nothing else. Fine, but then the first agent session's Approach is
  the only description and the row doesn't show it.
- Node detail doesn't show `Parent:` or any link to the correction/answer a node carries
  out (§2.5).

### 2.5 The loop: raises, answers, corrections, critic, review

What the author sees at each stage:

| Event | Where it shows | Problem |
|---|---|---|
| Raise | Red row, full text under the node; `● raise` | Good (options, recommendation, "strongest case against" read well). Identified by an ISO timestamp. |
| Answer | `3` log only. **The raise leaves the tree on the spot.** Status line says "act on it first". | Node W7.2 later says "per the author's answer (a)" and the tree shows neither question nor answer. |
| Correction (`f`) | Node mark flips to `✗`; below pruned | The node shows `status: refuted` over its old `Determination: Confirmed: … The Goal is met.`. The pruned child (`W3.3 ·`) keeps "Confirmed… Goal met". The author's *reason* is only in `3`. The carrying-out node (W3.4 `Corrects: <ts>`) shows the opaque timestamp. A reader six weeks on gets a contradiction and no why. |
| Add (`i`) | Open node, title (+ optional approach) | No warning when the cursor is on a refuted/pruned node: I added W3.4 under refuted W3.2 and it was born pruned (`·`). |
| Critic | `3`: `critic · ok` | No body: the critic's remarks aren't recorded (`tree.py log … critic ok` takes none); the W3 critic noted the W3.3 test passes on the old code and the leak, only in its reply. |
| Review `ok` | `3` only | **Minor findings live in the log body**, and the skill says "the author reads them when they accept". The accept prompt and the item pane never show them. |
| Review `issues` | New open nodes under the old one | Reviewer fix nodes become *siblings* of the confirmed work (W1.3 ✓, W1.4 ○, W1.5 ○ under W1.2), which reads as alternatives, the opposite of what the skill says siblings mean. |

The review-buries-defects point is not hypothetical. The W3 reviewer wrote "`_locks` grows
one Lock per key forever" and "waiters retry serially, each making its own upstream call"
as *minor findings* (the law says a lesser finding is never a node). The `ok` verdict
passed, the critic passed it, and the leak surfaced to me only because I went and read log pane `3`. The serial-retry flaw then reappeared as a real bug in W6 (W6.4: four
concurrent calls returned 504 after 2, 4, 6, 8 s). Nothing on screen connected the two.

**Fix:** show answered raises collapsed under their node (`✓ answered: "Go with (a)…"`);
show the correction's reason under the corrected node and replace the stale determination
with "(agent said confirmed; overruled by you)"; show a node's `Corrects:` as the text it
carries out; warn on `i` under a dead node; surface review/critic `ok` bodies as "reviewer
notes" in the item pane and the accept prompt; parent reviewer nodes at the chain's tip
(or tag them `review finding`); let `critic ok` carry a body.

### 2.6 Layout, scale, small terminals

- **Linear chains become a staircase.** `--open` writes every todo as a child of the
  previous one, so a 20-step task puts its last node 40 columns in. Text starts at column
  ~44 and every row is a stack of `│`. Indentation here means "order", and branching
  (the thing the tree is for) is indistinguishable from "next step". W9's real branch
  (W9.5 vs W9.6 vs parked W9.7) is only legible because it is short.
  *Fix:* render a single-child chain at one indent and indent only at real forks. (Later: a chain step
  steps in half a step so it is never level with its parent; see the done list, item 7.)
- **80×24:** the item list keeps ~8 rows (header + 9 tasks) while the tree is focused, so
  the pane has ~12 rows. One opened node fills it; Evidence is below the fold. The task
  title, status and "sessions n of 10" lines scroll out of the pane, so you stop knowing
  which task you're in except from the `>` in the list. *Fix:* tree focus hides the list
  behind a one-line breadcrumb.
- `G`/`g` in the tree scroll the text and leave the cursor on the Goal row off-screen. The
  next `⏎` then acts on a row you can't see (`j` jumps the view back).
- `f` asks `[c]onfirmed or [r]efuted:` which looks like a single key; it needs `⏎`.
- 150+ columns: the activity column reads `nothing yet` and takes a column's width from
  the tree, which is where long text then wraps and truncates sooner.
- The raise's identity is its ISO timestamp in the row, the log and the prompt; relative
  age + first line would do.
- The `Summary` is shown only when the task is `done` (reviewed). The best four sentences
  in the tree (W2's: "met as a loud warning with opt-in failure, not a hard failure for
  everyone") are invisible during the whole review window, i.e. when the author is
  deciding whether to trust the tree. *Fix:* show it, labelled "draft, review pending".
- `/` inside the tree matches only the visible text.

### 2.7 What works, and should be kept

- Footer is contextual and degrades by dropping hints before keys; `?` is a key table you
  can press from; `esc` never quits. Needed no explanation.
- Raises ride on their row and a fold can't hide them (`● n raises below`).
- Goal always above the tree; Summary above that when done: right order.
- Each node shows its commit(s), found from the subject, so evidence is one `git show` away.
- Dimming for refuted/parked/pruned; `✓ ✗ ○ ‖ ·` are readable without colour.
- Raise text with options, recommendation and the strongest case against it (the skill's
  format) is the most readable thing on the screen. The `i`/`f`/`a` forms show the node's
  own text in the editor frame.
- It never crashed at 80×24 or 150×45.

## 3. Data-quality findings (skill/briefing, not the screen)

1. **Two instructions disagree on how often to state a Hypothesis.** `ROADMAP.md` §0:
   "Anything you could reasonably choose, choose, and write it down as the node's
   hypothesis." `SKILL.md`: "the few nodes… an ordinary choice the check settles is not
   one… Do not invent a Hypothesis." Result: 3/28. Assumptions I'd flag as an author and
   that weren't:
   - W3.2: exceptions in `compute`, per-key state never freed (found later by the reviewer).
   - W6.2: abandoned worker threads keep running, so a hung upstream still exhausts
     threads, which is the Goal's stated problem. It's in the **Summary** only, and the
     reviewer filed it as a minor finding.
   - W4.2: the skew allowance is one-sided (`now > expiry + 30`).
   - W8.1: `flock` on a "shared filesystem" is assumed local (NFS locks differ).
2. **Caveats migrate to prose the ⚑ pass never reads**: Summary (W6, W3), Determination
   "Next, …" (14/28), review minor findings.
3. **Determination mixes the finding with the handoff.** "Confirmed: …passes. Next, check
   that a slow key does not block others." Half the sample. A reviewer wants the verdict
   line only.
4. **Alternatives aren't laid out as nodes.** W8 was open-ended and the agent kept "static
   rate/2 split" as prose ("parked on paper, not tested") inside W8.1's evidence; no parked
   node. Zero parked/alternative nodes in 28. So in practice the "decision tree" is a
   line plus a log, and the screen's branch features (parked mark, guides, fold) were
   only exercised by my W9.
5. **The standalone rule is unlinted** (§2.4). **Vacuous checks pass**: the critic said
   W3.3's test "would also pass on the old code" and let it through as valid.

## 4. Prioritised changes

| # | Change | Where | Effort |
|---|---|---|---|
| 1 | Skim mode: first sentence of Determination under each row | `lines_tree` (tui.py ~3340) | S |
| 2 | Don't cut the falsifier: full Hypothesis or split at "wrong if"; fix clipped `…` | tui.py 3385-3392 | S |
| 3 | Style the opened node: dim labels, amber Hypothesis, coloured +/− evidence | tui.py 3398-3406 | S |
| 4 | Keep the author's words on the tree: answered raise collapsed under its node; correction reason under the node; overruled determination marked; `Corrects:` shown as text | `tree_entries`, `node_detail` | M |
| 5 | Review queue: `n`/`N` next ⚑/raise/open; `rev` counts what its docstring says; accept prompt quotes assumptions + reviewer notes | `act`, `review_cell`, `state.py:143`, `tree_accept` | M |
| 6 | Show review/critic `ok` bodies; let `critic ok` carry one; stop filing leaks and correctness risks as "minor" | `tree.py`, review brief | M |
| 7 | Single-child chains at one indent; indent only at forks | `tree_entries` depth | M |
| 8 | Tree focus gives the pane the full height; task id in the pane rule | `draw` | S |
| 9 | Show Summary before `done`, marked draft | `tree_entries` 241 | S |
| 10 | `G`/`g` move the cursor in the tree; `f` takes a single key; warn on `i` under a dead node | `act` | S |
| 11 | Reconcile ROADMAP §0 and SKILL on Hypothesis frequency; add an `Ask:`-style marker for questions to the author; have the skill ask for "Wrong if:" separately | skill/templates | S |
| 12 | Lint standalone-ness in `check.py` (warn on Approach under N chars or `^(see|as in|same as|fix what)`) | `check.py` | S |
| 13 | Cross-task assumptions pane | new pane | M |

Items 1–3 and 9 are the ones I'd do first: small, and they change what the author can see
in the first ten seconds on a task.

## 5. Status

Built after this review (the numbers are §4's):

| # | Done as |
|---|---|
| 1 | A skim line under each determined node: the verdict's first sentence, without the verdict word. |
| 2 | The assumption and `wrong if` are separate lines, clipped with an ellipsis inside the column; the whole of it is in the panel. |
| 3 | The node is drawn as a card: dim labels, amber Hypothesis, green `+` and red `−` evidence. The same card is the panel at the right (from 110 columns, while the tree has the keys; `^d`/`^u` scroll it) and a node opened in place. |
| 4 | An answered raise is marked `✎ answered` and shown with its answer; a correction shows its reason; an overruled determination is said to be overruled; `Corrects:` shows the correction it carries out. |
| 5 | `n`/`N` jump between raises, ⚑ and asks; `rev` counts assumptions and asks (a finished tree with none reads `accept`); the accept prompt quotes each assumption's falsifier and counts reviewer notes. |
| 6 | Reviewer and critic notes are a section above the Goal until accepted; `critic ok` takes a body; the review brief says a leak or an unhandled failure is a node, and titles fixes `Fix:`. |
| 7 | A single-child chain steps in half a step (a column in the terminal), a fork a full one (two), capped at eight steps; siblings share a column and a child is never level with its parent. (First done as one column for the whole chain, which read as the parent's sibling.) |
| 8 | With the tree focused the list is one row; the pane rule names the task. |
| 9 | The Summary shows before the task is done, marked as a draft. |
| 10 | `g`/`G` move the tree's cursor; `f` takes one key; `i` under a refuted or pruned node asks first. |
| 11 | ROADMAP §0 and SKILL agree: a hypothesis only where the choice matters; `Wrong if` in the same field; a new `Ask:` field for a question to the author, shown in red and counted with the assumptions. |
| 12 | `check.py` prints a note (never a refusal) for a new node whose Approach or Determination points at another node or is too short to stand alone. |
| 13 | `H` lists every open assumption and ask across the tasks; ⏎ goes to it in its tree. |

Not done: `/` in the tree still searches only what is on screen, and the light-terminal
palette has not been looked at with the new amber and red lines.
