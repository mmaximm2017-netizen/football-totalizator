import re
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask


ROOT = Path(__file__).resolve().parents[1]


class Cursor:
    def __init__(self, fetchone_results):
        self.fetchone_results = list(fetchone_results)
        self.executed = []
        self.rowcount = 1

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchone(self):
        return self.fetchone_results.pop(0) if self.fetchone_results else None


class Connection:
    def __init__(self, cursor):
        self.cursor_value = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class RplManualAdminTests(unittest.TestCase):
    def make_app(self):
        from app.routes.admin import admin_bp
        from app.routes.admin_matches import admin_matches_bp

        app = Flask(__name__)
        app.secret_key = "test-secret"
        app.register_blueprint(admin_bp)
        app.register_blueprint(admin_matches_bp)
        return app

    def post(self, url, payload, route_cursor):
        app = self.make_app()
        user_conn = Connection(Cursor([(1, 0)]))
        route_conn = Connection(route_cursor)
        with app.test_client() as client:
            with client.session_transaction() as session:
                session["user_id"] = 1
            with (
                patch("app.routes.admin_common.get_db", return_value=user_conn),
                patch("app.routes.admin_common.close_db"),
                patch("app.routes.admin_matches.get_db", return_value=route_conn),
                patch("app.routes.admin_matches.close_db"),
                patch("app.routes.admin_matches.recalc_match_points") as recalc,
            ):
                response = client.post(url, data=payload)
        return response, route_conn, recalc

    def test_create_does_not_require_api_fields_and_uses_manual_defaults(self):
        cursor = Cursor([(5, "Чемпионат России 🇷🇺", 1, None), None, (42,)])
        response, conn, _ = self.post(
            "/admin/russia_2027_add",
            {
                "home_team": "Спартак",
                "away_team": "Зенит",
                "match_date": "2027-07-18",
                "match_time": "19:00",
            },
            cursor,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(conn.commits, 1)
        insert_sql, params = cursor.executed[-1]
        self.assertIn("VALUES (NULL", insert_sql)
        self.assertEqual(params[4], "SCHEDULED")
        self.assertEqual(params[6], "")

    def test_manual_result_is_scoped_to_rpl_and_recalculates(self):
        cursor = Cursor([(5, "Чемпионат России 🇷🇺", 1, None), (10, "SCHEDULED")])
        response, conn, recalc = self.post(
            "/admin/russia_2027_edit",
            {
                "match_id": "10",
                "home_team": "Спартак",
                "away_team": "Зенит",
                "match_date": "2027-07-18",
                "match_time": "19:00",
                "home_score": "2",
                "away_score": "1",
            },
            cursor,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(conn.commits, 1)
        recalc.assert_called_once_with(10, tournament_id=5, conn=conn, cur=cursor)
        update_sql = cursor.executed[-1][0]
        self.assertIn("AND tournament_id = %s", update_sql)
        self.assertIn("AND league = 'rpl'", update_sql)

    def test_deleting_manual_result_clears_prediction_points(self):
        cursor = Cursor([(5, "Чемпионат России 🇷🇺", 1, None), (10, "FINISHED")])
        response, conn, recalc = self.post(
            "/admin/russia_2027_edit",
            {
                "match_id": "10",
                "home_team": "Спартак",
                "away_team": "Зенит",
                "match_date": "2027-07-18",
                "match_time": "19:00",
                "status": "FINISHED",
                "delete_score": "1",
            },
            cursor,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(conn.commits, 1)
        recalc.assert_not_called()
        self.assertIn("UPDATE predictions", cursor.executed[-1][0])

    def test_old_rpl_import_route_is_removed(self):
        app = self.make_app()
        self.assertNotIn("/admin/russia_2027_import", {rule.rule for rule in app.url_map.iter_rules()})

    def test_rpl_admin_template_has_no_api_controls(self):
        html = (ROOT / "templates" / "admin_russia_2027.html").read_text(encoding="utf-8")
        self.assertNotIn("Understat", html)
        self.assertNotIn("api_match_id", html)
        self.assertNotIn("russia_2027_import", html)
        self.assertNotIn("Проверить календарь", html)
        self.assertNotIn("Импортировать / обновить", html)
        self.assertIn('action="{{ url_for(\'admin_matches.admin_russia_2027_add\') }}"', html)

    def test_rpl_api_functions_and_season_are_absent(self):
        service = (ROOT / "app" / "services" / "match_service.py").read_text(encoding="utf-8")
        self.assertNotRegex(service, re.compile(r"fetch_rpl|resolve_rpl|Understat|RPL_SEASON", re.IGNORECASE))


if __name__ == "__main__":
    unittest.main()
