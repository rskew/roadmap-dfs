#!/usr/bin/env python3
"""The claude hook that hands the author's messages to a running work session.

    inbox.py post    as a PostToolUse hook: the next model call sees the messages
    inbox.py stop    as a Stop hook: a session about to end takes one more turn for them
    inbox.py pre     as kiro's preToolUse hook: a tool call is held back, with the messages as its result
    inbox.py --settings   the `--settings` JSON that installs both, as run.sh passes it
    inbox.py --codex-args   the same for codex: the flags, one per line, that go BEFORE `exec`
    inbox.py --opencode-config   the same for opencode: its `OPENCODE_CONFIG_CONTENT`, naming the plugin
    inbox.py --kiro-agent DIR   the same for kiro: writes the agent `dfs-inbox` under DIR/.kiro/agents

`run.sh` starts each claude work session with both hooks and `DFS_INBOX`, the run
directory's `inbox/`, which the screen and the page write into (`runs.py`,
`post_message`). With nothing waiting the hook prints nothing and the session goes on
as if it were not there.

⚠️ CLAIMED BY RENAME, THEN READ. Each message is moved to `delivered/` before it is
read, and a move that fails means another hook took it, so two hooks running at once
(parallel tool calls each fire one) never hand over the same message twice. Moved,
it counts as delivered: the screen shows it so from then on.

codex takes the same two hooks as `-c` overrides (`codex_args`), with the same JSON out.

opencode 2.1.1 has no hook for a session about to end, but its plugin (`opencode_inbox/`) can send the
session a prompt: it polls the inbox, claims each message with `inbox.py post` and hands it over with
`ctx.session.prompt(..., delivery: "steer")`, which opencode takes at its next step or, if the model is
on its last answer, as one more turn. Only a message that arrives after the session has ended stays waiting.

kiro-cli 2.31.1 runs hooks in a custom agent but hands the model the output of none that fires after
a tool call or at a stop; only a preToolUse hook that exits 2 reaches it, as the held call's result
(probe_kiro_hooks.py). So `pre` claims the waiting messages and exits 2 with them on stderr: the
call does not run, the model reads them, and asks for the call again. A message that arrives after
the session's last tool call stays waiting.

Checked against a real claude-code 2.1.308 under `-p`: PostToolUse's
`additionalContext` is in the very next request, and a Stop hook's `block` makes the
session answer its `reason` (test_inbox.py).
"""
import json
import os
import shlex
import subprocess
import sys

HEAD = "The author sent this to the running session from the roadmap's screen or page:"


def claim(box):
    """Move every waiting message to `delivered/` beside `box` and return their texts,
    oldest first."""
    try:
        names = sorted(n for n in os.listdir(box) if n.endswith(".txt") and not n.startswith("."))
    except OSError:
        return []
    done = os.path.join(os.path.dirname(os.path.abspath(box)), "delivered")
    os.makedirs(done, exist_ok=True)
    texts = []
    for name in names:
        dest = os.path.join(done, name)
        try:
            os.rename(os.path.join(box, name), dest)
            os.utime(dest)      # its mtime is now when it was delivered
            with open(dest) as fh:
                texts.append(fh.read().strip())
        except OSError:
            continue
    return [t for t in texts if t]


def answer(event, texts):
    """The hook's JSON for `event` (`post` or `stop`), or None when there is nothing."""
    if not texts:
        return None
    said = HEAD + "\n\n" + "\n\n---\n\n".join(texts)
    if event == "stop":
        return {"decision": "block", "reason": said}
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": said}}


HELD = ("The author sent this to the running session from the roadmap's screen or page, and this tool call "
        "was held back so you read it first. Take it into account, then run the tool call again:")


def held(texts):
    """What kiro's model reads in place of the held tool call's result, or None with nothing waiting."""
    return HELD + "\n\n" + "\n\n---\n\n".join(texts) if texts else None


KIRO_AGENT = "dfs-inbox"


def kiro_agent():
    """The kiro agent that installs the `pre` hook. `tools: ["*"]` keeps every tool the default agent
    gives a work session. A hook is found only in an agent: the default one runs none."""
    me = shlex.quote(os.path.abspath(__file__))
    return json.dumps({"name": KIRO_AGENT, "description": "the roadmap's work session, reading the run's inbox",
                       "tools": ["*"], "hooks": {"preToolUse": [{"command": "python3 %s pre" % me}]}}, indent=2)


def install_kiro_agent(directory):
    """Write the agent to `.kiro/agents/` in `directory`, the only place `--agent dfs-inbox` finds it besides the
    home directory, and keep it out of the repository's status through `info/exclude` when that is a repository."""
    agents = os.path.join(directory, ".kiro", "agents")
    os.makedirs(agents, exist_ok=True)
    path = os.path.join(agents, KIRO_AGENT + ".json")
    with open(path, "w") as fh:
        fh.write(kiro_agent() + "\n")
    try:
        exclude = subprocess.run(["git", "-C", directory, "rev-parse", "--git-path", "info/exclude"],
                                 capture_output=True, text=True, check=True).stdout.strip()
        exclude = os.path.join(directory, exclude)
        line = "/.kiro/agents/%s.json" % KIRO_AGENT
        os.makedirs(os.path.dirname(exclude), exist_ok=True)
        have = []
        if os.path.exists(exclude):
            with open(exclude) as fh:
                have = fh.read().splitlines()
        if line not in have:
            with open(exclude, "a") as fh:
                fh.write(("" if not have or have[-1] == "" else "\n") + line + "\n")
    except (OSError, subprocess.CalledProcessError):
        pass
    return path


def settings():
    """The claude `--settings` JSON naming this file as both hooks."""
    me = shlex.quote(os.path.abspath(__file__))
    hook = lambda ev: [{"hooks": [{"type": "command", "command": "python3 %s %s" % (me, ev)}]}]
    return json.dumps({"hooks": {"PostToolUse": hook("post"), "Stop": hook("stop")}})


def codex_args():
    """The flags that install both hooks in `codex exec`: hooks switched on, the trust prompt
    for a hook it has not seen bypassed, and each event as a TOML array of tables."""
    me = os.path.abspath(__file__)
    hook = lambda ev: '[{hooks=[{type="command",command=%s}]}]' % json.dumps("python3 %s %s" % (shlex.quote(me), ev))
    return ["-c", "features.hooks=true", "--dangerously-bypass-hook-trust",
            "-c", "hooks.PostToolUse=" + hook("post"), "-c", "hooks.Stop=" + hook("stop")]


def opencode_config(env):
    """`OPENCODE_CONFIG_CONTENT` for opencode, `env`'s own (a JSON object, or none) with the plugin directory
    added to its `plugins`; opencode joins that with the plugins of its other config."""
    cfg = json.loads(env.get("OPENCODE_CONFIG_CONTENT") or "{}")
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "opencode_inbox")
    cfg["plugins"] = list(cfg.get("plugins") or []) + [here]
    return json.dumps(cfg)


def main(argv):
    if argv[1:] == ["--settings"]:
        print(settings())
        return 0
    if argv[1:] == ["--codex-args"]:
        print("\n".join(codex_args()))
        return 0
    if argv[1:] == ["--opencode-config"]:
        print(opencode_config(os.environ))
        return 0
    if len(argv) == 3 and argv[1] == "--kiro-agent":
        print(install_kiro_agent(argv[2]))
        return 0
    if len(argv) != 2 or argv[1] not in ("post", "stop", "pre"):
        print("usage: inbox.py post | stop | pre | --settings | --codex-args | --opencode-config | --kiro-agent DIR", file=sys.stderr)
        return 2
    try:
        sys.stdin.read()        # the hook's own input; drained so claude's write never blocks
    except (OSError, ValueError):
        pass
    box = os.environ.get("DFS_INBOX")
    if argv[1] == "pre":
        said = held(claim(box)) if box else None
        if said:
            print(said, file=sys.stderr)
            return 2        # kiro: the tool call does not run, and this is its result
        return 0
    out = answer(argv[1], claim(box)) if box else None
    if out:
        print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
