#!/usr/bin/env python3
"""A net under the autonomous work — the one thing here that spends money unwatched.

What is checked is the POLICY: given how a chain ended, whether the trees moved while it ran,
and what a depth-first work would take next, does the work run again, wait, or stop?
The tree's own shape — who blocks whom, what `next_item` means — is checked beside it
in `test_order.py`; this file assumes that and asks only what happens afterwards.

⚠️ The test worth having is not "does it keep going". It is "does it STOP", because
the failure that costs something is a work that treats a broken item as a temporary
one and restarts it all night.

Run:  python3 roadmap-dfs/scripts/test_work.py
"""
import importlib.util
import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

TUI_SPEC = importlib.util.spec_from_file_location("dfs_tui", HERE / "tui.py")
TUI = importlib.util.module_from_spec(TUI_SPEC)
TUI_SPEC.loader.exec_module(TUI)


def rec(**kw):
    """A chain the work started and has not yet judged, as `work_chains` holds it."""
    row = dict(dir="/runs/mine", item="W7", started=0.0, content=set(), pin=False, cap=20)
    row.update(kw)
    return row


class Deciding(unittest.TestCase):
    """`work_decide(stop, moved, next_item)` — the whole policy, as a value."""

    def test_a_finished_or_blocked_item_moves_the_work_on(self):
        # `done` and `blocked` are both PROGRESS: one finishes a branch, the other
        # hands the author a question and frees the work to take the next branch.
        self.assertEqual(TUI.work_decide("done", True, "W8"), ("run", "W8"))
        self.assertEqual(TUI.work_decide("blocked", True, "W9"), ("run", "W9"))

    def test_the_cap_runs_the_same_item_again(self):
        # A chain that used its whole cap with the item still open has more to do
        # there, and `next_item` says so by naming it again.
        self.assertEqual(TUI.work_decide("cap", True, "W7"), ("run", "W7"))

    def test_only_a_usage_limit_waits(self):
        action, why = TUI.work_decide("limit", False, "W7")
        self.assertEqual(action, "hold")
        self.assertIn("usage limit", why)
        self.assertEqual(TUI.WORK_HOLD, 3600)

    def test_a_usage_limit_stops_the_work_when_the_work_was_told_to(self):
        action, why = TUI.work_decide("limit", False, "W7", stop_on_limit=True)
        self.assertEqual(action, "stop")
        self.assertIn("usage limit", why)
        # the option is about limits only: a finished chain still goes on
        self.assertEqual(TUI.work_decide("done", True, "W8", stop_on_limit=True), ("run", "W8"))

    def test_an_ending_about_the_item_raises_on_it_and_never_holds(self):
        # ⚠️ A usage limit and a crash are both `rc 1`. If any of these ever fell
        # through to "hold", an unattended work would retry a broken item hourly
        # until somebody noticed the bill. They RAISE: the item waits for the author
        # and the work takes the next branch, rather than every branch waiting.
        for stop in ("idle",):
            self.assertEqual(TUI.work_decide(stop, True, "W7")[0], "raise", stop)

    def test_a_chain_that_could_not_brief_a_session_says_why_it_stopped(self):
        action, why = TUI.work_decide("brief", False, "W7", "the worktree has no W7.md")
        self.assertEqual(action, "stop")
        self.assertEqual(why, "a session could not be briefed: the worktree has no W7.md")
        self.assertEqual(TUI.work_decide("", False, "W7")[1], "the chain ended without recording why")

    def test_what_no_other_item_would_survive_stops_the_work(self):
        for stop in ("interrupt", "died", "something-new",
                     "failed", "overbudget", "overturns"):
            self.assertEqual(TUI.work_decide(stop, True, "W7")[0], "stop", stop)

    def test_a_chain_that_recorded_nothing_stops_rather_than_guesses(self):
        action, why = TUI.work_decide("", False, "W7")
        self.assertEqual(action, "stop")
        self.assertIn("without recording why", why)

    def test_a_happy_chain_that_moved_nothing_raises_on_its_item(self):
        # The redundant guard: the runner refuses to repeat an empty session inside
        # a chain, and this refuses to start another chain on an item producing
        # nothing. It RAISES rather than stops: the suspicion is about one item, and
        # a raise blocks that item while the work takes the next branch.
        action, why = TUI.work_decide("cap", False, "W8")
        self.assertEqual(action, "raise")
        self.assertIn("moved nothing", why)

    def test_a_chain_that_finished_its_item_moves_on_though_it_moved_nothing(self):
        # A finished item's last session is the review that found nothing, and a
        # verdict is not tree content, so it always moves nothing. W20 raised on
        # that 2026-09-30; a done item closes, and the work takes the next branch.
        self.assertEqual(TUI.work_decide("done", False, "W8"), ("run", "W8"))
        action, why = TUI.work_decide("done", False, None)
        self.assertEqual(action, "pend")
        self.assertIn("blocked or finished", why)

    def test_an_idle_session_raises_on_its_item(self):
        action, why = TUI.work_decide("idle", True, "W8")
        self.assertEqual(action, "raise")
        self.assertIn("changed nothing", why)

    def test_nothing_left_to_do_is_a_wait_not_a_stop(self):
        action, why = TUI.work_decide("done", True, None)
        self.assertEqual(action, "pend")
        self.assertIn("blocked or finished", why)

    def test_a_hold_reads_in_whole_minutes_and_never_zero(self):
        self.assertEqual(TUI.work_held_for(3600), "60m")
        self.assertEqual(TUI.work_held_for(90), "1m")
        self.assertEqual(TUI.work_held_for(0), "1m")


class Placing(unittest.TestCase):
    """`chain_progress` — which budget a running chain is counted against."""

    def ui(self, **kw):
        ui = object.__new__(TUI.UI)
        ui.work_on = True
        ui.work_budget = 100
        ui.work_spent = 3
        ui.work_chains = [rec(dir="/runs/work")]
        ui.chains = []
        ui.__dict__.update(kw)
        return ui

    def chain(self, **kw):
        row = dict(dir="/runs/work", item="W15", run="1", cap="97", live=True)
        row.update(kw)
        return row

    def test_a_works_chain_is_placed_in_the_WORK(self):
        # ⚠️ The regression this exists for. `cap` on a work-started chain is the
        # budget that was REMAINING, so `run/cap` read `run 1/97` for the fourth
        # session of a hundred — a fraction of two different denominators.
        c = self.chain()
        ui = self.ui(chains=[c])
        self.assertEqual(ui.chain_progress(c), "run 4/100")

    def test_a_chain_started_by_hand_keeps_its_own_cap(self):
        # Off `s`, the cap IS the bound somebody typed, so it is the honest one.
        c = self.chain(dir="/runs/by-hand", cap="5", run="2")
        ui = self.ui(chains=[c])
        self.assertEqual(ui.chain_progress(c), "run 2/5")

    def test_a_chain_beside_a_work_is_not_counted_into_it(self):
        # Another agent's chain in the same tree shares the screen, never the budget.
        other = self.chain(dir="/runs/somebody-else", item="W7", cap="5", run="1")
        ui = self.ui(chains=[self.chain(), other])
        self.assertEqual(ui.chain_progress(other), "run 1/5")

    def test_with_the_work_off_every_chain_keeps_its_cap(self):
        # A TUI restarted mid-work has no work state, so it must not invent one.
        c = self.chain()
        ui = self.ui(work_on=False, chains=[c])
        self.assertEqual(ui.chain_progress(c), "run 1/97")


class Ticks:
    """The fake screen the tick tests drive: its world is a few attributes and stubs."""

    def ui(self, **kw):
        ui = object.__new__(TUI.UI)
        ui.msg = ""
        ui.work_on = True
        ui.work_budget = 20
        ui.work_spent = 0
        ui.work_until = 0.0
        ui.work_parallel = 1
        ui.work_chains = []
        ui.work_carry = []
        ui.work_note = ""
        ui.work_next = None
        ui.work_pending = False
        ui.chains = []
        # The seam the tick re-asks through. Stubbed to a no-op so a test says which
        # chains are on the box; `re_ask` below hands it a list to find them in.
        ui.re_asked = 0
        def refresh_chains(rows=None):
            ui.re_asked += 1
            if rows is not None:
                ui.chains = rows
        ui.refresh_chains = refresh_chains
        # The other re-read: the trees as they are once the chain has ended. A test
        # says what they moved to by setting `ui.data` in the stub. `content` is
        # `dfs_state.content` over every tree: what the trees SAY, as a set.
        ui.reloaded = 0
        def reload():
            ui.reloaded += 1
        ui.reload = reload
        ui.data = {"contents": {"W7": ["a", "b", "new"]}, "next_item": "W7"}
        # A raise is a write to a real task file; the tests record it instead.
        ui.raised = []
        real = TUI.dfs_tree.append_log
        TUI.dfs_tree.append_log = lambda item, kind, args=(), body="": (
            ui.raised.append((item, kind, body)))
        self.addCleanup(setattr, TUI.dfs_tree, "append_log", real)
        ui.started = []
        ui.start_chain = lambda item, cap: (ui.started.append((item, cap))
                                            or "/runs/new")
        # Whether a task gets a worktree: yes, unless a test says otherwise (the real answer
        # is about the checkout the tests run in).
        ui.work_cuts = lambda item: True
        ui.__dict__.update(kw)
        ui.data.setdefault("items", [])
        return ui

    rec = staticmethod(rec)

    def live(self, ui, rows):
        ui.chains = rows
        ui.live = lambda mode="work": [r for r in rows if r["live"]]

    def chain(self, **kw):
        row = dict(dir="/runs/mine", item="W7", live=False, stop="done", resets="",
                   run="1")
        row.update(kw)
        return row

    def lowering(self):
        lowered = []
        real = TUI.dfs_runs.lower_cap
        TUI.dfs_runs.lower_cap = lambda d, cap: lowered.append((d, cap))
        self.addCleanup(setattr, TUI.dfs_runs, "lower_cap", real)
        return lowered

    def items(self, ui, **status):
        ui.data["items"] = [dict(id=k, status=v) for k, v in status.items()]


class Ticking(Ticks, unittest.TestCase):
    """The tick around that policy: what it waits for, and whose chain it judges."""

    def test_it_waits_while_ANY_chain_is_live(self):
        # ⚠️ Not only its own. Two agents appending to §3 at once is how this
        # roadmap's ids collided in the first place.
        ui = self.ui()
        self.live(ui, [self.chain(dir="/runs/somebody-else", live=True)])
        ui.work_tick()
        self.assertEqual(ui.started, [], "started work beside a live chain")

    def test_with_nothing_workable_it_waits_and_stays_on(self):
        ui = self.ui(data={"contents": {"W7": ["a"]}, "next_item": None})
        events = []
        ui.event = lambda kind, text: events.append(text) or text
        self.live(ui, [])
        ui.work_tick()
        ui.work_tick()
        self.assertTrue(ui.work_on)
        self.assertTrue(ui.work_pending)
        self.assertEqual(ui.started, [])
        self.assertEqual(len(events), 1, "the wait is said once, not on every tick")

    def test_a_chain_that_leaves_nothing_workable_ends_in_a_wait_not_a_stop(self):
        ui = self.ui(data={"contents": {"W7": ["a", "b", "new"]},
                                                    "next_item": None}, work_chains=[self.rec(dir="/runs/mine")])
        self.live(ui, [self.chain()])
        ui.work_tick()
        self.assertTrue(ui.work_on)
        self.assertTrue(ui.work_pending)
        self.assertEqual(ui.work_chains, [])

    def test_a_task_that_appears_while_it_waits_is_taken(self):
        ui = self.ui(data={"contents": {"W7": ["a"]}, "next_item": None})
        ui.event = lambda kind, text: text
        self.live(ui, [])
        ui.work_tick()
        ui.data = {"contents": {"W7": ["a", "b"]}, "next_item": "W9", "items": []}
        ui.work_tick()
        self.assertFalse(ui.work_pending)
        self.assertEqual(ui.started, [("W9", "20")])

    def test_it_starts_the_next_item_when_nothing_is_running(self):
        ui = self.ui()
        self.live(ui, [])
        ui.work_tick()
        self.assertEqual(ui.started, [("W7", "20")],
                         "the chain's cap is the work's remaining budget")
        self.assertEqual([(c["dir"], c["item"], c["content"]) for c in ui.work_chains],
                         [("/runs/new", "W7", {"a", "b", "new"})],
                         "records what the task's tree says, to compare against")

    def test_a_chain_that_died_before_its_meta_says_why(self):
        # run.sh exits 2 when `agent.py --rev` cannot fetch nixpkgs, before any
        # meta.json: the work sees no chain, and the cause is only in console.log.
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d)
        cause = ("--rev failed: error: unable to download "
                 "'https://api.github.com/repos/nixos/nixpkgs/commits/" + "a" * 40 +
                 "': HTTP error 404 \u2014 404: Not Found")
        Path(d, "console.log").write_text(
            "$ run.sh W7\n\ndfs_agent: " + cause + "\ndfs_agent: set AGENT_NIXPKGS_REV=<rev>\n")
        ui = self.ui(work_chains=[self.rec(dir=d, started=time.time() - 60)])
        self.live(ui, [])
        ui.work_tick()
        self.assertFalse(ui.work_on)
        self.assertIn(cause, ui.work_note)
        self.assertIn(cause, ui.msg)
        # No console, or none of ours: the old reason, unchanged.
        ui = self.ui(work_chains=[self.rec(dir=d + "/nope", started=time.time() - 60)])
        self.live(ui, [])
        ui.work_tick()
        self.assertEqual(ui.work_note, "the chain it started is no longer on the box")

    def test_it_holds_without_starting_anything(self):
        ui = self.ui(work_until=time.time() + 600,
                     work_chains=[self.rec(dir="/runs/mine", started=time.time())])
        self.live(ui, [])
        ui.work_tick()
        self.assertEqual(ui.started, [])
        self.assertTrue(ui.work_on, "a hold is not a stop")

    def test_a_usage_limit_holds_and_says_when_it_resets(self):
        ui = self.ui(work_chains=[self.rec(dir="/runs/mine", content={"a", "b"})])
        self.live(ui, [self.chain(stop="limit", resets="resets 3pm")])
        ui.work_tick()
        self.assertEqual(ui.started, [])
        self.assertTrue(ui.work_on)
        self.assertGreater(ui.work_until, time.time() + 3000,
                           "an unparseable reset falls back to the flat hour")
        self.assertIn("resets 3pm", ui.msg)

    def test_a_limit_ends_the_work_when_it_was_told_to_stop_on_one(self):
        ui = self.ui(work_stop_on_limit=True,
                     work_chains=[self.rec(dir="/runs/mine", content={"a", "b"})])
        self.live(ui, [self.chain(stop="limit", resets="resets 3pm")])
        ui.work_tick()
        self.assertEqual(ui.started, [])
        self.assertFalse(ui.work_on, "the work is over, not held")
        self.assertEqual(ui.work_until, 0.0)
        self.assertIn("usage limit", ui.work_note)
        self.assertIn("resets 3pm", ui.work_note)

    def test_the_stop_on_limit_option_is_this_work_s_and_is_refused_unless_a_bool(self):
        ui = self.ui()
        ui.work_on = False
        ui.work_begin = lambda budget, par=1, stop=False: ui.__dict__.setdefault("begun", []).append((budget, par, stop))
        ui.work_start(5, 1, True)
        ui.work_start(5)
        self.assertEqual(ui.begun, [(5, 1, True), (5, 1, False)])
        for bad in ("yes", 1, None):
            with self.assertRaises(ValueError):
                ui.work_start(5, 1, bad)

    def test_a_parseable_reset_holds_until_it_not_an_hour(self):
        import calendar
        later = time.gmtime(time.time() + 5 * 3600)
        stamp = "resets %s %d, %d:%02d%s (UTC)" % (
            time.strftime("%b", later), later.tm_mday,
            later.tm_hour % 12 or 12, later.tm_min, "pm" if later.tm_hour >= 12 else "am")
        ui = self.ui(work_chains=[self.rec(dir="/runs/mine", content={"a", "b"})])
        self.live(ui, [self.chain(stop="limit", resets=stamp)])
        ui.work_tick()
        want = calendar.timegm(later[:5] + (0,)) + TUI.dfs_limit.RESET_MARGIN
        self.assertEqual(int(ui.work_until), want)
        self.assertRegex(ui.msg, r"holding \d+m")

    def test_a_finished_chain_leads_to_the_next_one(self):
        ui = self.ui(work_chains=[self.rec(dir="/runs/mine", content={"a", "b"})])
        self.live(ui, [self.chain(stop="done", run="3")])
        ui.data["next_item"] = "W8"
        ui.work_tick()
        self.assertEqual(ui.started, [("W8", "17")], "three sessions already spent")
        self.assertEqual(ui.work_spent, 3)
        self.assertEqual(ui.reloaded, 1, "judged §3 without re-reading it")

    def test_an_entry_written_in_the_chain_s_last_second_still_counts(self):
        # ⚠️ `moved` is the guard between a broken item and an overnight loop, so it
        # must be asked of §3 as it is NOW: `poll` reloads only after this tick, and
        # a chain whose RAISE and whose exit land inside one second would otherwise
        # be read as having appended nothing — stopping the work and blaming it.
        ui = self.ui(work_chains=[self.rec(dir="/runs/mine", content={"a", "b"})])
        self.live(ui, [self.chain(stop="blocked", run="1")])
        ui.data["contents"] = {"W7": ["a", "b"]}     # the tick before its last append
        ui.reload = lambda: ui.data.__setitem__("contents", {"W7": ["a", "b", "raise"]})
        ui.data["next_item"] = "W8"
        ui.work_tick()
        self.assertEqual(ui.started, [("W8", "19")], "stopped on a stale §3")

    def test_a_chain_that_moved_nothing_raises_and_the_work_moves_on(self):
        # A suspect item is put to the author and the work takes the next branch;
        # it used to turn the whole work off, holding every other item hostage.
        ui = self.ui(work_chains=[self.rec(dir="/runs/mine", content={"a", "b"})])
        self.live(ui, [self.chain(item="W7", stop="cap", run="2")])
        ui.data["contents"] = {"W7": ["a", "b"]}
        # The raise blocks W7, so the re-read after it names the next branch.
        ui.reload = lambda: (ui.data.__setitem__("next_item", "W8")
                             if ui.raised else None)
        ui.work_tick()
        self.assertEqual([(i, k) for i, k, _ in ui.raised], [("W7", "raise")])
        self.assertIn("moved nothing", ui.raised[0][2])
        self.assertTrue(ui.work_on, "a suspect item is a raise, not a stop")
        self.assertEqual(ui.started, [("W8", "18")])

    def test_a_tidy_into_the_archive_is_not_a_chain_that_moved_nothing(self):
        # ⚠️ The case that turned the work off, 2026-09-27: a W13 session did real
        # work and obeyed dfs_check's budget by moving determined nodes' Plans to
        # the archive. The COUNT fell 148 -> 143; the SET gained what the work wrote.
        ui = self.ui(work_chains=[self.rec(dir="/runs/mine", content={"plan1", "plan2", "ev1"})])
        self.live(ui, [self.chain(item="W13", stop="done", run="1")])
        ui.data.update(contents={"W13": ["ev1", "ev2"]}, next_item="W14")
        ui.work_tick()
        self.assertEqual(ui.raised, [])
        self.assertEqual(ui.started, [("W14", "19")])

    def test_a_raise_that_does_not_block_the_item_stops_the_work(self):
        # The raise is what moves the work on, so it is checked: an item still next
        # after it would be started again, which is the loop this guards against.
        ui = self.ui(work_chains=[self.rec(dir="/runs/mine", content={"a"})])
        self.live(ui, [self.chain(item="W7", stop="cap", run="1")])
        ui.data.update(contents={"W7": ["a"]}, next_item="W7")
        ui.work_tick()
        self.assertEqual(len(ui.raised), 1)
        self.assertFalse(ui.work_on)
        self.assertEqual(ui.started, [])
        self.assertIn("did not block it", ui.work_note)

    def test_a_chain_that_vanished_stops_the_work(self):
        # Its directory is the only handle there is; without it nothing can say how
        # it ended, and guessing is the one thing this must not do.
        ui = self.ui(work_chains=[self.rec(dir="/runs/gone", started=time.time() - 600)])
        self.live(ui, [])
        ui.work_tick()
        self.assertFalse(ui.work_on)
        self.assertEqual(ui.started, [])
        self.assertEqual(ui.re_asked, 1, "condemned a chain without asking again")

    def test_a_chain_that_has_not_written_its_meta_yet_is_not_a_dead_one(self):
        # ⚠️ The bug this file existed and did not catch, measured 2026-09-16: the
        # run directory is made by `start_chain` and IDENTIFIED by the chain about
        # 110 ms later, while `start_chain`'s own reload lists the directories at
        # about 55 ms — so the work's own chain is missing from the list the work is
        # then handed, and the work turned itself off ONE SECOND after starting its
        # first chain. Every work. The chain itself ran on, unwatched.
        ui = self.ui(work_chains=[self.rec(dir="/runs/mine", started=time.time())])
        self.live(ui, [])
        ui.work_tick()
        self.assertTrue(ui.work_on, "a chain that has not spoken yet is not a dead one")
        self.assertEqual(ui.started, [], "and it is not a second chain either")

    def test_it_judges_the_chain_once_the_list_catches_up(self):
        # The other half: the wait is for the chain to IDENTIFY itself, not a timer
        # that eventually gives up. As soon as re-asking finds it, it is judged.
        ui = self.ui(work_chains=[self.rec(dir="/runs/mine", content={"a", "b"}, started=time.time())])
        self.live(ui, [])
        ui.refresh_chains = lambda: (setattr(ui, "re_asked", ui.re_asked + 1),
                                     self.live(ui, [self.chain(stop="done", run="1")]))
        ui.data["next_item"] = "W8"
        ui.work_tick()
        self.assertEqual(ui.re_asked, 1)
        self.assertEqual(ui.started, [("W8", "19")], "went on to the next branch")

    def test_the_grace_is_not_a_second_chain(self):
        # While it waits, it must not start anything else — the budget is spent by
        # chains, and a work that started one and then started another because it
        # could not see the first is the overnight loop this file is written against.
        ui = self.ui(work_chains=[self.rec(dir="/runs/mine", started=time.time())])
        self.live(ui, [])
        for _ in range(5):
            ui.work_tick()
        self.assertEqual(ui.started, [])
        self.assertTrue(ui.work_on)

    def test_the_budget_is_over_the_WORK_not_over_an_item(self):
        # ⚠️ The mistake this replaced: a per-item cap bounded nothing, because a
        # chain that used one up left the item open, so the work started another on
        # the same item. Spending has to be counted across chains or not at all.
        ui = self.ui(work_budget=4, work_spent=1, work_chains=[self.rec(dir="/runs/mine", content={"a", "b"})])
        self.live(ui, [self.chain(stop="cap", run="3")])
        ui.work_tick()
        self.assertEqual(ui.work_spent, 4)
        self.assertFalse(ui.work_on, "spent its budget over two chains")
        self.assertEqual(ui.started, [])
        self.assertIn("4 sessions spent", ui.msg)

    def test_a_chain_cannot_overshoot_what_is_left(self):
        ui = self.ui(work_budget=20, work_spent=18)
        self.live(ui, [])
        ui.work_tick()
        self.assertEqual(ui.started, [("W7", "2")])

    def test_the_live_chain_counts_against_the_budget_as_it_goes(self):
        # Sessions that have BEGUN are charged: a session that started has already
        # cost its context, whatever it goes on to do.
        ui = self.ui(work_spent=5, work_chains=[self.rec(dir="/runs/mine")])
        self.live(ui, [self.chain(live=True, run="4")])
        self.assertEqual(ui.work_used(), 9)

    def test_turning_it_off_forgets_the_chain_and_the_hold(self):
        ui = self.ui(work_until=time.time() + 600, work_chains=[self.rec(dir="/runs/mine")])
        ui.work_off("by hand")
        self.assertFalse(ui.work_on)
        self.assertEqual(ui.work_chains, [])
        self.assertEqual(ui.work_until, 0.0)

    def test_turning_it_off_by_hand_ends_its_chain_after_the_run_in_flight(self):
        # ⚠️ The work's chain holds the work's whole remaining budget as its cap, so
        # an off work that left it alone let it run on unwatched (W19: run 3 -> 5 of 33).
        lowered = []
        real = TUI.dfs_runs.lower_cap
        TUI.dfs_runs.lower_cap = lambda d, cap: lowered.append((d, cap))
        self.addCleanup(setattr, TUI.dfs_runs, "lower_cap", real)
        ui = self.ui(work_chains=[self.rec(dir="/runs/mine")])
        self.live(ui, [self.chain(live=True, run="3")])
        ui.work_toggle()
        self.assertFalse(ui.work_on)
        self.assertEqual(lowered, [("/runs/mine", 3)])
        self.assertIn("ends after run 3", ui.msg)

    def test_turning_it_off_leaves_somebody_else_s_chain_alone(self):
        lowered = []
        real = TUI.dfs_runs.lower_cap
        TUI.dfs_runs.lower_cap = lambda d, cap: lowered.append((d, cap))
        self.addCleanup(setattr, TUI.dfs_runs, "lower_cap", real)
        ui = self.ui(work_chains=[self.rec(dir="/runs/mine")])
        self.live(ui, [self.chain(dir="/runs/by-hand", live=True, run="3")])
        ui.work_toggle()
        self.assertEqual(lowered, [])

    def typed(self, line, budget=12, parallel=1):
        """`w` on an off work, with `line` read off the footer: "" is ⏎, None is Esc."""
        ui = self.ui(work_on=False, work_budget=budget)
        ui.pane, ui.scroll, ui.answers, ui.labels, ui.begun = "items", 0, [], [], []
        ui.read_line = lambda label: ui.labels.append(label) or line
        ui.work_parallel = parallel
        ui.work_begin = lambda budget, par=1, stop_on_limit=False: ui.begun.append(
            (budget, par, True) if stop_on_limit else budget if par == 1 else (budget, par))
        ui.work_toggle()
        return ui

    def test_enter_at_the_work_prompt_works_the_budget_it_shows(self):
        # W82: ⏎ on a number the screen is showing took nothing and said
        # "work: not started"; it is the previous work's budget again.
        ui = self.typed("")
        self.assertEqual(ui.labels, ["work: how many sessions, and at once? [12], esc cancels:"])
        self.assertEqual(ui.begun, [12])

    def test_esc_at_the_work_prompt_leaves_the_work_off(self):
        ui = self.typed(None)
        self.assertEqual(ui.begun, [])
        self.assertIn("not started", ui.msg)

    def test_a_number_typed_at_the_work_prompt_is_the_budget(self):
        self.assertEqual(self.typed("7").begun, [7])
        self.assertEqual(self.typed("seven").begun, [])

    def test_a_second_number_at_the_work_prompt_is_the_chains_at_once(self):
        self.assertEqual(self.typed("20 3").begun, [(20, 3)])
        self.assertEqual(self.typed("20 1").begun, [20])
        self.assertEqual(self.typed("20 three").begun, [])
        self.assertEqual(self.typed("20 3 1").begun, [])

    def test_the_word_stop_at_the_work_prompt_ends_the_work_at_a_usage_limit(self):
        ui = self.typed("7 stop")
        self.assertEqual(ui.begun, [(7, 1, True)])
        self.assertEqual(self.typed("20 3 stop").begun, [(20, 3, True)])
        for bad in ("stop", "stop 7", "7 stop 3", "7 3 4 stop", "7 halt"):
            ui = self.typed(bad)
            self.assertEqual(ui.begun, [], bad)
            self.assertIn("not a number of sessions", ui.msg)

    def test_the_work_command_s_arguments_are_the_prompts_two_numbers(self):
        # `:work-all 20 3` answers the one prompt with "20" and leaves "3" for it.
        ui = self.ui(work_on=False)
        ui.pane, ui.scroll, ui.labels, ui.begun = "items", 0, [], []
        ui.answers = ["20", "3"]
        ui.work_begin = lambda budget, par=1: ui.begun.append((budget, par))
        ui.work_toggle()
        self.assertEqual(ui.begun, [(20, 3)])
        self.assertEqual(ui.answers, [])
        ui.answers = ["20"]
        ui.work_toggle()
        self.assertEqual(ui.begun, [(20, 3), (20, 1)])

    def test_enter_at_the_work_prompt_works_the_last_works_parallel_too(self):
        ui = self.typed("", parallel=3)
        self.assertEqual(ui.labels, ["work: how many sessions, and at once? [12 3], esc cancels:"])
        self.assertEqual(ui.begun, [(12, 3)])

    def test_the_badge_names_every_live_chain_of_the_work(self):
        ui = self.ui(work_chains=[rec(dir="/runs/a", item="W7"), rec(dir="/runs/b", item="W8")])
        self.live(ui, [self.chain(dir="/runs/a", item="W7", live=True),
                       self.chain(dir="/runs/b", item="W8", live=True)])
        self.assertIn("W7 W8", ui.work_badge())

    def test_why_it_stopped_outlives_the_message_line(self):
        # ⚠️ `work_off` writes `self.msg`, and `poll` overwrites it with "refreshed"
        # in the same tick — so the reason was gone before the screen was drawn and
        # the author saw a badge disappear. The badge is what carries it now, until
        # the next work starts.
        ui = self.ui(work_chains=[self.rec(dir="/runs/mine")])
        ui.work_off("W7: the chain appended nothing to §3")
        badge = ui.work_badge()
        self.assertIn("W7", badge)
        self.assertIn("appended nothing", badge)
        ui.work_note = ""
        self.assertEqual(ui.work_badge(), "", "an off work with nothing to say is silent")

    # ── `n`: the author pins the item the work's next chain goes to ────────────

    def test_a_pinned_item_is_where_the_next_chain_starts(self):
        ui = self.ui(work_next="W9")
        self.items(ui, W7="open", W9="open")
        self.live(ui, [])
        ui.work_tick()
        self.assertEqual(ui.started, [("W9", "20")])
        self.assertIsNone(ui.work_next, "a pin is for one chain; the order has the rest")

    def test_a_pinned_draft_is_where_the_next_chain_starts(self):
        # A draft is never the order's answer, and the pin is how the author takes one.
        # `work_pinned` and `work_target` never read `draft`, so this only guards against
        # a future check there; the order's side is `test_order.py`.
        ui = self.ui(work_next="W9")
        ui.data["items"] = [dict(id="W7", status="open"), dict(id="W9", status="open", draft=1)]
        self.live(ui, [])
        ui.work_tick()
        self.assertEqual(ui.started, [("W9", "20")])

    def test_a_pinned_draft_whose_chain_hits_a_limit_is_resumed_after_the_hold(self):
        # The pin is spent when the chain starts, and `next_item` never offers a draft.
        ui = self.ui(work_next="W9")
        ui.data["items"] = [dict(id="W7", status="open"), dict(id="W9", status="open", draft=1)]
        ui.event = lambda kind, text: text
        self.live(ui, [])
        ui.work_tick()
        self.assertEqual(ui.started, [("W9", "20")])
        self.assertIsNone(ui.work_next)
        self.live(ui, [self.chain(dir="/runs/new", item="W9", stop="limit", run="2")])
        ui.work_tick()                      # the limit: hold
        self.assertEqual(len(ui.started), 1)
        ui.work_until = 0.0                 # the hold is over
        self.live(ui, [])
        ui.work_chains = []
        ui.work_tick()
        self.assertEqual(ui.started[-1][0], "W9", "resumed the draft, not next_item")

    def test_a_limit_hold_does_not_override_a_newer_pin(self):
        ui = self.ui(work_next="W9", work_chains=[self.rec(dir="/runs/mine", pin=True)])
        ui.work_next = "W8"
        self.items(ui, W7="open", W8="open", W9="open")
        ui.event = lambda kind, text: text
        self.live(ui, [self.chain(item="W9", stop="limit")])
        ui.work_tick()
        self.assertEqual(ui.work_next, "W8")

    def test_a_pin_overrides_what_the_ended_chain_would_have_led_to(self):
        ui = self.ui(work_next="W9", work_chains=[self.rec(dir="/runs/mine", content={"a", "b"})])
        self.items(ui, W7="open", W9="open")
        self.live(ui, [self.chain(stop="cap", run="2")])
        ui.work_tick()
        self.assertEqual(ui.started, [("W9", "18")])

    def test_a_pin_on_an_item_that_stopped_being_open_is_dropped(self):
        # A chain on a done item ends "done before run 1" having moved nothing,
        # which the work would then raise on.
        ui = self.ui(work_next="W9")
        self.items(ui, W7="open", W9="done")
        self.live(ui, [])
        ui.work_tick()
        self.assertEqual(ui.started, [("W7", "20")])
        self.assertIsNone(ui.work_next)

    def test_pinning_ends_the_work_s_chain_on_another_item_after_its_run(self):
        lowered = self.lowering()
        ui = self.ui(work_chains=[self.rec(dir="/runs/mine")])
        self.live(ui, [self.chain(live=True, run="3")])
        ui.work_pin(dict(id="W9", status="open"))
        self.assertEqual(ui.work_next, "W9")
        self.assertEqual(lowered, [("/runs/mine", 3)])
        self.assertTrue(ui.work_on, "a switch is not a stop")
        self.assertIn("→ W9", ui.work_badge())

    def test_marking_the_item_the_work_s_chain_is_on_a_draft_ends_that_chain_after_its_run(self):
        lowered = self.lowering()
        ui = self.ui(work_chains=[self.rec(dir="/runs/mine")])
        ui.event = lambda kind, text: text
        self.live(ui, [self.chain(live=True, run="3")])
        ui.work_draft_marked("W7")
        self.assertEqual(lowered, [("/runs/mine", 3)])
        self.assertTrue(ui.work_on, "ending a chain is not a stop")

    def test_a_task_made_a_draft_while_its_chain_ran_is_not_carried_on(self):
        ui = self.ui(work_chains=[self.rec(dir="/runs/mine")], work_budget=20)
        ui.event = lambda kind, text: text
        ui.data["items"] = [dict(id="W7", status="open", draft=1, reachable=True),
                            dict(id="W8", status="open", reachable=True)]
        ui.data["next_item"] = "W8"
        ui.data["contents"] = {"W7": ["moved"]}
        self.live(ui, [self.chain(dir="/runs/mine", stop="cap", run="3")])
        ui.work_tick()
        self.assertEqual([i for i, _ in ui.started], ["W8"])

    def test_a_carried_task_that_an_open_ancestor_now_holds_is_not_started(self):
        ui = self.ui(work_chains=[self.rec(dir="/runs/mine", item="W8")])
        ui.event = lambda kind, text: text
        ui.data["items"] = [dict(id="W7", status="open", reachable=True),
                            dict(id="W8", status="open", reachable=True, parent="W7")]
        ui.data["next_item"] = "W7"
        ui.data["contents"] = {"W8": ["moved"]}
        self.live(ui, [self.chain(dir="/runs/mine", item="W8", stop="cap", run="3")])
        ui.work_tick()
        self.assertEqual([i for i, _ in ui.started], ["W7"])

    def test_a_pinned_draft_that_ends_on_its_cap_twice_is_still_carried_on(self):
        ui = self.ui(work_chains=[self.rec(dir="/runs/mine", item="W9", pin=True)])
        ui.event = lambda kind, text: text
        ui.data["items"] = [dict(id="W7", status="open", reachable=True),
                            dict(id="W9", status="open", draft=1, reachable=True)]
        ui.data["contents"] = {"W9": ["moved"]}
        self.live(ui, [self.chain(dir="/runs/mine", item="W9", stop="cap", run="3")])
        ui.work_tick()
        self.assertEqual([i for i, _ in ui.started], ["W9"])
        ui.work_chains = [self.rec(dir="/runs/new", item="W9")]
        self.live(ui, [self.chain(dir="/runs/new", item="W9", stop="cap", run="3")])
        ui.work_tick()
        self.assertEqual([i for i, _ in ui.started], ["W9", "W9"])

    def test_marking_a_draft_leaves_a_pinned_chain_and_other_items_alone(self):
        lowered = self.lowering()
        ui = self.ui(work_chains=[self.rec(dir="/runs/mine")])
        ui.event = lambda kind, text: text
        self.live(ui, [self.chain(live=True, run="3")])
        ui.work_draft_marked("W9")                  # a chain on another item
        ui.work_chains[0]["pin"] = True
        ui.work_draft_marked("W7")                  # the author asked for this chain
        ui.work_chains[0]["pin"], ui.work_on = False, False
        ui.work_draft_marked("W7")                  # no work
        self.assertEqual(lowered, [])

    def test_pinning_the_item_the_chain_is_on_leaves_it_running(self):
        lowered = self.lowering()
        ui = self.ui(work_chains=[self.rec(dir="/runs/mine")])
        self.live(ui, [self.chain(live=True, run="3")])
        ui.work_pin(dict(id="W7", status="open"))
        self.assertEqual(lowered, [])

    def test_pinning_leaves_somebody_else_s_chain_alone(self):
        lowered = self.lowering()
        ui = self.ui(work_chains=[self.rec(dir="/runs/mine")])
        self.live(ui, [self.chain(dir="/runs/by-hand", live=True, run="3")])
        ui.work_pin(dict(id="W9", status="open"))
        self.assertEqual(lowered, [])
        self.assertEqual(ui.work_next, "W9")

    def test_n_again_takes_the_pin_away_and_a_closed_item_is_refused(self):
        ui = self.ui(work_next="W9")
        ui.work_pin(dict(id="W9", status="open"))
        self.assertIsNone(ui.work_next)
        ui.work_pin(dict(id="W8", status="blocked"))
        self.assertIsNone(ui.work_next)
        self.assertIn("blocked", ui.msg)


class Parallel(Ticks, unittest.TestCase):
    """`parallel` chains at once: each on a task of its own, sharing one budget."""

    def tasks(self, ui, *ids, parent=None, **status):
        ui.data["items"] = [dict(id=i, status=status.get(i, "open"), parent=(parent or {}).get(i), reachable=True)
                            for i in ids]
        return ui

    def two(self, **kw):
        ui = self.ui(work_parallel=2, **kw)
        self.tasks(ui, "W7", "W8", "W9")
        ui.event = lambda kind, text: text
        return ui

    def test_two_slots_fill_with_two_tasks_whose_caps_sum_to_the_budget(self):
        ui = self.two(work_budget=5)
        self.live(ui, [])
        ui.work_tick()
        self.assertEqual(ui.started, [("W7", "2"), ("W8", "3")])
        self.assertEqual([c["item"] for c in ui.work_chains], ["W7", "W8"])

    def test_one_slot_is_filled_once_and_the_other_task_waits_for_it(self):
        ui = self.two(work_budget=10)
        self.live(ui, [])
        ui.work_tick()
        ui.work_tick()                         # the same tick again: both slots are taken
        self.assertEqual(len(ui.started), 2)

    def test_a_task_under_an_open_parent_is_not_started_beside_it(self):
        ui = self.ui(work_parallel=2)
        self.tasks(ui, "W7", "W8", "W9", parent={"W8": "W7"})
        self.live(ui, [])
        ui.work_tick()
        self.assertEqual([i for i, _ in ui.started], ["W7", "W9"])

    def test_a_task_under_a_done_parent_whose_chain_is_still_live_is_not_started(self):
        ui = self.ui(work_parallel=2)
        self.tasks(ui, "W7", "W8", "W9", parent={"W8": "W7"}, W7="done")
        ui.work_chains = [dict(dir="/runs/mine", item="W7", started=0.0, content=set(),
                               pin=False, cap=5)]
        self.live(ui, [self.chain(live=True)])
        ui.work_tick()
        self.assertEqual([i for i, _ in ui.started], ["W9"])

    def test_the_continuation_does_not_start_a_child_of_a_done_parent_still_merging(self):
        ui = self.ui(work_parallel=2)
        self.tasks(ui, "W7", "W8", "W9", parent={"W8": "W7"}, W7="done")
        ui.data["next_item"] = "W8"       # what full() gives: continue_last fell back to next_item
        ui.work_chains = [dict(dir="/runs/mine", item="W7", started=0.0, content=set(),
                               pin=False, cap=5)]
        self.live(ui, [self.chain(live=True)])
        ui.work_tick()
        self.assertEqual([i for i, _ in ui.started], ["W9"])

    def test_begin_sets_the_stop_on_limit_option_for_that_work_only(self):
        ui = self.two(work_budget=10)
        self.live(ui, [])
        ui.work_begin(4, 1, True)
        self.assertTrue(ui.work_stop_on_limit)
        self.assertIn("usage limit", ui.msg)
        ui.work_off("stopped by hand")
        ui.work_begin(4)
        self.assertFalse(ui.work_stop_on_limit)

    def test_without_worktrees_the_work_runs_one_chain_in_the_checkout_at_a_time(self):
        ui = self.two(work_budget=10)
        ui.work_cuts = lambda item: item == "W9"      # only W9 has a worktree of its own
        ui.work_parallel = 3
        self.live(ui, [])
        ui.work_tick()
        self.assertEqual([i for i, _ in ui.started], ["W7", "W9"], "W9 runs beside W7")
        ui.work_tick()
        self.assertEqual([i for i, _ in ui.started], ["W7", "W9"], "W8 would share W7's checkout")

    def test_a_later_task_with_a_worktree_takes_the_slot_a_second_in_place_task_cannot(self):
        ui = self.ui(work_parallel=2)
        self.tasks(ui, "W7", "W8", "W9")
        ui.work_cuts = lambda item: item == "W9"      # W7 and W8 would share the checkout
        self.live(ui, [])
        ui.work_tick()
        self.assertEqual([i for i, _ in ui.started], ["W7", "W9"])

    def test_the_prompt_says_when_no_worktree_is_cut_for_a_parallel_work(self):
        ui = self.ui(work_on=False)
        ui.work_cuts_new = lambda: False
        ui.event = lambda kind, text: text
        ui.work_begin(10, 3)
        self.assertIn("one at a time", ui.msg)
        ui.work_off("x")
        ui.work_cuts_new = lambda: True
        ui.work_begin(10, 3)
        self.assertNotIn("one at a time", ui.msg)

    def test_a_chain_that_ends_has_its_slot_refilled_from_what_is_left_of_the_budget(self):
        ui = self.two(work_budget=10, work_chains=[
            self.rec(dir="/runs/a", item="W7", content={"a", "b"}, cap=5),
            self.rec(dir="/runs/b", item="W8", cap=5)])
        ui.data.update(next_item="W9", contents={"W7": ["a", "b", "new"], "W8": []})
        self.live(ui, [self.chain(dir="/runs/a", item="W7", stop="done", run="2"),
                       self.chain(dir="/runs/b", item="W8", live=True, run="1")])
        ui.work_tick()
        self.assertEqual(ui.work_spent, 2)
        self.assertEqual(ui.started, [("W9", "3")], "10 - 2 spent - 5 held by W8's chain")
        self.assertEqual([c["item"] for c in ui.work_chains], ["W8", "W9"])

    def test_nothing_is_started_while_the_live_chains_hold_the_whole_budget(self):
        ui = self.two(work_budget=6, work_chains=[
            self.rec(dir="/runs/a", item="W7", cap=3), self.rec(dir="/runs/b", item="W8", cap=3)])
        self.live(ui, [self.chain(dir="/runs/a", live=True), self.chain(dir="/runs/b", item="W8", live=True)])
        ui.work_parallel = 3
        ui.work_tick()
        self.assertEqual(ui.started, [])
        self.assertTrue(ui.work_on, "reserved is not spent: it waits for a chain to end")

    def test_a_chain_judged_by_its_own_task_s_tree_not_by_its_neighbour_s(self):
        # ⚠️ The guard against a broken task looping needs the TASK's content: with two
        # chains, W8's good work must not clear W7's chain that appended nothing.
        ui = self.two(work_chains=[self.rec(dir="/runs/a", item="W7", content={"a"}),
                                   self.rec(dir="/runs/b", item="W8", content={"x"}, cap=5)])
        ui.data.update(contents={"W7": ["a"], "W8": ["x", "y"]})
        self.live(ui, [self.chain(dir="/runs/a", item="W7", stop="cap", run="1"),
                       self.chain(dir="/runs/b", item="W8", live=True)])
        ui.reload = lambda: (ui.data.__setitem__("next_item", "W9") if ui.raised else None)
        ui.work_tick()
        self.assertEqual([(i, k) for i, k, _ in ui.raised], [("W7", "raise")])
        self.assertEqual(ui.started, [("W9", "14")], "20 - 1 spent - 5 held by W8")

    def test_a_task_that_ended_on_its_cap_goes_before_the_order(self):
        ui = self.two(work_chains=[self.rec(dir="/runs/a", item="W7", content={"a"}, cap=4),
                                   self.rec(dir="/runs/b", item="W8", content={"x"}, cap=4)])
        ui.data.update(contents={"W7": ["a", "n"], "W8": ["x"]}, next_item="W9")
        self.live(ui, [self.chain(dir="/runs/a", item="W7", stop="cap", run="4"),
                       self.chain(dir="/runs/b", item="W8", live=True)])
        ui.work_tick()
        self.assertEqual([i for i, _ in ui.started], ["W7"], "carried on, not left for W9")

    def test_a_limit_holds_new_starts_while_the_other_chain_runs_on(self):
        ui = self.two(work_chains=[self.rec(dir="/runs/a", item="W7", content={"a"}),
                                   self.rec(dir="/runs/b", item="W8")])
        ui.data.update(contents={"W7": ["a", "n"]})
        self.live(ui, [self.chain(dir="/runs/a", item="W7", stop="limit", run="2"),
                       self.chain(dir="/runs/b", item="W8", live=True)])
        ui.work_tick()
        self.assertEqual(ui.started, [])
        self.assertTrue(ui.work_on)
        self.assertGreater(ui.work_until, time.time())
        self.assertEqual([c["item"] for c in ui.work_chains], ["W8"])
        self.assertEqual(ui.work_carry, ["W7"], "W7 goes first when the hold is over")

    def test_a_chain_started_by_hand_fills_a_slot_and_its_task_is_busy(self):
        ui = self.two()
        self.live(ui, [self.chain(dir="/runs/by-hand", item="W7", live=True)])
        ui.work_tick()
        self.assertEqual([i for i, _ in ui.started], ["W8"])

    def test_a_stop_ends_the_chains_still_running(self):
        lowered = self.lowering()
        ui = self.two(work_chains=[self.rec(dir="/runs/a", item="W7", content={"a"}),
                                   self.rec(dir="/runs/b", item="W8")])
        ui.data.update(contents={"W7": ["a", "n"]})
        self.live(ui, [self.chain(dir="/runs/a", item="W7", stop="failed", run="2"),
                       self.chain(dir="/runs/b", item="W8", live=True, run="3")])
        ui.work_tick()
        self.assertFalse(ui.work_on)
        self.assertEqual(lowered, [("/runs/b", 3)])

    def test_stopping_by_hand_ends_every_chain_after_its_run(self):
        lowered = self.lowering()
        ui = self.two(work_chains=[self.rec(dir="/runs/a", item="W7"), self.rec(dir="/runs/b", item="W8")])
        self.live(ui, [self.chain(dir="/runs/a", item="W7", live=True, run="2"),
                       self.chain(dir="/runs/b", item="W8", live=True, run="1")])
        ui.work_end_chain()
        self.assertFalse(ui.work_on)
        self.assertEqual(lowered, [("/runs/a", 2), ("/runs/b", 1)])
        self.assertIn("2 chains end", ui.msg)

    def test_pinning_with_a_free_slot_ends_nothing_and_with_none_ends_the_newest_chain(self):
        lowered = self.lowering()
        ui = self.two(work_chains=[self.rec(dir="/runs/a", item="W7", started=1.0)])
        self.live(ui, [self.chain(dir="/runs/a", item="W7", live=True, run="2")])
        ui.work_pin(dict(id="W9", status="open"))
        self.assertEqual(lowered, [])
        ui.work_chains = [self.rec(dir="/runs/a", item="W7", started=1.0),
                          self.rec(dir="/runs/b", item="W8", started=2.0)]
        self.live(ui, [self.chain(dir="/runs/a", item="W7", live=True, run="2"),
                       self.chain(dir="/runs/b", item="W8", live=True, run="1")])
        ui.work_next = None
        ui.work_pin(dict(id="W9", status="open"))
        self.assertEqual(lowered, [("/runs/b", 1)])

    def test_a_work_is_begun_with_a_number_of_chains_and_refuses_none(self):
        ui = self.ui(work_on=False)
        ui.event = lambda kind, text: text
        ui.work_tick = lambda: None
        ui.work_begin(8, 3)
        self.assertEqual((ui.work_budget, ui.work_parallel), (8, 3))
        with self.assertRaises(ValueError):
            ui.work_begin(8, 0)
        with self.assertRaises(ValueError):
            ui.work_start(8, TUI.MAX_PARALLEL + 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
