#!/usr/bin/env python3
"""Interactive agents that keep running with nobody looking, and can be looked at.

A chat is an interactive agent on a pty this process holds the other end of. It runs in the
BACKGROUND: the web page types into it (`Relay.send`) and reads what it says from the agent's
own transcript. The terminal screen can ATTACH to it (`Relay.attach`), see and type into it as
if it had started it, and DETACH (ctrl-]) leaving it running. One `Relay` per chat; `ChatHost`
keeps them, so any number of chats can be going at once.
"""
import fcntl
import os
import pty
import queue
import select
import signal
import struct
import sys
import termios
import threading
import time
import tty

DETACH = b"\x1d"            # ctrl-]
QUIET = 1.0                 # seconds without output before a fresh agent is taken to be ready
READY_TIMEOUT = 25.0
GRACE = 2.0                 # seconds a closed agent has to stop before it is killed


def _winsize(fd):
    try:
        return fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8)
    except OSError:
        return struct.pack("HHHH", 40, 120, 0, 0)


def _stat(pid):
    """(ppid, state, start time) of a process from /proc, or None if there is no such process."""
    try:
        with open("/proc/%d/stat" % pid) as f:
            raw = f.read()
    except OSError:
        return None
    f = raw[raw.rindex(")") + 2:].split()       # after the command name, which may hold spaces
    return int(f[1]), f[0], f[19]


def tree_of(pid):
    """{pid: start time} of `pid` and everything it started that is still running, found by
    parent links in /proc. A process that moved itself to another session is still in it; one
    that was already re-parented to init is not."""
    kids = {}
    for name in os.listdir("/proc"):
        if name.isdigit():
            st = _stat(int(name))
            if st:
                kids.setdefault(st[0], []).append(int(name))
    found, todo = {}, [pid]
    while todo:
        p = todo.pop()
        st = _stat(p)
        if st and p not in found:
            found[p] = st[2]
            todo += kids.get(p, [])
    return found


def _running(pid, started):
    """Whether this very process (not a later one with the pid) is still running, not a zombie."""
    st = _stat(pid)
    return bool(st and st[2] == started and st[1] != "Z")


def finish(pid, gone):
    """Wait up to GRACE seconds for the processes in `gone` ({pid: start time}) to stop, then
    kill them and whatever else `pid` has started since, and wait for that to take."""
    end = time.time() + GRACE
    while time.time() < end and any(_running(p, t) for p, t in gone.items()):
        time.sleep(0.05)
    gone.update(tree_of(pid))
    for p, t in gone.items():
        if _running(p, t):
            _try(os.kill, p, signal.SIGKILL)
    end = time.time() + 1.0             # a kill is delivered, not instant
    while time.time() < end and any(_running(p, t) for p, t in gone.items()):
        time.sleep(0.02)


def stop_tree(pid):
    """End `pid` and everything it started: ask, wait, kill."""
    gone = tree_of(pid)
    for p in gone:
        _try(os.kill, p, signal.SIGTERM)
    finish(pid, gone)


def _try(fn, *a):
    try:
        return fn(*a)
    except (OSError, ValueError, termios.error):
        return None


class Relay:
    def __init__(self):
        self.fd = None
        self.pid = None
        self.rc = None
        self.lock = threading.Lock()
        self.attached = False
        self.closing = False
        self.started = self.first_out = self.last_out = 0.0
        self.outbox = queue.Queue()
        self.out_fd = None          # where the terminal is, while attached

    # ── life ───────────────────────────────────────────────────────────────────────────
    @property
    def live(self):
        return self.fd is not None and not self.closing

    @property
    def active(self):
        """Output within the last couple of seconds: it is saying something (thinking, answering)."""
        return self.live and time.time() - self.last_out < 2.0

    def start(self, argv, env=None, cwd=None, size=None):
        """Run argv on a pty, in the background."""
        pid, master = pty.fork()
        if pid == 0:
            try:
                if cwd:
                    os.chdir(cwd)
                os.execvpe(argv[0], argv, env if env is not None else os.environ)
            except OSError as e:
                os.write(2, ("cannot run %s: %s\n" % (argv[0], e.strerror or e)).encode())
            os._exit(127)
        self.pid, self.fd, self.started = pid, master, time.time()
        rows, cols = size or (40, 120)
        _try(fcntl.ioctl, master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        threading.Thread(target=self._pump, daemon=True).start()
        threading.Thread(target=self._typist, daemon=True).start()
        return self

    def _pump(self):
        master = self.fd
        while True:
            try:
                data = os.read(master, 65536)
            except OSError:
                break
            if not data:
                break
            now = time.time()
            self.first_out = self.first_out or now
            self.last_out = now
            if self.attached and self.out_fd is not None:
                _try(os.write, self.out_fd, data)
        with self.lock:
            self.fd = None
        _try(os.close, master)
        try:
            _, status = os.waitpid(self.pid, 0)
            self.rc = os.waitstatus_to_exitcode(status)
        except ChildProcessError:
            self.rc = 0

    def close(self, wait=False):
        """End it, and everything it started: hang up on the agent and ask the rest to stop,
        then kill what is still there after GRACE seconds. With `wait` it returns when none of
        them is left (a process that is exiting cannot finish a thread it left behind), else
        that is done in the background."""
        pid = self.pid
        if not self.live:
            return
        self.closing = True
        gone = tree_of(pid)
        _try(os.kill, pid, signal.SIGHUP)
        for p in gone:
            if p != pid:
                _try(os.kill, p, signal.SIGTERM)

        def reap():
            finish(pid, gone)
        if wait:
            reap()
        else:
            threading.Thread(target=reap, daemon=True).start()

    # ── typing into it ─────────────────────────────────────────────────────────────────
    def send(self, text):
        """Queue `text` to be typed into the agent, as a paste and then enter. It waits for a
        fresh agent to be ready, and does not block the caller. False if it is not running."""
        if not self.live:
            return False
        self.outbox.put(text)
        return True

    def _typist(self):
        while self.live:
            try:
                text = self.outbox.get(timeout=0.5)
            except queue.Empty:
                continue
            end = time.time() + READY_TIMEOUT      # not before the agent has drawn itself and settled
            while self.live and time.time() < end and (not self.first_out or time.time() - self.last_out < QUIET):
                time.sleep(0.1)
            with self.lock:
                fd = self.fd
                if fd is None:
                    return
                _try(os.write, fd, b"\x1b[200~" + text.encode() + b"\x1b[201~")
            time.sleep(0.15)
            with self.lock:
                if self.fd is not None:
                    _try(os.write, self.fd, b"\r")

    # ── looking at it ──────────────────────────────────────────────────────────────────
    def attach(self):
        """Wire the real terminal to it until it ends or ctrl-] is pressed. Returns True if it
        was detached (still running), False if it ended."""
        stdin, stdout = sys.stdin.fileno(), sys.stdout.fileno()
        saved = _try(termios.tcgetattr, stdin)
        if saved is not None:
            _try(tty.setraw, stdin)

        def fit(redraw=False):
            rows, cols, x, y = struct.unpack("HHHH", _winsize(stdout))
            if redraw and cols > 1:             # a resize is what makes it draw itself again
                _try(fcntl.ioctl, self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols - 1, x, y))
                time.sleep(0.05)
            _try(fcntl.ioctl, self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, x, y))
            _try(os.kill, self.pid, signal.SIGWINCH)

        try:
            old = signal.signal(signal.SIGWINCH, lambda *a: fit())
        except ValueError:                      # not the main thread: only a test is
            old = None
        self.out_fd = stdout
        self.attached = True
        fit(redraw=True)
        detached = False
        try:
            while self.live:
                try:
                    ready, _, _ = select.select([stdin], [], [], 0.3)
                except InterruptedError:
                    continue
                except OSError:
                    break
                if not ready:
                    continue
                data = os.read(stdin, 65536)
                if not data:
                    break
                if DETACH in data:
                    data = data.split(DETACH)[0]
                    detached = True
                if data:
                    with self.lock:
                        if self.fd is not None:
                            _try(os.write, self.fd, data)
                if detached:
                    break
        finally:
            self.attached = False
            self.out_fd = None
            if old is not None:
                signal.signal(signal.SIGWINCH, old)
            if saved is not None:
                termios.tcsetattr(stdin, termios.TCSADRAIN, saved)
        return detached or self.live


class ChatHost:
    """The chats that are running, by scope: a task's id, or `project`."""

    def __init__(self):
        self.lock = threading.Lock()
        self.relays = {}

    def get(self, scope):
        with self.lock:
            r = self.relays.get(scope)
        return r if r is not None and r.live else None

    def ensure(self, scope, make):
        """The running chat for `scope`, started by `make()` -> (argv, env, cwd) if there is none."""
        with self.lock:
            r = self.relays.get(scope)
            if r is None or not r.live:
                argv, env, cwd = make()
                r = self.relays[scope] = Relay().start(argv, env=env, cwd=cwd)
            return r

    def close(self, scope, wait=False):
        with self.lock:
            r = self.relays.pop(scope, None)
        if r is not None:
            r.close(wait)

    def close_all(self):
        """End every chat and return when they are gone: this is what the screen does as it exits."""
        with self.lock:
            relays, self.relays = list(self.relays.values()), {}
        threads = [threading.Thread(target=r.close, args=(True,)) for r in relays]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def reap_idle(self, limit, now=None):
        """Close the chats nobody has used for `limit` seconds and forget the ones that ended:
        no output from the agent, nothing waiting to be typed, and no terminal attached. Their
        conversation is in the agent's own transcript, so the next message starts the chat again
        where it was. Returns the scopes closed."""
        now = now or time.time()
        with self.lock:
            stale = [s for s, r in self.relays.items() if not r.live]
            idle = [s for s, r in self.relays.items() if r.live and not r.attached and r.outbox.empty()
                    and now - max(r.started, r.last_out) > limit]
            for s in stale:
                del self.relays[s]
        for s in idle:
            self.close(s)
        return idle

    def live_scopes(self):
        with self.lock:
            return [s for s, r in self.relays.items() if r.live]
