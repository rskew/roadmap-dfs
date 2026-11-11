# Code explainer

`scripts/explain.py NOTES.json --out PAGE.html` writes one page that shows a piece of code from
six angles: data flow, state, resources, failure, queue behaviour and errors handled. A page
starts with the lens buttons, the call tree (who calls whom), the legend, then one section per lens.

## Lens lights

The lens buttons sit above the tree and stay at the top of the screen while the page scrolls. Tap a
lens and every tree function with a note in that lens is lit (a thick border in the lens colour),
the others fade, a line of words says how many of the tree's functions are lit, and only that lens's
section is shown. "All" puts the page back. Each tree function carries the lenses it has a note in
(`data-lenses`, read from the notes), so nothing new is written for this. A repeated function
(marked ↺) is faded, since its notes are on the line above. Without JavaScript the page shows every
section and no function is faded.

## What the machine does and what a person writes

- The call tree and the `try`/`except`/`raise` list of the "errors handled" lens are read from the
  code with `ast`. A call is drawn only when the text fixes its target: `f()` for a function in the
  files read, `self.f()` for a method of the class, its bases, or (for a mixin) the one subclass that
  defines it, and `Class.f()` or `Class(...).f()` for a class in the files read. A call on anything
  else (`table.get()`, `thread.start()`) is left out, as is a call on a name the calling function binds
  itself (a parameter or a local), so the tree can miss a call but does not draw one the code lacks.
- The tree starts at the notes' `entries`. A function a note names that no entry reaches by a drawn
  call (often one reached only through a call that was left out) is added after them, under "Named in
  the notes below", so every function a note marks is in the tree with its lens dots.
- Every other fact is a note in `docs/explain/<subject>.json`. A note names the function it is
  about; the generator fails if that function (or any function an arrow joins) is not in the
  files read, so a rename shows up as an error instead of a page that quietly lies.
- `files` are paths relative to the current directory, so run `explain.py` from the repository root.
  With no `entries`, the tree starts from the functions no other function calls, or, when every
  function is called by another, from the first function of each file.
- A note is checked only for names, not for what it says. A task that changes `scripts/inbox.py`,
  `scripts/ptyrelay.py` or `scripts/walk.py` must update `docs/explain/*.json` for them too: renaming
  a noted function fails `scripts/test_explain.py`, but changed behaviour under the same name does not.

## Notes file

```
{ "title": ..., "about": ..., "files": ["scripts/x.py"], "entries": ["main"],
  "places":  { id: {"kind": <box kind>, "label": ..., "note": ...} },
  "edges":   [ {"kind": <arrow kind>, "from": id|function, "to": id|function, "label": ..., "at": function|null} ],
  "handled": [ {"at": function, "error": ..., "how": ...} ] }
```

`from` and `to` are a place id or a function in the files read. `at` is the function whose code
does it, or `null` when it happens outside the files read.

## The grammar

One symbol means one thing on every page, and the legend on the page is built from the same
tables as the drawing (`PLACES` and `KINDS` in `explain.py`).

Colour is the lens: blue data, purple state, orange resources, red failure, green queue, teal
errors. A box also has a symbol and an arrow also has a head, so colour is never the only clue.

| box | is |
| --- | --- |
| ƒ | code in the files read |
| ◉ | a person or program outside them |
| ▤ | a file or directory holding data |
| ◆ | a value that is remembered and changes |
| ⬢ | something opened that must be let go |
| ≣ | things waiting, taken in order |

| arrow | lens | meaning |
| --- | --- | --- |
| moves ▶ | data | a value travels |
| sets ▶ (thick) | state | the left changes the state on the right |
| reads ▶ (dotted) | state | the left looks at the state |
| acquires ● | resource | takes hold |
| releases ○ (dashed) | resource | lets go |
| fails ▶ (dashed) | failure | the failure travels on |
| stops ■ (dashed) | failure | the failure ends here, nobody further out hears of it |
| puts ▶ | queue | adds to the queue |
| takes ▶ (dotted) | queue | removes from the queue |
| handles ▣ | errors | catches an error and does what the label says |
