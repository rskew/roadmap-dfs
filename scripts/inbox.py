#!/usr/bin/env python3
"""The claude hook that hands the author's messages to a running work session.

    inbox.py post    as a PostToolUse hook: the next model call sees the messages
    inbox.py stop    as a Stop hook: a session about to end takes one more turn for them
    inbox.py --settings   the `--settings` JSON that installs both, as run.sh passes it

`run.sh` starts each claude work session with both hooks and `DFS_INBOX`, the run
directory's `inbox/`, which the screen and the page write into (`runs.py`,
`post_message`). With nothing waiting the hook prints nothing and the session goes on
as if it were not there.

⚠️ CLAIMED BY RENAME, THEN READ. Each message is moved to `delivered/` before it is
read, and a move that fails means another hook took it, so two hooks running at once
(parallel tool calls each fire one) never hand over the same message twice. Moved,
it counts as delivered: the screen shows it so from then on.

Checked against a real claude-code 2.1.308 under `-p`: PostToolUse's
`additionalContext` is in the very next request, and a Stop hook's `block` makes the
session answer its `reason` (test_inbox.py).
"""
import json
import os
import shlex
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


def settings():
    """The claude `--settings` JSON naming this file as both hooks."""
    me = shlex.quote(os.path.abspath(__file__))
    hook = lambda ev: [{"hooks": [{"type": "command", "command": "python3 %s %s" % (me, ev)}]}]
    return json.dumps({"hooks": {"PostToolUse": hook("post"), "Stop": hook("stop")}})


def main(argv):
    if argv[1:] == ["--settings"]:
        print(settings())
        return 0
    if len(argv) != 2 or argv[1] not in ("post", "stop"):
        print("usage: inbox.py post | stop | --settings", file=sys.stderr)
        return 2
    try:
        sys.stdin.read()        # the hook's own input; drained so claude's write never blocks
    except (OSError, ValueError):
        pass
    box = os.environ.get("DFS_INBOX")
    out = answer(argv[1], claim(box)) if box else None
    if out:
        print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
