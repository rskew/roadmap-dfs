# The design system

The roadmap's web page is one small system, written down so the next change starts from it
rather than from the last screenshot. The tokens and components are `scripts/web/design.css`;
`/design` (served by `web.py`) shows all of it, in either theme, and measures the colours
live. The page's own layout is in `index.html` and uses nothing but those tokens.

## Calm Bauhaus

Quiet paper, hairline rules, the system's own type. Colour is **for meaning, never for
decoration**, and there are three meanings:

| meaning | colour | says | used as |
|---|---|---|---|
| settled | blue | decided, confirmed, done | text, a status stripe, a tag |
| attention | red | waits on you: a raise, a blocker, a refusal | text, a tint, a card, an error |
| assumption | amber | rests on a choice nobody could settle | text, a tint, a ruled block |

Each has an **ink** (text), a **wash** (the tint a whole row or card takes) and, for amber, a
**fill**. Red wins over amber where both apply: a blocker is more urgent than an assumption.
One more colour is not a meaning but a name: **`--project`**, the project's own colour
(`.dfs/theme`, else made from its name), set by the server on `<html>`. It marks which
project this is (the stripe along the top of the bar, the browser's `theme-color`, the
installed app) and is never used for status or text. `--project-mark` is how it is drawn:
as it is on paper, lightened in the dark theme so it still shows.
Everything else is paper, panel, ink, muted and two rules. Square corners, no shadows, no
icons but the theme switch. The one flourish is the two-pixel line of the three, under the bar.

## Tokens

- **Colour**: `--paper --panel --sel --ink --muted --line --faint` and the three meanings
  (`--settled --attention --assumption`, each `-wash`; `--assumption-fill`). Text is held to
  4.5 to 1 on the surface it is used on, in both themes; `/design` prints the ratios.
- **Type**, five steps: `--fs-label 12` (caps labels, tags), `--fs-control 13` (caps on a
  control), `--fs-small 15`, `--fs-body 17`, `--fs-title 22`. Caps are tracked and only ever
  small. The font is the system's own.
- **Space**, a 4px base: `--s1 … --s6` = 4 8 12 16 24 32.
- **Rules**: `--rule 1` (a rule), `--rule-strong 2` (the current tab, the flourish),
  `--rule-status 3` (what a node came to). **Shape**: `--radius 0`.
- **Reach**: `--hit 44`, the smallest thing a thumb is asked to press. `--measure 68ch`, the
  longest line of prose.

## Components

`.btn` (default, `.primary` the one thing the screen wants done, `.danger`), `.textbtn`,
`.textlink`, `.iconbtn` (the theme switch only), `a.art` / `.link` (a link in prose), `.tag`
(plain, `.settled`, `.attention`, `.assumption`), `.field`, `.seg` (a choice of one among
few), `details.sec` (a ruled row that opens), `.raise` (attention card), `.asm` (assumption
block), `.actions`, `dialog`, `#toast`.

## Rules

1. A colour that does not mean one of the three things is not used.
2. Nothing smaller than `--hit` is pressed, except a link inside a sentence, which is as
   big as its words.
3. A value that appears twice is a token; a token that appears once is a decision to look at.
4. Both themes are the same system with different values: a component never names a colour,
   only a token, so it is checked once.
5. Change the system in `design.css`, look at `/design`, then change the page. `test_ui.py`
   audits `/design` and every screen of the page, both themes, three widths, for overflow,
   contrast and tap targets.
