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

## The web page on a phone

`python3 scripts/web.py` serves the roadmap as a phone-sized page and an installable app.
There is no login, so it listens on this machine only until you say otherwise.

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
one. Closing the screen, or stopping `web.py`, ends the chats and the page's turns in flight.
