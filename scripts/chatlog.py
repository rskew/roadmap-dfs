#!/usr/bin/env python3
"""The record of a chat: one file per scope, `<run root>/chat/<scope>.log`, a line per thing that
happened to it (started, a message queued and typed, quiet, ended, closed and why, a page turn
and how it went). A chat that stopped answering is diagnosed from this, not from the screen,
which says nothing about a chat nobody is attached to.

Never raises: a log that cannot be written must not take the chat down with it. At MAX_BYTES the
file moves to `<scope>.log.1` (the one before it is dropped), so a chat keeps its last stretch.
"""
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

MAX_BYTES = 1_000_000
ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[()][0-9A-B]|[@-Z\\-_])")


def path(scope):
    import runs as dfs_runs
    return Path(dfs_runs.ensure_run_root()) / "chat" / (scope + ".log")


def screen_text(raw, keep=1500):
    """What the agent last drew, as text a person can read in a log: escapes removed, carriage
    returns and runs of blank lines collapsed, the last `keep` characters."""
    text = ANSI.sub("", raw.decode(errors="replace") if isinstance(raw, (bytes, bytearray)) else raw)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[^\S\n]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[-keep:]


def log(scope, text, detail=""):
    """Append `text` (one line) and, indented beneath it, `detail` (the agent's last words)."""
    try:
        p = path(scope)
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            if p.stat().st_size > MAX_BYTES:
                os.replace(p, p.with_name(p.name + ".1"))
        except OSError:
            pass
        lines = [time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()) + " " + text.replace("\n", " ")]
        lines += ["    | " + ln for ln in detail.splitlines()] if detail else []
        with open(p, "a") as fh:
            fh.write("\n".join(lines) + "\n")
    except Exception:
        pass
