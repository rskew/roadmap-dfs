# Artefacts

A picture for a raise, an assumption or a finished task, whenever the author would
otherwise reconstruct a shape from prose: how parts are wired, before and after, options
set side by side, what ran and what it showed, measured results. Committed,
so the URL in a raise, which the Log never edits, still resolves from any clone long
after the raise was answered.

⚠️ **Two of them are not raise pictures.** `sysml-model-workstreams.html` and
`sysml-model-shape.html` are standing maps for an epic, read whenever one of its
items is picked rather than once and then dead. They are named for their subjects
rather than with uuids, because the uuid rule below exists only to survive a raise
id being allocated after the artefact, which does not apply to them. Everything
else here — self-contained, stated layout, the checker, the screenshot read once —
applies unchanged.

⚠️ **The checker does not catch every clipped label, so step 3 is not optional.**
Both of those artefacts passed the assertions clean while six or seven boxes had
their last line cut off by their own border. The screenshot is what found it, both
times. A box needs noticeably more height than its text appears to want.

**A parallel experiment** (`roadmap-dfs/docs/epic-agent-roadmap.md`). The link goes
inside the raise's prose, so this can be abandoned without withdrawing a law. ⚠️
**The raise stays answerable with the artefact gone**: the decision is written out in
the raise, and a diagram is an aid, never the carrier.

## Writing one

1. Name it with a uuid — `python3 -c 'import uuid; print(uuid.uuid4())'` — and put
   the human name in the `<title>`, since the filename can no longer carry it. A
   uuid is used because a raise's id is only allocated when the entry is appended,
   which is after the artefact exists, and a concurrent session in the same tree
   can take the id you predicted.
2. Copy `roadmap-dfs/templates/_TEMPLATE.html` into `.dfs/artefacts/`. Keep it
   **self-contained** — no CDN, no fonts, no scripts fetched at open time. A page
   that renders blank offline looks like nothing was produced, which is the worst
   failure available here.
3. Check it, and then LOOK at it:

       <scripts>/artefact_check.sh .dfs/artefacts/<uuid>.html

   (`<scripts>` is the tool's scripts directory, which your briefing names.) It
   takes `playwright` from PATH, else from the dev shell `.dfs/playwright-shell`
   names, else from nixpkgs. It renders headless and fails on overlapping boxes, text that does not fit,
   text below 12px, console errors, failed requests and a blank page — then writes
   `/tmp/artefact-check/<uuid>.png`. Read the image once the checks are clean; it
   catches what no assertion does (a diagram that is legible and still says the
   wrong thing), and reading it costs context, so not on every iteration.
4. Reference it by its path, `.dfs/artefacts/<uuid>.html`, in the raise (or the
   Hypothesis, Ask or Summary it belongs to). The web page links that path to the page it
   serves at `/artefacts/<uuid>.html`, and lists every artefact a task names under the
   task; the screen's `v` pane lists them too. The older form,
   `http://localhost:<port>/<uuid>.html` (`DFS_ARTEFACT_PORT`, 3016 unless set), still
   works in both, but it needs something serving that port, and the path does not.

   The web page serves artefacts sandboxed (no network, no way back to its own controls),
   because an artefact is a page an agent wrote. That is what "self-contained" is for.

How the author opens one is in the README, under "Raise artefacts".

## An assumption's artefact

Every node that states a Hypothesis names one, as `.dfs/artefacts/<uuid>.html` inside the
Hypothesis, and `check.py` refuses a commit that adds a Hypothesis without it. Its job
is different from a raise's picture: it explains the assumption and lets the author
decide it, so it has to be **interactive**, a page they operate and not one they read.
Start from `templates/_TEMPLATE_ASSUMPTION.html`. Give the reader the thing the
assumption turns on to change (the options to switch between, the input to move, the
evidence to open) and show what follows from each setting, ending on what would show the
assumption wrong. The rest of this file (self-contained, checked, screenshot read once)
applies unchanged. What `check.py` tests is only that the page exists, has a control
(`<input>`, `<button>`, `<select>`, `<textarea>` or `<details>`) and a handler that
answers it (`addEventListener`, or an `onclick`-style attribute). It does not judge that
the controls tell the reader anything; the screenshot and the author do.

## What a reader does on a page, and answering with it

A page is served sandboxed (no network, no storage, no way to `/api`), so nothing the
reader does on it is kept: the only record of a decision is the `answer` entry in the
task's log. A page can still hand its result to the web page. When the author taps
Answer on a raise that names the page, the answer form opens with the page beside it, and
`parent.postMessage({dfs: "answer", body: "<text>"}, "*")` from the page puts `body` in
the form's text box. The author reads it, edits it and presses Record, and the log gets
their words; the page cannot answer by itself, and the form hears only the frame it
opened. The template's "Send as my answer" button does this with the result on the page.
Opened alone, or from the terminal, there is no form and the message is ignored. The
form opens only the first page a raise names, and the form that confirms or refutes a
node hosts none, so a page named only in a Hypothesis is operated from its link and its
button does nothing there; the author states the verdict in their own words.

## Conventions the checker relies on

- Every box carries `data-node="<name>"`. That is how overlaps get reported by name
  rather than as two rectangles.
- Layout is stated, not solved: each box gives its own `left`/`top`/`width`/`height`
  and the connectors are hand-placed SVG. A layout engine's output is the thing
  nobody can see, which is what this whole mechanism exists to fix.

## Screenshots

A `.png` (`.jpg`, `.gif`, `.webp` or `.svg`) saved in `.dfs/artefacts/` is an artefact too, for
work whose result is something to look at: a layout, a chart, a page before and after.
Name it with a uuid like a page. Write it into the prose of a node or raise as
`![what it shows](.dfs/artefacts/<uuid>.png)`; the web page draws it there (tap for full
size) and previews it in the task's Artefacts section, served by the same sandboxed
`/artefacts/<name>`. `artefact_check.sh <page> --out .dfs/artefacts/<uuid>.png` writes the
checker's screenshot straight there. Keep them small: they are committed.
