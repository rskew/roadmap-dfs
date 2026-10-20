#!/usr/bin/env python3
"""Start a real claude through `ptyrelay.Relay` the way the screen's chat does (real HOME, real cwd, a message
queued at the start) and record, per start, when claude first wrote, when its `❯` prompt appeared, when the message
was typed, whether a second Enter was pressed, and whether the message was submitted.

This spends real model calls on the logged-in account, so it is a probe to run by hand, not a test:

    python3 scripts/probe_claude_start.py [--resume SESSION] [--runs N] [--gap SECONDS] [--cwd DIR]

Without --resume claude starts a fresh session. With it the session is resumed and forked (`--fork-session`), so
the saved one is not appended to. The message asks for one word and no tools. "Submitted" is read from the new
transcript in the project's directory under HOME: it holds the message as a user turn. The transcripts the probe
made are deleted at the end.
"""
import argparse
import glob
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import chatlog  # noqa: E402
import ptyrelay  # noqa: E402
from test_claude_chat import CLAUDE  # noqa: E402

MESSAGE = "W90 probe: reply with only the word ok, and use no tools."


def transcripts(cwd):
    d = Path(os.environ["HOME"]) / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd))
    return {p: p.stat().st_mtime for p in d.glob("*.jsonl")}


def submitted(cwd, before):
    """The transcripts made or changed since `before` that hold the probe's message as a user turn."""
    found = []
    for p, mtime in transcripts(cwd).items():
        if before.get(p) == mtime:
            continue
        for line in p.read_text(errors="replace").splitlines()[-400:]:
            if MESSAGE in line and '"user"' in line:
                found.append(p)
                break
    return found


def one(resume, cwd, wait, runsh=False):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CLAUDE", "AI_AGENT")) and k != "ANTHROPIC_API_KEY"}
    env.update(TERM="xterm-256color", DISABLE_AUTOUPDATER="1")
    if runsh:       # as the screen starts it: run.sh, which runs claude under `nix run`; --fork-session keeps the saved session as it is
        argv = [str(HERE / "run.sh"), "--chat", "project"]
        env.update(PERMISSION_FLAGS="--dangerously-skip-permissions" + " --fork-session" * bool(resume))
        if resume:
            env.update(CHAT_SID=resume, CHAT_RESUME="1")
    else:
        argv = [CLAUDE, "--dangerously-skip-permissions"] + (["--resume", resume, "--fork-session"] if resume else [])
    before = transcripts(cwd)
    logged = []
    marks = dict(prompt=None, enter2=None)
    relay = ptyrelay.Relay(agent="claude", log=lambda text, detail="": logged.append((time.time(), text)))
    relay.start(argv, env=env, cwd=cwd)
    first = [None, ""]
    relay.send(MESSAGE)                   # queued at once, as the page does
    t0, end = relay.started, relay.started + wait
    while time.time() < end and relay.live:
        if first[0] is None and relay.first_out:
            first[0], first[1] = relay.first_out, bytes(relay.tail[:60]).decode(errors="replace")
        if marks["prompt"] is None and "❯" in chatlog.screen_text(relay.tail, keep=ptyrelay.TAIL):
            marks["prompt"] = time.time()
        if time.time() - t0 > ptyrelay.SUBMIT_WAIT + 3 and submitted(cwd, before):
            break
        time.sleep(0.2)
    ok = submitted(cwd, before)
    screen = chatlog.screen_text(relay.tail, keep=ptyrelay.TAIL)
    relay.close(wait=True)
    made = [p for p in transcripts(cwd) if p not in before or before[p] != transcripts(cwd)[p]]
    sec = lambda t: None if not t else round(t - t0, 2)
    typed = [t for t, text in logged if text.startswith("typed ")]
    again = [t for t, text in logged if "pressed Enter again" in text]
    row = dict(first_out=sec(first[0]), first_bytes=first[1], prompt=sec(marks["prompt"]), typed=sec(typed[0] if typed else None),
               enter_again=sec(again[0] if again else None), submitted=bool(ok), alive_at_end=relay.live,
               tail=screen[-300:].replace("\n", " | ") if not ok else "")
    for p in made:
        if p not in before:
            p.unlink(missing_ok=True)
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--resume")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--gap", type=float, default=0)
    ap.add_argument("--cwd", default=os.getcwd())
    ap.add_argument("--runsh", action="store_true", help="start it through run.sh --chat project, as the screen does")
    ap.add_argument("--wait", type=float, default=60)
    a = ap.parse_args()
    for i in range(a.runs):
        print(json.dumps(one(a.resume, a.cwd, a.wait, a.runsh)), flush=True)
        time.sleep(a.gap)


if __name__ == "__main__":
    main()
