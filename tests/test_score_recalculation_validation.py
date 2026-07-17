import unittest
from unittest.mock import patch

from flask import Flask

from app.routes import admin_matches
from app.services import scoring_recalculation_service as service


class Cursor:
    def __init__(self, matches, predictions=None):
        self.matches = matches
        self.predictions = predictions or [(7, 0, 0, 1)]
        self.mode = None
        self.match_id = None
        self.updates = []

    def execute(self, query, params=None):
        if "FROM matches" in query and "WHERE id = %s" in query:
            self.mode = "match"
            self.match_id = params[0]
        elif "SELECT id" in query and "FROM matches" in query:
            self.mode = "all_matches"
        elif "FROM predictions" in query:
            self.mode = "predictions"
        elif "UPDATE predictions" in query:
            self.updates.append(params)

    def fetchone(self):
        return self.matches.get(self.match_id)

    def fetchall(self):
        if self.mode == "all_matches":
            return [(match_id,) for match_id in self.matches]
        if self.mode == "predictions":
            return self.predictions
        return []


class Connection:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class ScoreRecalculationValidationTests(unittest.TestCase):
    def test_finished_missing_scores_skip_without_calling_calculator(self):
        for home_score, away_score in ((None, None), (1, None), (None, 1)):
            with self.subTest(score=(home_score, away_score)):
                cur = Cursor({1: (1, "FINISHED", home_score, away_score, 5)})
                with patch.object(service, "calculate_points") as calculate, self.assertLogs(service.logger, level="WARNING"):
                    result = service.recalc_match_points(1, conn=Connection(), cur=cur)

                self.assertTrue(result["skipped"])
                self.assertEqual(result["updated"], 0)
                calculate.assert_not_called()
                self.assertEqual(cur.updates, [])

    def test_finished_nil_nil_score_is_valid_and_calculated(self):
        cur = Cursor({1: (1, "FINISHED", 0, 0, 5)})
        with patch.object(service, "calculate_points", return_value=10) as calculate:
            result = service.recalc_match_points(1, conn=Connection(), cur=cur)

        self.assertEqual(result["updated"], 1)
        calculate.assert_called_once_with(0, 0, 0, 0)
        self.assertEqual(cur.updates[0][0], 10)

    def test_invalid_finished_match_skips_while_valid_match_is_recalculated(self):
        cur = Cursor(
            {
                1: (1, "FINISHED", None, None, 5),
                2: (2, "FINISHED", 0, 0, 5),
            }
        )
        with patch.object(service, "calculate_points", return_value=10) as calculate, self.assertLogs(service.logger, level="WARNING"):
            result = service.recalc_all_points(conn=Connection(), cur=cur)

        self.assertEqual(result, {"matches": 2, "updated": 1, "skipped": 1})
        calculate.assert_called_once_with(0, 0, 0, 0)
        self.assertEqual(len(cur.updates), 1)


class ManualResultValidationTests(unittest.TestCase):
    def test_manual_score_validation_rejects_missing_score_and_accepts_nil_nil(self):
        self.assertEqual(admin_matches.validate_score(None, "0"), (None, None))
        self.assertEqual(admin_matches.validate_score("0", None), (None, None))
        self.assertEqual(admin_matches.validate_score("0", "0"), (0, 0))

    def test_manual_result_handler_rejects_missing_score_before_database_update(self):
        app = Flask(__name__)
        app.secret_key = "test"
        app.add_url_rule("/admin", "admin.admin", lambda: "admin")

        with app.test_request_context("/admin/set_result", method="POST", data={"match_id": "5", "home_score": "", "away_score": "0"}):
            response = admin_matches.handle_set_result(None, None)

        self.assertEqual(response.status_code, 302)

    def test_manual_result_handler_accepts_finished_nil_nil(self):
        class ManualCursor:
            rowcount = 1

            def __init__(self):
                self.executed = []

            def execute(self, query, params=None):
                self.executed.append((query, params))

        class ManualConnection:
            def __init__(self):
                self.commits = 0

            def commit(self):
                self.commits += 1

        app = Flask(__name__)
        app.secret_key = "test"
        app.add_url_rule("/admin", "admin.admin", lambda: "admin")
        cur = ManualCursor()
        conn = ManualConnection()

        with app.test_request_context("/admin/set_result", method="POST", data={"match_id": "5", "home_score": "0", "away_score": "0"}), patch.object(admin_matches, "recalc_match_points") as recalc:
            response = admin_matches.handle_set_result(conn, cur)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(cur.executed[0][1], (0, 0, "5"))
        recalc.assert_called_once_with("5", conn=conn, cur=cur)
        self.assertEqual(conn.commits, 1)
