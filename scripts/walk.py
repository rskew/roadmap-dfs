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
MAX_PARALLEL = 16    # chains at once, as far as a page may ask
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
    "brief": "a session could not be briefed",
    "lost": "the chain died with its supervisor and never finished",
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


def walk_decide(stop, moved, next_item, cause=""):
    """What the walk does after a chain ends, as `(action, why)`.

    `run` carries the item, `hold`, `pend` and `stop` carry a reason to show a person.
    `pend` is nothing workable now: the walk stays on and waits for a task to appear.
    `cause` is what the chain recorded beside its stop (`brief` has one), added to a stop's reason.

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
        why = WALK_STOP.get(stop) or stop or "the chain ended without recording why"
        return ("stop", "%s: %s" % (why, cause) if cause else why)
    if not moved and stop != "done":
        return ("raise", "the chain moved nothing in the task tree")
    if next_item is None:
        return ("pend", "every branch is blocked or finished")
    return ("run", next_item)


def walk_held_for(seconds):
    """A hold, in the units a person reads: whole minutes, floor 1."""
    return "%dm" % max(1, int(seconds // 60))


class WalkMixin:
    """The walk's state and steps. See the module docstring for the world it asks of
    `self`. Everything the walk does it also says through `event`."""

    # Defaults for a screen built without `walk_init` (the tests do): never changed in
    # place, only rebound, so one value serves every instance.
    walk_parallel = 1
    walk_chains = ()
    walk_carry = ()
    walk_carry_pinned = ()
    walk_stops = frozenset()
    walk_on = False

    def close_lost_runs(self):
        """Close, and say, each run record a dead supervisor left open: called once as a
        screen starts, the terminal one and the standalone page alike."""
        for r in dfs_runs.close_lost():
            self.event("died", "%s's chain died with its supervisor (%s, run %s): its pid %d "
                       "was gone and it never finished" % (
                           r["item"], os.path.basename(r["dir"]), r["run"] or "?", r["pid"]))

    def walk_init(self):
        self.walk_on = False
        self.walk_budget = 20       # SESSIONS, over the whole walk — see walk_toggle
        self.walk_spent = 0         # sessions run by chains this walk has finished
        self.walk_until = 0.0       # epoch; a usage-limit hold
        self.walk_parallel = 1      # chains at once, each on a task of its own
        # One record per chain THIS walk started and has not yet judged: `dir` (its run
        # directory), `item`, `started` (epoch — see walk_tick), `content` (what that
        # task's tree said when it started), `pin` (the author pinned it) and `cap`
        # (the sessions it may run: its share of the budget, lowered when it is told to end).
        self.walk_chains = []
        self.walk_carry = []        # tasks whose chain ended on its cap or a limit, still open: they go first
        self.walk_carry_pinned = set()  # of those, the ones the author pinned: a draft among them stays workable
        self.walk_note = ""         # why the last walk stopped, until the next one
        self.walk_next = None       # an item the author pinned for the next chain — walk_pin
        self.walk_pending = False   # on, but nothing is workable: waiting for a task to appear
        self.walk_stops = frozenset()   # tasks behind a stop break that held them this walk

    def walk_begin(self, budget, parallel=1):
        """Turn the walk on with `budget` sessions, run `parallel` chains at a time: what `w`
        does once the numbers are in, and what the web page's Start does. A ValueError says
        why not."""
        if budget < 1:
            raise ValueError("a budget of %d sessions is not a walk" % budget)
        if parallel < 1:
            raise ValueError("a walk runs at least one chain at a time, not %d" % parallel)
        if parallel > MAX_PARALLEL:
            raise ValueError("%d chains at once is more than a machine runs: at most %d"
                             % (parallel, MAX_PARALLEL))
        self.walk_budget = budget
        self.walk_parallel = parallel
        self.walk_spent = 0
        self.walk_on = True
        self.walk_until = 0.0
        self.walk_chains = []
        self.walk_carry = []
        self.walk_carry_pinned = set()
        self.walk_note = ""
        self.walk_pending = False
        self.walk_stops = frozenset()
        both = "%d sessions" % budget + (", %d at a time" % parallel if parallel > 1 else "")
        self.msg = "walk: on, %s — w to stop" % both
        if parallel > 1 and not self.walk_cuts_new():
            # No worktree is cut here (the checkout is off main/master, has no commit, or is
            # no repo), so a task without one would be worked in the one checkout; see
            # `walk_tick`.
            self.msg += ("; no worktree is cut here, so tasks without one run in this "
                         "checkout one at a time")
        self.event("walk", "walk on, %s" % both)
        self.walk_tick()

    def walk_off(self, why):
        """Turn the walk off, and end every chain of it still running after the run it is
        in. Returns `(lowered, failed)`: the `(item, run)` pairs told to end and the
        `(item, error)` pairs that could not be.

        ⚠️ A walk's chain carries a share of the walk's budget as its cap, so a walk turned
        off with chains left alone let them run every session they had, unwatched — W19
        went from run 3 to run 5 of 33 after the walk was off. Each cap is lowered to the
        run its chain is on (`dfs_runs.lower_cap`), which is a chain of one from here: the
        session in flight finishes its node's work, and no further one starts. `K` is
        still the key for stopping the run in flight as well."""
        chains = self.walk_chains
        self.walk_on = False
        self.walk_chains = []
        self.walk_carry = []
        self.walk_carry_pinned = set()
        self.walk_until = 0.0
        self.walk_pending = False
        self.walk_note = why
        self.msg = "walk: stopped — %s" % why
        self.event("stop", "walk off — %s" % why)
        if why != "stopped by hand":
            self.notify("walk stopped: %s" % why)
        return self.walk_lower(chains)

    def walk_lower(self, chains):
        """End the live ones of `chains` (records) after the run each is in; see `walk_off`."""
        lowered, failed = [], []
        for rec in chains:
            run = self.walk_run_of(rec)
            if not (run and run["live"]):
                continue
            n = int(run["run"] or 0)
            try:
                self.lower_cap(run["dir"], n)
            except OSError as e:
                failed.append((rec["item"], e))
                continue
            rec["cap"] = n
            lowered.append((rec["item"], n))
            self.event("stop", "%s's chain told to end after run %d" % (rec["item"], n))
        return lowered, failed

    def walk_end_chain(self):
        """Turn the walk off by hand, and end its chains after the run each is in."""
        lowered, failed = self.walk_off("stopped by hand")
        if failed:
            self.msg = ("walk: stopped, but %s's chain could not be told to end (%s) — "
                        "K stops it" % failed[0])
        elif len(lowered) > 1:
            self.msg = ("walk: stopped — %d chains end after their current runs; K stops "
                        "those runs too" % len(lowered))
        elif lowered:
            item, n = lowered[0]
            self.msg = ("walk: stopped — %s's chain ends after run %d; K stops that run too"
                        % (item, n) if n else
                        "walk: stopped — %s's chain ends before its first run" % item)

    def walk_run_of(self, rec):
        """The run directory behind a chain record, as `dfs_runs` currently sees it."""
        return next((r for r in self.chains if r["dir"] == rec["dir"]), None)

    def walk_runs(self):
        """The runs of the chains this walk started that `dfs_runs` can see, oldest first."""
        return [r for r in (self.walk_run_of(c) for c in self.walk_chains) if r]

    def walk_run(self):
        """The first run this walk started, as `dfs_runs` currently sees it (None with none)."""
        return next(iter(self.walk_runs()), None)

    def walk_dirs(self):
        """The run directories of the chains this walk started: how a chain is told to be its."""
        return {c["dir"] for c in self.walk_chains}

    def walk_foreign(self):
        """The live work chains this walk did not start: each fills a slot and its task is busy."""
        own = self.walk_dirs()
        return [r for r in self.live() if r["dir"] not in own]

    def walk_used(self):
        """Sessions spent so far: the finished chains, plus each live chain's own count.

        ⚠️ Read off the chain rather than counted here. `run.sh` records the run
        it is ON before starting it, so this is the number of sessions that have
        BEGUN — which is the one that should be charged against a budget, since a
        session that started has already cost its context.
        """
        return self.walk_spent + sum(int(r["run"] or 0) for r in self.walk_runs())

    def walk_tick(self):
        """One step of the walk, on the poll this screen already runs.

        ⚠️ It counts ANY live work chain against its slots, not only its own: a chain
        started by hand fills one and its task is busy. The walk is a second pair of
        hands, not a second author, and two chains on one task would append to its tree
        at the same time, which is how this roadmap's ids collided in the first place.
        Chains on different tasks share nothing (each has its own worktree).
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
        ended = []
        for rec in self.walk_chains:
            run = self.walk_run_of(rec)
            if run is None:
                # Made, but not yet named by the chain itself. That is a start in
                # progress, not a death — and the difference is how long it has had.
                if time.time() - rec["started"] < WALK_START_GRACE:
                    continue
                self.walk_off("the chain it started is no longer on the box"
                              + chain_cause(rec["dir"]))
                return
            if not run["live"]:
                ended.append((rec, run))
        if ended:
            # ⚠️ And the ROADMAP is read after the chains ended, for the same reason
            # the chain list is. `poll` reloads only once this tick has returned, so
            # a chain whose last append and whose exit landed inside the same second
            # would be judged against a §3 that predates its own entry — and an
            # entry the walk cannot see is `moved` false, which STOPS the walk and
            # blames the item for appending nothing.
            self.reload()
            for rec, run in ended:
                if not self.walk_judge(rec, run):
                    return
        # Recorded on every tick the walk is on, before the returns for a hold and for
        # no free slot: a walk whose slots a hand-started chain filled still waits at
        # the break, and must end there rather than go past it once that chain is done.
        stop = self.walk_stop_at()
        if time.time() < self.walk_until:
            return

        foreign = self.walk_foreign()
        free = self.walk_parallel - len(self.walk_chains) - len(foreign)
        left = self.walk_budget - self.walk_spent
        if left < 1 and not self.walk_chains:
            self.walk_off("%d sessions spent" % self.walk_spent)
            return
        if free < 1:
            return
        busy = {c["item"] for c in self.walk_chains} | {r["item"] for r in foreign}
        items = [i for i in self.items if stop is None or i.get("segment", 0) < stop]
        before = {i["id"] for i in items}
        # ⚠️ A CHAIN WITH NO WORKTREE WORKS IN THE ONE CHECKOUT. Two of them there find each
        # other's half-done work as foreign dirt and interleave commits on one branch, so at
        # most one chain without a worktree runs at a time; those with one run beside it.
        in_place = any(not self.walk_cuts(i) for i in busy)
        behind = {i["id"] for i in self.items} - {i["id"] for i in items}
        lead = [x for x in self.walk_lead(busy)
                if x not in behind or x == self.walk_next or x in self.walk_carry_pinned]
        picks, skipped = [], set()
        while True:
            # A task that would share the checkout with a chain already there is passed
            # over, and the slot goes to the next task, which may have a worktree of its own.
            asked = dfs_state.next_items(items, free - len(picks), busy | skipped | set(picks),
                                         first=[x for x in lead if x not in picks])
            if not asked:
                break
            for item in asked:
                if not self.walk_cuts(item):
                    if in_place:
                        skipped.add(item)
                        continue
                    in_place = True
                picks.append(item)
            if len(picks) >= free or not skipped & set(asked):
                break
        if not picks and stop is not None and not self.walk_chains and not any(
                r["item"] in before for r in foreign) and not any(
                i.get("waiting_on") for i in self.items if i.get("segment", 0) == stop):
            # Everything before the stop break is done and no chain is still working on
            # it, the walk's own or one started by hand (it may still be merging the last
            # task): the end the author asked for, so they review before going on.
            first = next(i["id"] for i in self.items if i.get("segment", 0) == stop)
            self.walk_off("stopped at the stop break above %s: everything before it is "
                          "done — review it, then start a walk to go on" % first)
            return
        if not picks:
            # ⚠️ NOTHING TO TAKE IS A WAIT, NOT AN END. The walk stays on and asks
            # again on every tick (`reload` re-reads the trees), so a task created
            # later, or one a raise's answer unblocks, is taken without the walk
            # being started again. Nothing is spent while it waits. Free slots beside
            # a running chain are not a wait: that chain is the walk's work.
            if not (self.walk_chains or foreign or self.walk_pending):
                self.walk_pending = True
                self.msg = self.event("walk", "walk: nothing workable — waiting for a task")
            return
        for item in picks:
            # ⚠️ A chain's cap is its share of the budget the live chains have not
            # already reserved, which is what makes the budget a real bound rather
            # than a thing checked between chains: the caps of the chains running at
            # once never add up to more than the walk has left to spend.
            avail = left - sum(c["cap"] for c in self.walk_chains)
            if avail < 1:
                return
            if self.walk_pending:
                self.walk_pending = False
                self.event("walk", "walk: %s is workable — resuming" % item)
            cap = max(1, avail // (free - picks.index(item)))
            pin = self.walk_next == item
            self.walk_next = None if pin else self.walk_next
            self.walk_carry = [c for c in self.walk_carry if c != item]
            content = set(self.data.get("contents", {}).get(item, ()))
            started = time.time()
            rundir = self.start_chain(item, str(cap))
            if rundir is None:
                self.walk_off("could not start a chain on %s" % item)
                return
            self.walk_chains = self.walk_chains + [dict(
                dir=rundir, item=item, started=started, content=content, pin=pin, cap=cap)]

    def walk_stop_at(self, keep=True):
        """The segment this walk takes nothing from, or None: the first one a STOP BREAK
        (`--- stop`, `order.py`) opens that has held a task while the walk was on.

        ⚠️ HELD WHILE THE WALK WAS ON, not merely there. A stop break is for the author
        to review what was worked before it, so the walk that worked up to it ends there
        (`walk_tick`), and the walk they start afterwards, which finds it open, passes it.
        The tasks it held are kept by id, so a fence it held stays this walk's end once it
        opens, and a renumbering of segments by a break added mid-walk does not move it.
        Only the walk reads it: what the order says (`next_item`) is the fence as ever.
        The pin is the author overriding the order, and goes past it. `keep` False reads
        without recording what is held now (`walk_target`)."""
        if not self.walk_on:
            return None
        stops = self.walk_stops | {i["id"] for i in self.items
                                   if i.get("stop") and i.get("waiting_on")}
        if keep:
            self.walk_stops = frozenset(stops)
        return min((i.get("segment", 0) for i in self.items
                    if i.get("stop") and i["id"] in stops), default=None)

    def walk_cuts_new(self):
        """Whether a task with no worktree yet gets one cut (`paths.cuts_worktrees`)."""
        return dfs_paths.cuts_worktrees()

    def walk_cuts(self, item):
        """Whether `item`'s chain is worked in a worktree of its own (`paths.gets_worktree`)."""
        return dfs_paths.gets_worktree(item)

    def walk_judge(self, rec, run):
        """Judge one chain that ended: charge its sessions, then what `walk_decide` says.
        False when the walk is off now (and the tick ends)."""
        item = run["item"]
        moved = bool(set(self.data.get("contents", {}).get(item, ())) - rec["content"])
        action, why = walk_decide(run["stop"], moved, self.walk_target(),
                                  run.get("cause", ""))
        self.walk_spent += int(run["run"] or 0)
        self.walk_chains = [c for c in self.walk_chains if c is not rec]
        if action == "hold":
            # Until the stated reset when there is one; the flat hour only when
            # the provider said nothing we can parse (dfs_limit.reset_at).
            now = time.time()
            at = dfs_limit.reset_at(run["resets"], now)
            self.walk_until = max(self.walk_until, at if at is not None else now + WALK_HOLD)
            tail = (" (%s)" % run["resets"]) if run["resets"] else ""
            self.msg = self.event("hold", "walk: %s hit %s — holding %s%s" % (
                item, why, walk_held_for(self.walk_until - now), tail))
            # ⚠️ A limit means "try the same thing again": the task goes first when the
            # hold is over, a draft the author pinned included (`next_item` never offers
            # one). A newer pin is the author's later word and still goes before it.
            self.walk_carry_add(item, rec)
            return True
        if action == "stop":
            self.walk_off("%s: %s" % (item, why))
            return False
        if action == "raise":
            return self.walk_raise(item, why)
        # "run" and "pend" carry on to fill the slots; a chain that used its share of the
        # budget with the task still open has more to do there, and goes first.
        if run["stop"] == "cap":
            self.walk_carry_add(item, rec)
        return True

    def walk_carry_add(self, item, rec):
        """Carry `item` to go first next time. Whether the author pinned it is remembered,
        since a pinned draft is workable here and no other draft is."""
        self.walk_carry = self.walk_carry + [item]
        if rec["pin"] or item in self.walk_carry_pinned:
            self.walk_carry_pinned = set(self.walk_carry_pinned) | {item}

    def walk_lead(self, busy=()):
        """The tasks the walk takes before the order: the pin, the tasks its chains left
        on a cap or a limit while still workable, then the walk's continuation.

        ⚠️ A carried task is asked again what the order would ask: it may have been made a
        draft (`walk_draft_marked` ends its chain on the cap, which is how it got here),
        or an ancestor's raise or a fence may hold it now. Only a draft the author pinned
        is carried on. The continuation is asked the same of a parent in `busy`: it falls
        back to `next_item`, which names the child of a parent whose review has just
        landed and whose chain is still merging it."""
        by = {i["id"]: i for i in self.items}
        pin = self.walk_pinned()    # drops a pin that cannot be worked, and says so
        lead = [pin] if pin else []
        for c in self.walk_carry:
            it = by.get(c)
            if (it and it["status"] == "open" and it.get("reachable", True)
                    and (not it.get("draft") or c in self.walk_carry_pinned)
                    and not dfs_state.held_by_parent(by, it, busy)):
                lead.append(c)
        nxt = self.data.get("next_item")
        it = by.get(nxt)
        if it is None or not dfs_state.held_by_parent(by, it, busy):
            lead.append(nxt)
        return lead

    def walk_target(self):
        """The one answer to "which task does the walk take next": the pin while that
        task is open, else the order's answer (`next_item`), unless that is behind the
        stop break this walk ends at (`walk_stop_at`). The screen's header and walk
        preview, the page's `next`, and the chain the walk starts all read this, so
        what is shown is what is taken. Reads only; `walk_pinned` is what drops a dead pin."""
        pin = self.walk_next
        if pin is not None and any(i["id"] == pin and i["status"] == "open"
                                   for i in self.items):
            return pin
        nxt = self.data.get("next_item")
        stop = self.walk_stop_at(keep=False)
        if stop is not None and any(i["id"] == nxt and i.get("segment", 0) >= stop
                                    for i in self.items):
            return None
        return nxt

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

        A chain's cap is a share of the walk's budget, so "next" would otherwise mean
        "whenever a slot is free". With every slot taken, the walk's most recently
        started chain on another item is ended after the run in flight
        (`dfs_runs.lower_cap`, as `w` does), so the switch happens at the next
        session. After that one chain, the order decides as usual — and since a walk
        finishes what it started (`dfs_state.continue_last`), that keeps it on the
        pinned item while it is open and nothing holds it. `n` on the pinned item takes
        the pin away."""
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
        live = [c for c in self.walk_chains if (self.walk_run_of(c) or {}).get("live")]
        full = len(self.walk_chains) + len(self.walk_foreign()) >= self.walk_parallel
        if not (self.walk_on and live and full) or any(c["item"] == it["id"] for c in live):
            self.msg = self.event("walk", "walk: its next chain goes to %s" % it["id"]
                                  if self.walk_on else
                                  "walk: %s goes first when w starts it" % it["id"])
            return
        rec = max(live, key=lambda c: c["started"])
        run = self.walk_run_of(rec)
        n = int(run["run"] or 0)
        try:
            self.lower_cap(run["dir"], n)
        except OSError as e:
            self.msg = ("walk: %s pinned, but %s's chain could not be told to end (%s)"
                        % (it["id"], run["item"], e))
            return
        rec["cap"] = n
        self.msg = self.event("walk", "walk: pinned %s — %s's chain ends after run %d" % (
            it["id"], run["item"], n))

    def walk_draft_marked(self, item):
        """`item` was just marked a draft: end the walk's own chain on it after the run in
        flight, as `walk_pin` does for a chain on another item. A chain carries a share
        of the walk's budget and `run.sh` has no draft stop, so it would otherwise work
        the draft to the end. A chain the author pinned stays: pinning was asking for it."""
        rec = next((c for c in self.walk_chains if c["item"] == item), None)
        run = self.walk_on and rec and self.walk_run_of(rec)
        if not (run and run["live"]) or rec["pin"]:
            return
        n = int(run["run"] or 0)
        try:
            self.lower_cap(run["dir"], n)
        except OSError as e:
            self.msg = "walk: %s is a draft, but its chain could not be told to end (%s)" % (item, e)
            return
        rec["cap"] = n
        self.msg = self.event("walk", "walk: %s is a draft — its chain ends after run %d" % (item, n))

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

    def runner(self, *args, agent=None):
        """The runner argv for a pass, under `agent` (the screen's, by default).

        `can_fold` and the `--fold` it put on the line are gone with the fold session:
        the fold is the first act of the session that picks the item up (§0.1 step 3),
        so there is no flag and nothing to ask about.
        """
        argv = [RUNNER]
        agent = agent or self.agent
        if agent != "claude":
            argv.append("--" + agent)
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

    def walk_start(self, budget, parallel=1):
        """Start a walk from outside the prompt: the page's Start, validated as `w`'s
        numbers are (and bounded, since a web page is further from the person than a key)."""
        if not isinstance(budget, int) or isinstance(budget, bool) or budget < 1:
            raise ValueError("a walk needs a budget of at least one session")
        if budget > 500:
            raise ValueError("a budget of %d sessions is not a walk, it is a month" % budget)
        if not isinstance(parallel, int) or isinstance(parallel, bool) or parallel < 1:
            raise ValueError("a walk runs at least one chain at a time")
        if parallel > MAX_PARALLEL:
            raise ValueError("%d chains at once is more than a machine runs: at most %d"
                             % (parallel, MAX_PARALLEL))
        if self.walk_on:
            raise ValueError("the walk is already on")
        self.walk_begin(budget, parallel)

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
        items = list(self.items)
        auto = self.data.get("next_item")
        goal = next((i["goal"] for i in items if i["id"] == auto), "")
        return dict(
            next=self.walk_target(),
            on=self.walk_on, pending=self.walk_pending,
            budget=self.walk_budget, used=self.walk_used(), parallel=self.walk_parallel,
            hold=max(0, int(self.walk_until - now)) if self.walk_until > now else 0,
            note=self.walk_note, agent=self.agent, agents=list(AGENTS),
            pinned=self.walk_next, auto=auto, auto_goal=goal,
            chains=[dict(item=r["item"], run=r["run"], cap=r["cap"],
                         progress=dfs_runs.progress(r), live=r["live"])
                    for r in self.walk_runs()],
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

    def chat_failure(self, scope):
        return None

    def chat_close(self, scope):
        pass

    def chat_interrupt(self, scope):
        return None

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
    def start(self, budget, parallel=1):
        with self.lock:
            self.reload()
            self.walk_start(budget, parallel)

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

    def draft_marked(self, item):
        with self.lock:
            self.refresh_chains()
            self.walk_draft_marked(item)

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
