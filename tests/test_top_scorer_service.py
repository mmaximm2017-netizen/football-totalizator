import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE_PATH = ROOT / "app" / "services" / "top_scorer_service.py"


def load_top_scorer_module():
    app_module = types.ModuleType("app")
    db_module = types.ModuleType("app.db")
    db_module.get_db = lambda: None
    db_module.close_db = lambda conn, cur: None

    original_app = sys.modules.get("app")
    original_db = sys.modules.get("app.db")

    sys.modules["app"] = app_module
    sys.modules["app.db"] = db_module

    try:
        spec = importlib.util.spec_from_file_location("top_scorer_under_test", SERVICE_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if original_app is None:
            sys.modules.pop("app", None)
        else:
            sys.modules["app"] = original_app

        if original_db is None:
            sys.modules.pop("app.db", None)
        else:
            sys.modules["app.db"] = original_db


top_scorers = load_top_scorer_module()


def prediction(user_id, username, pred_home, pred_away, home_score, away_score, points=0, status="FINISHED"):
    return {
        "user_id": user_id,
        "username": username,
        "pred_home": pred_home,
        "pred_away": pred_away,
        "home_score": home_score,
        "away_score": away_score,
        "points": points,
        "status": status,
    }


class TopScorerServiceTests(unittest.TestCase):
    def test_exact_regular_score_counts_as_one_goal(self):
        rows = [prediction(1, "Anton", 2, 0, 2, 0, points=10)]

        result = top_scorers.build_top_scorers(rows)

        self.assertEqual(result[0]["scorer_goals"], 1)

    def test_exact_big_score_counts_as_one_goal(self):
        rows = [prediction(1, "Anton", 4, 1, 4, 1, points=11)]

        result = top_scorers.build_top_scorers(rows)

        self.assertEqual(result[0]["scorer_goals"], 1)

    def test_exact_difference_does_not_count_as_goal(self):
        rows = [prediction(1, "Anton", 3, 1, 2, 0, points=7)]

        self.assertEqual(top_scorers.build_top_scorers(rows), [])

    def test_correct_outcome_does_not_count_as_goal(self):
        rows = [prediction(1, "Anton", 1, 0, 2, 0, points=3)]

        self.assertEqual(top_scorers.build_top_scorers(rows), [])

    def test_unfinished_match_is_ignored(self):
        rows = [prediction(1, "Anton", 2, 0, 2, 0, points=10, status="IN_PLAY")]

        self.assertEqual(top_scorers.build_top_scorers(rows), [])

    def test_table_sorts_by_exact_scores_count(self):
        rows = [
            prediction(1, "Anton", 1, 0, 1, 0, points=10),
            prediction(2, "Boris", 2, 0, 2, 0, points=10),
            prediction(2, "Boris", 3, 0, 3, 0, points=10),
        ]

        result = top_scorers.build_top_scorers(rows)

        self.assertEqual([row["username"] for row in result], ["Boris", "Anton"])

    def test_tie_uses_total_points_as_secondary_sort(self):
        rows = [
            prediction(1, "Anton", 1, 0, 1, 0, points=10),
            prediction(1, "Anton", 2, 1, 2, 0, points=3),
            prediction(2, "Boris", 1, 1, 1, 1, points=10),
        ]

        result = top_scorers.build_top_scorers(rows)

        self.assertEqual([row["username"] for row in result], ["Anton", "Boris"])


if __name__ == "__main__":
    unittest.main()
