# roadmap-dfs

todo list for agents:
- Blockers are raised for your review
- Assumptions are declared
- Agent sessions are short and non-interactive, for token and cache efficiency

## Usage

```sh
nix run github:rskew/roadmap-dfs#init                    # setup repo
nix run github:rskew/roadmap-dfs                         # the tui
nix run github:rskew/roadmap-dfs#sandbox exec dfs-tui    # the tui in a sandbox
```

## Adopting an existing roadmap

A `.dfs` that already holds tasks, written before the guard (the pre-commit hook `init` installs) saw them, is
adopted by committing its task files in one commit with the hook on and `DFS_ADOPT=1` in the environment
(`DFS_ADOPT=1 git commit`) on the default branch (on any other branch, an item's `dfs/<task>` where a session
commits a new task's first node or one it works in place, the variable does nothing). The guard then judges each staged task file git does not hold yet against nothing,
whether git holds no task file or holds some (a blanket `.*` rule with a few force-added): every finding on
what the file already holds (a rule added after a task was written, a file over the size budget) is printed as
grandfathered and none refuses the commit, and an artefact page a task names is committed only if you stage
it. A task file git already holds is judged on what the commit changes, with or without the variable. Without
`DFS_ADOPT` the commit is judged in full, since a task file git lacks is also new work and its first node is a
session's; a refusal that adds such a file names the variable (on the default branch). Every later commit is judged only on what it
changes, so an old rule finding never blocks one. The size budget is the exception: a file adopted over it is
refused ("GREW it") by the next commit that adds to it, a session's log line included, until `tree.py shelve`
brings it under.

## Where an item is worked

Each item is worked on its own branch, `dfs/<task>`, in its own git worktree under
`.dfs/worktrees/<task>`, so a walk never switches the checkout you have open (the screen
and the web page read the task through its worktree). The first run cuts the branch from
`main` (or `master`). When the review passes, or you accept the tree, the branch is
merged into the default branch with `--no-ff` (local, never pushed) and the worktree and
branch are removed. A rebase that conflicts reopens the item with a node to resolve it;
uncommitted edits of yours that the merge would overwrite, or a merge, cherry-pick, revert
or rebase of yours left unfinished in the checkout, raise instead and are left alone. A
gitignored `.dfs/` is worked the same way: the worktree links to your checkout's ignored
planning files, so the screen and the sessions read and write one copy. That includes task
files and `order.md` that a blanket rule such as `.*` hides but you force-added: they are
edited in your checkout through the link and left modified and uncommitted there after the
merge, for you to commit. Gitignore it only by a rule naming it (`.dfs/`), with nothing in
it tracked: a broader rule such as `.*` or `*.html`, or files force-added past one, leaves
part of the roadmap out of git, and `init`, each chain's start and the screen's header
(`git hides .dfs`, the rule under `h`) say so.

There is no branch when the checkout is off `main`/`master` or the repo has no commit yet:
the item is worked in place, on whatever is checked out, and nothing is merged. A task that
already has a worktree stays in it off `main`/`master`, and its merge raises until you are
back on the default branch.

## GitHub mode

Write the git remote to push to into `.dfs/github` (an empty file means `origin`) and a finished task is
no longer merged: a passing review, or your accept, **holds** it, its branch `dfs/<task>` and worktree kept
and `main` untouched, and the task reads done, "held unmerged for its pull request". Remove the file for
today's local merge. The mode needs `.dfs` committed (a gitignored roadmap has no task file on the branch
to push) and the GitHub CLI logged in (`gh auth login`); `DFS_GH` names another command to run as `gh`.

- **Open PR** is your mark that the held task is ready: `P` on the screen's item list, the Open PR button on
  the task's page on the web, or `python3 scripts/github.py pr <task>` from the main checkout. It rebases
  `dfs/<task>` onto the default branch (a conflict reopens the task with a node, as a merge's does), pushes it
  with `--force-with-lease` and runs `gh pr create` with the task's title, Summary and nodes as the body. The
  request's url is logged as a `pr` entry and shown on the task (the screen's tree pane, the page's Pull
  request link). It is not the draft/ready mark, which only says whether the walk may pick a task up. A
  second mark is refused.
- **The watch** reads each open pull request every five minutes while a walk runs (it stops when the
  walk's budget is spent or the walk is stopped; `github.py watch [<task>]` looks by hand and, like the
  walk, passes over a task a work chain is on). Each new conversation comment, review with a body and line
  comment becomes one open node at the root of the task, as a reviewer's finding does: who wrote it, the
  text, the file and line, its link, and `(github <kind> <id>)` (ending the node's Approach) by which it
  is never added twice. An approval with no body adds nothing; every other comment does, your own
  replies and bots' included. Delete the node of a comment you do not want acted on and it is
  dismissed (shelved or not): the watch does not add it again, and editing such a node keeps its mark.
  The task is open again, so the walk works and raises on the node as on any other; nothing is posted
  back to GitHub.
- **The push**: when that task is finished anew (the fix reviewed, or accepted) the hold pushes `dfs/<task>` to
  the pull request, so it shows reviewed work and not a half-done node. If the branch moved on GitHub (a
  suggestion committed there) the push is refused, and the task reopens with a node asking for a rebase onto
  the remote branch.
  A push that fails for the network or a credential (it never waits on a prompt) reopens it with a node
  saying so instead.
- **Merged or closed on GitHub**: the watch also reads the pull request's state. When it is merged, the watch
  fetches the remote and fast-forwards the default branch to it, carries the task's file (with the log entries
  no commit holds) across, removes the worktree and `dfs/<task>`, and logs `pr <url> merged`, so the task
  reads done and merged and is no longer watched. The default branch must match its upstream, so the landing
  commits nothing to it: the task's file is left modified and uncommitted in the main checkout, and the
  fast-forward is all it does to the branch. That needs the main checkout on the default branch with no merge
  or rebase of yours unfinished, and nothing of yours that the pull request overwrites; if the branch cannot
  be fast-forwarded (a commit only here, an edit in the way) it raises once, asking you to move your commits
  to a branch for a pull request and `git pull --ff-only <remote> <default>`, and lands on the watch after
  that. A branch holding commits the pull request never got, or a worktree holding changes beyond the task's
  files, is kept as `dfs-merged/<task>` (`-2`, `-3` … if taken) and not deleted. Comments still open when it
  merges are worked on a fresh branch cut from the updated default branch, and their finished fix is held
  for a new pull request: the Open PR mark applies again, as the merged one no longer counts as the task's
  pull request. A pull request **closed unmerged** is a raise (logged once as `pr <url> closed`): reopen it
  on GitHub and the watch carries on, or tell the walk to drop the task. When the cause of a raise the
  landing or the watch made is put right (the pull request lands, or is reopened), the tool logs it
  `settled`: the task stops being blocked without an answer, so no work session is started on it.

## Parallel walks

A walk takes a budget of sessions and, optionally, a number of chains to run at once: `w` on the
screen reads `20` or `20 3` (⏎ takes the last walk's numbers), `:walk 20 3` does the same, and the
web page's walk box has a "Chains" field beside its Budget. The default is 1, one chain at a time as
before. With more, each free slot takes a task of its own, the walk's continuation first and then
the order, so separate branches run together, each in its own worktree. Where no worktree is cut
(the checkout is off `main`/`master`, has no commit yet, or is not a repo) a chain would work in
the one checkout, so the walk runs a single such chain at a time, and says so when it starts; a
task that already has a worktree still runs beside it. A task nested under another in `order.md`
still waits until its parent (any open, non-draft ancestor) is done and the parent's chain has
merged it, and a task that already has a chain, started by hand included, is never given a
second. The chains share the one budget: each starts with a cap that, summed over the live chains,
never exceeds the sessions left, and the walk's header names every live chain. A stop (of the walk, or of any one
of its chains), or turning the walk off, ends every chain after the run it is in. Merges and worktree cuts run one at a time
under a lock in the git common directory, so two chains finishing together merge in turn.

## Choosing the models

A claude session runs on one of two models: the **plan** model for the session that opens a task with
no nodes and for every critic and review (opus unless told otherwise), and the **implement** model for
every other work session (sonnet). The walk can choose both: `:model plan opus` and
`:model implement sonnet[1m]` on the screen, the walk box's "Plan model" and "Implement model" fields
on the web page. `:model plan default` leaves claude to its own default, `:model plan none` takes the
choice away (the runner's opus and sonnet again), and `:model` alone shows the choices. They are kept in
the run root's `models` file, shared by the screen and the page, and reach every chain the walk starts afterwards
as `PLAN_MODEL` and `IMPLEMENT_MODEL`, the variables `run.sh` reads (set them yourself for a run by
hand). A name is letters, digits and `. _ : [ ] -`, not starting with `-`. They apply to claude only:
codex, kiro and opencode read their model from their own config. The walk header does not show the
choices (it has no room); `:model` alone does.

## Running the sandbox as a daemon

`sandbox daemon [command args]` runs the command (default: the repo's `CONTAINER_APP_CMD`, from `.container.env`)
detached, with the engine's `--restart unless-stopped`, so it comes back after a reboot. The nix sidecar gets the same
policy. The engine must itself start at boot (`systemctl enable docker`; rootless podman needs
`podman-restart.service`). The container mounts the host's `/nix/store` and runs the tool from a store path, so keep
that path from garbage collection (a GC root, e.g. `nix profile install`) or the restart after a GC fails. Running it
again replaces the daemon; `sandbox stop` removes it and its sidecar for good. Only `daemon` itself is detached: the
other modes (`shell`, `claude`, `exec`, `run`, `app`) stay throwaway, and `daemon` does not start the background app
that `exec` does, since its command is the container's main process.

```sh
nix run github:rskew/roadmap-dfs#sandbox daemon dfs-web   # the web page, up from now on
docker logs <repo>-dev                                    # its output (podman logs with CONTAINER_ENGINE=podman)
nix run github:rskew/roadmap-dfs#sandbox stop
```

## Stop breaks

A break in `order.md` (a `---` line between root branches) holds everything after it until
everything before it is done, and a walk carries on through it once it opens. Written
`--- stop`, the break also **ends the walk** there: a walk that had to wait at it turns itself
off once everything before it is done and its chains have ended, saying which break it stopped
at, so you can review that work first. The walk you start next finds the break open and goes
past it; a task you pin with `n` goes past it too. `B` on the tui's item list, or the Stop break / Plain break button beside Break
on the web page's Reorder list, makes the break above the selected task a stop break, or plain
again (the tui adds one, after asking, where there is none); `b` still takes a break away.

## Drafts

A task can be a **draft**: still open, but the walk never picks it as its next item, never carries on into it
and does not wait on it at a fence. Create one with the Draft box on the web page's new-task form (or `draft: true`
to `/api/new`, or `y` at `run.sh --open`'s draft prompt), and mark an existing task a draft, or ready again, with `s`
on the tui's item list or the Draft button on a task's page. Each is a `draft` or `ready` entry in the task's log,
the later one winning. A draft is worked by hand: `n` on the tui, or the pin on the web page, makes it the walk's next chain, and `run.sh <task>` runs its
sessions as any open task's. When that chain ends the walk passes it over again, until it is marked ready; a usage-limit hold does not end it, and
the walk resumes the pinned draft when the hold is over.

## Deleting

An item or a node can be deleted from the tui (`D` on the item list, or on a node in the tree) and from the web
page (Delete task on a task's page, Delete on a node's sheet), each after a confirmation. A deleted **item**
is recoverable: its files move under `.dfs/archive/deleted/` (move them back to restore it), it leaves
`order.md` with its children taking its place, and its number is not handed out again. A deleted **node** goes
with everything under it, whatever its status, and is logged: a `delete` entry in the task's log keeps their
text verbatim, a work session's briefing shows it, and the numbers are not reused. A task a live session is
working, or a node an unanswered raise names, is refused until that is settled.

## Archiving

A finished, reviewed and pushed task can be archived from the web page's task list (the box beside its row). Its
files move under `.dfs/archive/archived/` (move its `items/` back to `.dfs/items` and its `archive/` to `.dfs/archive/items` to restore it), it leaves `order.md`, and the walk
no longer sees it. The list's **Show archived** button (the choice is kept in the browser) draws the archived tasks
after the live ones, marked `archived`; one opens read-only, with its tree, nodes and artefacts but no
answer, accept, edit, add or delete control. The screen (`tui.py`) does not list archived tasks.

## The web page on a phone

`python3 scripts/web.py` serves the roadmap as a phone-sized page and an installable app.
There is no login, so it listens on this machine only until you say otherwise.
Installed, the page gets the browser's own pull-to-refresh, on the task list too: the page cancels no touch and sets
no `overscroll-behavior` or `touch-action` on the page or the bar, so it has no gesture of its own.

On a screen 900px wide or more, the task list has a **Table** button above it (the choice is kept in the browser). It
draws one line per task, with a column each for status, review, branch and run, in place of the two-line card; a
task that is blocked or running keeps its reason on a line under it. The phone, Reorder and the archived tasks
always draw the card, and so does the list when a task is open beside it and the column is under 820px.

Each project can look its own way, set in the project dialog (tap the project's name): a colour for the top
of the page (`.dfs/theme`), a background colour for light mode and another for dark (`.dfs/background`), and
a background picture for each of light and dark mode (`.dfs/background-image-light` and `-dark`, each a png,
jpeg, webp or gif of 8 MB or less). The page draws the picture of the mode it is in, fixed behind itself under
a veil of the page's own paper at 90%, so text stays readable on any picture (body text at least 10:1 and
muted text at least 4:1 on a pure black or white pixel), and the light/dark switch changes it; "No picture"
removes one mode's. They are plain files, so a picture put there by hand works too (`.dfs/background-image`
serves a mode that has none of its own), and they travel with the repo like the name and the colour.

The project dialog also has **Claude account…**, for when the login every run and chat uses has expired or its
account has hit the weekly limit. It says who is signed in (email and plan); Sign in shows claude's own link, you
open it, sign in to the account you want and paste the code the page shows back into the box, and the page then says
who is signed in now, or claude's own words if it refused the code. It runs `claude auth login` through
`scripts/run.sh --claude-auth login` (the same pinned claude as the runs), so it replaces the one stored login, the
terminal's and the sandbox's included, from the next run on. In the sandbox that holds when the host's `~/.claude` and
`~/.claude.json` existed when `sandbox.sh` started the container (it mounts them only if present); with no host login
yet, a sign-in from the page lands in the container's own home and is lost when it restarts. It does not keep several
accounts or any token of its own. Like starting the walk, it is open to anyone who can reach the page, and `--no-walk`
turns it off. The code exchange itself, a valid code signing claude in, has only been tried against claude's refusal of
a made-up one, so your first real sign-in is its first full run.

Each live node on a task's page carries a mark before its id, so its state does not rest on the colour
of its left rule: a tick for a confirmed node, a filled circle for an open node a session has started, an
empty circle for one not yet begun. A node you confirmed with a correction, which no session built, has no tick: it
is `◇` in the terminal and says "confirmed by you, not built" in its chip. A refuted or parked node has no mark and says so in its chip. The
terminal's tree draws the same marks (`✓`, `●`, `○`). *Started* is a node status (`Status: started`) that
everything else reads as open: a session runs `python3 scripts/tree.py start <task> <node>` before it
changes code for the node, which edits the task file and commits nothing, and the node's own commit
replaces it with confirmed or refuted. A started node is seen only while a session is on it, or after
one was cut off mid-node.

A task's page lists the chains that worked it under **Runs**; tap one to read its console log
(the same `console.log` the terminal's `l` opens), which keeps growing while the chain is live.
Each session the chain ran is a tab beside the console, drawn as the terminal's log pane draws
it: what the agent said, the commands it ran and what they printed. Under codex that is where
the work is, since its console holds only the runner's own lines.
Whoever can reach the page can read those logs too.

## Sending a message to a running session

While a chain is live you can say something to the session it is running. On the terminal, `M` in the
chain's log asks for a line; on the page, a live run's console or session log has a one-line box and Send
under it. Either leaves the line in the run's inbox, `.dfs/runs/<run>/inbox/`, and the log lists it as
**waiting**, then **delivered** with the time a hook handed it over. A message left in an inbox when the
chain ended reads **never read**, and nothing can be sent to a chain that has ended.

A message never cuts into a tool call or an answer already running; it lands at the next point the agent
lets one in, and what that is differs by agent (`scripts/inbox.py`, the hook every one of them runs):

| agent | when it lands | a message after the last tool call |
|---|---|---|
| claude | after the tool call that is running, as that call's extra context; or, if the session was about to end, as one more turn | one more turn |
| codex | the same: its PostToolUse and Stop hooks | one more turn |
| opencode | the plugin in `scripts/opencode_inbox/` polls the inbox and sends the session a prompt: after the tool call that is running, or as one more turn if the model is writing its last answer | stays waiting once the session has ended |
| kiro | kiro hands the model the output of no hook after a tool call, so a preToolUse hook **holds the next tool call back** with the message as its result; the model reads it and asks for the call again | stays waiting: with no further tool call there is nothing to hold back |

A kiro run also starts under a custom agent, `dfs-inbox`, which `inbox.py --kiro-agent` writes to
`.kiro/agents/` in the session's directory (git is told to ignore it); the agent keeps the default one's
tools (less one that only the custom agent had). Every session a chain runs (work, critic and review)
takes a message; `run.sh --review` (the author's answering pass) and the chat sessions are not part of a
chain and take none. The hooks name the run's inbox in their own command, so nothing else the agent
runs can read it (opencode's plugin, inside its process, reads `DFS_INBOX`).

A message is for the session you are watching, not for a subagent it starts: claude and codex mark a
subagent's hook calls, and the hook then leaves the message waiting for the main session's next tool call
or stop. kiro marks nothing, so the `dfs-inbox` agent cannot be invoked as a sub-agent; any other agent the
repository defines runs without the hook.

Chrome on Android offers Install only on a secure origin: https, or `localhost`. Over plain
`http://<lan address>:8765` the page works, but there is no Install. Two ways to https:

```sh
# a TLS proxy on the tailnet: the server stays on loopback, the proxy forwards its own name
python3 scripts/web.py --allow-host <machine>.<tailnet>.ts.net
tailscale serve --bg 8765

# or serve https yourself, with a certificate the phone trusts (mkcert, a real domain)
python3 scripts/web.py --host 0.0.0.0 --cert cert.pem --key key.pem
```

A self-signed certificate the phone does not trust will not do: Chrome refuses to install
from it. `--allow-host` can be given more than once, or as DFS_WEB_ALLOW_HOST, comma-separated;
the page the terminal screen serves takes it only as DFS_WEB_ALLOW_HOST.

A chat the terminal screen runs in the background is ended (its conversation is kept, and the
next message resumes it) after DFS_CHAT_IDLE seconds without output, default 1800; 0 never ends
one. Quitting the screen ends the chats with SIGTERM, as ctrl-c ends a claude in a terminal, and
waits for them.

Every chat keeps a log, `.dfs/runs/chat/<scope>.log` (the scope is a task id or `project`; it moves to
`.log.1` at 1 MB). It records when the chat started and what it ran, each message the page sent and
whether it was typed, refused or dropped, each headless turn with its duration and, if it failed, why,
and when the agent exited (code or signal) with the last of what it had drawn, and who closed it (idle,
new chat, the screen ending). A message followed by DFS_CHAT_STALL seconds (default 90; 0 off) of silence
from the agent is logged once with its last screen, which shows a dialog it is stuck at or a hang. Read the
log of a chat that stopped answering first.
