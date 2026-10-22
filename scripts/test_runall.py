"""What test_all.py runs by default, and when it adds the modules that start a real agent."""
import contextlib
import io
import json
import os
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
