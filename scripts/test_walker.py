#!/usr/bin/env python3
"""HeadlessWalk: the walk engine behind a lock and a thread, for `web.py` run without a
screen. The engine is `walk.WalkMixin`, the same one the terminal screen mixes in, and its
policy (`walk_decide`, the tick, the pin) is stood over by test_walk.py through the screen;
what is checked here is the page-facing interface around it: the budget bounds, the
page's pins, stopping, and the snapshot. The world is in memory.

Run:  python3 roadmap-dfs/scripts/test_walker.py
"""
import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import walk as dfs_walk   # noqa: E402


def run(d, item, live=False, stop="", run_no="1", cap="5", resets=""):
    return dict(dir=d, item=item, live=live, mode="work", stop=stop, run=run_no, cap=cap,
                resets=resets, pid=4242, agent="claude", state="live" if live else "finished",
                rc="", sid="", peak="", turns="", started="", finished="", console="", mtime=0)


class FakeWalker(dfs_walk.HeadlessWalk):
    """The world, in memory: items, chains, and what was asked of them."""

    def __init__(self):
        super().__init__()
        self.world_items = [dict(id="W1", goal="one", status="open"), dict(id="W2", goal="two", status="open"),
                            dict(id="W3", goal="three", status="done")]
        self.next_item = "W1"
        self.world_content = set()
        self.run_dirs = []
        self.started_chains, self.lowered, self.raised = [], [], []
        self.raise_blocks = True
        self.chosen = "claude"

    def restore(self):
        pass

    def lower_cap(self, rundir, n):
        self.lowered.append((rundir, n))

    def raise_on(self, item, body):
        self.raised.append((item, "raise"))
        if self.raise_blocks:
            self.next_item = "W2"

    @property
    def agent(self):
        return self.chosen

    @agent.setter
    def agent(self, name):
        self.chosen = name

    def reload(self):
        self.data = dict(items=self.world_items, next_item=self.next_item,
                         content=sorted(self.world_content))

    def refresh_chains(self):
        self.chains = list(self.run_dirs)

    def event(self, kind, text):
        self.events.append((time.time(), kind, text))
        return text

    def start_chain(self, item, cap):
        self.started_chains.append((item, cap))
        return "d%d" % len(self.started_chains)


class Interface(unittest.TestCase):
    def setUp(self):
        self.w = FakeWalker()
        self.addCleanup(self.w.restore)

    def test_a_walk_starts_a_chain_on_the_next_item_with_the_whole_budget_as_its_cap(self):
        self.w.start(7)
        self.assertEqual(self.w.started_chains, [("W1", "7")])
        self.assertTrue(self.w.walk_on)

    def test_a_walk_with_nothing_workable_pends_and_takes_the_task_that_appears(self):
        self.w.next_item = None
        self.w.start(4)
        self.assertTrue(self.w.walk_on)
        self.assertTrue(self.w.snapshot()["pending"])
        self.assertEqual(self.w.started_chains, [])
        self.w.next_item = "W2"             # a task is created; the loop re-reads the trees
        self.w.reload()
        self.w.walk_tick()
        self.assertEqual(self.w.started_chains, [("W2", "4")])
        self.assertFalse(self.w.snapshot()["pending"])

    def test_a_budget_that_is_not_a_walk_is_refused(self):
        for bad in (0, -3, "5", True, 10_000):
            with self.assertRaises(ValueError):
                self.w.start(bad)
        self.assertFalse(self.w.walk_on)

    def test_a_second_start_is_refused(self):
        self.w.start(3)
        with self.assertRaises(ValueError):
            self.w.start(3)

    def test_a_pin_is_where_the_next_chain_goes_and_is_spent_by_it(self):
        self.w.pin("W2")
        self.w.start(5)
        self.assertEqual(self.w.started_chains, [("W2", "5")])
        self.assertIsNone(self.w.walk_next)

    def test_a_pin_that_cannot_be_worked_is_refused(self):
        for bad in ("W3", "W9"):
            with self.assertRaises(ValueError):
                self.w.pin(bad)

    def test_pinning_again_or_with_none_takes_the_pin_away(self):
        self.w.pin("W2")
        self.w.pin("W2")
        self.assertIsNone(self.w.walk_next)
        self.w.pin("W1")
        self.w.pin(None)
        self.assertIsNone(self.w.walk_next)

    def test_pinning_while_a_chain_runs_ends_that_chain_after_its_run(self):
        self.w.start(10)
        self.w.run_dirs = [run("d1", "W1", live=True, run_no="2")]
        self.w.refresh_chains()
        self.w.pin("W2")
        self.assertEqual(self.w.lowered, [("d1", 2)])

    def test_stopping_ends_the_chain_after_the_run_it_is_in_so_it_cannot_run_free(self):
        self.w.start(10)
        self.w.run_dirs = [run("d1", "W1", live=True, run_no="4")]
        self.w.refresh_chains()
        self.w.walk_dir = "d1"
        self.w.stop()
        self.assertFalse(self.w.walk_on)
        self.assertEqual(self.w.walk_note, "stopped by hand")
        self.assertEqual(self.w.lowered, [("d1", 4)])

    def test_the_agent_is_chosen_from_the_known_ones(self):
        self.w.set_agent("codex")
        self.assertEqual(self.w.snapshot()["agent"], "codex")
        with self.assertRaises(ValueError):
            self.w.set_agent("rm -rf")

    def test_the_snapshot_says_where_things_stand(self):
        self.w.pin("W2")
        self.w.start(5)
        self.w.run_dirs = [run("d1", "W2", live=True, run_no="2", cap="5")]
        self.w.refresh_chains()
        self.w.walk_dir = "d1"
        snap = self.w.snapshot()
        self.assertEqual((snap["on"], snap["budget"], snap["used"]), (True, 5, 2))
        self.assertEqual(snap["chain"]["item"], "W2")
        self.assertEqual([c["id"] for c in snap["candidates"]], ["W1", "W2"])
        self.assertEqual(snap["auto"], "W1")
        self.assertTrue(snap["events"])
        self.assertEqual(snap["events"][0]["age"], "just now")           # newest first

    def test_a_tick_that_ended_badly_stops_the_walk_and_says_why(self):
        self.w.start(10)
        self.w.world_content = {"a"}
        self.w.run_dirs = [run("d1", "W1", stop="failed")]
        self.w.walk_tick()
        self.assertFalse(self.w.walk_on)
        self.assertIn("W1", self.w.walk_note)

    def test_the_loop_steps_the_walk_and_a_failure_stops_it_rather_than_killing_the_thread(self):
        import threading
        self.w.start(10)
        self.w.walk_tick = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        stop = threading.Event()
        t = threading.Thread(target=self.w.loop, args=(stop, 0.01), daemon=True)
        t.start()
        time.sleep(0.15)
        stop.set()
        t.join(2)
        self.assertFalse(self.w.walk_on)
        self.assertIn("boom", self.w.walk_note)


class Ago(unittest.TestCase):
    def test_how_long_since_in_the_largest_whole_unit(self):
        self.assertEqual([dfs_walk.ago(s) for s in (5, 90, 7200, 3 * 86400 + 5)],
                         ["just now", "1m ago", "2h ago", "3d ago"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
