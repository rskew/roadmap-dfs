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

## Mobile first

The author usually reads an artefact on a phone, so a page is designed for a 360px-wide
screen first and widened under `@media (min-width: …)`, never the other way about. In
practice that is: `<meta name="viewport" content="width=device-width, initial-scale=1">`
(without it a phone lays the page out at 980px and shrinks it); a column of full-width
boxes with the arrows between them pointing down, running left to right only from
720px up; body text 16px; buttons, inputs and `<summary>` at least 44px high; no
fixed widths and no horizontal scroll. Both templates already do this, so start from
one. A diagram that cannot be stacked (a real grid, a timeline) scrolls inside its own
box (`overflow-x: auto`) rather than widening the page, and says so in its caption.

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
   names, else from nixpkgs. It renders headless, once at desktop width and once as a
   360px touch screen (`--mobile-width N`, `0` to skip), and fails on overlapping boxes,
   text that does not fit, text below 12px, console errors, failed requests, a blank page,
   a missing `width=device-width` viewport meta, content wider than the phone, and
   controls under 32px high, and a slider that changes nothing on the page but its own
   number (each range input is moved to its minimum and its maximum, the page is let to
   settle, and what is compared is the page without the slider's own label and every
   `<output>`, so put a result in an ordinary element, never an `<output>`) — then writes `/tmp/artefact-check/<uuid>.png` and
   `<uuid>-mobile.png`. Read the mobile image first once the checks are clean; it
   catches what no assertion does (a diagram that is legible and still says the
   wrong thing), and reading it costs context, so not on every iteration.
4. Reference it by its path, `.dfs/artefacts/<uuid>.html`, in the raise (or the
   Hypothesis or Summary it belongs to). The web page makes that path an `<a>` with the
   relative href `artefacts/<uuid>.html` (the text is read in the page, which serves it
   there), as it does a bare `<uuid>.html` naming a file the task has, and lists every artefact a task names under the
   task; the screen's `v` pane lists them too. The older form,
   `http://localhost:<port>/<uuid>.html` (`DFS_ARTEFACT_PORT`, 3016 unless set), still
   works in both, but it needs something serving that port, and the path does not.

   The web page serves artefacts sandboxed (no network but its own state and answer route,
   below; no way back to its own controls), because an artefact is a page an agent wrote.
   That is what "self-contained" is for.

A task worker may embed a screenshot of the finished work in the task's `## Summary`,
`![what it shows](.dfs/artefacts/<uuid>.png)`, when it judges one helps; the web page draws
it in the Summary and lists it among the task's artefacts. Nothing requires one.

How the author opens one is in the README, under "Raise artefacts".

## An assumption's artefact

Every node that states a Hypothesis names one, as `.dfs/artefacts/<uuid>.html` inside the
Hypothesis, and `check.py` refuses a commit that adds a Hypothesis without it. Its job
is different from a raise's picture: it explains the assumption and lets the author
decide it, so it has to be **interactive**, a page they operate and not one they read.
Start from `templates/_TEMPLATE_ASSUMPTION.html`. Give the reader the thing the
assumption turns on to change (the options to switch between, the input to move, the
evidence to open) and show what follows from each setting, ending on what would show the
assumption wrong. Every control earns its place: it changes something the reader can see on the page.
The template has no slider, because a stock one is copied into every page and moves
nothing. Add a slider (or any input) only when the assumption turns on a quantity and the
result text or a drawing changes as it moves; otherwise the case buttons and `<details>`
are the controls. The check refuses only a dead slider; one whose only effect is a figure
inside a result sentence passes it, so that judgement is the author's. The rest of this file (self-contained, checked, screenshot read once)
applies unchanged. What `check.py` tests is that the page exists, has a control
(`<input>`, `<button>`, `<select>`, `<textarea>` or `<details>`), a handler that answers it
(`addEventListener`, or an `onclick`-style attribute), and can **rule on its node**: it
reaches `/artefact-state/` and sends a `verdict`, below. It does not judge that the controls
tell the reader anything; the screenshot and the author do.

## A page that answers, and keeps where the reader left off

The web page serves a page sandboxed (an opaque origin, no storage, no way to `/api`), with
one exception: it may reach `/artefact-state/<its own file name>`, and nothing else on the
server. There it can
- **keep its state**: `POST` a JSON body `{"state": <anything>}` and `GET` returns it as
  `{"state": ..., "raise": ...}` the next time, in any tab, so the page opens as the reader
  left it. It is kept in `.dfs/artefact-state/<file>.json`, which is local and not committed
  (`init.py` gitignores it);
- **answer a raise**: `POST {"answer": "<text>"}` appends the `answer` entry to the log,
  the same entry the terminal writes, but only for an open raise that names this page on a
  line of its own, `Answer with: .dfs/artefacts/<uuid>.html`. A raise may link several
  pages to look at; the `Answer with:` line picks the one that answers, and `tree.py log
  raise` refuses one that names a page that is not there. `raise` in the GET is that raise
  while it is open, so the page can show its send button only when there is something to
  answer.

Send the body as plain text (`fetch(url, {method: "POST", body: JSON.stringify(...)})`, no
header): a JSON content type needs a preflight, which this server never grants. When the page
sits in the web page's answer form (tapping Answer on a raise that has an `Answer with:`
page opens it beside a text box), `parent.postMessage({dfs: "answered"}, "*")` after a
successful answer closes the form; opened alone, that message goes nowhere. The text box
stays, for answering in words instead. The template's "Send as my answer" button does all of
this. The page that answers is the one the author is shown, so write the answer as the
decision, not as a dump of the state. An agent's page can therefore answer its own raise;
the author reads the log entry, and a raise that names no page is answered by none.
`tree.py log raise` refuses an `Answer with:` page that cannot answer (it has no
`/artefact-state/` route and `answer` field to send).

**Confirm or refute an assumption.** A page a node's Hypothesis names
(`.dfs/artefacts/<uuid>.html` inside it) may rule on that node: `POST {"state": ..., "verdict":
"confirmed" | "refuted", "answer": "<why>"}` appends the `correct` entry the web form's Refute/
confirm writes, with `answer` as its directive. It is accepted only while the node is live,
not parked, and not yet ruled on; the GET's `node` (`{task, id}`) is that node, or `null`, so
the page shows its buttons only when it can rule. In the web page, Refute/confirm on a node
with such a page opens it beside the form and closes on `{dfs: "answered"}`, as a raise's does;
the text box stays for ruling in words. The template's Confirm and Refute buttons do all of
this, and `check.py` refuses a new Hypothesis whose page cannot post a `verdict`.

## Conventions the checker relies on

- Every box carries `data-node="<name>"`. That is how overlaps get reported by name
  rather than as two rectangles.
- Layout is stated, not solved, and stated for the phone: boxes are plain blocks in a
  flex column, arrows are the `.link` rows between them, and a media query turns the
  column into a row. A solver's output is the thing nobody can see, which is what this
  whole mechanism exists to fix. Absolute `left`/`top`/`width` boxes are for a standing
  map that scrolls in its own container (see the two named above), not for a raise
  picture.

## Screenshots

A `.png` (`.jpg`, `.gif`, `.webp` or `.svg`) saved in `.dfs/artefacts/` is an artefact too, for
work whose result is something to look at: a layout, a chart, a page before and after.
Name it with a uuid like a page. Write it into the prose of a node or raise as
`![what it shows](.dfs/artefacts/<uuid>.png)`; the web page draws it there (tap for full
size) and previews it in the task's Artefacts section, served by the same sandboxed
`/artefacts/<name>`. `artefact_check.sh <page> --out .dfs/artefacts/<uuid>.png` writes the
checker's screenshot straight there. Keep them small: they are committed.
