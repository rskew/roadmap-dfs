#!/usr/bin/env python3
"""Signing this machine's claude in to an Anthropic account, from the web page.

Every run and chat here is the claude CLI, and it reads one stored login (`~/.claude`). This
replaces it, through claude's own `auth login` (`run.sh --claude-auth login`, so it is the same
claude the runs start, from CLAUDE_CMD or the nixpkgs pin) with stdin and stdout as PIPES: with
no terminal it prints the sign-in link and then reads the code the sign-in page shows, which
the author pastes on the page:

    start()     ->  starting   run.sh is resolving claude (a nix run, seconds on a cold cache)
                ->  waiting    `url` is the link to open; send the code it shows
    code(c)     ->  checking   claude is trading the code for a login
                ->  done       `account` is who is signed in now
                ->  failed     `error` is claude's own last words
    cancel()    ->  idle

One sign-in at a time: a second `start` while one is going returns that one. `account` is
`claude auth status --json`, read on the first ask, after each sign-in and by refresh() (the page
calls it each time the dialog opens, since the login can change outside the page), never on every
poll (each read is a nix run). Nothing here keeps a secret: claude writes its own store.

Usage:  login.py status   print the account as the page gets it
"""
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths as dfs_paths   # noqa: E402

URL_RE = re.compile(r"https://\S+/oauth/authorize\?\S+")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[()][0-9A-B]|\r")
LOGIN_TIMEOUT = 15 * 60     # a sign-in left waiting this long is ended: the link has gone stale
STATUS_TIMEOUT = 120        # `auth status` through a nix run that may have to fetch claude first
ACCOUNT_KEYS = ("loggedIn", "authMethod", "email", "orgName", "subscriptionType")


def argv(*args):
    return [str(dfs_paths.SCRIPTS / "run.sh"), "--claude-auth"] + list(args)


def plain(text):
    return ANSI_RE.sub("", text)


def tail(text, n=600):
    text = plain(text).strip()
    return text if len(text) <= n else "…" + text[-n:]


def read_account():
    """Who claude is signed in as: the fields of `claude auth status --json` the page shows,
    or `error` with why it could not say."""
    try:
        p = subprocess.run(argv("status", "--json"), stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, timeout=STATUS_TIMEOUT, cwd=dfs_paths.work_root())
    except (OSError, subprocess.TimeoutExpired) as e:
        return dict(loggedIn=False, error="claude auth status: %s" % e)
    out = plain(p.stdout)
    start = out.find("{")
    try:
        data = json.loads(out[start:]) if start >= 0 else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        # signed out, claude says so and exits 1 with its JSON all the same; anything else is a fault
        return dict(loggedIn=False, error=tail(p.stdout + p.stderr) or "claude auth status exited %d" % p.returncode)
    return {k: data[k] for k in ACCOUNT_KEYS if k in data}


class Login:
    def __init__(self, read=read_account):
        self.lock = threading.Lock()
        self.read = read
        self.proc = None
        self.phase, self.url, self.error, self.output = "idle", "", "", ""
        self.account = None     # not read yet
        self.reading = False
        self.expired = None     # the process expire() ended
        self.asked = self.stored = 0    # account reads begun, and the latest whose answer is kept

    def state(self):
        """What the page shows. The first ask reads the account; later ones say what is known."""
        with self.lock:
            first = self.account is None and not self.reading
        if first:
            self.check()
        with self.lock:
            return dict(phase=self.phase, url=self.url, error=self.error, account=self.account)

    def refresh(self):
        """Read the account again, unless a sign-in is going: the login can change outside the
        page (the terminal, another container, a lapse), and a failed read should not stick."""
        with self.lock:
            if self.phase in ("starting", "waiting", "checking"):
                return self.account
        return self.check()

    def check(self):
        """Read the account. A read that began before the newest one stored is dropped, so
        the first ask, still running when a sign-in ends, cannot put the old login back."""
        with self.lock:
            self.reading = True
            self.asked += 1
            mine = self.asked
        try:
            account = self.read()
        finally:
            with self.lock:
                self.reading = False
        with self.lock:
            if mine > self.stored:
                self.account, self.stored = account, mine
            return self.account

    def start(self, console=False):
        with self.lock:
            if self.proc and self.proc.poll() is None:
                return
            env = dict(os.environ, BROWSER=os.environ.get("DFS_LOGIN_BROWSER", "true"))   # the server's own desktop is not where the author is
            self.proc = subprocess.Popen(argv("login", "--console" if console else "--claudeai"),
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, env=env, start_new_session=True,
                                         cwd=dfs_paths.work_root())
            self.phase, self.url, self.error, self.output = "starting", "", "", ""
            proc = self.proc
        threading.Thread(target=self.follow, args=(proc,), daemon=True).start()
        threading.Thread(target=self.expire, args=(proc,), daemon=True).start()

    def follow(self, proc):
        """Read what claude says until it ends: the link once it is printed, then the verdict."""
        try:
            self.read_out(proc)
        finally:
            for f in (proc.stdin, proc.stdout):
                try:
                    f.close()
                except OSError:
                    pass

    def read_out(self, proc):
        fd = proc.stdout.fileno()
        while True:
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            with self.lock:
                if proc is not self.proc:
                    return
                self.output += chunk.decode("utf-8", "replace")
                found = URL_RE.search(plain(self.output))
                if found and not self.url:
                    self.url = found.group(0)
                    if self.phase == "starting":
                        self.phase = "waiting"
        rc = proc.wait()
        with self.lock:
            if proc is not self.proc or self.phase == "idle":
                return
            said = tail(self.output.split("Paste code here if prompted >")[-1]) or tail(self.output)
        if rc == 0:
            self.check()
            with self.lock:
                if proc is self.proc:
                    self.phase, self.error = "done", ""
        else:
            with self.lock:
                if proc is self.proc:
                    self.phase, self.error = "failed", (
                        "no code came in %d minutes, so the sign-in was ended: start again" % (LOGIN_TIMEOUT // 60)
                        if proc is self.expired else said or "claude auth login exited %d" % rc)

    def expire(self, proc):
        try:
            proc.wait(LOGIN_TIMEOUT)
        except subprocess.TimeoutExpired:
            with self.lock:
                self.expired = proc
            self.end(proc)

    def code(self, code):
        code = code.strip()
        if not code or "\n" in code:
            raise ValueError("paste the one code the sign-in page shows")
        with self.lock:
            if self.phase != "waiting" or not self.proc:
                raise ValueError("no sign-in is waiting for a code")
            self.phase = "checking"
            try:
                self.proc.stdin.write((code + "\n").encode())
                self.proc.stdin.flush()
            except OSError:
                pass    # claude has gone; follow() says why

    def cancel(self):
        with self.lock:
            proc, self.phase, self.url, self.error = self.proc, "idle", "", ""
        if proc:
            self.end(proc)

    @staticmethod
    def end(proc):
        if proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass


if __name__ == "__main__":
    if sys.argv[1:] != ["status"]:
        sys.exit(__doc__.split("Usage:")[1].strip())
    print(json.dumps(read_account(), indent=2))
