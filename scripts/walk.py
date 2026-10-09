#!/usr/bin/env python3
"""The depth-first walk: ONE engine, for the terminal screen and for the web page alone.

A chain ends and something has to decide what happens next; `walk_decide` is that decision
as a function, and `WalkMixin` is the bookkeeping around it: the budget, the usage-limit
hold, the pin, which chain is the walk's own, raising on a suspect item. The screen
(`tui.UI`) mixes it in and steps it from its own poll; `HeadlessWalk`, below, is the same
engine behind a lock and a thread, for `web.py` run without a screen. Neither repeats a
decision: they differ only in the world they hand it (the `world` methods below), which is
what lets one set of tests stand over both.

The world an engine needs, as methods of `self`:
    refresh_chains()   re-read `self.chains` (the run directories on the box)
    live(mode="work")  the live chains of that mode
    reload()           re-read `self.data` (the trees) and so `self.items`
    start_chain(item, cap) -> run directory | None
    event(kind, text) -> text      record what the walk did
    notify(text)                   say so to somebody not looking
    msg                            a line for a person, where there is one
"""
import calendar
import collections
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import limit as dfs_limit    # noqa: E402
import paths as dfs_paths    # noqa: E402
import runs as dfs_runs      # noqa: E402
import state as dfs_state    # noqa: E402
import tree as dfs_tree      # noqa: E402

RUNNER = dfs_paths.rel(dfs_paths.SCRIPTS / "run.sh")
AGENTS = ("claude", "codex", "kiro", "opencode")
TICK = 2.0           # seconds between a headless walk's steps
EVENTS_KEEP = 400    # activity lines held (the screen reads them back from the file)


def agent_file():
    """Where the last `x` choice is kept, so the next screen launches the same agent:
    beside `activity.log`, gitignored, and outside what the file watch reads."""
    return os.path.join(dfs_runs.run_root(), "agent")


def read_agent(path):
    """The agent `path` names, or claude when it names none (or nothing we know)."""
    try:
        with open(path, errors="replace") as fh:
            name = fh.read().strip()
    except OSError:
        return "claude"
    return name if name in AGENTS else "claude"


# ── the walk ──────────────────────────────────────────────────────────────────
#
# A chain ends; something has to decide what happens next. That decision is a
# FUNCTION OF THREE FACTS and nothing else, so it lives out here where it can be
# read and tested without a terminal: what the chain recorded about why it stopped
# (`run.sh` writes it, `runs.py` gives it meaning), whether §3 actually
# grew while it ran, and what a depth-first walk would take now (`dfs_state`).
#
# ⚠️ ONLY `limit` MEANS "WAIT AND TRY THE SAME THING AGAIN". Every other unhappy
# ending — a crash, a session that appended nothing, a blown context ceiling —
# means something about the machinery is wrong, and an autonomous spender that
# treats those as "probably temporary" is one that burns a subscription overnight
# on a loop. A usage limit and a crash are both `rc 1`, which is exactly why the
# reason is recorded rather than inferred.
#
# ⚠️ A SUSPECT ITEM IS A RAISE, NOT A STOP. A chain that moved nothing in its tree,
# or a session the runner found `idle`, says something about THAT item, and the
# author's instrument for "look at this item" is a raise: it blocks the item, and
# `next_item` then takes the next branch. Stopping the whole walk instead held every
# other branch hostage to one item, overnight, for a check that can be wrong (see
# `dfs_state.content`). It is still bounded: each raise blocks one item for good, so
# a broken machine raises once per item and ends at "every branch is blocked".
WALK_CONTINUE = {"blocked", "done", "cap"}
# Endings that say something about THE ITEM, so the author hears about it on the item
# and the walk takes the next branch.
WALK_RAISE = {
    "idle": "a session on it changed nothing in the repo",
}
# Endings that STOP the walk, because the next item would fare no better or because
# they are significant enough that a person should look before more is spent.
# ⚠️ `overbudget` and `overturns` stay here: the context ceiling (CHAIN_CEILING,
# 400,000) was set far past any normal overrun precisely so that reaching it means
# the budget is not holding (the highest peak on record is ~237,000). And `failed`
# stays here because it is two things in one word: a session that exited non-zero
# (on 2026-09-25 that was nix failing to fetch the agent, which every item would hit
# next), and a critic or reviewer that changed code or gave no verdict. Raising on
# the item for the first would raise on every item in turn.
WALK_STOP = {
    "interrupt": "interrupted",
    "overbudget": "a session went past the context ceiling; the budget is not holding",
    "overturns": "a session went past the turn ceiling with no context reading",
    "failed": "a session failed; its console says whether the agent exited non-zero "
              "or a critic or reviewer changed code or gave no verdict",
}
WALK_HOLD = 3600                # the fallback when the reset time is unparseable

# ⚠️ A CHAIN IS NOT ON THE BOX UNTIL IT HAS SAID SO, AND IT SAYS SO SECOND.
# `start_chain` MAKES the run directory and `run.sh` IDENTIFIES it, by writing
# `meta.json` — and `dfs_runs.run_dirs` skips a directory that has none, because a
# directory without one cannot be asked which item it is or whether it is alive. The
# gap between those two moments is real and was measured on 2026-09-16: the chain
# writes its meta about 110 ms after exec (three python starts before it), while
# `start_chain`'s own `reload()` reaches `run_dirs()` at about 55 ms. So the walk's
# own chain was absent from the list the walk then judged it by. This is how long it
# may stay absent before that is read as a chain that DIED rather than one that has
# not spoken yet.
WALK_START_GRACE = 15           # seconds — see `walk_tick`


def chain_cause(rundir):
    """`: <why>` from the last `dfs_agent:` line in a run's console, else nothing.

    A chain that dies before its `meta.json` (`run.sh` exits 2 when `agent.py --rev`
    cannot resolve nixpkgs) leaves its cause only in `console.log`, which neither the
    screen's message line nor the page reads; the walk's stop reason is where it shows."""
    try:
        with open(os.path.join(rundir, "console.log"), "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 8192))
            tail = fh.read().decode("utf-8", "replace")
    except OSError:
        return ""
    lines = [l[len("dfs_agent:"):].strip() for l in tail.splitlines()
             if l.startswith("dfs_agent:")]
    # `agent.py` follows the failure with a hint line; the failure is the first of them.
    failed = [l for l in lines if " failed: " in l]
    return ": " + (failed or lines)[-1] if lines else ""


def walk_decide(stop, moved, next_item):
    """What the walk does after a chain ends, as `(action, why)`.

    `run` carries the item, `hold` and `stop` carry a reason to show a person.

    `raise` carries the question to put to the author on the item; the walk then
    goes on to the next item, which the raise has just made the item stop being.

    ⚠️ The `moved` cross-check is deliberately redundant with the runner's own
    `idle` stop. The runner refuses to repeat a session that appended nothing
    WITHIN a chain; this refuses to start another chain on an item that produced
    nothing, which is the same guarantee one level up and the only thing standing
    between a broken item and an unattended loop. Both end in a raise, which blocks
    the item, rather than in a stop, which would block every other item too.

    ⚠️ A chain that stopped `done` is exempt. `done` means the item is finished (every
    live node confirmed and the last review found nothing, or the author accepted
    it), and the review that says so is a verdict, not tree content, so a chain whose
    last session was that review always moves nothing. No next chain goes to a done
    item, so there is no loop to guard against. W20 raised on exactly this on
    2026-09-30: its tree was complete, the review came back ok, and the author was
    asked to look at an item that had only finished.
    """
    if stop == "limit":
        return ("hold", "a usage limit")
    if stop in WALK_RAISE:
        return ("raise", WALK_RAISE[stop])
    if stop not in WALK_CONTINUE:
        return ("stop", WALK_STOP.get(stop) or stop
                or "the chain ended without recording why")
    if not moved and stop != "done":
        return ("raise", "the chain moved nothing in the task tree")
    if next_item is None:
        return ("stop", "every branch is blocked or finished")
    return ("run", next_item)


def walk_held_for(seconds):
    """A hold, in the units a person reads: whole minutes, floor 1."""
    return "%dm" % max(1, int(seconds // 60))


class WalkMixin:
    """The walk's state and steps. See the module docstring for the world it asks of
    `self`. Everything the walk does it also says through `event`."""

    def walk_init(self):
        self.walk_on = False
        self.walk_budget = 20       # SESSIONS, over the whole walk — see walk_toggle
        self.walk_spent = 0         # sessions run by chains this walk has finished
        self.walk_until = 0.0       # epoch; a usage-limit hold
        self.walk_dir = None        # the run directory of the chain THIS started
        self.walk_started = 0.0     # epoch; when it was started — see walk_tick
        self.walk_content = set()   # what the trees said when that chain started
        self.walk_note = ""         # why the last walk stopped, until the next one
        self.walk_next = None       # an item the author pinned for the next chain — walk_pin

    def walk_begin(self, budget):
        """Turn the walk on with `budget` sessions: what `w` does once the number is in,
        and what the web page's Start does. A ValueError says why not."""
        if budget < 1:
            raise ValueError("a budget of %d sessions is not a walk" % budget)
        self.walk_budget = budget
        self.walk_spent = 0
        self.walk_on = True
        self.walk_until = 0.0
        self.walk_dir = None
        self.walk_note = ""
        self.msg = "walk: on, %d sessions — w to stop" % budget
        self.event("walk", "walk on, %d sessions" % budget)
        self.walk_tick()

    def walk_off(self, why):
        self.walk_on = False
        self.walk_dir = None
        self.walk_until = 0.0
        self.walk_note = why
        self.msg = "walk: stopped — %s" % why
        self.event("stop", "walk off — %s" % why)
        if why != "stopped by hand":
            self.notify("walk stopped: %s" % why)

    def walk_end_chain(self):
        """Turn the walk off by hand, and end its chain after the run it is in.

        ⚠️ The walk's chain carries the walk's whole remaining budget as its cap
        (`walk_tick`), so turning the walk off and leaving that chain alone left it
        free to run every session the walk had left, with nothing watching it — W19
        went from run 3 to run 5 of 33 after the walk was off. The cap is lowered to
        the run it is on (`dfs_runs.lower_cap`), which is a chain of one from here:
        the session in flight finishes its node's work, and no further one starts.
        `K` is still the key for stopping the run in flight as well."""
        run = self.walk_run()
        self.walk_off("stopped by hand")
        if not (run and run["live"]):
            return
        n = int(run["run"] or 0)
        try:
            self.lower_cap(run["dir"], n)
        except OSError as e:
            self.msg = ("walk: stopped, but %s's chain could not be told to end (%s) — "
                        "K stops it" % (run["item"], e))
            return
        self.event("stop", "%s's chain told to end after run %d" % (run["item"], n))
        self.msg = ("walk: stopped — %s's chain ends after run %d; K stops that run too"
                    % (run["item"], n) if n else
                    "walk: stopped — %s's chain ends before its first run" % run["item"])

    def walk_run(self):
        """The run directory this walk started, as `dfs_runs` currently sees it."""
        return next((r for r in self.chains if r["dir"] == self.walk_dir), None)

    def walk_used(self):
        """Sessions spent so far: the finished chains, plus the live one's own count.

        ⚠️ Read off the chain rather than counted here. `run.sh` records the run
        it is ON before starting it, so this is the number of sessions that have
        BEGUN — which is the one that should be charged against a budget, since a
        session that started has already cost its context.
        """
        run = self.walk_run()
        return self.walk_spent + (int(run["run"] or 0) if run else 0)

    def walk_tick(self):
        """One step of the walk, on the poll this screen already runs.

        ⚠️ It waits on ANY live work chain, not only its own: two agents in one tree
        append to §3 at the same time, which is how this roadmap's ids collided in
        the first place. The walk is a second pair of hands, not a second author.
        """
        if not self.walk_on:
            return
        # ⚠️ THE WALK DECIDES ON A LIST IT MADE ITSELF. `poll` runs this tick BEFORE
        # it re-derives `self.chains`, so what it hands over is the list
        # `start_chain`'s own reload made — and that reload happens BEFORE the chain
        # has written its `meta.json` (see `WALK_START_GRACE`). Every question below
        # is about chains, so every one of them was being asked of a list that could
        # not contain the chain this walk had just started: the walk turned itself
        # off one second after starting its first chain, every time, and the chain
        # ran on unwatched. Nobody saw why either — `walk_off` writes `self.msg` and
        # the same `poll` overwrote it with "refreshed" before the screen was drawn,
        # which is why a stop is now in the HEADER too (`walk_badge`).
        self.refresh_chains()
        if self.live():
            return
        if time.time() < self.walk_until:
            return

        if self.walk_dir is not None:
            run = self.walk_run()
            if run is None:
                # Made, but not yet named by the chain itself. That is a start in
                # progress, not a death — and the difference is how long it has had.
                if time.time() - self.walk_started < WALK_START_GRACE:
                    return
                self.walk_off("the chain it started is no longer on the box"
                              + chain_cause(self.walk_dir))
                return
            if run["live"]:
                return
            # ⚠️ And the ROADMAP is read after the chain ended, for the same reason
            # the chain list is. `poll` reloads only once this tick has returned, so
            # a chain whose last append and whose exit landed inside the same second
            # would be judged against a §3 that predates its own entry — and an
            # entry the walk cannot see is `moved` false, which STOPS the walk and
            # blames the item for appending nothing.
            self.reload()
            moved = bool(set(self.data.get("content", ())) - self.walk_content)
            action, why = walk_decide(run["stop"], moved,
                                      self.walk_target())
            self.walk_spent += int(run["run"] or 0)
            self.walk_dir = None
            if action == "hold":
                # Until the stated reset when there is one; the flat hour only when
                # the provider said nothing we can parse (dfs_limit.reset_at).
                now = time.time()
                at = dfs_limit.reset_at(run["resets"], now)
                self.walk_until = at if at is not None else now + WALK_HOLD
                tail = (" (%s)" % run["resets"]) if run["resets"] else ""
                self.msg = self.event("hold", "walk: %s hit %s — holding %s%s" % (
                    run["item"], why, walk_held_for(self.walk_until - now), tail))
                return
            if action == "stop":
                self.walk_off("%s: %s" % (run["item"], why))
                return
            if action == "raise":
                if not self.walk_raise(run["item"], why):
                    return

        left = self.walk_budget - self.walk_spent
        if left < 1:
            self.walk_off("%d sessions spent" % self.walk_spent)
            return
        self.walk_pinned()          # drops a pin that cannot be worked, and says so
        item = self.walk_target()
        if item is None:
            self.walk_off("every branch is blocked or finished")
            return
        self.walk_next = None
        self.walk_content = set(self.data.get("content", ()))
        self.walk_started = time.time()
        # ⚠️ The chain's cap IS the remaining budget, which is what makes the budget
        # a real bound rather than a thing checked between chains: a chain cannot
        # overshoot it by running one more session than the walk has left to spend.
        self.walk_dir = self.start_chain(item, str(left))
        if self.walk_dir is None:
            self.walk_off("could not start a chain on %s" % item)

    def walk_target(self):
        """The one answer to "which task does the walk take next": the pin while that
        task is open, else the order's answer (`next_item`). The screen's header and walk
        preview, the page's `next`, and the chain the walk starts all read this, so
        what is shown is what is taken. Reads only; `walk_pinned` is what drops a dead pin."""
        pin = self.walk_next
        if pin is not None and any(i["id"] == pin and i["status"] == "open"
                                   for i in self.items):
            return pin
        return self.data.get("next_item")

    def walk_pinned(self):
        """The pinned item, while it can still be worked; a pin that cannot is dropped.

        ⚠️ Checked when the chain STARTS, not when the key was pressed: a chain on an
        item that finished or raised in between would end "done before run 1" having
        moved nothing, and `walk_decide` would raise on it for that. Only the item's
        OWN status is asked — a fence or an ancestor's raise is exactly the order the
        author is overriding by pinning it."""
        if self.walk_next is None:
            return None
        row = next((i for i in self.items if i["id"] == self.walk_next), None)
        if row is None or row["status"] != "open":
            self.msg = "walk: dropped the pin on %s — it is %s" % (
                self.walk_next, row["status"] if row else "gone")
            self.walk_next = None
            return None
        return self.walk_next

    def walk_pin(self, it):
        """`n`: the walk's next chain goes to the selected item, not `next_item`.

        The walk's chain carries the walk's whole remaining budget, so "next" would
        otherwise mean "whenever this item stops being workable". A chain on another
        item is ended after the run in flight (`dfs_runs.lower_cap`, as `w` does), so
        the switch happens at the next session. After that one chain, `next_item`
        decides as usual — and since a walk finishes what it started
        (`dfs_state.continue_last`), that keeps it on the pinned item while it is
        open and nothing holds it. `n` on the pinned item takes the pin away."""
        if it is None:
            return
        if self.walk_next == it["id"]:
            self.walk_next = None
            self.msg = self.event("walk", "walk: pin on %s removed" % it["id"])
            return
        if it["status"] != "open":
            self.msg = "walk: %s is %s — nothing for a chain to do there" % (
                it["id"], it["status"])
            return
        self.walk_next = it["id"]
        run = self.walk_run()
        if not (self.walk_on and run and run["live"]) or run["item"] == it["id"]:
            self.msg = self.event("walk", "walk: its next chain goes to %s" % it["id"]
                                  if self.walk_on else
                                  "walk: %s goes first when w starts it" % it["id"])
            return
        n = int(run["run"] or 0)
        try:
            self.lower_cap(run["dir"], n)
        except OSError as e:
            self.msg = ("walk: %s pinned, but %s's chain could not be told to end (%s)"
                        % (it["id"], run["item"], e))
            return
        self.msg = self.event("walk", "walk: pinned %s — %s's chain ends after run %d" % (
            it["id"], run["item"], n))

    def walk_raise(self, item, why):
        """Put a suspect item to the author and re-read the trees; True to carry on.

        ⚠️ The raise is what moves the walk on, so it is checked rather than trusted:
        if `next_item` still names the item afterwards, the raise did not block it,
        and starting another chain there is the loop this exists to prevent."""
        body = ("The walk suspects a problem with %s: %s. Look at the last chain's "
                "run and the tree (%s --review %s): answer this to let the walk take "
                "it again, or correct the node where it went wrong."
                % (item, why, RUNNER, item))
        try:
            self.raise_on(item, body)
        except (OSError, ValueError) as e:
            self.walk_off("%s: %s, and the raise failed (%s)" % (item, why, e))
            return False
        self.reload()
        if self.data.get("next_item") == item:
            self.walk_off("%s: %s, and the raise did not block it" % (item, why))
            return False
        self.msg = self.event("raise", "walk: raised on %s — %s; moving on" % (item, why))
        return True

    # ---- what both the screen and the page ask of the walk ------------------------

    WATCH_HINT = ""

    def lower_cap(self, rundir, n):
        """End a chain after its run `n`: the cap is lowered to the run it is on."""
        dfs_runs.lower_cap(rundir, n)

    def raise_on(self, item, body):
        """Put `body` to the author on `item`, as a raise in its log."""
        dfs_tree.append_log(item, "raise", (), body)

    def runner(self, *args):
        """The runner argv for a pass.

        `can_fold` and the `--fold` it put on the line are gone with the fold session:
        the fold is the first act of the session that picks the item up (§0.1 step 3),
        so there is no flag and nothing to ask about.
        """
        argv = [RUNNER]
        if self.agent != "claude":
            argv.append("--" + self.agent)
        return argv + [a for a in args if a]

    def start_chain(self, item, cap):
        """Start a work chain in the background and come straight back.

        ⚠️ The run directory is made HERE and handed down. The runner would mktemp
        its own, and a parent that never reads the child's stdout could not learn
        the name — so the console this pane tails would be a file nobody could find.

        Detached (`start_new_session`) so it is not in this screen's process group:
        a Ctrl-C aimed at the TUI, or quitting it, must not take a chain that has
        been running for an hour with it. The flip side is that nothing else will
        stop it either, which is what `K` is for.
        """
        live = dfs_runs.live_for(item)
        if live:
            self.msg = "%s already has a chain running (pid %s, %s)%s" % (
                item, live["pid"], dfs_runs.progress(live), self.WATCH_HINT)
            return
        root = dfs_runs.ensure_run_root()
        rundir = tempfile.mkdtemp(prefix="dfs_run_", dir=root)
        console = os.path.join(rundir, "console.log")
        argv = self.runner(item, cap)
        env = dict(os.environ, RUNDIR=rundir)
        try:
            with open(console, "ab") as fh:
                fh.write(("$ %s\n\n" % " ".join(argv)).encode())
                proc = subprocess.Popen(argv, cwd=dfs_paths.work_root(), env=env,
                                        stdin=subprocess.DEVNULL, stdout=fh,
                                        stderr=subprocess.STDOUT,
                                        start_new_session=True)
        except OSError as e:
            self.msg = "could not start %s: %s" % (argv[0], e)
            return None
        self.reload()
        # The COMMAND, not a paraphrase of it: what this key did is the runner line
        # the console opens with, and saying it here is how the screen teaches the
        # CLI underneath it — the thing that runs without the screen, from a script.
        self.event("run", "$ %s  (pid %d)" % (shlex.join(argv), proc.pid))
        self.msg = "$ %s  — in the background, pid %d%s" % (
            shlex.join(argv), proc.pid, self.WATCH_HINT)
        # ⚠️ The DIRECTORY, not the pid: the walk has to judge the chain it started
        # and no other, and a pid is reused while a run directory is not (see
        # `dfs_runs.pid_is_chain`, which needs both to answer even "is it alive").
        return rundir

    def interrupt_chain(self, item):
        """The signal itself, without the question: what `K` does after its `y`, and what
        the web page's Stop chain does after its own confirmation. ValueError says why not."""
        r = dfs_runs.live_for(item)
        if r is None:
            raise ValueError("no chain is running on %s" % item)
        try:
            os.killpg(r["pid"], signal.SIGINT)
        except OSError as e:
            raise ValueError("could not signal pid %s: %s" % (r["pid"], e))
        self.event("stop", "interrupted %s's chain (pid %s)" % (item, r["pid"]))
        self.msg = ("interrupted %s's chain — it stops after the run it is in; the "
                    "next session picks up the node's uncommitted work" % item)
        self.reload()

    def set_agent(self, name):
        """Which agent run.sh launches next. The screen also keeps it in `agent_file`."""
        if name not in AGENTS:
            raise ValueError("agent must be one of " + ", ".join(AGENTS))
        self.agent = name
        self.msg = "agent: " + name
        self.event("walk", "agent: " + name)

    def walk_start(self, budget):
        """Start a walk from outside the prompt: the page's Start, validated as `w`'s
        number is (and bounded, since a web page is further from the person than a key)."""
        if not isinstance(budget, int) or isinstance(budget, bool) or budget < 1:
            raise ValueError("a walk needs a budget of at least one session")
        if budget > 500:
            raise ValueError("a budget of %d sessions is not a walk, it is a month" % budget)
        if self.walk_on:
            raise ValueError("the walk is already on")
        self.walk_begin(budget)

    def walk_pin_id(self, item):
        """The page's `n`: pin an item by id, or with None (or the pinned one again) take
        the pin away. ValueError says why not."""
        if item is None or item == self.walk_next:
            if self.walk_next is not None:
                self.walk_next = None
                self.event("walk", "walk: pin removed")
            return
        row = next((i for i in self.items if i["id"] == item), None)
        if row is None:
            raise ValueError("no task %s" % item)
        if row["status"] != "open":
            raise ValueError("%s is %s: nothing for a chain to do there" % (item, row["status"]))
        self.walk_pin(row)

    def walk_snapshot(self):
        """Where the walk stands, for the page: read, never written."""
        now = time.time()
        run = self.walk_run()
        items = list(self.items)
        auto = self.data.get("next_item")
        goal = next((i["goal"] for i in items if i["id"] == auto), "")
        return dict(
            next=self.walk_target(),
            on=self.walk_on, budget=self.walk_budget, used=self.walk_used(),
            hold=max(0, int(self.walk_until - now)) if self.walk_until > now else 0,
            note=self.walk_note, agent=self.agent, agents=list(AGENTS),
            pinned=self.walk_next, auto=auto, auto_goal=goal,
            chain=(dict(item=run["item"], run=run["run"], cap=run["cap"],
                        progress=dfs_runs.progress(run), live=run["live"]) if run else None),
            live=[dict(item=r["item"], pid=r["pid"], progress=dfs_runs.progress(r))
                  for r in self.live()],
            candidates=[dict(id=i["id"], goal=i["goal"]) for i in items if i["status"] == "open"],
            events=[dict(age=ago(now - t), kind=k, text=x)
                    for t, k, x in list(self.events)[-12:]][::-1])


def ago(secs):
    """`2m ago`: how long since, in the largest whole unit."""
    for size, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if secs >= size:
            return "%d%s ago" % (secs // size, unit)
    return "just now"


class HeadlessWalk(WalkMixin):
    """The same engine with no screen: `web.py` run on its own. The world is read from
    disk; a lock serialises the page's controls with the thread that steps the walk."""

    # no screen, so no chat of the screen's to join: the page runs its own turns
    has_screen = False

    def chat_live(self, scope):
        return False

    def chat_active(self, scope):
        return False

    def chat_open(self, scope):
        return False

    def chat_send(self, scope, text):
        return False

    def chat_close(self, scope):
        pass

    def chat_interrupt(self, scope):
        return False

    def __init__(self, on_change=None):
        self.lock = threading.RLock()
        self.on_change = on_change or (lambda: None)
        self.msg = ""
        self.chains = []
        self.data = {}
        self.events = collections.deque(maxlen=EVENTS_KEEP)
        self._agent = None
        self.walk_init()

    # the world
    @property
    def agent(self):
        return self._agent or read_agent(agent_file())

    @agent.setter
    def agent(self, name):
        self._agent = name
        try:
            with open(os.path.join(dfs_runs.ensure_run_root(), "agent"), "w") as fh:
                fh.write(name + "\n")
        except OSError:
            pass

    @property
    def items(self):
        return self.data.get("items", [])

    def reload(self):
        self.data = dfs_state.full()

    def refresh_chains(self):
        self.chains = dfs_runs.run_dirs()

    def live(self, mode="work"):
        return [r for r in self.chains if r["live"] and r["mode"] == mode]

    def event(self, kind, text):
        now = time.time()
        self.events.append((now, kind, text))
        try:         # the same file the terminal screen's `h` pane reads
            path = os.path.join(dfs_runs.ensure_run_root(), "activity.log")
            with open(path, "a") as fh:
                fh.write("%.0f\t%s\t%s\n" % (now, kind, text.replace("\n", " ")))
        except OSError:
            pass
        self.on_change()
        return text

    def notify(self, text):
        pass                # nobody at a terminal to ring

    # the interface the page calls, each under the lock
    def start(self, budget):
        with self.lock:
            self.reload()
            self.walk_start(budget)

    def stop(self):
        with self.lock:
            self.refresh_chains()
            if self.walk_on:
                self.walk_end_chain()

    def pin(self, item):
        with self.lock:
            self.refresh_chains()
            self.reload()
            self.walk_pin_id(item)

    def stop_chain(self, item):
        with self.lock:
            self.interrupt_chain(item)

    def set_agent(self, name):
        with self.lock:
            WalkMixin.set_agent(self, name)

    def snapshot(self, data=None):
        with self.lock:
            self.refresh_chains()
            self.reload()
            return self.walk_snapshot()

    def loop(self, stop, interval=TICK):
        while True:
            with self.lock:
                try:
                    self.refresh_chains()
                    self.reload()
                    self.walk_tick()
                except Exception as e:          # a thread that dies stops a walk silently
                    if self.walk_on:
                        self.walk_off("the walk failed: %s" % e)
            if stop.wait(interval):
                return
