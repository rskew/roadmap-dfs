# Artefacts

An artefact is one self-contained HTML page (or a screenshot) in `.dfs/artefacts/`. It
is committed, so the path a raise or node names still resolves from any clone long after
the entry was written. There are two kinds:

- **A picture** for a raise, a Summary or any node: wiring before and after, options side
  by side, what ran and what it showed, measured results. Use one whenever the author
  would otherwise rebuild a shape from prose.
- **An answer page**: one the author operates and that posts their ruling. A Hypothesis
  always names one (it confirms or refutes the node), and a raise may name one (it answers
  the raise). It is drawn freely, above the one part every answer page shares: the
  answer form in `templates/_TEMPLATE_ANSWER.html`.

Either way the entry that names a page stays complete without it: the options and the
recommendation are written out in the raise, the assumption in the Hypothesis. The page
is an aid, never the carrier.

## Writing one

1. **Name it with a uuid**, `python3 -c 'import uuid; print(uuid.uuid4())'`, and put the
   human name in the `<title>`. A raise's id is only allocated when the entry is
   appended, after the page exists, and a concurrent session can take the id you
   predicted.
2. **Copy a template into `.dfs/artefacts/<uuid>.html`**: `templates/_TEMPLATE.html` for
   a picture, `templates/_TEMPLATE_ANSWER.html` for an answer page. Keep the page
   self-contained: no CDN, no fonts, no scripts fetched at open time, since a page that
   renders blank offline looks like nothing was produced.
3. **Write it mobile first.** The author usually reads on a phone, so design for 360px
   and widen under `@media (min-width: 720px)`, never the other way about. That means
   `<meta name="viewport" content="width=device-width, initial-scale=1">` (without it a
   phone lays out at 980px), full-width boxes in a column with the arrows pointing down,
   16px body text, buttons, inputs and `<summary>` at least 44px high, no fixed widths and
   no horizontal scroll. A diagram that cannot stack (a grid, a timeline) scrolls inside
   its own box (`overflow-x: auto`) and says so in its caption. Both templates already
   do all this.
4. **State the layout, do not solve it.** Boxes are plain blocks in a flex column, the
   arrows are the `.link` rows between them, and a media query turns the column into a
   row. Every box carries `data-node="<name>"`, which is how the checker reports an
   overlap by name rather than as two rectangles.
5. **Check it, then look at it:**

       <scripts>/artefact_check.sh .dfs/artefacts/<uuid>.html

   (`<scripts>` is the directory your briefing names.) It takes `playwright` from PATH,
   else from the dev shell `.dfs/playwright-shell` names, else from nixpkgs. It renders
   headless at desktop width and as a 360px touch screen (`--mobile-width N`, `0` to
   skip) and fails on: overlapping boxes, text that does not fit or is under 12px,
   console errors, failed requests, a blank page, no `width=device-width` viewport,
   content wider than the phone, controls under 32px high, and a slider that changes
   nothing but its own number (each range input is moved to its minimum and maximum and
   the page compared without that slider's label and every `<output>`, so put a result
   in an ordinary element). It writes `/tmp/artefact-check/<uuid>.png` and
   `<uuid>-mobile.png`.

   **Read the mobile image once the checks are clean, and do not skip it.** The checker
   misses clipped labels and diagrams that are legible and say the wrong thing; a box
   needs noticeably more height than its text appears to want. Reading it costs
   context, so do it once, not on every iteration.
6. **Name it by its path**, `.dfs/artefacts/<uuid>.html`, in the raise, Hypothesis or
   Summary. The web page turns the path (or a bare `<uuid>.html` the task has) into a link
   and lists every artefact a task names under the task; the screen's `v` pane lists
   them too. `http://localhost:<port>/<uuid>.html` (`DFS_ARTEFACT_PORT`, 3016 unless set)
   still works, but needs something serving that port.

The web page serves artefacts sandboxed, because an agent wrote them: no network, and no
route but the one below. That is what "self-contained" is for.

## An answer page

A Hypothesis names its page as `.dfs/artefacts/<uuid>.html` inside the Hypothesis, and
`check.py` refuses a commit that adds one without it. A raise names its page on a line of
its own, `Answer with: .dfs/artefacts/<uuid>.html`; a raise may link several pages to look
at, and this line picks the one that answers. `tree.py log raise` refuses a page that is
not there or cannot answer.

### What an answer page shows

The answer template has no layout: its body is empty, because no one shape suits every
decision. Draw the issue the way it is best seen, and draw before you write: a caption
under a drawing, not paragraphs beside one. What a page usually needs:

- **The mechanism.** How the part of the system the decision touches works now: what
  calls what, where the data goes, which step decides. The author rules faster on a
  wiring they can see than on one they rebuild from prose, and can check the drawing.
- **Each option, as a change to that drawing.** What it adds, removes or rewires, set
  side by side or switched between with the page's own buttons, so the difference is
  what moves on screen.
- **What follows from each.** What it costs, what it risks, what it commits later work
  to, and where it breaks: a number that moves, a step that turns red, a list that
  changes with the option.
- **What would show it wrong**, for an assumption: the observation that refutes it,
  and the evidence so far.

Pick the form the issue has: a flow of boxes and arrows (`templates/_TEMPLATE.html`'s
`.dg`, `.box` and `.link`), a before and after, a timeline of what ran, a table of the
options against the same axes, a chart of measured results. Every control changes
something visible. Add a slider only when the decision turns on a quantity and a result
or drawing changes as it moves; otherwise buttons and `<details>` are the controls. The
checker refuses only a dead slider; whether the page tells the reader anything is the
screenshot's and the author's judgement.

### The answer form

The form at the foot of `templates/_TEMPLATE_ANSWER.html`, from the comment that says to
keep it to the end of the file, is the one part every answer page shares. It carries its
own style and script and needs nothing above it, so the page around it is yours. Edit
only its options: one radio button each, whose `value` is the answer as the author should
be seen to give it, so write the decision, not a letter (a Hypothesis page whose Confirm
and Refute say it all may drop them). The form sends the option chosen and the author's
note as the answer to an open raise, or with Confirm or Refute as the ruling on the node,
and keeps both so the page reopens as the author left it. The page's own script may call
`dfsAnswer.choose(<value>)` so its option buttons pick the answer too, define
`window.dfsPageState()` to keep its own state, and listen for the `dfs-state` event,
whose `detail` is that state when the page reopens.

`check.py` tests that the page exists, has a control (`<input>`, `<button>`, `<select>`,
`<textarea>` or `<details>`), has a handler (`addEventListener` or an `onclick`-style
attribute), and can post a ruling: it reaches `/artefact-state/` and sends a `verdict`.

### The route a page may reach

The web page gives a sandboxed page one route, `/artefact-state/<its own file name>`, and
nothing else on the server. Send each body as plain text,
`fetch(url, {method: "POST", body: JSON.stringify(...)})` with no header, because a JSON
content type needs a preflight this server never grants.

- **Keep state.** `POST {"state": <anything>}`; the next `GET` returns
  `{"state": ..., "raise": ..., "node": ...}` in any tab, so the page opens as the reader
  left it. It is stored in `.dfs/artefact-state/<file>.json`, local and gitignored.
- **Answer a raise.** `POST {"answer": "<text>"}` appends the `answer` entry the terminal
  would write, only for an open raise that names this page in its `Answer with:` line. The
  GET's `raise` is that raise while it is open, so show the send button only then.
- **Confirm or refute a node.** `POST {"state": ..., "verdict": "confirmed" | "refuted",
  "answer": "<why>"}` appends the `correct` entry the web form's Confirm and Refute write,
  with `answer` as its directive. It is accepted only while the node is live, not parked
  and not yet ruled on. The GET's `node` (`{task, id}`) is that node, or `null`, so show
  the buttons only when it can rule.

When the web page opens the artefact beside its form (tapping Answer on a raise with an
`Answer with:` page, or Confirm or Refute on a node whose Hypothesis names one),
`parent.postMessage({dfs: "answered"}, "*")` after a successful post closes the form;
opened alone, that message goes nowhere. The text box stays, for ruling in words. The
answer form's "Send as my answer", Confirm and Refute buttons do all of this. The author
reads what is posted, so write it as the decision, not a dump of the state.

## Screenshots

A `.png`, `.jpg`, `.gif`, `.webp` or `.svg` saved in `.dfs/artefacts/` is an artefact for
work whose result is something to look at: a layout, a chart, a page before and after.
Name it with a uuid like a page and write it into a node, raise or Summary as
`![what it shows](.dfs/artefacts/<uuid>.png)`; the web page draws it there (tap for full
size) and previews it under the task's Artefacts. `artefact_check.sh <page> --out
.dfs/artefacts/<uuid>.png` writes the checker's screenshot straight there. A node that
changes what the user sees names one; the Summary of a finished task may. Keep them small:
they are committed.
