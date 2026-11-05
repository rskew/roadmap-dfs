# Code explainer

`scripts/explain.py NOTES.json --out PAGE.html` writes one page that shows a piece of code from
six angles: data flow, state, resources, failure, queue behaviour and errors handled. A page
starts with the call tree (who calls whom), then the legend, then one section per lens.

## What the machine does and what a person writes

- The call tree and the `try`/`except`/`raise` list of the "errors handled" lens are read from the
  code with `ast`, so they are true by construction.
- Every other fact is a note in `docs/explain/<subject>.json`. A note names the function it is
  about; the generator fails if that function (or any function an arrow joins) is not in the
  files read, so a rename shows up as an error instead of a page that quietly lies.

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
