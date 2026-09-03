import unittest
import re
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, render_template
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class RecordingCursor:
    def __init__(self, tournament_row=None, match_rows=None):
        self.tournament_row = tournament_row
        self.match_rows = match_rows or []
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchone(self):
        return self.tournament_row

    def fetchall(self):
        return self.match_rows


class SequenceCursor:
    def __init__(self, fetchone_results=None, fetchall_results=None):
        self.fetchone_results = list(fetchone_results or [])
        self.fetchall_results = list(fetchall_results or [])
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchone(self):
        return self.fetchone_results.pop(0) if self.fetchone_results else None

    def fetchall(self):
        return self.fetchall_results.pop(0) if self.fetchall_results else []


class SequenceConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class ResultCursor:
    def __init__(self, tournament_row, rowcount=1, fail_on_execute=False):
        self.tournament_row = tournament_row
        self.rowcount = rowcount
        self.fail_on_execute = fail_on_execute
        self.executed = []

    def execute(self, query, params=None):
        if self.fail_on_execute and self.executed:
            raise RuntimeError("database failure")
        self.executed.append((query, params))

    def fetchone(self):
        return self.tournament_row


class RussianCupUiTests(unittest.TestCase):
    def make_app(self):
        from app.routes.admin import admin_bp
        from app.routes.admin_matches import admin_matches_bp

        app = Flask(
            __name__,
            template_folder=str(ROOT / "templates"),
            static_folder=str(ROOT / "static"),
        )
        app.secret_key = "test-secret"
        app.register_blueprint(admin_bp)
        app.register_blueprint(admin_matches_bp)
        return app

    def make_full_page_app(self):
        from app.routes.main import main_bp
        from app.routes.predictions import predictions_bp
        from app.routes.table import table_bp

        app = Flask(
            __name__,
            template_folder=str(ROOT / "templates"),
            static_folder=str(ROOT / "static"),
        )
        app.secret_key = "test-secret"
        app.register_blueprint(main_bp)
        app.register_blueprint(table_bp)
        app.register_blueprint(predictions_bp)
        app.add_url_rule("/logout", "auth.logout", lambda: "")
        app.add_url_rule("/login", "auth.login", lambda: "")
        return app

    def render_day_block(self, match):
        app = self.make_app()
        with app.test_request_context("/"):
            return render_template(
                "partials/home/_day_block.html",
                month_idx=1,
                day_idx=1,
                day_is_open=True,
                day={"type": "future", "label": "12 июля", "count": 1, "matches": [match]},
                has_prediction=False,
                csrf_token="test-csrf",
                get_flag=lambda team: "",
                get_club_logo=lambda team: "",
                to_msk=lambda value: "12.07 19:00 МСК",
            )

    def base_match(self, **overrides):
        match = {
            "id": 10,
            "home_team": "Спартак",
            "away_team": "Зенит",
            "kickoff_time": datetime(2026, 7, 12, 16, 0, tzinfo=timezone.utc),
            "deadline": datetime(2026, 7, 12, 8, 0, tzinfo=timezone.utc),
            "status": "SCHEDULED",
            "league": "rpl",
            "finished": False,
            "deadline_passed": False,
            "pred_home": "",
            "pred_away": "",
            "playoff_stage": "",
            "playoff_stage_css_class": "",
            "event_css_class": "",
            "event_label": "",
            "event_type": "",
            "is_rpl_category": True,
            "is_supercup": False,
            "is_russia_category": False,
            "is_russian_cup": False,
            "tournament_name": "Чемпионат России 🇷🇺",
            "tournament_slug": "",
            "tournament_logo_path": "",
            "tournament_logo_alt": "",
            "tournament_logo_fallback_path": "",
        }
        match.update(overrides)
        return match

    def test_rcup_league_match_gets_russian_cup_class(self):
        html = self.render_day_block(self.base_match(league="rcup", is_russian_cup=True, tournament_name="Кубок России", tournament_slug="rcup"))

        self.assertIn("match-card match-card--russian-cup", html)
        self.assertIn('data-league="rcup"', html)

    def test_closed_rcup_match_renders_closed_status_inside_rcup_branch(self):
        html = self.render_day_block(self.base_match(
            league="rcup",
            is_russian_cup=True,
            tournament_name="Кубок России",
            tournament_slug="rcup",
            deadline_passed=True,
            status="SCHEDULED",
        ))

        self.assertIn('class="status-pill-v2 status-closed"', html)
        self.assertIn("⏰ Закрыто", html)
        self.assertIn("rcup-match-deadline", html)

    def test_regular_match_does_not_get_russian_cup_class(self):
        html = self.render_day_block(self.base_match())

        self.assertNotIn("match-card--russian-cup", html)

    def test_russian_cup_class_appears_by_tournament_name_without_league(self):
        html = self.render_day_block(self.base_match(league="", tournament_name="Кубок России", is_russian_cup=True))

        self.assertIn("match-card match-card--russian-cup", html)

    def test_russian_cup_class_appears_by_rcup_league_only(self):
        html = self.render_day_block(self.base_match(league="rcup", tournament_name="Other", is_russian_cup=False))

        self.assertIn("match-card match-card--russian-cup", html)

    def test_admin_russian_cup_forms_point_to_new_endpoints(self):
        app = self.make_app()
        match = {
            "id": 10,
            "home_team": "Спартак",
            "away_team": "Зенит",
            "match_date_msk": "2026-07-12",
            "match_time_msk": "19:00",
            "deadline_date_msk": "2026-07-12",
            "deadline_time_msk": "11:00",
            "status": "SCHEDULED",
            "home_score": None,
            "away_score": None,
            "stage": "Групповой этап",
            "round_label": "Групповой этап",
            "is_hidden": False,
            "api_match_id": "",
            "league": "rcup",
        }
        context = {
            "russian_cup_tournament": {"id": 1, "name": "Кубок России"},
            "russian_cup_matches": [match],
            "russian_cup_stage_groups": [{
                "stage": "Групповой этап",
                "matches_count": 1,
                "finished_count": 0,
                "dates": [{"date": "2026-07-12", "matches_count": 1, "finished_count": 0, "matches": [match]}],
            }],
            "russian_cup_matches_count": 1,
            "russian_cup_finished_count": 0,
            "russian_cup_statuses": ("SCHEDULED", "TIMED", "LIVE", "FINISHED", "POSTPONED", "CANCELLED"),
            "russian_cup_stages": (("Групповой этап", "Групповой этап"),),
            "russian_cup_stage_values": ["Групповой этап"],
            "current_tournament_name": "Кубок России",
            "current_tournament_slug": "rcup",
            "current_tournament_id": 1,
            "tournaments": [],
            "active_tournaments": [],
            "csrf_token": "test-csrf",
            "admin_match_filters": {"view": "upcoming", "page": 1},
            "admin_match_list": {
                "total": 1,
                "groups": [{"label": "Сегодня", "matches": [{
                    "id": 10, "home_team": "Спартак", "away_team": "Зенит",
                    "match_time_msk": "19:00", "match_date_msk": "2026-07-12",
                    "status": "SCHEDULED", "home_score": None, "away_score": None,
                    "stage": "Групповой этап", "has_result": False,
                    "deadline_time_msk": "11:00",
                }]}],
                "pages": 1, "page": 1, "first": 1, "last": 1,
                "fallback_notice": False, "pending_count": 0,
            },
        }
        with app.test_request_context("/admin/russian-cup"):
            html = render_template("admin_russian_cup.html", **context)

        self.assertIn('action="/admin/russian_cup_add"', html)
        add_form = re.search(r'<form[^>]+class="rc-add-form".*?</form>', html, re.DOTALL).group(0)
        self.assertNotIn('name="stage"', add_form)
        self.assertNotIn('name="status"', add_form)
        self.assertNotIn('name="stage_custom"', add_form)
        self.assertNotIn("Другая стадия", add_form)
        self.assertIn('name="deadline_date"', add_form)
        self.assertIn('name="deadline_time"', add_form)
        self.assertRegex(html, r'href="/admin/russian-cup/matches/10/edit\?return_to=[^"]*admin/russian-cup')
        self.assertIn('>Редактировать матч</a>', html)
        self.assertNotIn('>Изменить</a>', html)
        self.assertIn('aria-label="Действия с матчем"', html)
        self.assertIn('action="/admin/russian_cup_result"', html)
        self.assertIn('action="/admin/russian_cup_visibility"', html)
        self.assertIn('action="/admin/russian_cup_delete"', html)
        self.assertIn('action="/admin/russian_cup_recalc"', html)
        self.assertIn("clubs/Fonbet_Russian_Cup.png", html)
        self.assertNotIn("Поиск по команде или ID", html)
        self.assertNotIn("Все статусы", html)
        self.assertNotIn('name="q"', html)
        self.assertNotIn('name="status"', html)
        self.assertNotIn('name="period"', html)
        self.assertNotIn("admin.admin_matches", html)
        self.assertNotIn('placeholder="Не поддерживается текущей схемой БД" disabled', html)
        self.assertNotIn('placeholder="Нет колонки БД"', html)

    def test_import_preview_uses_compact_ready_and_expanded_review_markup(self):
        app = self.make_app()
        context = {
            "russian_cup_tournament": {"id": 1, "name": "Кубок России"},
            "russian_cup_import_draft": {"matches": [
                {"status": "ready", "home_team": "Спартак", "away_team": "Зенит", "date": "2026-10-10", "time": "18:00", "raw_home_team": "Спартак", "raw_away_team": "Зенит", "reasons": []},
                {"status": "needs_review", "home_team": "", "away_team": "Зенит", "date": "2026-10-10", "time": "", "raw_home_team": "Неизвестно", "raw_away_team": "Зенит", "reasons": ["Время не распознано"]},
            ]},
        }
        with app.test_request_context("/admin/russian-cup"):
            html = render_template("admin_russian_cup.html", **context)
        self.assertIn("data-import-compact", html)
        self.assertIn("data-import-editor", html)
        self.assertIn("data-import-toggle", html)
        self.assertIn("✓ Готово", html)
        self.assertIn("Требуется проверить: Время не распознано", html)
        self.assertNotIn("Статус: ready", html)

    def test_russian_cup_card_actions_have_only_result_form_and_menu(self):
        app = self.make_app()
        context = {
            "russian_cup_tournament": {"id": 1, "name": "Кубок России"},
            "russian_cup_matches_count": 1,
            "russian_cup_finished_count": 0,
            "russian_cup_statuses": ("SCHEDULED",),
            "russian_cup_stages": (("Групповой этап", "Групповой этап"),),
            "csrf_token": "test-csrf",
            "admin_match_filters": {"view": "upcoming", "page": 2},
            "admin_match_list": {
                "total": 1,
                "groups": [{"label": "Сегодня", "matches": [{
                    "id": 10, "home_team": "Спартак", "away_team": "Зенит",
                    "match_time_msk": "19:00", "match_date_msk": "2026-07-12",
                    "status": "SCHEDULED", "home_score": None, "away_score": None,
                    "stage": "Групповой этап", "has_result": False,
                    "deadline_time_msk": "11:00",
                }]}],
                "pages": 1, "page": 2, "first": 1, "last": 1,
                "fallback_notice": False, "pending_count": 0,
            },
        }
        with app.test_request_context("/admin/russian-cup?view=upcoming&page=2&q=Спартак"):
            html = render_template("admin_russian_cup.html", **context)

        actions = re.search(r'<div class="rc-card-actions">(.*?)</div>\s*</article>', html, re.DOTALL)
        self.assertIsNotNone(actions)
        actions_html = actions.group(1)
        self.assertEqual(actions_html.count('class="rc-quick-result"'), 1)
        self.assertEqual(actions_html.count('class="rc-more"'), 1)
        self.assertNotIn('>Изменить</a>', actions_html)
        self.assertIn('return_to=/admin/russian-cup?view%3Dupcoming%26page%3D2', actions_html)

    def test_russian_cup_action_css_stays_scoped_and_fits_mobile(self):
        root = Path(__file__).resolve().parents[1]
        css = (root / "static" / "css" / "tournaments" / "russian-cup.css").read_text(encoding="utf-8")
        self.assertIn(".rc-card-actions { display: grid; grid-template-columns: minmax(0, 1fr) 44px;", css)
        self.assertIn(".rc-more > summary { display: inline-flex", css)
        self.assertIn("width: 44px; height: 44px", css)
        self.assertIn(".rc-quick-result { grid-template-columns: 34px 10px 34px minmax(0, 1fr);", css)
        self.assertIn("@media (max-width: 430px)", css)
        self.assertNotIn("grid-template-columns: minmax(0, 1fr) auto auto", css)

    def test_russian_cup_service_filters_by_tournament_and_league(self):
        from app.services.russian_cup_admin_service import prepare_russian_cup_admin_data

        cursor = RecordingCursor(tournament_row=(5, "Кубок России", 1, "2026-07-01", "2027-05-31"), match_rows=[])

        data = prepare_russian_cup_admin_data(cursor)

        query = "\n".join(q for q, _ in cursor.executed)
        self.assertIn("WHERE tournament_id = %s", query)
        self.assertIn("AND league = 'rcup'", query)
        self.assertEqual(data["russian_cup_matches"], [])

    def test_russian_cup_css_is_connected_once(self):
        app = self.make_app()
        with app.test_request_context("/"):
            html = render_template(
                "base.html",
                current_tournament_name="Кубок России",
                current_tournament_slug="rcup",
                current_tournament_id=1,
                tournaments=[],
                active_tournaments=[],
                csrf_token="test-csrf",
            )

        self.assertEqual(html.count("css/tournaments/russian-cup.css"), 1)

    def test_home_table_and_my_predictions_render_rcup_body_and_single_css(self):
        app = self.make_full_page_app()
        tournaments = [{"id": 5, "name": "Кубок России", "is_active": 1, "start_date": "—"}]

        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user_id"] = 1

            home_cursor = SequenceCursor(fetchall_results=[[]])
            with (
                patch("app.routes.main.get_db", return_value=SequenceConnection(home_cursor)),
                patch("app.routes.main.close_db"),
                patch("app.routes.main.get_all_tournaments", return_value=tournaments),
                patch("app.routes.main.get_selected_tournament_id", return_value=5),
            ):
                home = client.get("/?tid=5")

            table_cursor = SequenceCursor(
                fetchone_results=[("Кубок России", 1, None)],
                fetchall_results=[[(5, "Кубок России", 1, None)]],
            )
            with (
                patch("app.routes.table.get_db", return_value=SequenceConnection(table_cursor)),
                patch("app.routes.table.close_db"),
                patch("app.routes.table.get_selected_tournament_id", return_value=5),
                patch("app.routes.table.get_tournament_status", return_value="current"),
                patch("app.routes.table.get_tournament_ranking", return_value=[]),
                patch("app.routes.table.get_tournament_top_scorers", return_value=[]),
            ):
                table = client.get("/table?tid=5")

            predictions_cursor = SequenceCursor(fetchall_results=[[], [], []])
            with (
                patch("app.routes.predictions.get_db", return_value=SequenceConnection(predictions_cursor)),
                patch("app.routes.predictions.close_db"),
                patch("app.routes.predictions.get_all_tournaments", return_value=tournaments),
                patch("app.routes.predictions.get_selected_tournament_id", return_value=5),
            ):
                my_predictions = client.get("/my-predictions?tid=5")

        for response in (home, table, my_predictions):
            html = response.get_data(as_text=True)
            self.assertEqual(response.status_code, 200)
            self.assertIn('class="tournament-rcup', html)
            self.assertEqual(html.count("css/tournaments/russian-cup.css"), 1)

    def test_admin_russian_cup_post_endpoints_are_scoped_and_redirect(self):
        app = self.make_app()
        endpoints = [
            (
                "/admin/russian_cup_add",
                {
                    "home_team": "Спартак",
                    "away_team": "Зенит",
                    "match_date": "2026-07-12",
                    "match_time": "19:00",
                },
                [(5, "Кубок России", 1, "2026-07-01", "2027-05-31"), None],
                [],
            ),
            (
                "/admin/russian_cup_edit",
                {
                    "match_id": "10",
                    "home_team": "Спартак",
                    "away_team": "Зенит",
                    "match_date": "2026-07-12",
                    "match_time": "19:00",
                    "status": "SCHEDULED",
                    "stage": "Групповой этап",
                },
                [(5, "Кубок России", 1, "2026-07-01", "2027-05-31"), (10, "SCHEDULED")],
                [],
            ),
            (
                "/admin/russian_cup_visibility",
                {"match_id": "10", "visibility_action": "hide"},
                [(5, "Кубок России", 1, "2026-07-01", "2027-05-31")],
                [],
            ),
            (
                "/admin/russian_cup_recalc",
                {},
                [(5, "Кубок России", 1, "2026-07-01", "2027-05-31")],
                [[(10,)]],
            ),
            (
                "/admin/russian_cup_delete",
                {"match_id": "10"},
                [(5, "Кубок России", 1, "2026-07-01", "2027-05-31")],
                [],
            ),
        ]

        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user_id"] = 1

            for url, payload, fetchone_results, fetchall_results in endpoints:
                user_conn = SequenceConnection(SequenceCursor(fetchone_results=[(1, 0)]))
                route_cursor = SequenceCursor(fetchone_results=fetchone_results, fetchall_results=fetchall_results)
                route_conn = SequenceConnection(route_cursor)

                with (
                    patch("app.routes.admin_common.get_db", return_value=user_conn),
                    patch("app.routes.admin_common.close_db"),
                    patch("app.routes.admin_matches.get_db", return_value=route_conn),
                    patch("app.routes.admin_matches.close_db"),
                    patch("app.routes.admin_matches.recalc_match_points"),
                ):
                    response = client.post(url, data=payload)

                self.assertEqual(response.status_code, 302, url)
                self.assertEqual(response.headers["Location"], "/admin/russian-cup")
                sql = "\n".join(query for query, _ in route_cursor.executed)
                self.assertIn("Кубок России", str(route_cursor.executed))
                if url.endswith("add"):
                    insert_params = route_cursor.executed[-1][1]
                    self.assertEqual(insert_params[5], "rcup")
                    self.assertEqual(insert_params[8], "russian_cup")
                    self.assertEqual(route_cursor.executed[-1][1][4], "SCHEDULED")
                    self.assertEqual(route_cursor.executed[-1][1][7], "")
                elif url.endswith("recalc"):
                    self.assertIn("AND league = 'rcup'", sql)
                    self.assertIn("WHERE tournament_id = %s", sql)
                else:
                    self.assertIn("AND tournament_id = %s", sql)
                    self.assertIn("AND league = 'rcup'", sql)




class RussianCupResultTests(unittest.TestCase):
    def setUp(self):
        self.app = RussianCupUiTests().make_app()
        self.tournament = (5, "Кубок России", 1, "2026-07-01", "2027-05-31")

    def post_result(self, cursor, payload):
        user_conn = SequenceConnection(SequenceCursor(fetchone_results=[(1, 0)]))
        route_conn = SequenceConnection(cursor)
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user_id"] = 1
            with (
                patch("app.routes.admin_common.get_db", return_value=user_conn),
                patch("app.routes.admin_common.close_db"),
                patch("app.routes.admin_matches.get_db", return_value=route_conn),
                patch("app.routes.admin_matches.close_db"),
                patch("app.routes.admin_matches.recalc_match_points") as recalc,
            ):
                response = client.post("/admin/russian_cup_result", data=payload)
        return response, route_conn, recalc

    def test_add_result_updates_only_rcup_and_recalculates(self):
        cursor = ResultCursor(self.tournament)
        response, conn, recalc = self.post_result(
            cursor,
            {"match_id": "10", "home_score": "2", "away_score": "1"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(conn.commits, 1)
        self.assertEqual(conn.rollbacks, 0)
        recalc.assert_called_once_with(10, tournament_id=5, conn=conn, cur=cursor)
        sql = "\n".join(query for query, _ in cursor.executed)
        self.assertIn("AND tournament_id = %s", sql)
        self.assertIn("AND league = 'rcup'", sql)

    def test_delete_result_resets_prediction_points(self):
        cursor = ResultCursor(self.tournament)
        response, conn, recalc = self.post_result(
            cursor,
            {"match_id": "10", "delete_result": "1"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(conn.commits, 1)
        recalc.assert_not_called()
        self.assertIn("UPDATE predictions", cursor.executed[2][0])
        self.assertEqual(cursor.executed[2][1], (10, 5))

    def test_missing_or_foreign_match_rolls_back_without_recalculation(self):
        cursor = ResultCursor(self.tournament, rowcount=0)
        response, conn, recalc = self.post_result(
            cursor,
            {"match_id": "999", "home_score": "2", "away_score": "1"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(conn.commits, 0)
        self.assertEqual(conn.rollbacks, 1)
        recalc.assert_not_called()
        self.assertEqual(len(cursor.executed), 2)
        self.assertIn("AND league = 'rcup'", cursor.executed[1][0])

    def test_database_error_rolls_back(self):
        cursor = ResultCursor(self.tournament, fail_on_execute=True)
        response, conn, recalc = self.post_result(
            cursor,
            {"match_id": "10", "home_score": "2", "away_score": "1"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(conn.commits, 0)
        self.assertEqual(conn.rollbacks, 1)
        recalc.assert_not_called()


class RussianCupDeadlineTests(unittest.TestCase):

    def setUp(self):
        from app.routes.admin_matches import build_russian_cup_deadline_utc
        self.build = build_russian_cup_deadline_utc

    def _as_utc(self, date_str, time_str):
        from zoneinfo import ZoneInfo
        msk = ZoneInfo("Europe/Moscow")
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        return dt.replace(tzinfo=msk).astimezone(timezone.utc)

    def test_match_1930_deadline_becomes_1100_same_day(self):
        kickoff, deadline = self.build("2026-08-18", "19:30", "", "")
        expected_kickoff = self._as_utc("2026-08-18", "19:30")
        expected_deadline = self._as_utc("2026-08-18", "11:00")
        self.assertEqual(kickoff, expected_kickoff)
        self.assertEqual(deadline, expected_deadline)

    def test_match_1200_deadline_becomes_1100_same_day(self):
        kickoff, deadline = self.build("2026-08-18", "12:00", "", "")
        expected_kickoff = self._as_utc("2026-08-18", "12:00")
        expected_deadline = self._as_utc("2026-08-18", "11:00")
        self.assertEqual(kickoff, expected_kickoff)
        self.assertEqual(deadline, expected_deadline)

    def test_match_exactly_1100_rejects_auto_deadline(self):
        with self.assertRaises(ValueError) as ctx:
            self.build("2026-08-18", "11:00", "", "")
        self.assertIn("Матч начинается раньше стандартного дедлайна 11:00", str(ctx.exception))

    def test_match_before_1100_rejects_auto_deadline(self):
        with self.assertRaises(ValueError) as ctx:
            self.build("2026-08-18", "10:30", "", "")
        self.assertIn("Матч начинается раньше стандартного дедлайна 11:00", str(ctx.exception))

    def test_manual_deadline_is_not_replaced(self):
        kickoff, deadline = self.build("2026-08-18", "19:30", "2026-08-18", "09:00")
        expected_kickoff = self._as_utc("2026-08-18", "19:30")
        expected_deadline = self._as_utc("2026-08-18", "09:00")
        self.assertEqual(kickoff, expected_kickoff)
        self.assertEqual(deadline, expected_deadline)

    def test_edit_existing_match_preserves_deadline(self):
        kickoff, deadline = self.build("2026-08-18", "19:30", "2026-08-17", "15:00")
        expected_kickoff = self._as_utc("2026-08-18", "19:30")
        expected_deadline = self._as_utc("2026-08-17", "15:00")
        self.assertEqual(kickoff, expected_kickoff)
        self.assertEqual(deadline, expected_deadline)

    def test_date_change_gets_new_date_and_1100(self):
        kickoff, deadline = self.build("2026-08-20", "19:30", "", "")
        expected_kickoff = self._as_utc("2026-08-20", "19:30")
        expected_deadline = self._as_utc("2026-08-20", "11:00")
        self.assertEqual(kickoff, expected_kickoff)
        self.assertEqual(deadline, expected_deadline)

    def test_time_change_only_still_1100(self):
        kickoff, deadline = self.build("2026-08-18", "20:00", "", "")
        expected_kickoff = self._as_utc("2026-08-18", "20:00")
        expected_deadline = self._as_utc("2026-08-18", "11:00")
        self.assertEqual(kickoff, expected_kickoff)
        self.assertEqual(deadline, expected_deadline)

    def test_timezone_is_europe_moscow(self):
        from zoneinfo import ZoneInfo
        msk = ZoneInfo("Europe/Moscow")
        kickoff, deadline = self.build("2026-08-18", "19:30", "", "")
        kickoff_msk = kickoff.astimezone(msk)
        deadline_msk = deadline.astimezone(msk)
        self.assertEqual(kickoff_msk.strftime("%H:%M"), "19:30")
        self.assertEqual(deadline_msk.strftime("%H:%M"), "11:00")

    def test_other_tournaments_deadline_unchanged(self):
        from app.routes.admin_matches import build_manual_deadline_utc
        kickoff, deadline = build_manual_deadline_utc("2026-08-18", "19:30", "", "")
        expected_kickoff = self._as_utc("2026-08-18", "19:30")
        expected_deadline = self._as_utc("2026-08-18", "11:00")
        self.assertEqual(kickoff, expected_kickoff)
        self.assertEqual(deadline, expected_deadline)

    def test_shared_deadline_helper_supports_rpl_and_cup_early_match_contract(self):
        from app.routes.admin_matches import build_manual_deadline_utc
        with self.assertRaises(ValueError):
            build_manual_deadline_utc("2026-08-18", "11:00", "", "", reject_early_auto=True)
        _, deadline = build_manual_deadline_utc("2026-08-18", "19:30", "", "", reject_early_auto=True)
        self.assertEqual(deadline, self._as_utc("2026-08-18", "11:00"))


if __name__ == "__main__":
    unittest.main()
