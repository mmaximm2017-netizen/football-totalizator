import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RANKING_PATH = ROOT / "app" / "services" / "ranking_service.py"


def load_ranking_module():
    app_module = types.ModuleType("app")
    db_module = types.ModuleType("app.db")
    db_module.get_db = lambda: None
    db_module.close_db = lambda conn, cur: None

    original_app = sys.modules.get("app")
    original_db = sys.modules.get("app.db")

    sys.modules["app"] = app_module
    sys.modules["app.db"] = db_module

    try:
        spec = importlib.util.spec_from_file_location("ranking_under_test", RANKING_PATH)
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


ranking = load_ranking_module()


def row(user_id, place, points=0):
    return {
        "user_id": user_id,
        "username": f"user{user_id}",
        "points": points,
        "exact_scores": 0,
        "exact_diffs": 0,
        "outcomes": 0,
        "place": place,
        "shared": False,
    }


class RankingMovementTests(unittest.TestCase):
    def movements(self, current, previous, has_finished_match=True):
        return {
            item["user_id"]: item["movement"]
            for item in ranking.apply_rank_movements(
                current,
                previous,
                has_finished_match=has_finished_match,
            )
        }

    def test_player_moved_up_after_last_match(self):
        movements = self.movements([row(1, 1)], [row(1, 2)])

        self.assertEqual(movements[1], "up")

    def test_player_moved_down_after_last_match(self):
        movements = self.movements([row(1, 3)], [row(1, 1)])

        self.assertEqual(movements[1], "down")

    def test_player_stayed_in_same_place(self):
        movements = self.movements([row(1, 2)], [row(1, 2)])

        self.assertIsNone(movements[1])

    def test_multiple_players_changed_places(self):
        current = [row(1, 1), row(2, 2), row(3, 3), row(4, 4)]
        previous = [row(1, 3), row(2, 1), row(3, 4), row(4, 2)]

        movements = self.movements(current, previous)

        self.assertEqual(movements[1], "up")
        self.assertEqual(movements[2], "down")
        self.assertEqual(movements[3], "up")
        self.assertEqual(movements[4], "down")

    def test_no_finished_matches_hides_all_movements(self):
        movements = self.movements([row(1, 1), row(2, 2)], [], has_finished_match=False)

        self.assertIsNone(movements[1])
        self.assertIsNone(movements[2])

    def test_latest_finished_match_ignores_future_kickoffs(self):
        class Cursor:
            def execute(self, query, params):
                self.query = query
                self.params = params

            def fetchone(self):
                return (42,)

        cur = Cursor()

        match_id = ranking._fetch_latest_played_finished_match_id(cur, tournament_id=7)

        self.assertEqual(match_id, 42)
        self.assertEqual(cur.params, (7,))
        self.assertIn("kickoff_time <= NOW()", cur.query)

    def test_latest_finished_match_returns_none_when_missing(self):
        class Cursor:
            def execute(self, query, params):
                pass

            def fetchone(self):
                return None

        self.assertIsNone(
            ranking._fetch_latest_played_finished_match_id(Cursor(), tournament_id=7)
        )

    def test_ranking_aggregates_only_played_finished_matches(self):
        class Cursor:
            def execute(self, query, params):
                self.query = query
                self.params = params

            def fetchall(self):
                return []

        cur = Cursor()

        ranking._fetch_ranking(
            cur,
            tournament_id=7,
            tournament_is_active=True,
            exclude_match_id=42,
        )

        self.assertEqual(cur.params, (7, 42, 42, True))
        self.assertIn("LEFT JOIN matches m", cur.query)
        self.assertIn("UPPER(m.status) IN ('FINISHED', 'COMPLETE', 'COMPLETED')", cur.query)
        self.assertIn("m.kickoff_time <= NOW()", cur.query)
        self.assertIn("m.id <> %s", cur.query)

    def leader_status_for_gap(self, gap):
        ranking_rows = [row(1, 1, 100), row(2, 2, 100 - gap)]

        return ranking.apply_leader_status(ranking_rows)[0]["leader_status"]

    def outsider_status_for_gap(self, gap):
        ranking_rows = [row(1, 1, 100), row(2, 2, 100 - gap)]

        return ranking.apply_leader_status(ranking_rows)[-1]["outsider_status"]

    def test_leader_status_gap_zero_is_leader(self):
        self.assertEqual(self.leader_status_for_gap(0), "leader")

    def test_leader_status_gap_one_is_leader(self):
        self.assertEqual(self.leader_status_for_gap(1), "leader")

    def test_leader_status_gap_nineteen_is_leader(self):
        self.assertEqual(self.leader_status_for_gap(19), "leader")

    def test_leader_status_gap_twenty_is_confident(self):
        self.assertEqual(self.leader_status_for_gap(20), "confident")

    def test_leader_status_gap_twenty_nine_is_confident(self):
        self.assertEqual(self.leader_status_for_gap(29), "confident")

    def test_leader_status_gap_thirty_is_dominant(self):
        self.assertEqual(self.leader_status_for_gap(30), "dominant")

    def test_leader_status_gap_thirty_nine_is_dominant(self):
        self.assertEqual(self.leader_status_for_gap(39), "dominant")

    def test_leader_status_gap_forty_is_absolute(self):
        self.assertEqual(self.leader_status_for_gap(40), "absolute")

    def test_leader_status_gap_fifty_is_absolute(self):
        self.assertEqual(self.leader_status_for_gap(50), "absolute")

    def test_leader_status_only_first_sorted_player(self):
        ranking_rows = [row(1, 1, 100), row(2, 1, 100)]

        annotated = ranking.apply_leader_status(ranking_rows)

        self.assertEqual(annotated[0]["leader_status"], "leader")
        self.assertIsNone(annotated[1]["leader_status"])

    def test_outsider_status_gap_zero_is_outsider(self):
        self.assertEqual(self.outsider_status_for_gap(0), "outsider")

    def test_outsider_status_gap_one_is_outsider(self):
        self.assertEqual(self.outsider_status_for_gap(1), "outsider")

    def test_outsider_status_gap_nineteen_is_outsider(self):
        self.assertEqual(self.outsider_status_for_gap(19), "outsider")

    def test_outsider_status_gap_twenty_is_confident(self):
        self.assertEqual(self.outsider_status_for_gap(20), "confident")

    def test_outsider_status_gap_twenty_nine_is_confident(self):
        self.assertEqual(self.outsider_status_for_gap(29), "confident")

    def test_outsider_status_gap_thirty_is_dominant(self):
        self.assertEqual(self.outsider_status_for_gap(30), "dominant")

    def test_outsider_status_gap_thirty_nine_is_dominant(self):
        self.assertEqual(self.outsider_status_for_gap(39), "dominant")

    def test_outsider_status_gap_forty_is_absolute(self):
        self.assertEqual(self.outsider_status_for_gap(40), "absolute")

    def test_outsider_status_gap_fifty_is_absolute(self):
        self.assertEqual(self.outsider_status_for_gap(50), "absolute")

    def test_outsider_status_only_last_sorted_player(self):
        ranking_rows = [row(1, 1, 100), row(2, 2, 90), row(3, 2, 90)]

        annotated = ranking.apply_leader_status(ranking_rows)

        self.assertIsNone(annotated[1]["outsider_status"])
        self.assertEqual(annotated[2]["outsider_status"], "outsider")


if __name__ == "__main__":
    unittest.main()
