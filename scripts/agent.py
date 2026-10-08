#!/usr/bin/env python3
"""Which claude-code (and codex, and kiro-cli) run.sh launches: one pinned nixpkgs revision.

    agent.py --rev      print the pinned revision; with none pinned yet, pin the
                            one nix already has cached, or resolve one if it has none
    agent.py --update   resolve nixpkgs now, BUILD claude-code at it, then pin it,
                            and print what changed

⚠️ THE PIN MOVES ONLY AFTER THE BUILD. tui.py runs `--update` in the background
when it starts, and a `c` pressed meanwhile reads the OLD pin and starts at once. Pinning
first would hand that `c` a revision whose claude-code is still downloading, so it
would sit and wait, which is what the update is in the background to avoid.

⚠️ IN `.dfs/runs/`, NOT COMMITTED. A background write to a tracked file dirties the
tree, and a chain's first session commits or stashes whatever dirt is not its own
(state.py --foreign-dirt), so the pin would end up in some task's history. The
runs directory is already gitignored in every project that has a roadmap. It is also
per MACHINE in effect, which is right: what the pin names has to be in this nix store.

A chain reads the pin once, when run.sh starts, so an update never changes the
agent under a chain halfway through.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths as dfs_paths  # noqa: E402

NIXPKGS = "github:nixos/nixpkgs"
REV_LEN = 40
NIX_TIMEOUT = 900  # a first build of a new revision is a download, not a compile


def pin_path() -> Path:
    return dfs_paths.runs() / "agent-nixpkgs.rev"


def read_pin():
    try:
        rev = pin_path().read_text().strip()
    except OSError:
        return None
    return rev if len(rev) == REV_LEN and all(c in "0123456789abcdef" for c in rev) else None


def write_pin(rev):
    path = pin_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".agent-nixpkgs.")
    with os.fdopen(fd, "w") as f:
        f.write(rev + "\n")
    os.replace(tmp, path)  # atomic: a run.sh reading it sees the old rev or the new one


def nix(*args, timeout=NIX_TIMEOUT, stream=False):
    """Run nix and return its stdout. `stream` lets its stderr through to whoever is
    watching instead of capturing it: a caller with a terminal (`--rev`, from a key
    pressed in the screen) should see nix working or failing, not a blank wait."""
    env = dict(os.environ, NIXPKGS_ALLOW_UNFREE="1")
    return subprocess.run(["nix", *args], stdout=subprocess.PIPE,
                          stderr=None if stream else subprocess.PIPE, text=True,
                          timeout=timeout, env=env, check=True).stdout


def resolve(*flags, **kw):
    meta = json.loads(nix("flake", "metadata", *flags, "--json", NIXPKGS, **kw))
    return meta["locked"]["rev"]


def version(rev):
    return nix("eval", "--impure", "--raw", "%s/%s#claude-code.version" % (NIXPKGS, rev))


# A launch waits on this, with a person at the other end of it: a minute is a long time
# to wait to be told nix cannot reach github, and fifteen is a hang.
REV_TIMEOUT = 90


def current():
    rev = read_pin()
    if rev:
        return rev
    try:
        rev = resolve("--offline", timeout=REV_TIMEOUT, stream=True)
    except subprocess.CalledProcessError:
        rev = resolve(timeout=REV_TIMEOUT, stream=True)
    write_pin(rev)
    return rev


def update():
    old = read_pin()
    rev = resolve("--refresh")
    if rev == old:
        return "claude-code %s, nixpkgs unchanged" % version(rev)
    nix("build", "--no-link", "--impure", "%s/%s#claude-code" % (NIXPKGS, rev))
    new_v = version(rev)
    old_v = version(old) if old else None
    write_pin(rev)
    if old_v == new_v:
        return "claude-code %s, pinned to newer nixpkgs %s" % (new_v, rev[:12])
    return "claude-code %s -> %s" % (old_v or "unpinned", new_v)


def reason(detail):
    """The cause in nix's stderr, on one line. Its LAST line is the end of a response
    body (`404: Not Found)`, or GitHub's `{"message":"API rate limit exceeded"...}`),
    which names neither the url nor the revision that failed; the cause is the last
    `error:` clause, with the first line of the body after it."""
    lines = [ln.strip() for ln in str(detail).strip().splitlines() if ln.strip()]
    errs = [i for i, ln in enumerate(lines) if "error:" in ln]
    if not errs:
        return lines[-1] if lines else ""
    i = errs[-1]
    out = lines[i][lines[i].rindex("error:"):]
    if lines[i + 1:i + 2] == ["response body:"] and i + 2 < len(lines):
        out += " — " + lines[i + 2].rstrip(")")[:160]
    return out


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        if arg == "--rev":
            print(current())
        elif arg == "--update":
            print(update())
        else:
            print(__doc__, file=sys.stderr)
            return 2
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError,
            ValueError, KeyError) as e:
        detail = getattr(e, "stderr", None) or str(e)
        print("dfs_agent: %s failed: %s" % (arg, reason(detail) or e), file=sys.stderr)
        if arg == "--rev":
            print("dfs_agent: set AGENT_NIXPKGS_REV=<rev> (or the agent's own command, "
                  "CLAUDE_CMD and the like) to skip asking nix", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
