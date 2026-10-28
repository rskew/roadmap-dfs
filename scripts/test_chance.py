import unittest

import chance


class KeepRuleChanceTest(unittest.TestCase):
    def test_test_1_is_the_exact_table_in_the_doc(self):
        self.assertAlmostEqual(chance.keep_exact(0.5, 0.5)[1], 0.074, places=3)
        self.assertAlmostEqual(chance.keep_exact(0.7, 0.7)[1], 0.058, places=3)
        self.assertAlmostEqual(chance.keep_exact(0.5, 0.9)[1], 0.742, places=3)

    def test_with_every_replay_exposed_nothing_can_be_kept(self):
        first, both, decidable = chance.keep_rate((0.5, 0.5), (0.9, 0.9), 1.0, runs=2000)
        self.assertGreater(first, 0.5)          # test 1 alone would keep it
        self.assertEqual((both, decidable), (0.0, 0.0))

    def test_with_no_replay_exposed_test_2_adds_nothing(self):
        first, both, _ = chance.keep_rate((0.5, 0.5), (0.5, 0.5), 0.0, runs=40_000)
        self.assertAlmostEqual(first, both, places=2)
        self.assertAlmostEqual(first, 0.075, delta=0.01)

    def test_decidable_share_is_exact(self):
        self.assertAlmostEqual(chance.decidable(0.75), 0.7083, places=3)
        self.assertEqual(chance.decidable(1.0), 0.0)

    def test_test_2_turns_down_a_lead_that_only_exposed_replays_show(self):
        first, both, _ = chance.keep_rate((0.5, 0.5), (0.5, 0.95), 0.75, runs=20_000)
        self.assertLess(both, first * 0.75)


if __name__ == "__main__":
    unittest.main()
