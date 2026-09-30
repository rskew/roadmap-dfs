# TUIs: what works, what doesn't, and for what

Notes from surveying two different sets of terminal UIs, taken 2026-09-30 with
`dfs_tui.py` in mind. The sets overlap less than you would expect, and the gap
between them is itself the first lesson:

- **Highly regarded** — what people recommend, write posts about, and put on
  "awesome" lists: lazygit, k9s, btop, helix, yazi, atuin, fzf, tig, ncdu, visidata,
  lnav, gitui, Posting, Harlequin, zellij.
- **Widely used** — what people actually have open every day, often without choosing
  it: vim, less, top/htop, tmux, nano, man, `git add -p`, Midnight Commander,
  mutt/neomutt, `menuconfig`, aptitude, the `git rebase -i` editor buffer.

The widely used set is old, mostly modal or F-key driven, and survives because it is
**everywhere and stable**: muscle memory built on one machine works on every other. The
highly regarded set is newer, and wins on **discoverability and density**: a first-time
user can do the common thing without reading anything. Almost no tool manages both,
and the few that come close (htop, fzf) are worth studying most.

Sources are linked where a claim is someone else's. Sections 1–8 are otherwise general
knowledge of these tools. Section 0 is a hands-on pass, run afterwards.

---

## 0. Hands-on (2026-09-30)

Nine of them, built from nixpkgs and run in a pty, 110×32 and smaller, with the screen
read back through `pyte`. Keys were sent, and each screen was snapshotted along with a
count of the distinct colour/attribute combinations on it. (pyte doesn't record dim
text, so the counts slightly understate the plainer tools.) k9s needs a cluster and
lnav needs a real log corpus, so they were left out.

| Tool | Styles on screen | What was seen |
|---|---|---|
| **lazygit** 0.65 | 12 | Rounded boxes with the title and a `[n]` jump key set into the top edge, and `1 of 5` in the bottom edge. The focused box's border is green; nearly all text is plain. Staging a file put `git add -- .` in the command log at once. `?` is a popup that filters as you type and runs the key on ⏎. At 70×22 the unfocused boxes collapse to one row, and the footer ends in `…`. |
| **tig** 2.6 | 8 | No boxes at all: one split line, and a status bar per view (`[main] Staged changes 100%`). The help lists key, **action name**, description (`m view-main Show main view`), and the names are what its `:` prompt and config take. |
| **htop** 3.5 | 16 | `/` and `\` (filter) take over the F-key bar with their own keys (`F3Next S-F3Prev EscCancel Search: python`). Both act as you type, and Esc cancels straight away. Between two snapshots a second apart, rows had swapped places under the sort. |
| **btop** 1.4 | 82 | Truecolor, and it paints its own black background over the terminal's theme. It looks calm anyway because the borders are dark grey (`303030`, `404040`), text is light grey, and colour is kept for data. Box keys are superscript digits in the border (`¹cpu ²mem ³net`). |
| **helix** 25.07 | 8 (theme) | `space` pops up every continuation with a one-line meaning. At `:`, typing `w` shows the matching commands as a grid, with a box above describing the top one (what it does, aliases, flags) before ⏎. |
| **fzf** 0.74 | 8 | `tuipy` finds `scripts/dfs_tui.py` (letters in order). The selected row is a dark grey background (`303030`) plus a thin coloured `▌` in the first column rather than reverse video. Matched letters are tinted, the chrome is mid-grey (`5f5f5f`), and a `1/39` count sits on the prompt's rule. |
| **ncdu** 2.11 | 3 | Plain, reverse and bold reverse, and nothing else. It reads perfectly well, and it parks the cursor on the selected row. |
| **visidata** 3.4 | 21 | After each key, the bottom-right shows the key and the command it ran (`Shift+F   freq-col`), and the bottom-left shows the stack of sheets as a breadcrumb (`1›t | 2📶t_item_freq`). A first-visit help sidebar opens on its own, which is useful the first time and in the way after that. |
| **yazi** 26.9 | 19 | Parent · current · preview columns. Long names are cut in the middle so the extension survives (`new….txt`). Its icons need a Nerd Font and show as unknown glyphs without one. |
| *dfs_tui.py*, for comparison | 5 | Two full-width reverse bars (the selected row and the footer) are the loudest things on screen. The header spends half its width on zeros (`refuted 0 · parked 0 · pruned 0`). |

What this changed from sections 1–8:

- **"Clean" comes from contrast between chrome and content, not from fewer colours.**
  btop uses 82 styles and still looks calm, because every border and label is a step
  darker than the data. ncdu uses 3 and looks fine. What looks loud is *large areas*
  of reverse video or background colour. fzf's selection (a faint background and a
  one-cell accent bar) is the lightest selection that still reads at a glance.
- **Painting your own background is a trade.** btop and helix look designed, and they
  override a light terminal theme to get there. fzf, lazygit, tig, htop and ncdu keep
  the terminal's background and only colour text, which is what a tool that lives in
  someone's tmux should do.
- **Keys are best taught where they're used.** Help screens work (lazygit, tig), but
  the two moments that teach most are helix's popup *before* the key and visidata's
  echo *after* it. tig's key → name → description triple is what connects keys to a
  command line.
- **Input belongs where the footer was.** htop and fzf put the input on the bottom line,
  with its own keys, acting as you type, and Esc cancels immediately. A `getstr` on the
  message line does none of that.
- **Unfocused panels shrink rather than disappear** (lazygit at 70×22). Each keeps its
  title row, so the layout's shape survives and only the depth goes.
- lazygit doesn't park its cursor (it sits in the bottom-right corner); ncdu does.

---

## 1. Layout: which shape fits which job

| Shape | Exemplars | Fits | Fails when |
|---|---|---|---|
| **Persistent multi-panel** (every list visible, one detail pane) | lazygit, gitui, btop, tig's main view | A small, fixed set of entity kinds you act on together (files / branches / commits) | The kinds grow past ~5, panels get too short to show more than 3 rows, and the layout starts collapsing panes on its own |
| **Drill-down stack** (one list at a time, Enter goes in, Esc comes out) | k9s, ncdu, aptitude, mutt (index → pager) | Deep hierarchies: cluster → namespace → pod → container → logs | You need to see a parent and child at once, or compare two siblings; the breadcrumb is the only map |
| **Miller columns** (parent · current · preview) | ranger, yazi, lf, nnn | Trees you navigate far more than you act on — filesystems | Wide rows (long names, many fields); three columns of 26 chars each on an 80-column terminal truncates everything |
| **Dashboard** (fixed tiles, no focus) | btop, bottom, glances, `watch` | Monitoring: you look, you don't act | You need to act on a row; btop's process list is the one interactive tile and it is the cramped one |
| **Single table + detail/pager** | htop, top, visidata, lnav, mutt | One homogeneous list, sorted and filtered | Rows of different kinds mixed together |
| **Editor/buffer** | vim, helix, `git rebase -i`, `crontab -e` | Composing or reordering text-shaped data | Anything that isn't really text; the rebase buffer works only because a plan *is* a list of lines |
| **Overlay/fuzzy finder** | fzf, atuin, telescope, Ctrl+P palettes | Picking one thing out of thousands by name | Browsing when you don't know the name |

What the good ones share, whatever the shape: **spatial consistency**. Users learn that
network traffic is top-right in btop and their eyes go there without thinking; spatial
memory becomes the navigation ([Hyperbliss](https://hyperbliss.tech/blog/terminal-renaissance/)).
A layout that moves panes around depending on state (a pane that appears only when
something is running, then shifts the others) throws that away. Lazygit's layout "hasn't
changed since day one" — show as much context as possible, side windows for the lists,
the main window for whatever is selected ([Duffield, Lazygit turns 5](https://jesseduffield.com/Lazygit-5-Years-On/)).

**Small terminals are the real test.** lazygit and k9s degrade by shrinking panels and
letting a focused one expand; btop drops whole tiles and says so. The bad failure is
silent truncation of the one field you needed (htop's command column at 80 columns;
k9s's resource names). Decide which column is sacrificed first and make it the least
important one, not the rightmost one.

---

## 2. Keys: discoverability versus speed

This is where the two sets differ most.

**The widely used set assumes you learned it elsewhere.** vim shows nothing; `less`
shows nothing; tmux shows nothing and puts everything behind a prefix. It works
because these tools are learned once over years and then transfer everywhere. It is
the wrong model for a tool someone uses for one project.

**The regarded set puts keys on screen**, in three increasingly capable ways:

1. **A footer of the keys for what's focused** — htop's F-key bar, nano's `^X Exit`
   row, Midnight Commander's `1Help 2Menu …`, lazygit's per-panel bottom line. nano
   and mc are the reason non-programmers can use a terminal editor or file manager at
   all. The limit is width: a footer holds 6–10 keys, so it must be *per context*, not
   global. lazygit's rule is that each panel has its own line.
2. **A `?` keymap overlay**, contextual and searchable — lazygit's `?` lists every key
   for the focused panel and lets you *execute* from the list; k9s's `?` is the same
   idea. Being able to act from the help list is what turns it from documentation into
   a menu.
3. **Leader-key popups (which-key)** — helix shows a popup of continuations after
   `space`, `g`, `m`; users credit it with making a modal editor learnable in days
   rather than the vim burn-out period ([helix discussion #1511](https://github.com/helix-editor/helix/discussions/1511),
   [HN](https://news.ycombinator.com/item?id=39221071)). Emacs's which-key and
   Neovim's plugin of the same name exist because the base tools lacked it.
4. **A fuzzy command palette** — Textual gives every app Ctrl+P by default
   ([Textual](https://github.com/Textualize/textual)); helix and Posting have one. It is
   the escape hatch for the long tail of rarely used actions that will never earn a key.

**What goes wrong with keys:**

- **Running out of letters.** k9s's source uses 59 shortcuts and leaves plugins 19;
  users can't bind `p` for pods because it's previous-logs, and Shift+N is sort-by-name
  ([#2793](https://github.com/derailed/k9s/issues/2793), [#625](https://github.com/derailed/k9s/issues/625)).
  The fix k9s already had is its `:` command mode (`:pods`, `:deploy`) — names scale,
  letters don't. A tool heading past ~30 actions needs either a palette or a command
  line.
- **Shifted digits and symbols break on non-US layouts** — on AZERTY, Shift+0 isn't a
  key ([k9s #1391](https://github.com/derailed/k9s/issues/1391)). `[`/`]`/`{`/`}` are
  AltGr chords on German and French layouts. Keep punctuation bindings for things that
  have a letter fallback, or make them rebindable.
- **Case as a modifier** (`r` vs `R`, `a` vs `A`) is cheap to add and easy to fat-finger
  when one is destructive. lazygit and tig put the dangerous variant behind a
  confirmation rather than trust the Shift key.
- **Esc meaning different things.** In vim-likes it's mode-exit; in k9s it's back; in
  some apps it quits. Tools that make Esc *back one level, never quit* and `q` the only
  exit (ncdu, k9s, `dfs_tui.py`) are the ones people stop being afraid of.
- **The conventions users arrive with**, which cost nothing to honour: `q` quit, `?`
  help, `/` search then `n`/`N`, `j`/`k` and arrows, `g`/`G` top/bottom, Tab to cycle
  focus, Enter to go in, Space to toggle/select, Ctrl+P palette
  ([griffen.codes](https://griffen.codes/post/tui-design-skill-claude/)). `/` is the most
  commonly missing one and the most commonly reached for.

---

## 3. Showing what the tool does to the world

The single most-liked lazygit feature was added late: a **command log** showing the git
commands each action ran. Duffield: it "made a huge difference and it's now one of the
things people like best about Lazygit"; users had asked for it in the survey
([Lazygit turns 5](https://jesseduffield.com/Lazygit-5-Years-On/)). The pattern
generalises:

- A TUI that wraps a CLI should **show the command** it ran (lazygit, lazydocker, k9s's
  `:xray` / describe views, aptitude's pending-actions screen before `g`). It turns the
  TUI from a black box into a teacher, and lets people reproduce the action in a script.
- **Preview before commit**: aptitude and `apt` list what will be installed before
  acting; mc asks before overwrite; `git add -p` shows the hunk before `y`. The
  regarded tools make destructive actions two-step (`d` then confirm) and *reversible*
  when they can (lazygit's undo via reflog, `z`).
- **State → action → new state** visible in one place is what a TUI does better than
  a CLI ([Duffield](https://jesseduffield.com/Lazygit-5-Years-On/)): press a key, see the
  list change. A TUI that runs something and then prints "done" in a status line has
  thrown away its one advantage.

---

## 4. Live data, background work, and not lying

Monitoring and long-running work is where TUIs genuinely beat both CLIs and GUIs, and
where they most often mislead.

- **Follow mode must be visible and must break on a deliberate scroll.** `less +F`,
  `tail -f`, lnav, and k9s's log view all tail; the good ones stop following when you
  scroll up and say so, and resume at the bottom. The bad pattern is a pane that
  auto-scrolls while you read, or claims to follow something that has ended.
- **Refresh rate is a choice, not a default.** htop/btop let you set it (`-d`, `+`/`-`);
  k9s refreshes every 2s and pauses on some views. Too fast burns CPU and, worse, makes
  rows jump under the cursor — htop's process list reorders under you mid-keystroke,
  which is why it has `F` (follow a process) and a freeze key (`Z`). **Selection must
  track the entity, not the row index**, or a refresh moves your selection to a
  different thing and the next key acts on it.
- **Staleness has to be shown.** A dashboard that silently stopped updating looks
  identical to one showing a quiet system. k9s shows connection errors in its header;
  btop shows its own update rate. Show last-updated or a heartbeat.
- **Background jobs belong in the screen, not instead of it.** tmux, zellij and lazygit
  (which runs git asynchronously with a spinner per panel) keep you in the UI while
  work runs. Tools that hand over the terminal for minutes (old `aptitude` installs,
  many `menuconfig`-style flows) lose the context the user came for.
- **A status change should be as loud as its importance.** A job that stopped for a bad
  reason shown as a badge quietly disappearing is a common, serious failure; the good
  tools leave a durable, dim "stopped: reason" in the same place the running indicator
  was.

---

## 5. Rendering and terminal hygiene

Table stakes that every tool in the widely used set gets right and new tools
regularly get wrong ([griffen.codes](https://griffen.codes/post/tui-design-skill-claude/)):

- Use the alternate screen and restore the terminal on **every** exit path, including
  exceptions and signals. A crashed TUI that leaves the terminal in raw mode with no
  echo is the most-remembered bad experience there is.
- Handle SIGWINCH (resize) and SIGTSTP (Ctrl+Z / `fg`), and redraw fully after a child
  process (an editor, a pager) has had the terminal.
- **Diff, don't clear.** Full-screen clears flicker over SSH and in tmux. curses and
  ncurses already diff; hand-rolled ANSI renderers and some React-style frameworks
  don't, and redraw everything on each state change.
- Assume 256 colours at most, and 16 to be safe; respect `NO_COLOR`; never carry meaning
  in colour alone (btop and htop use both colour *and* glyph/position). Reverse video
  and bold survive every terminal and every theme; a specific grey often vanishes on a
  light background.
- Unicode box drawing and Braille (btop's graphs) are fine on modern terminals, but
  wide characters and emoji have inconsistent widths between terminals and fonts, which
  misaligns columns. Keep emoji out of anything tabular.

---

## 6. Accessibility — the part the "text mode" assumption hides

Sighted developers tend to assume a terminal app is accessible because it's text. For
screen-reader users, **most modern TUIs are worse than a poor GUI**
([The Inclusive Lens](https://xogium.me/the-text-mode-lie-why-modern-tuis-are-a-nightmare-for-accessibility),
[ACM CHI 2021 on CLI accessibility](https://dl.acm.org/doi/fullHtml/10.1145/3411764.3445544)):

- Screen readers follow the **hardware cursor**. A spinner or elapsed-time counter
  that moves the cursor every tick makes the reader announce it forever
  ("Responding… 1s… Responding… 2s…"). Frameworks that treat the terminal as a canvas
  (Ink, Bubble Tea, tcell) do this by default.
- Re-rendering the whole screen on each keystroke gets slow as content grows and has
  crashed NVDA.
- Borders, ASCII art and tables are read as noise.

Named as accessible: nano, vim, menuconfig, irssi — all of which park the cursor
where the focus is and update sparingly. Recommendations from the same source: park or
hide the cursor where the user's focus actually is, don't animate, update only what
changed, and keep a **linear, non-TUI output mode** for everything (the CLI underneath
is the accessible interface, which is one more reason for a TUI to be a thin view over
commands that also run on their own).

---

## 7. Scope: what a TUI should and shouldn't be

- **TUIs are for navigating, monitoring and working with something over time; CLIs
  are for scripting and automation** ([Hyperbliss](https://hyperbliss.tech/blog/terminal-renaissance/)).
  The regarded tools are all thin views over a scriptable core (git, kubectl, the
  filesystem, `/proc`). None of them is the only way to do anything.
- **Start with the four actions people do every day** and let use drive the rest.
  Lazygit launched with staging, committing, checkout and conflict resolution
  ([Duffield](https://jesseduffield.com/Lazygit-5-Years-On/)). The tools that tried to
  expose the whole underlying CLI (early k9s, many kubectl wrappers) ended up with the
  letter shortage above.
- **Text entry is a TUI's weak spot.** Forms in curses are poor at multi-line editing,
  paste, undo and IME. The good tools hand off to `$EDITOR` for anything longer than a
  line — `git commit`, mutt composing, lazygit's commit-message editor option, `crontab
  -e` — and get vim/helix/nano users' own editor for free.
- **Test by driving it.** Lazygit's first recorded-keypress tests were unmaintainable
  ("a minified JSON containing a sequence of keypresses"); what worked was end-to-end
  tests written as code against named actions
  ([Duffield](https://jesseduffield.com/Lazygit-5-Years-On/)). Textual ships a pilot for
  the same reason. Keeping decisions in pure functions beside the drawing (as
  `row_state` and `walk_decide` are here) is the cheaper version.

---

## 8. By use case

| Use case | What works | What doesn't | Look at |
|---|---|---|---|
| **Act on a few entity kinds together** (VCS, containers) | Persistent panels, per-panel footer, `?` with execute, command log | Hiding the command run; modal dialogs for every action | lazygit, gitui, lazydocker, tig |
| **Browse a deep hierarchy** | Drill-down with breadcrumb, `:name` jumps, Esc = back | Letters as the only way to jump; no way to see parent and child | k9s, ncdu, aptitude |
| **Monitor** | Fixed tiles, configurable refresh, staleness shown, freeze key | Selection by row index under a reordering list; animation for its own sake | btop, htop, bottom, glances |
| **Read logs / long text** | Pager conventions (`/`, `n`, `G`, `F`), follow that breaks on scroll, filters | Soft-wrapping that breaks copy; no search | less, lnav, k9s logs |
| **Pick one of many** | Fuzzy overlay, preview pane, returns to the caller | Browsing unknown items | fzf, atuin, telescope |
| **Edit structured text** | Modal editing *with* which-key popups; `$EDITOR` hand-off | Home-grown text widgets | helix, vim, `git rebase -i` |
| **Occasional use by non-experts** | F-key/Ctrl footer always visible, menus, no modes | vim-style silence | nano, mc, htop, menuconfig |
| **Explore tabular data** | Column ops on single keys, frequency tables, undo | Too many single-letter commands without a palette | visidata, Harlequin |
| **Drive long-running automation** | Background jobs in-screen, live status per item, loud stops, durable reasons | Handing over the terminal; a job ending as a disappearing badge | tmux, zellij, lazygit's async ops |

---

## 9. For `dfs_tui.py` specifically

Where it already matches what works: Esc goes back one level and never quits, `q` is
the only exit (§2); chains run in the background with the screen kept (§4); follow
breaks on a deliberate scroll (§4); a stopped walk leaves a dim reason where the badge
was (§4); the footer is per context and computed from the same place as the action
(§2); the screen is a view over `dfs_run.sh` and `dfs_state.py`, and anything that
takes the author's words hands off to the runner (§7); decisions are pure functions
beside the drawing (§7).

What came of the gaps, taken 2026-09-30 in the order below (README, "The screen's
conventions"; tests in `scripts/test_dfs_screen.py`):

1. **`?` lists every key**, the part for the pane you were on first, and ⏎ presses the
   one under the cursor in the pane it belongs to. `KEYS` is the table, and a test
   fails when `act` takes a key it doesn't list. The footer keeps `? keys` to the end.
2. **A background chain's message is its runner line** (`$ scripts/dfs_run.sh W7 5 —
   in the background…`). The console already opened with it and `shell()` already
   printed it; the status line was the gap.
3. **`/` searches the current pane** (items, tree nodes, runs, artefacts, keys, or the
   text of a pane with no cursor). Lower case matches either case, it wraps and says
   so, and an empty `/` repeats the last search. `n`/`N` were taken (`n` pins the walk).
4. **`-` `=`/`+` `<` `>`** are aliases for `[` `]` `{` `}`. They're unshifted, or have
   their own key, on US, German and French layouts. `,`/`.` were left alone because
   `model-explorer.py` uses them.
5. **No change.** `A` and `K` confirm, `a`/`f` cancel on an empty compose, and `R`/`C`
   hand the terminal to interactive passes. `n` lowers a running chain's cap without
   asking, but only ends it after the session in flight, which is the behaviour it
   was built for.
6. **Fixed.** `reselect` re-finds the item, tree-node and run cursors by id on every
   reload, and falls back to the row only when the thing is gone. The run list had the
   same bug, since a new live chain sorts first.
7. **Done.** `draw` parks the hidden hardware cursor on the row the keys drive.

Found while driving it: **Esc in a prompt was typed as a character**, so Esc then ⏎ at
`cap [5]:` started a chain with the cap `"\x1b"`. Esc now cancels any prompt (one ⏎
later, since `getstr` needs it), and the cap must be a positive whole number.

### The second round (2026-09-30, after the hands-on pass)

Built from §0 and the list in the reply that followed it (README, "The screen's
conventions"):

- **The look**, from §0's finding that calm is chrome-versus-content contrast: grey
  rules and labels, plain text, one accent, softened status colours, fzf's band-and-bar
  selection, no painted background, and a light palette chosen by asking the terminal.
  Checked as rendered screenshots (pyte to PNG), dark at 110 and 170 columns, light, and
  64×22. The header drops whole segments when narrow instead of cutting `next W2` to
  `next W`.
- **An activity column from 150 columns**: the live chain's console tail, then the
  activity log, persisted in `.dfs/runs/activity.log`. The `h` pane shows it at any width.
- **`:` with names** (tig), completion listed as you type (helix), Tab to take one.
  Only an exact name or the start of exactly one runs, after a fuzzy match started
  `review-terminal` for `:reviwe`.
- **One line editor for every prompt**, on the footer row (htop, fzf): `/` moves as you
  type, and Esc cancels at once.
- **`m`** (needs you), **`z`** (undo an order move, refused over another session's edit),
  **the walk plan** while `w` asks for a budget, and **the bell** on a chain ending or
  the walk stopping by itself (`DFS_NOTIFY`).
- Found on the way: the screen crashed at start on a terminal without colour or cursor
  control (vt100). pyte, used for the screenshots, lacks `CSI T`/`S` (scroll), which
  curses uses when rows shift, so its snapshots can show stale rows a real terminal
  doesn't; `vt100` and a patched pyte agree the screen is right.

