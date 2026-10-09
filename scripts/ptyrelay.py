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

import chatlog

DETACH = b"\x1d"            # ctrl-]
QUIET = 1.0                 # seconds without output before a fresh agent is taken to be ready
READY_TIMEOUT = 25.0
TAIL = 16384                # bytes of the agent's last output kept, for the log
STALL = float(os.environ.get("DFS_CHAT_STALL", 90))     # seconds quiet after a message before the log says so


def _winsize(fd):
    try:
        return fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8)
    except (OSError, ValueError):
        return struct.pack("HHHH", 40, 120, 0, 0)


def _terminal_size():
    """(rows, cols) of this process's own terminal, 40x120 when it has none or it reports 0."""
    try:
        rows, cols = struct.unpack("HHHH", _winsize(sys.__stdout__.fileno()))[:2]
    except (AttributeError, ValueError, OSError):
        rows = cols = 0
    return (rows, cols) if rows and cols else (40, 120)


def _try(fn, *a):
    try:
        return fn(*a)
    except (OSError, ValueError, termios.error):
        return None


class Relay:
    def __init__(self, log=None):
        self.log = log or (lambda text, detail="": None)    # the chat's record: chatlog.log, bound to a scope
        self.tail = bytearray()     # the last TAIL bytes the agent wrote
        self.typed_at = 0.0         # when the last message went in, until the quiet after it is logged
        self.typed_out = 0          # bytes of output at that moment
        self.total_out = 0
        self.why = ""               # who closed it
        self.fd = None
        self.pid = None
        self.rc = None
        self.lock = threading.Lock()
        self.attached = False
        self.closing = False
        self.started = self.first_out = self.last_out = 0.0
        self.outbox = queue.Queue()
        self.unsent = 0             # messages queued or taken by the typist and not yet typed
        self.stops = 0              # interrupts so far: a message taken before one is not typed after it
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
        self.log("start pid=%d cwd=%s argv=%s" % (pid, cwd or os.getcwd(), " ".join(
            a if len(a) <= 60 else a[:57] + "..." for a in argv)))
        rows, cols = size or _terminal_size()
        _try(fcntl.ioctl, master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        threading.Thread(target=self._pump, daemon=True).start()
        threading.Thread(target=self._typist, daemon=True).start()
        threading.Thread(target=self._watch, daemon=True).start()
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
            self.total_out += len(data)
            self.tail += data
            del self.tail[:-TAIL]
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
        how = "signal %d" % -self.rc if self.rc < 0 else "rc=%d" % self.rc
        self.log("exit %s after %ds, %s%s" % (
            how, time.time() - self.started,
            "%ds since its last output" % (time.time() - self.last_out) if self.last_out else "no output",
            " (closed)" if self.why == "closed" else " (closed: %s)" % self.why if self.why else " (nobody closed it)"),
            chatlog.screen_text(self.tail))

    def _watch(self):
        """Say so in the log when a message has gone in and the agent then says nothing for STALL
        seconds: finished, or stuck at a dialog, or hung, which the last screen tells apart."""
        while self.live:
            time.sleep(0.5)
            if self.typed_at and STALL > 0 and time.time() - max(self.typed_at, self.last_out) > STALL:
                self.log("quiet for %ds after a message; %d bytes of output since it was typed" % (
                    time.time() - max(self.typed_at, self.last_out), self.total_out - self.typed_out),
                    chatlog.screen_text(self.tail))
                self.typed_at = 0.0

    def close(self, wait=False, why=""):
        """End it as ctrl-c ends a claude in a terminal: ask it to stop with SIGTERM, which has it
        stop what it started, and make sure with SIGKILL after a grace. `wait` blocks until it is
        gone, for a process that is about to exit (a daemon thread would not outlive it)."""
        pid = self.pid
        if not self.live:
            return
        self.closing = True
        self.why = why or "closed"
        self.log("close (%s): SIGTERM" % self.why)
        _try(os.kill, pid, signal.SIGTERM)

        def reap():
            end = time.time() + 2
            while self.fd is not None and time.time() < end:
                time.sleep(0.05)
            if self.fd is not None:
                self.log("close (%s): still running after 2s, SIGKILL" % self.why)
                _try(os.kill, pid, signal.SIGKILL)
        if wait:
            reap()
            end = time.time() + 2       # SIGKILL is delivered, not instant
            while self.fd is not None and time.time() < end:
                time.sleep(0.05)
        else:
            threading.Thread(target=reap, daemon=True).start()

    def resize(self, size=None):
        """Tell the agent its terminal is now `size` (rows, cols), by default this process's own,
        so that what it wraps in the background is wrapped for the width it will be looked at."""
        rows, cols = size or _terminal_size()
        with self.lock:
            if self.fd is None or self.attached:        # attached, `attach` follows the terminal itself
                return
            _try(fcntl.ioctl, self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        _try(os.kill, self.pid, signal.SIGWINCH)

    # ── typing into it ─────────────────────────────────────────────────────────────────
    def send(self, text):
        """Queue `text` to be typed into the agent, as a paste and then enter. It waits for a
        fresh agent to be ready, and does not block the caller. False if it is not running."""
        with self.lock:             # agrees with `ChatHost.reap_idle`'s close, which holds it
            if not self.live:
                self.log("message of %d characters refused: the agent is not running" % len(text))
                return False
            self.outbox.put(text)
            self.unsent += 1
            self.log("message of %d characters queued (%d waiting)" % (len(text), self.outbox.qsize()))
            return True

    def interrupt(self):
        """Stop what the agent is answering, as pressing Esc in its terminal does: messages still
        waiting to be typed are dropped, then ESC is written to it. Returns how many messages were
        dropped untyped, or None if it is not running."""
        with self.lock:
            if not self.live:
                return None
            dropped = self.unsent
            self.unsent = 0
            while True:
                try:
                    self.outbox.get_nowait()
                except queue.Empty:
                    break
            self.typed_at = 0.0
            self.stops += 1
            _try(os.write, self.fd, b"\x1b")
        self.log("interrupt: ESC sent%s" % (", %d unsent message(s) dropped" % dropped if dropped else ""))
        return dropped

    def _typist(self):
        while self.live:
            try:
                text = self.outbox.get(timeout=0.5)
            except queue.Empty:
                continue
            waited, stops = time.time(), self.stops
            end = waited + READY_TIMEOUT      # not before the agent has drawn itself and settled
            while self.live and time.time() < end and (not self.first_out or time.time() - self.last_out < QUIET):
                time.sleep(0.1)
            with self.lock:
                fd = self.fd
                if fd is None:
                    self.log("message of %d characters dropped: the agent ended before it was typed" % len(text))
                    return
                if self.stops != stops:
                    self.log("message of %d characters dropped: interrupted before it was typed" % len(text))
                    continue
                _try(os.write, fd, b"\x1b[200~" + text.encode() + b"\x1b[201~")
                self.typed_at, self.typed_out = time.time(), self.total_out
                self.unsent = max(0, self.unsent - 1)
            self.log("typed %d characters after %.1fs wait (%s)" % (
                len(text), time.time() - waited, "settled" if time.time() - waited < READY_TIMEOUT
                else "the agent had not settled in %ds, typed anyway" % READY_TIMEOUT))
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

    def __init__(self, log=chatlog.log):
        self.lock = threading.Lock()
        self.relays = {}
        self.log = log              # log(scope, text, detail): where each chat's record goes

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
                r = self.relays[scope] = Relay(lambda t, d="": self.log(scope, t, d)).start(argv, env=env, cwd=cwd)
            return r

    def close(self, scope, wait=False, why="closed"):
        with self.lock:
            r = self.relays.pop(scope, None)
        if r is not None:
            r.close(wait, why)

    def close_all(self):
        """End every chat and wait: this is the screen's exit, which would take the SIGKILL
        backstop with it."""
        with self.lock:
            relays, self.relays = list(self.relays.values()), {}
        threads = [threading.Thread(target=r.close, args=(True, "the screen ended")) for r in relays]
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
        closed = []
        with self.lock:
            for s, r in list(self.relays.items()):
                if not r.live:
                    del self.relays[s]
                    continue
                with r.lock:        # a message queued or a terminal attached meanwhile keeps it
                    if not r.attached and r.outbox.empty() and now - max(r.started, r.last_out) > limit:
                        del self.relays[s]
                        r.close(why="idle %ds, limit %ds" % (now - max(r.started, r.last_out), limit))
                        closed.append(s)
        return closed

    def resize(self, size=None):
        """Resize every running chat that nobody is attached to (see `Relay.resize`)."""
        with self.lock:
            relays = list(self.relays.values())
        for r in relays:
            r.resize(size)

    def live_scopes(self):
        with self.lock:
            return [s for s, r in self.relays.items() if r.live]
