# Raise artefacts

A picture for one raise, when the decision turns on how parts of the system are
wired together and prose makes the author reconstruct it in their head. Committed,
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
4. Reference it from the raise as `http://localhost:<port>/<uuid>.html`: the port
   is `DFS_ARTEFACT_PORT`, 3016 unless set, and something has to serve
   `.dfs/artefacts` there (the TUI shows the `python3 -m http.server` command when
   nothing is).

How the author opens one is in the README, under "Raise artefacts".

## Conventions the checker relies on

- Every box carries `data-node="<name>"`. That is how overlaps get reported by name
  rather than as two rectangles.
- Layout is stated, not solved: each box gives its own `left`/`top`/`width`/`height`
  and the connectors are hand-placed SVG. A layout engine's output is the thing
  nobody can see, which is what this whole mechanism exists to fix.
