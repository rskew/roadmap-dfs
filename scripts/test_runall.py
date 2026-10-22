"""What test_all.py runs by default, and when it adds the modules that start a real agent."""
import unittest

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
        found = {i.split(".", 1)[0] for i in test_all.discover([])}
        self.assertEqual(sorted(set(test_all.REAL_AGENT) - found), [])


if __name__ == "__main__":
    unittest.main()
