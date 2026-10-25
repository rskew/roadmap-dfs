"""What test_all.py runs by default, and when it adds the modules that start a real agent."""
import contextlib
import io
import json
import os
import signal
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import test_all

IDS = ["test_chain.Chain.test_a", "test_opencode_chat.OpencodeCrossing.test_b",
       "test_agent_start.FreshStart.test_c", "test_web.Web.test_d"]


class Split(unittest.TestCase):
    def test_the_default_run_leaves_the_real_agent_modules_out_and_counts_them(self):
        rest, real, left_out = test_all.split(IDS, False, [])
        self.assertEqual(rest, ["test_chain.Chain.test_a", "test_web.Web.test_d"])
        self.assertEqual((real, left_out), ([], 2))

    def test_real_agents_adds_them_as_their_own_pass(self):
        rest, real, left_out = test_all.split(IDS, True, [])
        self.assertEqual(rest, ["test_chain.Chain.test_a", "test_web.Web.test_d"])
        self.assertEqual((real, left_out), (IDS[1:3], 0))

    def test_naming_a_real_agent_module_or_one_of_its_tests_runs_it(self):
        for name in ("test_opencode_chat", "test_agent_start.FreshStart.test_c"):
            with self.subTest(name):
                self.assertEqual(test_all.split(IDS, False, [name])[2], 0)
                self.assertTrue(test_all.split(IDS, False, [name])[1])

    def test_naming_another_module_does_not(self):
        self.assertEqual(test_all.split(IDS, False, ["test_web"])[1:], ([], 2))

    def test_every_real_agent_module_exists_and_has_tests(self):
        found = {i.split(".", 1)[0] for i in test_all.discover()}
        self.assertEqual(sorted(set(test_all.REAL_AGENT) - found), [])


class Remembered(unittest.TestCase):
    def test_a_time_for_a_test_this_tree_lacks_is_dropped_and_the_rest_kept(self):
        known = {"test_a.A.old": 1.0, "test_a.A.kept": 2.0}
        self.assertEqual(test_all.remembered(known, ["test_a.A.kept", "test_a.A.new"]), {"test_a.A.kept": 2.0})

    def test_a_named_run_drops_a_stale_time_and_keeps_the_times_of_tests_it_did_not_name(self):
        named = "test_runall.Split.test_naming_another_module_does_not"
        other = "test_runall.Split.test_real_agents_adds_them_as_their_own_pass"
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_CACHE_HOME": d}):
            tf = test_all.times_file()
            tf.parent.mkdir(parents=True)
            tf.write_text(json.dumps({"test_gone.Gone.test_x": 9.0, other: 7.0}))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(test_all.main([named]), 0)
            times = json.loads(tf.read_text())
        self.assertNotIn("test_gone.Gone.test_x", times)
        self.assertEqual(times[other], 7.0)
        self.assertIn(named, times)


class Reported(unittest.TestCase):
    def test_a_class_that_skips_itself_is_named_in_the_shards_tally(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "test_skipping.py").write_text(
                "import unittest\nclass C(unittest.TestCase):\n"
                "    @classmethod\n    def setUpClass(cls):\n        raise unittest.SkipTest('no playwright')\n"
                "    def test_x(self):\n        pass\n")
            out = io.StringIO()
            with mock.patch.object(test_all, "HERE", Path(d)), mock.patch.object(sys, "path", sys.path[:]), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                test_all.run_shard(["test_skipping.C.test_x"])
            sys.modules.pop("test_skipping", None)
        tally = json.loads(out.getvalue().rpartition(test_all.MARK)[2])
        self.assertEqual(tally["run"], 0)
        self.assertEqual(len(tally["skips"]), 1)
        self.assertIn("test_skipping.C", tally["skips"][0])
        self.assertIn("no playwright", tally["skips"][0])

    def test_written_lists_the_files_made_since_the_start_and_not_older_ones(self):
        with tempfile.TemporaryDirectory() as d:
            old, new = Path(d) / "sub" / "old", Path(d) / "sub" / "new"
            old.parent.mkdir()
            old.write_text("x")
            os.utime(old, (time.time() - 100, time.time() - 100))
            new.write_text("x")
            self.assertEqual(test_all.written(time.time() - 10, Path(d)), [new])
            self.assertEqual(test_all.written(time.time() - 10, Path(d) / "none"), [])

    def test_a_shard_past_its_time_is_killed_with_what_it_started(self):
        with tempfile.TemporaryDirectory() as d:
            pidfile = Path(d) / "pid"
            script = Path(d) / "shard.sh"
            script.write_text("sleep 20 &\necho $! > %s\nwait\n" % pidfile)
            with mock.patch.object(test_all, "__file__", str(script)), mock.patch.object(sys, "executable", "sh"):
                _, rc, _, err, _, took = test_all.spawn(["x"], 1)
            self.assertEqual(rc, -1)
            self.assertLess(took, 10, "spawn waited for the sleep the shard started")
            self.assertIn("was killed", err)
            pid = int(pidfile.read_text())
            end = time.time() + 5
            while time.time() < end:
                try:
                    os.kill(pid, 0)
                except OSError:
                    break
                time.sleep(0.05)
            else:
                os.kill(pid, signal.SIGKILL)
                self.fail("the sleep the shard started was still running")


if __name__ == "__main__":
    unittest.main()
