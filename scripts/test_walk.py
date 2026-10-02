#!/usr/bin/env python3
"""A net under the autonomous walk — the one thing here that spends money unwatched.

What is checked is the POLICY: given how a chain ended, whether the trees moved while it ran,
and what a depth-first walk would take next, does the walk run again, wait, or stop?
The tree's own shape — who blocks whom, what `next_item` means — is checked beside it
in `test_order.py`; this file assumes that and asks only what happens afterwards.

⚠️ The test worth having is not "does it keep going". It is "does it STOP", because
the failure that costs something is a walk that treats a broken item as a temporary
one and restarts it all night.

Run:  python3 roadmap-dfs/scripts/test_walk.py
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


class Deciding(unittest.TestCase):
    """`walk_decide(stop, moved, next_item)` — the whole policy, as a value."""

    def test_a_finished_or_blocked_item_moves_the_walk_on(self):
        # `done` and `blocked` are both PROGRESS: one finishes a branch, the other
        # hands the author a question and frees the walk to take the next branch.
        self.assertEqual(TUI.walk_decide("done", True, "W8"), ("run", "W8"))
        self.assertEqual(TUI.walk_decide("blocked", True, "W9"), ("run", "W9"))

    def test_the_cap_runs_the_same_item_again(self):
        # A chain that used its whole cap with the item still open has more to do
        # there, and `next_item` says so by naming it again.
        self.assertEqual(TUI.walk_decide("cap", True, "W7"), ("run", "W7"))

    def test_only_a_usage_limit_waits(self):
        action, why = TUI.walk_decide("limit", False, "W7")
        self.assertEqual(action, "hold")
        self.assertIn("usage limit", why)
        self.assertEqual(TUI.WALK_HOLD, 3600)

    def test_an_ending_about_the_item_raises_on_it_and_never_holds(self):
        # ⚠️ A usage limit and a crash are both `rc 1`. If any of these ever fell
        # through to "hold", an unattended walk would retry a broken item hourly
        # until somebody noticed the bill. They RAISE: the item waits for the author
        # and the walk takes the next branch, rather than every branch waiting.
        for stop in ("idle",):
            self.assertEqual(TUI.walk_decide(stop, True, "W7")[0], "raise", stop)

    def test_what_no_other_item_would_survive_stops_the_walk(self):
        for stop in ("interrupt", "died", "something-new",
                     "failed", "overbudget", "overturns"):
            self.assertEqual(TUI.walk_decide(stop, True, "W7")[0], "stop", stop)

    def test_a_chain_that_recorded_nothing_stops_rather_than_guesses(self):
        action, why = TUI.walk_decide("", False, "W7")
        self.assertEqual(action, "stop")
        self.assertIn("without recording why", why)

    def test_a_happy_chain_that_moved_nothing_raises_on_its_item(self):
        # The redundant guard: the runner refuses to repeat an empty session inside
        # a chain, and this refuses to start another chain on an item producing
        # nothing. It RAISES rather than stops: the suspicion is about one item, and
        # a raise blocks that item while the walk takes the next branch.
        action, why = TUI.walk_decide("cap", False, "W8")
        self.assertEqual(action, "raise")
        self.assertIn("moved nothing", why)

    def test_a_chain_that_finished_its_item_moves_on_though_it_moved_nothing(self):
        # A finished item's last session is the review that found nothing, and a
        # verdict is not tree content, so it always moves nothing. W20 raised on
        # that 2026-09-30; a done item closes, and the walk takes the next branch.
        self.assertEqual(TUI.walk_decide("done", False, "W8"), ("run", "W8"))
        action, why = TUI.walk_decide("done", False, None)
        self.assertEqual(action, "stop")
        self.assertIn("blocked or finished", why)

    def test_an_idle_session_raises_on_its_item(self):
        action, why = TUI.walk_decide("idle", True, "W8")
        self.assertEqual(action, "raise")
        self.assertIn("changed nothing", why)

    def test_nothing_left_to_do_is_a_stop_not_an_error(self):
        action, why = TUI.walk_decide("done", True, None)
        self.assertEqual(action, "stop")
        self.assertIn("blocked or finished", why)

    def test_a_hold_reads_in_whole_minutes_and_never_zero(self):
        self.assertEqual(TUI.walk_held_for(3600), "60m")
        self.assertEqual(TUI.walk_held_for(90), "1m")
        self.assertEqual(TUI.walk_held_for(0), "1m")


class Placing(unittest.TestCase):
    """`chain_progress` — which budget a running chain is counted against."""

    def ui(self, **kw):
        ui = object.__new__(TUI.UI)
        ui.walk_on = True
        ui.walk_budget = 100
        ui.walk_spent = 3
        ui.walk_dir = "/runs/walk"
        ui.chains = []
        ui.__dict__.update(kw)
        return ui

    def chain(self, **kw):
        row = dict(dir="/runs/walk", item="W15", run="1", cap="97", live=True)
        row.update(kw)
        return row

    def test_a_walks_chain_is_placed_in_the_WALK(self):
        # ⚠️ The regression this exists for. `cap` on a walk-started chain is the
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

    def test_a_chain_beside_a_walk_is_not_counted_into_it(self):
        # Another agent's chain in the same tree shares the screen, never the budget.
        other = self.chain(dir="/runs/somebody-else", item="W7", cap="5", run="1")
        ui = self.ui(chains=[self.chain(), other])
        self.assertEqual(ui.chain_progress(other), "run 1/5")

    def test_with_the_walk_off_every_chain_keeps_its_cap(self):
        # A TUI restarted mid-walk has no walk state, so it must not invent one.
        c = self.chain()
        ui = self.ui(walk_on=False, chains=[c])
        self.assertEqual(ui.chain_progress(c), "run 1/97")


class Ticking(unittest.TestCase):
    """The tick around that policy: what it waits for, and whose chain it judges."""

    def ui(self, **kw):
        ui = object.__new__(TUI.UI)
        ui.msg = ""
        ui.walk_on = True
        ui.walk_budget = 20
        ui.walk_spent = 0
        ui.walk_until = 0.0
        ui.walk_dir = None
        ui.walk_started = 0.0
        ui.walk_content = set()
        ui.walk_note = ""
        ui.walk_next = None
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
        ui.data = {"content": ["a", "b", "new"], "next_item": "W7"}
        # A raise is a write to a real task file; the tests record it instead.
        ui.raised = []
        real = TUI.dfs_tree.append_log
        TUI.dfs_tree.append_log = lambda item, kind, args=(), body="": (
            ui.raised.append((item, kind, body)))
        self.addCleanup(setattr, TUI.dfs_tree, "append_log", real)
        ui.started = []
        ui.start_chain = lambda item, cap: (ui.started.append((item, cap))
                                            or "/runs/new")
        ui.__dict__.update(kw)
        return ui

    def live(self, ui, rows):
        ui.chains = rows
        ui.live = lambda mode="work": [r for r in rows if r["live"]]

    def chain(self, **kw):
        row = dict(dir="/runs/mine", item="W7", live=False, stop="done", resets="",
                   run="1")
        row.update(kw)
        return row

    def test_it_waits_while_ANY_chain_is_live(self):
        # ⚠️ Not only its own. Two agents appending to §3 at once is how this
        # roadmap's ids collided in the first place.
        ui = self.ui()
        self.live(ui, [self.chain(dir="/runs/somebody-else", live=True)])
        ui.walk_tick()
        self.assertEqual(ui.started, [], "started work beside a live chain")

    def test_it_starts_the_next_item_when_nothing_is_running(self):
        ui = self.ui()
        self.live(ui, [])
        ui.walk_tick()
        self.assertEqual(ui.started, [("W7", "20")],
                         "the chain's cap is the walk's remaining budget")
        self.assertEqual(ui.walk_dir, "/runs/new")
        self.assertEqual(ui.walk_content, {"a", "b", "new"},
                         "records what the trees say, to compare against")

    def test_it_holds_without_starting_anything(self):
        ui = self.ui(walk_until=time.time() + 600, walk_dir="/runs/mine")
        self.live(ui, [])
        ui.walk_tick()
        self.assertEqual(ui.started, [])
        self.assertTrue(ui.walk_on, "a hold is not a stop")

    def test_a_usage_limit_holds_and_says_when_it_resets(self):
        ui = self.ui(walk_dir="/runs/mine", walk_content={"a", "b"})
        self.live(ui, [self.chain(stop="limit", resets="resets 3pm")])
        ui.walk_tick()
        self.assertEqual(ui.started, [])
        self.assertTrue(ui.walk_on)
        self.assertGreater(ui.walk_until, time.time() + 3000,
                           "an unparseable reset falls back to the flat hour")
        self.assertIn("resets 3pm", ui.msg)

    def test_a_parseable_reset_holds_until_it_not_an_hour(self):
        import calendar
        later = time.gmtime(time.time() + 5 * 3600)
        stamp = "resets %s %d, %d:%02d%s (UTC)" % (
            time.strftime("%b", later), later.tm_mday,
            later.tm_hour % 12 or 12, later.tm_min, "pm" if later.tm_hour >= 12 else "am")
        ui = self.ui(walk_dir="/runs/mine", walk_content={"a", "b"})
        self.live(ui, [self.chain(stop="limit", resets=stamp)])
        ui.walk_tick()
        want = calendar.timegm(later[:5] + (0,)) + TUI.dfs_limit.RESET_MARGIN
        self.assertEqual(int(ui.walk_until), want)
        self.assertRegex(ui.msg, r"holding \d+m")

    def test_a_finished_chain_leads_to_the_next_one(self):
        ui = self.ui(walk_dir="/runs/mine", walk_content={"a", "b"})
        self.live(ui, [self.chain(stop="done", run="3")])
        ui.data["next_item"] = "W8"
        ui.walk_tick()
        self.assertEqual(ui.started, [("W8", "17")], "three sessions already spent")
        self.assertEqual(ui.walk_spent, 3)
        self.assertEqual(ui.reloaded, 1, "judged §3 without re-reading it")

    def test_an_entry_written_in_the_chain_s_last_second_still_counts(self):
        # ⚠️ `moved` is the guard between a broken item and an overnight loop, so it
        # must be asked of §3 as it is NOW: `poll` reloads only after this tick, and
        # a chain whose RAISE and whose exit land inside one second would otherwise
        # be read as having appended nothing — stopping the walk and blaming it.
        ui = self.ui(walk_dir="/runs/mine", walk_content={"a", "b"})
        self.live(ui, [self.chain(stop="blocked", run="1")])
        ui.data["content"] = ["a", "b"]              # the tick before its last append
        ui.reload = lambda: ui.data.__setitem__("content", ["a", "b", "raise"])
        ui.data["next_item"] = "W8"
        ui.walk_tick()
        self.assertEqual(ui.started, [("W8", "19")], "stopped on a stale §3")

    def test_a_chain_that_moved_nothing_raises_and_the_walk_moves_on(self):
        # A suspect item is put to the author and the walk takes the next branch;
        # it used to turn the whole walk off, holding every other item hostage.
        ui = self.ui(walk_dir="/runs/mine", walk_content={"a", "b"})
        self.live(ui, [self.chain(item="W7", stop="cap", run="2")])
        ui.data["content"] = ["a", "b"]
        # The raise blocks W7, so the re-read after it names the next branch.
        ui.reload = lambda: (ui.data.__setitem__("next_item", "W8")
                             if ui.raised else None)
        ui.walk_tick()
        self.assertEqual([(i, k) for i, k, _ in ui.raised], [("W7", "raise")])
        self.assertIn("moved nothing", ui.raised[0][2])
        self.assertTrue(ui.walk_on, "a suspect item is a raise, not a stop")
        self.assertEqual(ui.started, [("W8", "18")])

    def test_a_tidy_into_the_archive_is_not_a_chain_that_moved_nothing(self):
        # ⚠️ The case that turned the walk off, 2026-09-27: a W13 session did real
        # work and obeyed dfs_check's budget by moving determined nodes' Plans to
        # the archive. The COUNT fell 148 -> 143; the SET gained what the work wrote.
        ui = self.ui(walk_dir="/runs/mine", walk_content={"plan1", "plan2", "ev1"})
        self.live(ui, [self.chain(item="W13", stop="done", run="1")])
        ui.data.update(content=["ev1", "ev2"], next_item="W14")
        ui.walk_tick()
        self.assertEqual(ui.raised, [])
        self.assertEqual(ui.started, [("W14", "19")])

    def test_a_raise_that_does_not_block_the_item_stops_the_walk(self):
        # The raise is what moves the walk on, so it is checked: an item still next
        # after it would be started again, which is the loop this guards against.
        ui = self.ui(walk_dir="/runs/mine", walk_content={"a"})
        self.live(ui, [self.chain(item="W7", stop="cap", run="1")])
        ui.data.update(content=["a"], next_item="W7")
        ui.walk_tick()
        self.assertEqual(len(ui.raised), 1)
        self.assertFalse(ui.walk_on)
        self.assertEqual(ui.started, [])
        self.assertIn("did not block it", ui.walk_note)

    def test_a_chain_that_vanished_stops_the_walk(self):
        # Its directory is the only handle there is; without it nothing can say how
        # it ended, and guessing is the one thing this must not do.
        ui = self.ui(walk_dir="/runs/gone", walk_started=time.time() - 600)
        self.live(ui, [])
        ui.walk_tick()
        self.assertFalse(ui.walk_on)
        self.assertEqual(ui.started, [])
        self.assertEqual(ui.re_asked, 1, "condemned a chain without asking again")

    def test_a_chain_that_has_not_written_its_meta_yet_is_not_a_dead_one(self):
        # ⚠️ The bug this file existed and did not catch, measured 2026-09-16: the
        # run directory is made by `start_chain` and IDENTIFIED by the chain about
        # 110 ms later, while `start_chain`'s own reload lists the directories at
        # about 55 ms — so the walk's own chain is missing from the list the walk is
        # then handed, and the walk turned itself off ONE SECOND after starting its
        # first chain. Every walk. The chain itself ran on, unwatched.
        ui = self.ui(walk_dir="/runs/mine", walk_started=time.time())
        self.live(ui, [])
        ui.walk_tick()
        self.assertTrue(ui.walk_on, "a chain that has not spoken yet is not a dead one")
        self.assertEqual(ui.started, [], "and it is not a second chain either")

    def test_it_judges_the_chain_once_the_list_catches_up(self):
        # The other half: the wait is for the chain to IDENTIFY itself, not a timer
        # that eventually gives up. As soon as re-asking finds it, it is judged.
        ui = self.ui(walk_dir="/runs/mine", walk_started=time.time(), walk_content={"a", "b"})
        self.live(ui, [])
        ui.refresh_chains = lambda: (setattr(ui, "re_asked", ui.re_asked + 1),
                                     self.live(ui, [self.chain(stop="done", run="1")]))
        ui.data["next_item"] = "W8"
        ui.walk_tick()
        self.assertEqual(ui.re_asked, 1)
        self.assertEqual(ui.started, [("W8", "19")], "went on to the next branch")

    def test_the_grace_is_not_a_second_chain(self):
        # While it waits, it must not start anything else — the budget is spent by
        # chains, and a walk that started one and then started another because it
        # could not see the first is the overnight loop this file is written against.
        ui = self.ui(walk_dir="/runs/mine", walk_started=time.time())
        self.live(ui, [])
        for _ in range(5):
            ui.walk_tick()
        self.assertEqual(ui.started, [])
        self.assertTrue(ui.walk_on)

    def test_the_budget_is_over_the_WALK_not_over_an_item(self):
        # ⚠️ The mistake this replaced: a per-item cap bounded nothing, because a
        # chain that used one up left the item open, so the walk started another on
        # the same item. Spending has to be counted across chains or not at all.
        ui = self.ui(walk_budget=4, walk_spent=1, walk_dir="/runs/mine",
                     walk_content={"a", "b"})
        self.live(ui, [self.chain(stop="cap", run="3")])
        ui.walk_tick()
        self.assertEqual(ui.walk_spent, 4)
        self.assertFalse(ui.walk_on, "spent its budget over two chains")
        self.assertEqual(ui.started, [])
        self.assertIn("4 sessions spent", ui.msg)

    def test_a_chain_cannot_overshoot_what_is_left(self):
        ui = self.ui(walk_budget=20, walk_spent=18)
        self.live(ui, [])
        ui.walk_tick()
        self.assertEqual(ui.started, [("W7", "2")])

    def test_the_live_chain_counts_against_the_budget_as_it_goes(self):
        # Sessions that have BEGUN are charged: a session that started has already
        # cost its context, whatever it goes on to do.
        ui = self.ui(walk_spent=5, walk_dir="/runs/mine")
        self.live(ui, [self.chain(live=True, run="4")])
        self.assertEqual(ui.walk_used(), 9)

    def test_turning_it_off_forgets_the_chain_and_the_hold(self):
        ui = self.ui(walk_dir="/runs/mine", walk_until=time.time() + 600)
        ui.walk_off("by hand")
        self.assertFalse(ui.walk_on)
        self.assertIsNone(ui.walk_dir)
        self.assertEqual(ui.walk_until, 0.0)

    def test_turning_it_off_by_hand_ends_its_chain_after_the_run_in_flight(self):
        # ⚠️ The walk's chain holds the walk's whole remaining budget as its cap, so
        # an off walk that left it alone let it run on unwatched (W19: run 3 -> 5 of 33).
        lowered = []
        real = TUI.dfs_runs.lower_cap
        TUI.dfs_runs.lower_cap = lambda d, cap: lowered.append((d, cap))
        self.addCleanup(setattr, TUI.dfs_runs, "lower_cap", real)
        ui = self.ui(walk_dir="/runs/mine")
        self.live(ui, [self.chain(live=True, run="3")])
        ui.walk_toggle()
        self.assertFalse(ui.walk_on)
        self.assertEqual(lowered, [("/runs/mine", 3)])
        self.assertIn("ends after run 3", ui.msg)

    def test_turning_it_off_leaves_somebody_else_s_chain_alone(self):
        lowered = []
        real = TUI.dfs_runs.lower_cap
        TUI.dfs_runs.lower_cap = lambda d, cap: lowered.append((d, cap))
        self.addCleanup(setattr, TUI.dfs_runs, "lower_cap", real)
        ui = self.ui(walk_dir="/runs/mine")
        self.live(ui, [self.chain(dir="/runs/by-hand", live=True, run="3")])
        ui.walk_toggle()
        self.assertEqual(lowered, [])

    def test_why_it_stopped_outlives_the_message_line(self):
        # ⚠️ `walk_off` writes `self.msg`, and `poll` overwrites it with "refreshed"
        # in the same tick — so the reason was gone before the screen was drawn and
        # the author saw a badge disappear. The badge is what carries it now, until
        # the next walk starts.
        ui = self.ui(walk_dir="/runs/mine")
        ui.walk_off("W7: the chain appended nothing to §3")
        badge = ui.walk_badge()
        self.assertIn("W7", badge)
        self.assertIn("appended nothing", badge)
        ui.walk_note = ""
        self.assertEqual(ui.walk_badge(), "", "an off walk with nothing to say is silent")

    # ── `n`: the author pins the item the walk's next chain goes to ────────────

    def lowering(self):
        lowered = []
        real = TUI.dfs_runs.lower_cap
        TUI.dfs_runs.lower_cap = lambda d, cap: lowered.append((d, cap))
        self.addCleanup(setattr, TUI.dfs_runs, "lower_cap", real)
        return lowered

    def items(self, ui, **status):
        ui.data["items"] = [dict(id=k, status=v) for k, v in status.items()]

    def test_a_pinned_item_is_where_the_next_chain_starts(self):
        ui = self.ui(walk_next="W9")
        self.items(ui, W7="open", W9="open")
        self.live(ui, [])
        ui.walk_tick()
        self.assertEqual(ui.started, [("W9", "20")])
        self.assertIsNone(ui.walk_next, "a pin is for one chain; the order has the rest")

    def test_a_pin_overrides_what_the_ended_chain_would_have_led_to(self):
        ui = self.ui(walk_dir="/runs/mine", walk_content={"a", "b"}, walk_next="W9")
        self.items(ui, W7="open", W9="open")
        self.live(ui, [self.chain(stop="cap", run="2")])
        ui.walk_tick()
        self.assertEqual(ui.started, [("W9", "18")])

    def test_a_pin_on_an_item_that_stopped_being_open_is_dropped(self):
        # A chain on a done item ends "done before run 1" having moved nothing,
        # which the walk would then raise on.
        ui = self.ui(walk_next="W9")
        self.items(ui, W7="open", W9="done")
        self.live(ui, [])
        ui.walk_tick()
        self.assertEqual(ui.started, [("W7", "20")])
        self.assertIsNone(ui.walk_next)

    def test_pinning_ends_the_walk_s_chain_on_another_item_after_its_run(self):
        lowered = self.lowering()
        ui = self.ui(walk_dir="/runs/mine")
        self.live(ui, [self.chain(live=True, run="3")])
        ui.walk_pin(dict(id="W9", status="open"))
        self.assertEqual(ui.walk_next, "W9")
        self.assertEqual(lowered, [("/runs/mine", 3)])
        self.assertTrue(ui.walk_on, "a switch is not a stop")
        self.assertIn("→ W9", ui.walk_badge())

    def test_pinning_the_item_the_chain_is_on_leaves_it_running(self):
        lowered = self.lowering()
        ui = self.ui(walk_dir="/runs/mine")
        self.live(ui, [self.chain(live=True, run="3")])
        ui.walk_pin(dict(id="W7", status="open"))
        self.assertEqual(lowered, [])

    def test_pinning_leaves_somebody_else_s_chain_alone(self):
        lowered = self.lowering()
        ui = self.ui(walk_dir="/runs/mine")
        self.live(ui, [self.chain(dir="/runs/by-hand", live=True, run="3")])
        ui.walk_pin(dict(id="W9", status="open"))
        self.assertEqual(lowered, [])
        self.assertEqual(ui.walk_next, "W9")

    def test_n_again_takes_the_pin_away_and_a_closed_item_is_refused(self):
        ui = self.ui(walk_next="W9")
        ui.walk_pin(dict(id="W9", status="open"))
        self.assertIsNone(ui.walk_next)
        ui.walk_pin(dict(id="W8", status="blocked"))
        self.assertIsNone(ui.walk_next)
        self.assertIn("blocked", ui.msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
