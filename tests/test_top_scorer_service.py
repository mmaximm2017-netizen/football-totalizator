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

    def test_database_query_aggregates_and_orders_scorers_before_fetchall(self):
        class Cursor:
            def __init__(self):
                self.queries = []
                self.one_rows = iter([(1,)])

            def execute(self, query, params):
                self.queries.append((query, params))

            def fetchone(self):
                return next(self.one_rows)

            def fetchall(self):
                return [(2, "Boris", 2, 23), (1, "Anton", 1, 10)]

        class Connection:
            def __init__(self, cursor):
                self.cursor_value = cursor

            def cursor(self):
                return self.cursor_value

        cursor = Cursor()
        original_get_db, original_close_db = top_scorers.get_db, top_scorers.close_db
        top_scorers.get_db = lambda: Connection(cursor)
        top_scorers.close_db = lambda conn, cur: None
        try:
            result = top_scorers.get_tournament_top_scorers(7)
        finally:
            top_scorers.get_db, top_scorers.close_db = original_get_db, original_close_db

        query, params = cursor.queries[1]
        self.assertIn("GROUP BY u.id, u.username", query)
        self.assertIn("HAVING COUNT(*) FILTER", query)
        self.assertIn("ORDER BY scorer_goals DESC, points DESC, u.username ASC", query)
        self.assertEqual(params[0], 7)
        self.assertEqual(result, [
            {"user_id": 2, "username": "Boris", "scorer_goals": 2, "points": 23, "place": 1},
            {"user_id": 1, "username": "Anton", "scorer_goals": 1, "points": 10, "place": 2},
        ])


if __name__ == "__main__":
    unittest.main()
