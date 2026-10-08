import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app" / "service"))

from interest_scoring import feedback_signal, text_matches, updated_weight


class InterestScoringTests(unittest.TestCase):
    def test_combines_interest_and_relevance(self):
        self.assertEqual(feedback_signal(True, True), 1.0)
        self.assertEqual(feedback_signal(True, False), 0.0)
        self.assertEqual(feedback_signal(None, False), -1.0)

    def test_weight_is_bounded(self):
        self.assertEqual(updated_weight(2.0, 1.0), 2.0)
        self.assertEqual(updated_weight(-2.0, -1.0), -2.0)

    def test_fragment_matching(self):
        self.assertTrue(text_matches("HPPO 环氧丙烷", "某公司宣布 HPPO 新工艺投产"))
        self.assertFalse(text_matches("聚碳酸酯", "环氧丙烷装置检修"))


if __name__ == "__main__":
    unittest.main()
