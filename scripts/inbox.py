#!/usr/bin/env python3
"""The hook that hands the author's messages to a running session, whichever agent runs it.

    inbox.py post [BOX]   as a PostToolUse hook: the next model call sees the messages
    inbox.py stop [BOX]   as a Stop hook: a session about to end takes one more turn for them
    inbox.py pre [BOX]    as kiro's preToolUse hook: a tool call is held back, with the messages as its result
    inbox.py peek         opencode's plugin: the waiting messages, left where they are, as JSON {names, context}
    inbox.py ack NAME...  opencode's plugin: the messages it has handed over, moved to `delivered/`
    inbox.py --settings [BOX]   the `--settings` JSON that installs both, as run.sh passes it to claude
    inbox.py --codex-args [BOX]   the same for codex: the flags, one per line, that go BEFORE `exec`
    inbox.py --opencode-config   the same for opencode: its `OPENCODE_CONFIG_CONTENT`, naming the plugin
    inbox.py --kiro-agent DIR [BOX]   the same for kiro: writes the agent `dfs-inbox` under DIR/.kiro/agents

`run.sh` starts each session of a chain (work, critic and review) with these hooks, over BOX,
the run directory's `inbox/`, which the screen and the page write into (`runs.py`,
`post_message`). With nothing waiting the hook prints nothing and the session goes on
as if it were not there.

⚠️ THE BOX IS IN THE HOOK'S COMMAND, NOT IN THE AGENT'S ENVIRONMENT, for claude, codex and kiro:
everything the agent runs inherits its environment, and a tool or test that ran `inbox.py`
under a live session would claim the chain's messages. A hook without BOX reads `DFS_INBOX`,
which only opencode's plugin, living in the agent's own process, still gets.

⚠️ CLAIMED BY RENAME, THEN READ. Each message is moved to `delivered/` before it is
read, and a move that fails means another hook took it, so two hooks running at once
(parallel tool calls each fire one) never hand over the same message twice. Moved,
it counts as delivered: the screen shows it so from then on.

codex takes the same two hooks as `-c` overrides (`codex_args`), with the same JSON out.

opencode 2.1.1 has no hook for a session about to end, but its plugin (`opencode_inbox/`) can send the
session a prompt: it polls the inbox, reads the waiting messages with `inbox.py peek`, hands them over with
`ctx.session.prompt(..., delivery: "steer")`, which opencode takes at its next step or, if the model is
on its last answer, as one more turn, and only then moves them with `inbox.py ack`, so a prompt opencode
refuses leaves them waiting. Only a message that arrives after the session has ended stays waiting.

kiro-cli 2.31.1 runs hooks in a custom agent but hands the model the output of none that fires after
a tool call or at a stop; only a preToolUse hook that exits 2 reaches it, as the held call's result
(probe_kiro_hooks.py). So `pre` claims the waiting messages and exits 2 with them on stderr: the
call does not run, the model reads them, and asks for the call again. A message that arrives after
the session's last tool call stays waiting.

A message is for the session the author watches, never for a subagent it starts: claude and codex put an
`agent_id` in the input of a hook fired inside a subagent, and all three hooks then claim nothing, so the
message waits for the main session's next tool call or its stop. kiro's hook input has no such mark, so
`pre` refuses an `invoke_sub_agent` call naming the `dfs-inbox` agent itself, the one sub-agent that would
run this hook, and the agent leaves out `subagent_semantic_reviewer`, which the default agent lacks.

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


def waiting(box):
    """The names of the messages waiting in `box`, oldest first."""
    try:
        return sorted(n for n in os.listdir(box) if n.endswith(".txt") and not n.startswith("."))
    except OSError:
        return []


def claim(box):
    """Move every waiting message to `delivered/` beside `box` and return their texts,
    oldest first."""
    return take(box, waiting(box))


def take(box, names):
    """Move the messages `names` of `box` to `delivered/` beside it and return their texts, those
    another hook did not take first."""
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


def hook_input(data):
    """The hook's stdin as a dict, empty when it is not JSON."""
    try:
        event = json.loads(data)
    except ValueError:
        return {}
    return event if isinstance(event, dict) else {}


def from_subagent(event):
    """Whether the hook's input says the call is a subagent's: claude 2.1.308 and codex 0.163.0 add
    `agent_id` (and `agent_type`) to the input of a hook fired inside one, and the main loop's has neither.
    A subagent that took a message would end with it, and the session the author wrote to never read it."""
    return bool(event.get("agent_id"))


def said(texts):
    return HEAD + "\n\n" + "\n\n---\n\n".join(texts)


def peek(box):
    """The waiting messages, left where they are: their names and what the session is told."""
    pairs = []
    for name in waiting(box):
        try:
            with open(os.path.join(box, name)) as fh:
                text = fh.read().strip()
        except OSError:
            continue
        if text:
            pairs.append((name, text))
    return {"names": [n for n, _ in pairs], "context": said([t for _, t in pairs]) if pairs else ""}


def answer(event, texts):
    """The hook's JSON for `event` (`post` or `stop`), or None when there is nothing."""
    if not texts:
        return None
    text = said(texts)
    if event == "stop":
        return {"decision": "block", "reason": text}
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}


HELD = ("The author sent this to the running session from the roadmap's screen or page, and this tool call "
        "was held back so you read it first. Take it into account, then run the tool call again:")


def held(texts):
    """What kiro's model reads in place of the held tool call's result, or None with nothing waiting."""
    return HELD + "\n\n" + "\n\n---\n\n".join(texts) if texts else None


KIRO_AGENT = "dfs-inbox"


def command(event, box=None):
    """The hook's shell command for `event`: this file, and `box` when the hook is to read that inbox
    and not `DFS_INBOX`."""
    me = shlex.quote(os.path.abspath(__file__))
    return "python3 %s %s%s" % (me, event, " " + shlex.quote(os.path.abspath(box)) if box else "")


def kiro_agent(box=None):
    """The kiro agent that installs the `pre` hook. `tools: ["*"]` keeps every tool the default agent
    gives a work session, less `subagent_semantic_reviewer`, which only this agent would have. A hook is found
    only in an agent: the default one runs none."""
    return json.dumps({"name": KIRO_AGENT, "description": "the roadmap's work session, reading the run's inbox",
                       "tools": ["*"], "excludedTools": ["subagent_semantic_reviewer"],
                       "hooks": {"preToolUse": [{"command": command("pre", box)}]}}, indent=2)


SELF_AS_SUBAGENT = ("this agent is the roadmap's own work session: as a sub-agent it would take the author's messages "
                    "meant for the session that invoked it, since a hook cannot tell the two apart. Invoke another agent.")


def self_as_subagent(event):
    """Whether a kiro `invoke_sub_agent` call names this file's own agent. A sub-agent's tool calls fire the same
    preToolUse hook with the same session id and no mark of their own (kiro-cli 2.31.1), so the one that
    could take a held-back message is refused at the call that would start it."""
    return event.get("tool_name") == "invoke_sub_agent" and (event.get("tool_input") or {}).get("name") == KIRO_AGENT


def install_kiro_agent(directory, box=None):
    """Write the agent to `.kiro/agents/` in `directory`, the only place `--agent dfs-inbox` finds it besides the
    home directory, and keep it out of the repository's status through `info/exclude` when that is a repository."""
    agents = os.path.join(directory, ".kiro", "agents")
    os.makedirs(agents, exist_ok=True)
    path = os.path.join(agents, KIRO_AGENT + ".json")
    with open(path, "w") as fh:
        fh.write(kiro_agent(box) + "\n")
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


def settings(box=None):
    """The claude `--settings` JSON naming this file as both hooks."""
    hook = lambda ev: [{"hooks": [{"type": "command", "command": command(ev, box)}]}]
    return json.dumps({"hooks": {"PostToolUse": hook("post"), "Stop": hook("stop")}})


def codex_args(box=None):
    """The flags that install both hooks in `codex exec`: hooks switched on, the trust prompt
    for a hook it has not seen bypassed, and each event as a TOML array of tables."""
    hook = lambda ev: '[{hooks=[{type="command",command=%s}]}]' % json.dumps(command(ev, box))
    return ["-c", "features.hooks=true", "--dangerously-bypass-hook-trust",
            "-c", "hooks.PostToolUse=" + hook("post"), "-c", "hooks.Stop=" + hook("stop")]


def opencode_config(env):
    """`OPENCODE_CONFIG_CONTENT` for opencode, `env`'s own (a JSON object, or none) with the plugin directory
    added to its `plugins`; opencode joins that with the plugins of its other config."""
    cfg = json.loads(env.get("OPENCODE_CONFIG_CONTENT") or "{}")
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "opencode_inbox")
    cfg["plugins"] = [p for p in cfg.get("plugins") or [] if p != here] + [here]     # once, under an opencode that already has it
    return json.dumps(cfg)


USAGE = ("usage: inbox.py post|stop|pre [BOX] | peek | ack NAME... | --settings [BOX] | --codex-args [BOX] | "
         "--opencode-config | --kiro-agent DIR [BOX]")


def main(argv):
    args = argv[1:]
    if args[:1] == ["--settings"] and len(args) <= 2:
        print(settings(*args[1:]))
        return 0
    if args[:1] == ["--codex-args"] and len(args) <= 2:
        print("\n".join(codex_args(*args[1:])))
        return 0
    if args == ["--opencode-config"]:
        print(opencode_config(os.environ))
        return 0
    if args[:1] == ["--kiro-agent"] and len(args) in (2, 3):
        print(install_kiro_agent(*args[1:]))
        return 0
    if args[:1] in (["peek"], ["ack"]):
        box = os.environ.get("DFS_INBOX")
        if not box:
            print("inbox.py %s: DFS_INBOX names no inbox" % args[0], file=sys.stderr)
            return 2
        if args[0] == "peek":
            print(json.dumps(peek(box)))
        else:
            take(box, args[1:])
        return 0
    if not args or args[0] not in ("post", "stop", "pre") or len(args) > 2:
        print(USAGE, file=sys.stderr)
        return 2
    try:
        data = sys.stdin.read()     # the hook's own input; read so the agent's write never blocks
    except (OSError, ValueError):
        data = ""
    box = args[1] if len(args) == 2 else os.environ.get("DFS_INBOX")
    event = hook_input(data)
    if from_subagent(event):
        return 0        # the message is for the session the author is watching, not for a subagent's call
    if args[0] == "pre":
        if self_as_subagent(event):
            print(SELF_AS_SUBAGENT, file=sys.stderr)
            return 2
        text = held(claim(box)) if box else None
        if text:
            print(text, file=sys.stderr)
            return 2        # kiro: the tool call does not run, and this is its result
        return 0
    out = answer(args[0], claim(box)) if box else None
    if out:
        print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
