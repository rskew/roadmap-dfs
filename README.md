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
(`DFS_ADOPT=1 git commit`). While git holds no task file, the guard then judges that commit against nothing:
every finding on what the files already hold (a rule added after a task was written, a file over the size
budget) is printed as grandfathered and none refuses the commit, and an artefact page a task names is committed
only if you stage it. Without `DFS_ADOPT` the commit is judged in full, since git holding no task file is also a
new roadmap's state and its first node is a session's; a refusal there names the variable. Every later commit is
judged only on what it changes, so an old rule finding never blocks one. The size budget is the exception: a file
adopted over it is refused ("GREW it") by the next commit that adds to it, a session's log line included, until
`tree.py shelve` brings it under. A task file added once git already holds others is new work and is
judged in full.

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

## Parallel walks

A walk takes a budget of sessions and, optionally, a number of chains to run at once: `w` on the
screen reads `20` or `20 3` (⏎ takes the last walk's numbers), `:walk 20 3` does the same, and the
web page's Start form has a "Chains at once" field. The default is 1, one chain at a time as
before. With more, each free slot takes a task of its own, the walk's continuation first and then
the order, so separate branches run together, each in its own worktree. Where no worktree is cut
(the checkout is off `main`/`master`, has no commit yet, or is not a repo) a chain would work in
the one checkout, so the walk runs a single such chain at a time, and says so when it starts; a
task that already has a worktree still runs beside it. A task nested under another in `order.md`
still waits until its parent (any open, non-draft ancestor) is done and the parent's chain has
merged it, and a task that already has a chain, started by hand included, is never given a
second. The chains share the one budget: each starts with a cap that, summed over the live chains,
never exceeds the sessions left, and the walk's header names every live chain. A stop, or turning
the walk off, ends every chain after the run it is in. Merges and worktree cuts run one at a time
under a lock in the git common directory, so two chains finishing together merge in turn.

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

## The web page on a phone

`python3 scripts/web.py` serves the roadmap as a phone-sized page and an installable app.
There is no login, so it listens on this machine only until you say otherwise.

Each project can look its own way, set in the project dialog (tap the project's name): a colour for the top
of the page (`.dfs/theme`), a background colour for light mode and another for dark (`.dfs/background`), and
a background picture for each of light and dark mode (`.dfs/background-image-light` and `-dark`, each a png,
jpeg, webp or gif of 8 MB or less). The page draws the picture of the mode it is in, fixed behind itself under
a veil of the page's own paper at 90%, so text stays readable on any picture (body text at least 10:1 and
muted text at least 4:1 on a pure black or white pixel), and the light/dark switch changes it; "No picture"
removes one mode's. They are plain files, so a picture put there by hand works too (`.dfs/background-image`
serves a mode that has none of its own), and they travel with the repo like the name and the colour.

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
