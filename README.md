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
