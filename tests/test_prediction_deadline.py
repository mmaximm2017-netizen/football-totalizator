import unittest
from unittest.mock import patch

from flask import Flask


class Cursor:
    def __init__(self, results):
        self.results = list(results)
        self.queries = []
        self.rowcount = 0

    def execute(self, query, params=None):
        self.queries.append((query, params))
        if query.lstrip().startswith("INSERT"):
            self.rowcount = 1

    def fetchone(self):
        return self.results.pop(0) if self.results else None


class ProductionPredictionsSchemaCursor(Cursor):
    """Reject columns absent from the production predictions schema contract."""

    def execute(self, query, params=None):
        if "RETURNING id" in query:
            raise RuntimeError('column "id" does not exist')
        super().execute(query, params)


class Connection:
    def __init__(self, cursor):
        self.cursor_value = cursor
        self.commits = 0

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.commits += 1


class PredictionDeadlineTests(unittest.TestCase):
    def ajax_post(self, cursor, data=None, headers=None):
        from app.routes.main import main_bp

        app = Flask(__name__)
        app.secret_key = "test"
        app.register_blueprint(main_bp)
        app.add_url_rule("/login", "auth.login", lambda: "login")

        conn = Connection(cursor)

        with (
            patch("app.routes.main.get_db", return_value=conn),
            patch("app.routes.main.close_db"),
            patch("app.routes.main.get_all_tournaments", return_value=[{"id": 5, "is_active": 1}]),
            patch("app.routes.main.is_before_deadline", return_value=True),
        ):
            with app.test_client() as client:
                with client.session_transaction() as current_session:
                    current_session["user_id"] = 11
                    current_session["selected_tournament_id"] = 5
                return client.post(
                    "/",
                    data=data or {"match_id": "10", "home_goals": "1", "away_goals": "2"},
                    headers=headers or {"X-Requested-With": "XMLHttpRequest"},
                )

    def test_deadline_sql_check_passes_returns_200(self):
        response = self.ajax_post(Cursor([(10, "Home", "Away", None, None, "SCHEDULED", 5), (42,)]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.is_json)
        self.assertTrue(response.get_json()["ok"])

    def test_deadline_sql_check_fails_returns_409(self):
        response = self.ajax_post(Cursor([(10, "Home", "Away", None, None, "SCHEDULED", 5)]))
        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.is_json)
        self.assertFalse(response.get_json()["ok"])

    def test_deadline_sql_check_fails_non_ajax_redirects_with_flash(self):
        from app.routes.main import main_bp

        app = Flask(__name__)
        app.secret_key = "test"
        app.register_blueprint(main_bp)
        app.add_url_rule("/login", "auth.login", lambda: "login")

        cursor = Cursor([(10, "Home", "Away", None, None, "SCHEDULED", 5)])
        conn = Connection(cursor)

        with (
            patch("app.routes.main.get_db", return_value=conn),
            patch("app.routes.main.close_db"),
            patch("app.routes.main.get_all_tournaments", return_value=[{"id": 5, "is_active": 1}]),
            patch("app.routes.main.is_before_deadline", return_value=True),
        ):
            with app.test_client() as client:
                with client.session_transaction() as current_session:
                    current_session["user_id"] = 11
                    current_session["selected_tournament_id"] = 5
                response = client.post(
                    "/",
                    data={"match_id": "10", "home_goals": "1", "away_goals": "2"},
                )

        self.assertEqual(response.status_code, 302)

    def test_deadline_sql_check_fails_insert_has_no_on_conflict_in_query(self):
        cursor = Cursor([(10, "Home", "Away", None, None, "SCHEDULED", 5)])
        response = self.ajax_post(cursor)

        self.assertEqual(response.status_code, 409)
        insert_queries = [
            q for q, _ in cursor.queries
            if q.lstrip().startswith("INSERT")
        ]
        self.assertEqual(len(insert_queries), 1)
        self.assertIn("ON CONFLICT", insert_queries[0])
        self.assertIn("CURRENT_TIMESTAMP < m.deadline", insert_queries[0])
        self.assertIn("RETURNING 1", insert_queries[0])
        self.assertNotIn("RETURNING id", insert_queries[0])

    def test_production_predictions_schema_contract_accepts_upsert(self):
        cursor = ProductionPredictionsSchemaCursor([
            (10, "Home", "Away", None, None, "SCHEDULED", 5),
            (1,),
        ])

        response = self.ajax_post(cursor)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.is_json)
        self.assertTrue(response.get_json()["ok"])
