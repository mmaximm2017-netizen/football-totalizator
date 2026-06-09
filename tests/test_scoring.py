import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCORING_PATH = ROOT / "app" / "models" / "scoring.py"


def load_scoring_module():
    module_name = "scoring_under_test"
    spec = importlib.util.spec_from_file_location(module_name, SCORING_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scoring = load_scoring_module()


class ScoringRulesTests(unittest.TestCase):
    def assert_points(self, real_home, real_away, pred_home, pred_away, expected):
        self.assertEqual(
            scoring.calculate_points(real_home, real_away, pred_home, pred_away),
            expected,
            f"real {real_home}:{real_away}, pred {pred_home}:{pred_away}",
        )

    def test_three_nil_examples(self):
        cases = [
            (3, 0, 11),
            (4, 1, 8),
            (5, 2, 8),
            (4, 0, 5),
            (3, 1, 5),
            (2, 0, 5),
            (1, 0, 3),
            (2, 1, 3),
            (0, 0, 0),
        ]

        for pred_home, pred_away, expected in cases:
            with self.subTest(pred=f"{pred_home}:{pred_away}"):
                self.assert_points(3, 0, pred_home, pred_away, expected)

    def test_two_nil_examples(self):
        cases = [
            (2, 0, 10),
            (3, 1, 7),
            (1, 0, 5),
            (3, 0, 5),
            (1, 1, 0),
        ]

        for pred_home, pred_away, expected in cases:
            with self.subTest(pred=f"{pred_home}:{pred_away}"):
                self.assert_points(2, 0, pred_home, pred_away, expected)

    def test_one_one_examples(self):
        cases = [
            (1, 1, 10),
            (0, 0, 7),
            (2, 2, 7),
            (1, 0, 0),
            (0, 1, 0),
        ]

        for pred_home, pred_away, expected in cases:
            with self.subTest(pred=f"{pred_home}:{pred_away}"):
                self.assert_points(1, 1, pred_home, pred_away, expected)

    def test_nil_nil_examples(self):
        cases = [
            (0, 0, 10),
            (1, 1, 7),
            (2, 2, 7),
            (1, 0, 0),
            (0, 1, 0),
        ]

        for pred_home, pred_away, expected in cases:
            with self.subTest(pred=f"{pred_home}:{pred_away}"):
                self.assert_points(0, 0, pred_home, pred_away, expected)

    def test_four_one_examples(self):
        cases = [
            (4, 1, 11),
            (3, 0, 8),
            (5, 2, 8),
            (4, 0, 5),
            (3, 1, 5),
            (1, 0, 3),
            (1, 1, 0),
        ]

        for pred_home, pred_away, expected in cases:
            with self.subTest(pred=f"{pred_home}:{pred_away}"):
                self.assert_points(4, 1, pred_home, pred_away, expected)


if __name__ == "__main__":
    unittest.main()
