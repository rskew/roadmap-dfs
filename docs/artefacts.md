# Artefacts

An artefact is one self-contained HTML page (or a screenshot) in `.dfs/artefacts/`. It
is committed, so the path a raise or node names still resolves from any clone long after
the entry was written. There are two kinds:

- **A picture** for a raise, a Summary or any node: wiring before and after, options side
  by side, what ran and what it showed, measured results. Use one whenever the author
  would otherwise rebuild a shape from prose.
- **An answer page**: one the author operates and that posts their ruling. A Hypothesis
  always names one (it confirms or refutes the node), and a raise may name one (it answers
  the raise). It is a picture with controls, and it is built from
  `templates/_TEMPLATE_ASSUMPTION.html`.

Either way the entry that names a page stays complete without it: the options and the
recommendation are written out in the raise, the assumption in the Hypothesis. The page
is an aid, never the carrier.

## Writing one

1. **Name it with a uuid**, `python3 -c 'import uuid; print(uuid.uuid4())'`, and put the
   human name in the `<title>`. A raise's id is only allocated when the entry is
   appended, after the page exists, and a concurrent session can take the id you
   predicted.
2. **Copy a template into `.dfs/artefacts/<uuid>.html`**: `templates/_TEMPLATE.html` for
   a picture, `templates/_TEMPLATE_ASSUMPTION.html` for an answer page. Keep the page
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

The page explains the assumption or the decision and lets the author settle it, so it is
**interactive**: the author changes the thing it turns on (the options to switch between,
the input to move, the evidence to open) and sees what follows from each setting, ending
on what would show the assumption wrong. Every control changes something visible. The
template has no slider because a stock one moves nothing; add a slider or other input only
when the assumption turns on a quantity and a result or drawing changes as it moves,
otherwise case buttons and `<details>` are the controls. The checker refuses only a dead
slider; whether the controls tell the reader anything is the screenshot's and the author's
judgement.

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
template's "Send as my answer", Confirm and Refute buttons do all of this. The author
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
