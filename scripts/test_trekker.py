#!/usr/bin/env python3
"""HeadlessTrek: the trek engine behind a lock and a thread, for `web.py` run without a
screen. The engine is `trek.TrekMixin`, the same one the terminal screen mixes in, and its
policy (`trek_decide`, the tick, the pin) is stood over by test_trek.py through the screen;
what is checked here is the page-facing interface around it: the budget bounds, the
page's pins, stopping, and the snapshot. The world is in memory.

Run:  python3 roadmap-dfs/scripts/test_trekker.py
"""
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import state as dfs_state  # noqa: E402
import trek as dfs_trek   # noqa: E402


def run(d, item, live=False, stop="", run_no="1", cap="5", resets=""):
    return dict(dir=d, item=item, live=live, mode="work", stop=stop, run=run_no, cap=cap,
                resets=resets, pid=4242, agent="claude", kind="work", model="", state="live" if live else "finished",
                rc="", sid="", peak="", turns="", started="", finished="", console="", mtime=0)


class FakeTrekker(dfs_trek.HeadlessTrek):
    """The world, in memory: items, chains, and what was asked of them."""

    def trek_cuts(self, item):
        return True     # a worktree each; the real answer is about the checkout the tests run in

    def __init__(self):
        super().__init__()
        self.world_items = [dict(id="W1", goal="one", status="open", reachable=True),
                            dict(id="W2", goal="two", status="open", reachable=True),
                            dict(id="W3", goal="three", status="done", reachable=True)]
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

    def reload(self, data=None):     # the world is this trekker's own, whatever state the page built
        self.data = dict(items=self.world_items, next_item=self.next_item,
                         contents={"W1": sorted(self.world_content)})

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
        self.w = FakeTrekker()
        self.addCleanup(self.w.restore)

    def test_a_trek_starts_a_chain_on_the_next_item_with_the_whole_budget_as_its_cap(self):
        self.w.start(7)
        self.assertEqual(self.w.started_chains, [("W1", "7")])
        self.assertTrue(self.w.trek_on)

    def test_a_trek_of_two_chains_at_a_time_starts_two_tasks_that_share_the_budget(self):
        self.w.start(7, 2)
        self.assertEqual(self.w.started_chains, [("W1", "3"), ("W2", "4")])
        self.assertEqual(self.w.snapshot()["parallel"], 2)
        self.w.stop()
        self.assertEqual(self.w.trek_chains, [])

    def test_a_trek_with_nothing_workable_pends_and_takes_the_task_that_appears(self):
        self.w.next_item = None
        self.w.world_items = [dict(id="W1", goal="one", status="done", reachable=True)]
        self.w.start(4)
        self.assertTrue(self.w.trek_on)
        self.assertTrue(self.w.snapshot()["pending"])
        self.assertEqual(self.w.started_chains, [])
        self.w.next_item = "W2"             # a task is created; the loop re-reads the trees
        self.w.world_items.append(dict(id="W2", goal="two", status="open", reachable=True))
        self.w.reload()
        self.w.trek_tick()
        self.assertEqual(self.w.started_chains, [("W2", "4")])
        self.assertFalse(self.w.snapshot()["pending"])

    def test_a_budget_that_is_not_a_trek_is_refused(self):
        for bad in (0, -3, "5", True, 10_000):
            with self.assertRaises(ValueError):
                self.w.start(bad)
        self.assertFalse(self.w.trek_on)

    def test_a_second_start_is_refused(self):
        self.w.start(3)
        with self.assertRaises(ValueError):
            self.w.start(3)

    def test_a_pin_is_where_the_next_chain_goes_and_is_spent_by_it(self):
        self.w.pin("W2")
        self.w.start(5)
        self.assertEqual(self.w.started_chains, [("W2", "5")])
        self.assertIsNone(self.w.trek_next)

    def test_a_pin_that_cannot_be_worked_is_refused(self):
        for bad in ("W3", "W9"):
            with self.assertRaises(ValueError):
                self.w.pin(bad)

    def test_pinning_again_or_with_none_takes_the_pin_away(self):
        self.w.pin("W2")
        self.w.pin("W2")
        self.assertIsNone(self.w.trek_next)
        self.w.pin("W1")
        self.w.pin(None)
        self.assertIsNone(self.w.trek_next)

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
        self.w.stop()
        self.assertFalse(self.w.trek_on)
        self.assertEqual(self.w.trek_note, "stopped by hand")
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
        snap = self.w.snapshot()
        self.assertEqual((snap["on"], snap["budget"], snap["used"]), (True, 5, 2))
        self.assertEqual(snap["chains"][0]["item"], "W2")
        self.assertEqual([c["id"] for c in snap["candidates"]], ["W1", "W2"])
        self.assertEqual(snap["auto"], "W1")
        self.assertTrue(snap["events"])
        self.assertEqual(snap["events"][0]["age"], "just now")           # newest first

    def test_a_tick_that_ended_badly_stops_the_trek_and_says_why(self):
        self.w.start(10)
        self.w.world_content = {"a"}
        self.w.run_dirs = [run("d1", "W1", stop="failed")]
        self.w.trek_tick()
        self.assertFalse(self.w.trek_on)
        self.assertIn("W1", self.w.trek_note)

    def test_the_loop_steps_the_trek_and_a_failure_stops_it_rather_than_killing_the_thread(self):
        import threading
        self.w.start(10)
        self.w.trek_tick = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        stop = threading.Event()
        t = threading.Thread(target=self.w.loop, args=(stop, 0.01), daemon=True)
        t.start()
        time.sleep(0.15)
        stop.set()
        t.join(2)
        self.assertFalse(self.w.trek_on)
        self.assertIn("boom", self.w.trek_note)


class StopBreaks(unittest.TestCase):
    """⚠️ A trek ends at a `--- stop` break it had to wait for, and the next trek passes it.

    The items go through `dfs_state.trek`, so the fence is the one the order derives."""

    def setUp(self):
        self.w = FakeTrekker()
        self.addCleanup(self.w.restore)

    def shape(self, first, stop=True, second="open"):
        """W1 before the fence, W2 behind it."""
        self.w.world_items = dfs_state.trek([
            dict(id="W1", goal="one", depth=0, status=first, segment=0, stop=False),
            dict(id="W2", goal="two", depth=0, status=second, segment=1, stop=stop)])
        self.w.next_item = dfs_state.next_item(self.w.world_items)
        self.w.reload()

    def finish_w1(self):
        """W1's chain ends with W1 reviewed and done."""
        self.w.world_content = {"a"}
        self.shape("done", stop=self.w.world_items[1]["stop"])
        self.w.run_dirs = [run("d1", "W1", stop="done")]
        self.w.trek_tick()

    def test_the_trek_that_worked_up_to_it_ends_there(self):
        self.shape("open")
        self.w.start(10)
        self.assertEqual(self.w.started_chains, [("W1", "10")])
        self.finish_w1()
        self.assertFalse(self.w.trek_on)
        self.assertIn("stop break above W2", self.w.trek_note)
        self.assertEqual(self.w.started_chains, [("W1", "10")])

    def test_a_plain_break_lets_the_same_trek_go_on(self):
        self.shape("open", stop=False)
        self.w.start(10)
        self.finish_w1()
        self.assertTrue(self.w.trek_on)
        self.assertEqual([c for c, _ in self.w.started_chains], ["W1", "W2"])

    def test_the_trek_started_after_it_opened_goes_past_it(self):
        self.shape("done")
        self.w.start(10)
        self.assertEqual(self.w.started_chains, [("W2", "10")])

    def test_while_something_before_it_is_blocked_the_trek_waits_and_does_not_end(self):
        self.shape("blocked")
        self.w.start(10)
        self.assertTrue(self.w.trek_on)
        self.assertTrue(self.w.snapshot()["pending"])

    def test_the_trek_shows_nothing_next_once_it_is_at_the_break(self):
        self.shape("open")
        self.w.start(10)
        self.w.run_dirs = [run("d1", "W1", live=True)]
        self.shape("done")              # reviewed ok; its chain is still merging it
        self.assertEqual(self.w.data["next_item"], "W2")
        self.assertIsNone(self.w.snapshot()["next"])
        self.assertTrue(self.w.trek_on)

    def test_a_trek_with_every_slot_taken_still_holds_the_break(self):
        self.shape("open")
        self.w.run_dirs = [run("h1", "W1", live=True)]     # a chain started by hand
        self.w.start(10)
        self.assertEqual(self.w.started_chains, [])
        self.shape("done")
        self.w.run_dirs = [run("h1", "W1", stop="done")]   # it has ended
        self.w.trek_tick()
        self.assertEqual(self.w.started_chains, [])
        self.assertFalse(self.w.trek_on)
        self.assertIn("stop break above W2", self.w.trek_note)

    def test_a_hand_started_chain_still_merging_the_last_task_holds_the_trek_on(self):
        self.shape("open")
        self.w.start(10, 2)
        self.assertEqual([c for c, _ in self.w.started_chains], ["W1"])
        self.w.started_chains = []
        self.shape("done")
        # W1 is reviewed and done, its chain (the trek's own) ended, and a second chain
        # started by hand is still merging it.
        self.w.run_dirs = [run("d1", "W1", stop="done"), run("h1", "W1", live=True)]
        self.w.trek_tick()
        self.assertTrue(self.w.trek_on)
        self.assertEqual(self.w.started_chains, [])

    def test_a_pin_goes_past_it(self):
        self.shape("open")
        self.w.pin("W2")
        self.w.start(10)
        self.assertEqual(self.w.started_chains, [("W2", "10")])


class Ago(unittest.TestCase):
    def test_how_long_since_in_the_largest_whole_unit(self):
        self.assertEqual([dfs_trek.ago(s) for s in (5, 90, 7200, 3 * 86400 + 5)],
                         ["just now", "1m ago", "2h ago", "3d ago"])



class Snapshot(unittest.TestCase):
    def test_a_snapshot_given_the_state_builds_none_of_its_own(self):
        # web.state_json has just run state.full(); a second build doubled /api/state.
        built, real = [], dfs_state.full
        dfs_state.full = lambda: built.append(1) or dict(items=[], next_item=None, contents={})
        try:
            w = dfs_trek.HeadlessTrek()
            w.refresh_chains = lambda: setattr(w, "chains", [])
            given = dict(items=[dict(id="W1", goal="one", status="open", reachable=True)],
                         next_item="W1", contents={})
            self.assertEqual(w.snapshot(given)["next"], "W1")
            self.assertEqual(built, [])
            w.snapshot()
            self.assertEqual(built, [1])
        finally:
            dfs_state.full = real

    def test_the_loop_builds_no_state_while_the_trek_is_off(self):
        # Every TICK, nobody reading it: 40% of a core on a hundred tasks.
        import threading
        built, real = [], dfs_state.full
        dfs_state.full = lambda: built.append(1) or dict(items=[], next_item=None, contents={})
        try:
            w = dfs_trek.HeadlessTrek()
            w.refresh_chains = lambda: setattr(w, "chains", [])
            stop = threading.Event()
            stop.set()                      # one pass, then out
            w.loop(stop)
            self.assertEqual(built, [])
        finally:
            dfs_state.full = real

class PullRequests(unittest.TestCase):
    """GitHub mode: the trek looks at the open pull requests every PR_POLL seconds."""

    def setUp(self):
        self.w = FakeTrekker()
        self.w.trek_pr_sync = True
        self.looked = []
        self.real = (dfs_trek.dfs_paths.read_github, dfs_trek.dfs_github.watched,
                     dfs_trek.dfs_github.watch)
        dfs_trek.dfs_paths.read_github = lambda: "origin"
        dfs_trek.dfs_github.watched = lambda: ["W1", "W2"]
        dfs_trek.dfs_github.watch = lambda task: (self.looked.append(task) or
                                                  (["%s.9" % task] if task == "W2" else []))

    def tearDown(self):
        (dfs_trek.dfs_paths.read_github, dfs_trek.dfs_github.watched,
         dfs_trek.dfs_github.watch) = self.real

    def test_it_looks_at_each_pull_request_then_waits_out_the_interval(self):
        self.w.start(4)
        self.assertEqual(self.looked, ["W1", "W2"])
        self.assertTrue(any("1 new comment on its pull request" in e[2] and "W2.9" in e[2]
                            for e in self.w.events))
        self.w.trek_tick()
        self.assertEqual(self.looked, ["W1", "W2"])
        self.w.trek_pr_next = time.time() - 1
        self.w.trek_tick()          # the trek has a chain on W1 by now: only W2 is looked at
        self.assertEqual(self.looked, ["W1", "W2", "W2"])

    def test_a_task_with_a_chain_on_it_is_passed_over(self):
        self.w.trek_pr_next = 0.0
        self.w.trek_look_at_prs({"W1"})
        self.assertEqual(self.looked, ["W2"])

    def test_nothing_is_asked_of_github_with_the_mode_off(self):
        dfs_trek.dfs_paths.read_github = lambda: ""
        self.w.start(4)
        self.assertEqual(self.looked, [])

    def test_a_branch_error_from_a_landing_is_said_not_raised(self):
        def boom(task):
            raise dfs_trek.dfs_github.dfs_branch.BranchError("a rebase of yours is unfinished")
        dfs_trek.dfs_github.watch = boom
        self.w.trek_look_at_prs(set())
        self.assertTrue(any("a rebase of yours is unfinished" in e[2] for e in self.w.events))

    def test_a_failed_look_is_said_and_the_trek_goes_on(self):
        def boom(task):
            raise dfs_trek.dfs_github.GithubError("gh is not logged in")
        dfs_trek.dfs_github.watch = boom
        self.w.start(4)
        self.assertTrue(self.w.trek_on)
        self.assertTrue(any("could not be read: gh is not logged in" in e[2] for e in self.w.events))


REAL = dfs_trek.fetch_kiro_models   # Models patches the module's name; the parsing is tested through this


class Models(unittest.TestCase):
    """The models chosen for the trek: kept in the run root, given to a chain as the
    variables run.sh reads, claude's own roles and nothing else."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patch = mock.patch.dict(os.environ, {"DFS_RUN_DIR": tmp.name})
        patch.start()
        self.addCleanup(patch.stop)
        cache = mock.patch.dict(dfs_trek._kiro_models, {"ids": [], "at": 0.0, "busy": False})
        cache.start()
        self.addCleanup(cache.stop)
        fetch = mock.patch.object(dfs_trek, "fetch_kiro_models", return_value=[])   # never run the real kiro-cli
        self.fetch = fetch.start()
        self.addCleanup(fetch.stop)
        self.w = FakeTrekker()

    def wait_for_kiro_list(self):
        for _ in range(200):
            if not dfs_trek._kiro_models["busy"]:
                return
            time.sleep(0.01)
        self.fail("the kiro model list was never read")

    def test_nothing_is_chosen_until_one_is(self):
        self.assertEqual(self.w.snapshot()["models"], dict(plan="", implement=""))

    def test_a_model_is_chosen_per_role_and_kept_for_the_next_screen(self):
        self.w.set_model("plan", "claude-opus-4-5")
        self.w.set_model("implement", "sonnet[1m]")
        self.assertEqual(dfs_trek.read_models(), dict(plan="claude-opus-4-5", implement="sonnet[1m]"))
        self.assertEqual(FakeTrekker().snapshot()["models"]["plan"], "claude-opus-4-5")

    def test_an_empty_name_takes_the_choice_away(self):
        self.w.set_model("plan", "haiku")
        self.w.set_model("plan", "")
        self.assertEqual(self.w.models()["plan"], "")

    def test_a_role_or_a_name_that_is_not_one_is_refused(self):
        for role, name in (("review", "opus"), ("plan", "--help"), ("plan", "a b"),
                           ("plan", "opus; ls"), ("plan", None)):
            with self.assertRaises(ValueError, msg=(role, name)):
                self.w.set_model(role, name)
        self.assertEqual(self.w.models(), dict(plan="", implement=""))

    def test_a_file_holding_what_it_should_not_reads_as_nothing_chosen(self):
        with open(dfs_trek.models_file(), "w") as fh:
            fh.write('{"plan": "--bad", "implement": 3, "other": "x"}')
        self.assertEqual(self.w.models(), dict(plan="", implement=""))
        with open(dfs_trek.models_file(), "w") as fh:
            fh.write("not json")
        self.assertEqual(self.w.models(), dict(plan="", implement=""))

    def test_a_model_is_kept_per_agent_and_the_other_agents_choice_stays(self):
        self.w.set_model("plan", "opus")
        self.w.agent = "kiro"
        self.assertEqual(self.w.models(), dict(plan="", implement=""))   # claude's opus is not a kiro name
        self.w.set_model("implement", "claude-sonnet-5.5")
        self.assertEqual(self.w.models(), dict(plan="", implement="claude-sonnet-5.5"))
        self.w.agent = "claude"
        self.assertEqual(self.w.models(), dict(plan="opus", implement=""))

    def test_the_drop_down_lists_the_agents_models_and_keeps_a_name_already_chosen(self):
        self.assertEqual(self.w.model_choices(), dfs_trek.MODEL_CHOICES["claude"])
        self.assertIn("opus[1m]", self.w.model_choices())
        self.w.set_model("plan", "claude-opus-4-5")
        self.assertEqual(self.w.model_choices()[-1], "claude-opus-4-5")
        self.w.agent = "kiro"
        self.assertIn("claude-opus-5.5", self.w.model_choices())
        self.assertNotIn("opus", self.w.model_choices())
        self.assertEqual(self.w.snapshot()["model_choices"], self.w.model_choices())
        self.w.agent = "codex"
        self.assertEqual(self.w.model_choices(), [])
        with self.assertRaises(ValueError):
            self.w.set_model("plan", "opus")

    def test_the_kiro_drop_down_lists_every_model_kiro_cli_names_and_falls_back_when_it_cannot(self):
        self.w.agent = "kiro"
        self.assertEqual(self.w.model_choices(), dfs_trek.MODEL_CHOICES["kiro"])   # the first look: not read yet
        self.wait_for_kiro_list()
        self.assertEqual(self.w.model_choices(), dfs_trek.MODEL_CHOICES["kiro"])   # it said nothing: the fixed list
        self.assertEqual(self.fetch.call_count, 1)
        self.fetch.return_value = ["auto", "gpt-5.6-sol", "qwen3-coder-next"]
        dfs_trek._kiro_models["at"] = 0.0          # the list is old
        self.w.model_choices()                     # asks again, without waiting for the answer
        self.wait_for_kiro_list()
        self.assertEqual(self.w.model_choices(), ["auto", "gpt-5.6-sol", "qwen3-coder-next"])
        self.w.set_model("plan", "my-own-model")   # a typed name joins the list
        self.assertEqual(self.w.model_choices()[-1], "my-own-model")
        self.assertEqual(self.fetch.call_count, 2, "a fresh list is not read again")
        self.fetch.return_value = []               # a later failure keeps the last good list
        dfs_trek._kiro_models["at"] = 0.0
        self.w.model_choices()
        self.wait_for_kiro_list()
        self.assertIn("gpt-5.6-sol", self.w.model_choices())

    def test_a_kiro_trek_refuses_a_name_only_claude_has_and_says_which_names_kiro_has(self):
        self.w.agent = "kiro"
        for name in ("opus", "sonnet", "haiku", "opusplan", "opus[1m]", "sonnet[1m]"):
            with self.assertRaises(ValueError) as e:
                self.w.set_model("implement", name)
            self.assertIn("claude-opus-5.5", str(e.exception))      # it names the ids kiro does have
        self.assertEqual(self.w.models(), dict(plan="", implement=""))
        self.w.set_model("implement", "claude-opus-5.5")            # kiro's own id saves
        self.w.set_model("plan", "my-own-model")                    # so does a name nobody lists
        self.w.set_model("plan", "default")                         # run.sh passes no --model for this
        self.w.agent = "claude"
        self.w.set_model("implement", "opus")                       # claude still takes its alias

    def test_the_refusal_says_when_the_list_is_the_fixed_one_and_never_waits_for_kiro_cli(self):
        self.w.agent = "kiro"
        release = threading.Event()
        self.fetch.side_effect = lambda: (release.wait(10), ["auto", "claude-opus-5.5"])[1]
        try:
            started = time.time()
            with self.assertRaises(ValueError) as e:
                self.w.set_model("implement", "opus")        # the read is now in flight and blocked
            self.assertLess(time.time() - started, 2)
            self.assertIn("fixed list", str(e.exception))
            self.assertTrue(dfs_trek._kiro_models["busy"])
        finally:
            release.set()
        self.wait_for_kiro_list()
        with self.assertRaises(ValueError) as e:
            self.w.set_model("implement", "opus")
        self.assertNotIn("fixed list", str(e.exception))     # kiro answered: its own list

    def test_a_kiro_trek_takes_a_claude_looking_name_that_kiro_itself_lists(self):
        self.w.agent = "kiro"
        self.fetch.return_value = ["auto", "opus"]
        dfs_trek._kiro_models["at"] = 0.0
        self.w.model_choices()
        self.wait_for_kiro_list()
        self.w.set_model("plan", "opus")
        self.assertEqual(self.w.models()["plan"], "opus")

    def test_fetch_kiro_models_reads_the_ids_from_the_json_kiro_cli_prints(self):
        out = '{"models":[{"model_id":"auto"},{"model_id":"claude-opus-5.5"},{"model_id":"-x"},{"model_name":"n"}],"default_model":"auto"}'
        with mock.patch.object(dfs_trek.subprocess, "run") as run_:
            run_.return_value.stdout = out
            with mock.patch.dict(os.environ, {"KIRO_CMD": "kiro-cli --flag"}):
                self.assertEqual(REAL(), ["auto", "claude-opus-5.5"])
        self.assertEqual(run_.call_args.args[0], ["kiro-cli", "--flag", "chat", "--list-models", "--format", "json"])
        with mock.patch.object(dfs_trek.subprocess, "run", side_effect=OSError("no kiro-cli")):
            self.assertEqual(REAL(), [])
        with mock.patch.object(dfs_trek.subprocess, "run") as run_:
            run_.return_value.stdout = "not json"
            self.assertEqual(REAL(), [])

    def test_a_kiro_chain_gets_the_kiro_models(self):
        self.w.set_model("plan", "opus")
        self.w.agent = "kiro"
        self.w.set_model("plan", "claude-opus-5.5")
        w = dfs_trek.HeadlessTrek()
        w.agent = "kiro"
        w.reload = lambda data=None: None
        with mock.patch.object(dfs_trek.dfs_runs, "live_for", return_value=None), \
                mock.patch.object(dfs_trek.subprocess, "Popen") as popen:
            popen.return_value.pid = 1
            w.start_chain("W1", "5")
        env = popen.call_args.kwargs["env"]
        self.assertEqual(env["PLAN_MODEL"], "claude-opus-5.5")
        self.assertNotIn("IMPLEMENT_MODEL", env)

    def chain_env(self):
        """The environment a chain really started by `start_chain` is given."""
        w = dfs_trek.HeadlessTrek()
        w.reload = lambda data=None: None
        with mock.patch.object(dfs_trek.dfs_runs, "live_for", return_value=None), \
                mock.patch.object(dfs_trek.subprocess, "Popen") as popen:
            popen.return_value.pid = 1
            w.start_chain("W1", "5")
        return popen.call_args.kwargs["env"]

    def test_a_chain_gets_the_chosen_models_as_the_variables_run_sh_reads(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("PLAN_MODEL", None)
            os.environ.pop("IMPLEMENT_MODEL", None)
            env = self.chain_env()
            self.assertNotIn("PLAN_MODEL", env)         # not chosen: run.sh's own defaults
            self.assertNotIn("IMPLEMENT_MODEL", env)
            self.w.set_model("plan", "opus")
            self.w.set_model("implement", "default")
            env = self.chain_env()
            self.assertEqual((env["PLAN_MODEL"], env["IMPLEMENT_MODEL"]), ("opus", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
