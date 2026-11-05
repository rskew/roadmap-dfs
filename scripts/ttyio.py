"""Prompts on the real terminal, with line editing, for the passes `run.sh` runs as a heredoc.

`run.sh --open` and `--review` run their Python as `python3 - <<'PY'`, so stdin is the script
and not the keyboard. They used to open `/dev/tty` and call `readline()` on it, which hands
the terminal's arrow escapes to the program as text: left arrow typed `^[[D` into the answer.
`input()` edits the line (left, right, home, end, delete) only when stdin and stdout are the
process's own fds 0 and 1 and both are a terminal. So `attach` points those two at
`/dev/tty` first. Prompts still go to the terminal, never into a pipe the caller holds.
"""
import os
import sys

try:
    import readline  # noqa: F401  (importing it is what gives input() its line editing)
except ImportError:
    pass


def attach():
    """Make fds 0 and 1 the controlling terminal. Returns (ask, block, say)."""
    fd = os.open("/dev/tty", os.O_RDWR)
    os.dup2(fd, 0)
    os.dup2(fd, 1)
    if fd > 1:
        os.close(fd)
    sys.stdin = open(0, "r", closefd=False)
    sys.stdout = open(1, "w", closefd=False)

    def say(*a):
        print(*a, flush=True)

    def ask(prompt):
        try:
            return input(prompt).strip()
        except EOFError:
            return ""

    def block(label):
        """Lines until a lone '.' (or end of input): the author's own words."""
        say(label)
        lines = []
        while True:
            try:
                ln = input()
            except EOFError:
                break
            if ln.rstrip("\n") == ".":
                break
            lines.append(ln)
        return "\n".join(lines).strip()

    return ask, block, say
