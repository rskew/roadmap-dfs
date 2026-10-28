import unittest

import chance


class KeepRuleChanceTest(unittest.TestCase):
    def test_the_exact_table_in_the_doc(self):
        self.assertAlmostEqual(chance.keep_exact(0.5, 0.5)[1], 0.074, places=3)
        self.assertAlmostEqual(chance.keep_exact(0.7, 0.7)[1], 0.058, places=3)
        self.assertAlmostEqual(chance.keep_exact(0.5, 0.9)[1], 0.742, places=3)
        self.assertAlmostEqual(chance.keep_exact(0.5, 0.5, margin=2)[1], 0.252, places=3)

    def test_the_per_task_guard_only_ever_removes_runs(self):
        alone, both = chance.keep_exact(0.5, 0.75)
        self.assertLessEqual(both, alone)

    def test_a_lead_cannot_exceed_the_arm(self):
        self.assertEqual(chance.keep_exact(0.5, 0.5, margin=13)[0], 0.0)


if __name__ == "__main__":
    unittest.main()
