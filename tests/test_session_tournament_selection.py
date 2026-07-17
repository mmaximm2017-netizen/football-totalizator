import unittest
from unittest.mock import patch

from flask import Flask

from app.services.tournament_context_service import get_session_start_tournament_id


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


class Connection:
    def __init__(self, cursor):
        self.cursor_value = cursor
        self.commits = 0

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.commits += 1


class SessionTournamentSelectionTests(unittest.TestCase):
    def test_nearest_open_deadline_query_ignores_archived_and_closed_matches(self):
        cursor = Cursor([(7,)])
        selected = get_session_start_tournament_id(cursor)

        query = cursor.queries[0][0]
        self.assertEqual(selected, 7)
        self.assertIn("t.is_active = 1", query)
        self.assertIn("m.deadline > NOW()", query)
        self.assertIn("FINISHED", query)
        self.assertIn("CANCELLED", query)
        self.assertIn("POSTPONED", query)

    def test_no_eligible_match_uses_first_active_tournament_fallback(self):
        cursor = Cursor([None, (3,)])
        self.assertEqual(get_session_start_tournament_id(cursor), 3)
        self.assertEqual(len(cursor.queries), 2)

    def test_no_matches_and_no_active_tournaments_returns_none(self):
        cursor = Cursor([None, None])
        self.assertIsNone(get_session_start_tournament_id(cursor))

    def test_login_sets_session_tournament_and_redirects_with_explicit_tid(self):
        from app.routes.auth import auth_bp

        app = Flask(__name__)
        app.secret_key = "test"
        app.register_blueprint(auth_bp)
        app.add_url_rule("/", "main.index", lambda: "index")
        cursor = Cursor([(11, "password", 0)])
        conn = Connection(cursor)

        with (
            patch("app.routes.auth.get_db", return_value=conn),
            patch("app.routes.auth.close_db"),
            patch("app.routes.auth.get_session_start_tournament_id", return_value=5),
        ):
            with app.test_client() as client:
                response = client.post("/login", data={"username": "user", "password": "password"})
                self.assertEqual(response.status_code, 302)
                self.assertIn("/?tid=5", response.headers["Location"])
                with client.session_transaction() as current_session:
                    self.assertEqual(current_session["selected_tournament_id"], 5)

    def test_bare_main_request_redirects_to_session_selection(self):
        from app.routes.main import main_bp

        app = Flask(__name__)
        app.secret_key = "test"
        app.register_blueprint(main_bp)
        app.add_url_rule("/login", "auth.login", lambda: "login")
        cursor = Cursor([])
        conn = Connection(cursor)

        with patch("app.routes.main.get_db", return_value=conn), patch("app.routes.main.close_db"):
            with app.test_client() as client:
                with client.session_transaction() as current_session:
                    current_session["user_id"] = 11
                    current_session["selected_tournament_id"] = 5
                response = client.get("/")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/?tid=5", response.headers["Location"])

    def test_ajax_prediction_post_without_tid_returns_json_not_redirect(self):
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
            patch("app.routes.main.get_all_tournaments", return_value=[]),
            patch("app.routes.main.get_selected_tournament_id", return_value=5),
            patch("app.routes.main.is_before_deadline", return_value=True),
        ):
            with app.test_client() as client:
                with client.session_transaction() as current_session:
                    current_session["user_id"] = 11
                    current_session["selected_tournament_id"] = 5
                response = client.post(
                    "/",
                    data={"match_id": "10", "home_goals": "1", "away_goals": "2"},
                    headers={"X-Requested-With": "XMLHttpRequest"},
                    follow_redirects=False,
                )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.is_json)
        self.assertTrue(response.get_json()["ok"])
        self.assertEqual(conn.commits, 1)
        self.assertTrue(any("ON CONFLICT" in query for query, _ in cursor.queries))

    def test_prediction_post_rejects_selected_tournament_mismatch(self):
        from app.routes.main import main_bp

        app = Flask(__name__)
        app.secret_key = "test"
        app.register_blueprint(main_bp)
        app.add_url_rule("/login", "auth.login", lambda: "login")
        cursor = Cursor([(10, "Home", "Away", None, None, "SCHEDULED", 6)])
        conn = Connection(cursor)

        with (
            patch("app.routes.main.get_db", return_value=conn),
            patch("app.routes.main.close_db"),
            patch("app.routes.main.get_all_tournaments", return_value=[]),
            patch("app.routes.main.get_selected_tournament_id", return_value=5),
        ):
            with app.test_client() as client:
                with client.session_transaction() as current_session:
                    current_session["user_id"] = 11
                    current_session["selected_tournament_id"] = 5
                response = client.post(
                    "/",
                    data={"match_id": "10", "home_goals": "1", "away_goals": "2"},
                    headers={"X-Requested-With": "XMLHttpRequest"},
                )

        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.is_json)
        self.assertEqual(conn.commits, 0)
        self.assertFalse(any("ON CONFLICT" in query for query, _ in cursor.queries))

    def test_repeated_ajax_prediction_post_uses_existing_update_path(self):
        from app.routes.main import main_bp

        app = Flask(__name__)
        app.secret_key = "test"
        app.register_blueprint(main_bp)
        app.add_url_rule("/login", "auth.login", lambda: "login")
        match_row = (10, "Home", "Away", None, None, "SCHEDULED", 5)
        cursor = Cursor([match_row, match_row])
        conn = Connection(cursor)

        with (
            patch("app.routes.main.get_db", return_value=conn),
            patch("app.routes.main.close_db"),
            patch("app.routes.main.get_all_tournaments", return_value=[]),
            patch("app.routes.main.get_selected_tournament_id", return_value=5),
            patch("app.routes.main.is_before_deadline", return_value=True),
        ):
            with app.test_client() as client:
                with client.session_transaction() as current_session:
                    current_session["user_id"] = 11
                    current_session["selected_tournament_id"] = 5
                first = client.post("/", data={"match_id": "10", "home_goals": "1", "away_goals": "2"}, headers={"X-Requested-With": "XMLHttpRequest"})
                second = client.post("/", data={"match_id": "10", "home_goals": "3", "away_goals": "1"}, headers={"X-Requested-With": "XMLHttpRequest"})

        self.assertTrue(first.is_json and first.get_json()["ok"])
        self.assertTrue(second.is_json and second.get_json()["ok"])
        self.assertEqual(second.get_json()["home_goals"], 3)
        self.assertEqual(conn.commits, 2)
