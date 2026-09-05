import re
import unittest
from datetime import datetime, timezone
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
        from app.routes.auth import auth_bp
        from app.routes.admin import admin_bp
        from app.routes.admin_matches import admin_matches_bp

        app = Flask(
            __name__,
            template_folder=str(ROOT / "templates"),
            static_folder=str(ROOT / "static"),
        )
        app.secret_key = "test-secret"
        app.register_blueprint(auth_bp)
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

    def get(self, url, route_cursor):
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
            ):
                response = client.get(url)
        return response

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
        self.assertEqual(params[5], "rpl")
        self.assertEqual(params[6], 5)
        self.assertEqual(params[7], "")

    def test_rpl_create_uses_same_day_1100_msk_deadline_in_utc(self):
        cursor = Cursor([(5, "Чемпионат России 🇷🇺", 1, None), None, (42,)])
        response, conn, _ = self.post(
            "/admin/russia_2027_add",
            {"home_team": "Спартак", "away_team": "Зенит", "match_date": "2027-07-18", "match_time": "19:00"},
            cursor,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(conn.commits, 1)
        params = cursor.executed[-1][1]
        self.assertEqual(params[2].strftime("%Y-%m-%d %H:%M"), "2027-07-18 16:00")
        self.assertEqual(params[3].strftime("%Y-%m-%d %H:%M"), "2027-07-18 08:00")

    def test_rpl_manual_deadline_has_priority(self):
        cursor = Cursor([(5, "Чемпионат России 🇷🇺", 1, None), None, (42,)])
        self.post(
            "/admin/russia_2027_add",
            {"home_team": "Спартак", "away_team": "Зенит", "match_date": "2027-07-18", "match_time": "19:00", "manual_deadline": "1", "deadline_date": "2027-07-17", "deadline_time": "09:30"},
            cursor,
        )
        self.assertEqual(cursor.executed[-1][1][3].strftime("%Y-%m-%d %H:%M"), "2027-07-17 06:30")

    def test_hidden_deadline_values_are_ignored_without_manual_mode(self):
        cursor = Cursor([(5, "Чемпионат России 🇷🇺", 1, None), None, (42,)])
        self.post(
            "/admin/russia_2027_add",
            {
                "home_team": "Спартак",
                "away_team": "Зенит",
                "match_date": "2027-07-18",
                "match_time": "19:00",
                "deadline_date": "2027-07-01",
                "deadline_time": "09:30",
            },
            cursor,
        )
        self.assertEqual(cursor.executed[-1][1][3].strftime("%Y-%m-%d %H:%M"), "2027-07-18 08:00")

    def test_manual_deadline_requires_both_parts(self):
        cursor = Cursor([(5, "Чемпионат России 🇷🇺", 1, None)])
        _, conn, _ = self.post(
            "/admin/russia_2027_add",
            {
                "home_team": "Спартак",
                "away_team": "Зенит",
                "match_date": "2027-07-18",
                "match_time": "19:00",
                "manual_deadline": "1",
                "deadline_date": "2027-07-17",
            },
            cursor,
        )
        self.assertEqual(conn.commits, 0)
        self.assertEqual(conn.rollbacks, 1)

    def test_early_match_with_manual_deadline_is_allowed(self):
        cursor = Cursor([(5, "Чемпионат России 🇷🇺", 1, None), None, (42,)])
        _, conn, _ = self.post(
            "/admin/russia_2027_add",
            {
                "home_team": "Спартак",
                "away_team": "Зенит",
                "match_date": "2027-07-18",
                "match_time": "11:00",
                "manual_deadline": "1",
                "deadline_date": "2027-07-17",
                "deadline_time": "09:30",
            },
            cursor,
        )
        self.assertEqual(conn.commits, 1)
        self.assertEqual(cursor.executed[-1][1][3].strftime("%Y-%m-%d %H:%M"), "2027-07-17 06:30")

    def test_edit_without_manual_mode_rebuilds_deadline_after_date_change(self):
        cursor = Cursor([(5, "Чемпионат России 🇷🇺", 1, None), (10, "SCHEDULED")])
        self.post(
            "/admin/russia_2027_edit",
            {
                "match_id": "10",
                "home_team": "Спартак",
                "away_team": "Зенит",
                "match_date": "2027-08-20",
                "match_time": "19:00",
                "deadline_date": "2027-08-15",
                "deadline_time": "09:30",
                "status": "SCHEDULED",
                "match_category": "rpl",
            },
            cursor,
        )
        update_params = next(params for query, params in cursor.executed if "SET home_team" in query)
        self.assertEqual(update_params[3].strftime("%Y-%m-%d %H:%M"), "2027-08-20 08:00")

    def test_edit_manual_mode_preserves_custom_deadline(self):
        cursor = Cursor([(5, "Чемпионат России 🇷🇺", 1, None), (10, "SCHEDULED")])
        self.post(
            "/admin/russia_2027_edit",
            {
                "match_id": "10",
                "home_team": "Спартак",
                "away_team": "Зенит",
                "match_date": "2027-08-20",
                "match_time": "19:00",
                "manual_deadline": "1",
                "deadline_date": "2027-08-19",
                "deadline_time": "09:30",
                "status": "SCHEDULED",
                "match_category": "rpl",
            },
            cursor,
        )
        update_params = next(params for query, params in cursor.executed if "SET home_team" in query)
        self.assertEqual(update_params[3].strftime("%Y-%m-%d %H:%M"), "2027-08-19 06:30")

    def test_edit_form_opens_manual_deadline_for_nonstandard_value(self):
        cursor = Cursor([
            (5, "Чемпионат России 🇷🇺", 1, None),
            (10, "Спартак", "Зенит", datetime(2027, 8, 20, 16, 0, tzinfo=timezone.utc), datetime(2027, 8, 19, 6, 30, tzinfo=timezone.utc), "SCHEDULED", None, None, "Тур 1", "rpl"),
        ])
        response = self.get("/admin/russia-2027/matches/10/edit", cursor)
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-manual-deadline-details open', html)
        self.assertNotIn('name="manual_deadline" value="1" disabled', html)

    def test_edit_form_keeps_standard_deadline_automatic(self):
        cursor = Cursor([
            (5, "Чемпионат России 🇷🇺", 1, None),
            (10, "Спартак", "Зенит", datetime(2027, 8, 20, 16, 0, tzinfo=timezone.utc), datetime(2027, 8, 20, 8, 0, tzinfo=timezone.utc), "SCHEDULED", None, None, "Тур 1", "rpl"),
        ])
        response = self.get("/admin/russia-2027/matches/10/edit", cursor)
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-manual-deadline-details ', html)
        self.assertIn('name="manual_deadline" value="1" disabled', html)

    def test_rpl_early_match_without_manual_deadline_is_rejected(self):
        cursor = Cursor([(5, "Чемпионат России 🇷🇺", 1, None)])
        response, conn, _ = self.post(
            "/admin/russia_2027_add",
            {"home_team": "Спартак", "away_team": "Зенит", "match_date": "2027-07-18", "match_time": "11:00"},
            cursor,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(conn.commits, 0)
        self.assertEqual(conn.rollbacks, 1)

    def test_manual_result_is_scoped_to_rpl_and_recalculates(self):
        cursor = Cursor([(5, "Чемпионат России 🇷🇺", 1, None), (10, "SCHEDULED", None, None)])
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
        recalc.assert_called_once_with(10, tournament_id=5, conn=conn, cur=cursor, emit_result_event=True)
        update_sql = cursor.executed[-1][0]
        self.assertIn("AND tournament_id = %s", update_sql)
        self.assertIn("AND league = 'rpl'", update_sql)

    def test_quick_result_route_is_atomic_and_scoped(self):
        cursor = Cursor([(5, "Чемпионат России 🇷🇺", 1, None)])
        response, conn, recalc = self.post(
            "/admin/russia_2027_result",
            {"match_id": "10", "home_score": "2", "away_score": "1"},
            cursor,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(conn.commits, 1)
        self.assertEqual(conn.rollbacks, 0)
        recalc.assert_called_once_with(10, tournament_id=5, conn=conn, cur=cursor)
        self.assertEqual(len(cursor.executed), 2)
        self.assertIn("SET home_score = %s, away_score = %s, status = 'FINISHED'", cursor.executed[-1][0])
        self.assertIn("AND tournament_id = %s AND league = 'rpl'", cursor.executed[-1][0])
        self.assertNotIn("deadline =", cursor.executed[-1][0])

    def test_edit_page_preserves_safe_return_to(self):
        cursor = Cursor([
            (5, "Чемпионат России 🇷🇺", 1, None),
            (10, "Спартак", "Зенит", None, None, "SCHEDULED", None, None, "Тур 1", "rpl"),
        ])
        response = self.get(
            "/admin/russia-2027/matches/10/edit?return_to=/admin/russia-2027?view=finished%26page=2",
            cursor,
        )
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('action="/admin/russia_2027_edit"', html)
        self.assertIn('name="return_to" value="/admin/russia-2027?view=finished&amp;page=2"', html)

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

    def test_general_matches_page_is_removed(self):
        app = self.make_app()
        self.assertNotIn("/admin/matches", {rule.rule for rule in app.url_map.iter_rules()})
        with app.test_client() as client:
            self.assertEqual(client.get("/admin/matches").status_code, 404)

    def test_rpl_admin_template_has_no_api_controls(self):
        html = (ROOT / "templates" / "admin_russia_2027.html").read_text(encoding="utf-8")
        self.assertNotIn("Understat", html)
        self.assertNotIn("api_match_id", html)
        self.assertNotIn("russia_2027_import", html)
        self.assertNotIn("Проверить календарь", html)
        self.assertNotIn("Импортировать / обновить", html)
        self.assertIn("('upcoming'", html)
        self.assertIn("Редактировать матч", html)
        self.assertIn('aria-label="Действия с матчем"', html)
        self.assertIn("admin_russia_2027_result", html)
        self.assertIn("admin_russia_2027_edit_form", html)
        self.assertIn('action="{{ url_for(\'admin_matches.admin_russia_2027_add\') }}"', html)
        self.assertNotIn("Поиск по команде или ID", html)
        self.assertNotIn("Все статусы", html)
        self.assertNotIn('name="q"', html)
        self.assertNotIn('name="status"', html)
        self.assertNotIn('name="period"', html)
        self.assertNotIn("admin.admin_matches", html)

        edit_html = (ROOT / "templates" / "admin_rpl_edit.html").read_text(encoding="utf-8")
        self.assertNotIn("api_match_id", edit_html)
        self.assertNotIn("API", edit_html)

    def test_rpl_admin_shows_default_deadline_and_display_date_without_changing_inputs(self):
        html = (ROOT / "templates" / "admin_russia_2027.html").read_text(encoding="utf-8")
        self.assertIn("11:00 МСК в день матча", html)
        self.assertIn("Изменить дедлайн вручную", html)
        self.assertIn('name="manual_deadline"', html)
        self.assertIn("data-manual-deadline-details", html)
        self.assertIn("data-manual-deadline-field", html)
        self.assertIn("Для этого матча необходимо задать дедлайн вручную", html)
        self.assertIn("m.date_label", html)
        edit_html = (ROOT / "templates" / "admin_rpl_edit.html").read_text(encoding="utf-8")
        self.assertIn('type="date" name="match_date" value="{{ match.match_date_msk }}"', edit_html)
        self.assertIn("Изменить дедлайн вручную", edit_html)
        self.assertIn("match.uses_manual_deadline", edit_html)
        self.assertIn('name="manual_deadline"', edit_html)

        rcup_html = (ROOT / "templates" / "admin_russian_cup.html").read_text(encoding="utf-8")
        self.assertNotIn("manual_deadline", rcup_html)

    def test_rpl_admin_css_is_scoped_and_mobile_safe(self):
        css = (ROOT / "static" / "css" / "tournaments" / "rpl-admin.css").read_text(encoding="utf-8")
        self.assertIn("body.tournament-rpl .admin-rpl-page", css)
        self.assertIn("#020b27", css)
        self.assertIn("#0039A6", css)
        self.assertIn("#D52B1E", css)
        self.assertIn("rgba(255,255,255,0.82)", css)
        self.assertIn("backdrop-filter: blur(18px) saturate(145%)", css)
        self.assertNotIn("--rpl-admin-", css)
        self.assertNotIn("body.tournament-rcup", css)
        self.assertNotIn("#edf5ff", css)
        self.assertIn("width: 44px;", css)
        self.assertIn("height: 44px;", css)
        self.assertIn("@media (max-width: 430px)", css)

    def test_rpl_action_popover_is_opaque_contrast_safe_and_scoped(self):
        css = (ROOT / "static" / "css" / "tournaments" / "rpl-admin.css").read_text(encoding="utf-8")
        self.assertIn("body.tournament-rpl .admin-rpl-page .rpl-more-menu", css)
        self.assertIn("rgba(4,18,53,0.97)", css)
        self.assertIn("rgba(2,11,39,0.96)", css)
        self.assertIn("border: 1px solid rgba(255,255,255,0.24)", css)
        self.assertIn("border-radius: 18px", css)
        self.assertIn("0 18px 45px rgba(0,0,0,0.45)", css)
        self.assertIn("backdrop-filter: blur(20px) saturate(150%)", css)
        self.assertIn("color: rgba(255,255,255,0.94)", css)
        self.assertIn("color: rgba(255,255,255,0.68)", css)
        self.assertIn("color: #ff6b64", css)
        self.assertIn("min-height: 48px", css)
        self.assertIn("width: min(270px, calc(100vw - 32px))", css)
        self.assertIn("z-index: 100002", css)
        self.assertIn("rpl-more[open]::before", css)
        self.assertIn("background: rgba(255,255,255,0.07)", css)
        self.assertNotIn("body.tournament-rcup", css)

        template = (ROOT / "templates" / "admin_russia_2027.html").read_text(encoding="utf-8")
        self.assertIn("rpl-more-menu", template)
        self.assertIn("rpl-technical", template)
        self.assertNotIn("api_match_id", template)

    def test_rpl_action_popover_uses_fixed_mobile_bottom_sheet_positioning(self):
        css = (ROOT / "static" / "css" / "tournaments" / "rpl-admin.css").read_text(encoding="utf-8")
        mobile_start = css.index("@media (max-width: 600px)")
        mobile_css = css[mobile_start:]
        self.assertIn("position: fixed", mobile_css)
        self.assertIn("top: auto", mobile_css)
        self.assertIn("left: 16px", mobile_css)
        self.assertIn("right: 16px", mobile_css)
        self.assertIn("inset-inline-start: 16px", mobile_css)
        self.assertIn("inset-inline-end: 16px", mobile_css)
        self.assertIn("bottom: calc(var(--bottom-nav-height, 88px) + 16px + env(safe-area-inset-bottom))", mobile_css)
        self.assertIn("width: auto", mobile_css)
        self.assertIn("min-width: 0", mobile_css)
        self.assertIn("max-width: none", mobile_css)
        self.assertIn("max-height: min(70vh, 520px)", mobile_css)
        self.assertIn("overflow-y: auto", mobile_css)
        self.assertIn("margin: 0", mobile_css)
        self.assertIn("transform: none", mobile_css)
        self.assertIn("translate: none", mobile_css)
        self.assertIn("z-index: 100002", mobile_css)
        self.assertNotIn("right: -", mobile_css)
        self.assertIn("z-index: 100001", css)
        self.assertIn("position: fixed", css[css.index("rpl-more[open]::before"):])
        self.assertIn("inset: 0", css[css.index("rpl-more[open]::before"):])
        self.assertIn("overflow-wrap: anywhere", css)
        self.assertIn("word-break: break-word", css)
        self.assertNotIn("body.tournament-rcup", css)

        rcup_css = (ROOT / "static" / "css" / "tournaments" / "russian-cup.css").read_text(encoding="utf-8")
        self.assertNotIn("rpl-more-menu", rcup_css)

        edit_html = (ROOT / "templates" / "admin_rpl_edit.html").read_text(encoding="utf-8")
        self.assertIn('css/tournaments/rpl-admin.css', edit_html)
        self.assertNotIn('style="', edit_html)

    def test_rpl_api_functions_and_season_are_absent(self):
        service = (ROOT / "app" / "services" / "match_service.py").read_text(encoding="utf-8")
        self.assertNotRegex(service, re.compile(r"fetch_rpl|resolve_rpl|Understat|RPL_SEASON", re.IGNORECASE))


if __name__ == "__main__":
    unittest.main()
