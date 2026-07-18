import unittest
import re
from datetime import datetime, timezone
from types import SimpleNamespace
from pathlib import Path

from app.services.admin_view_service import (
    parse_admin_match_filters,
    parse_russian_cup_match_filters,
    parse_rpl_match_filters,
    prepare_rpl_match_list,
    prepare_russian_cup_match_list,
    prepare_wc_playoff_page_data,
)
from app.services.rpl_admin_service import prepare_rpl_admin_page_data
from app.services.russian_cup_admin_service import prepare_russian_cup_admin_page_data
from app.utils import format_admin_match_date


class Cursor:
    def __init__(self, total, rows):
        self.total = total
        self.rows = rows
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchone(self):
        return (self.total,)

    def fetchall(self):
        return self.rows


class SequenceCursor(Cursor):
    def __init__(self, fetchone_values=None, rows=None):
        super().__init__(0, rows or [])
        self.fetchone_values = list(fetchone_values or [])

    def fetchone(self):
        return self.fetchone_values.pop(0) if self.fetchone_values else None


class AdminMatchListTests(unittest.TestCase):
    def test_rpl_defaults_to_upcoming_with_five_matches(self):
        args = SimpleNamespace(get=lambda key, default=None, type=None: {}.get(key, default))
        filters = parse_rpl_match_filters(args)
        self.assertEqual(filters["view"], "upcoming")
        self.assertEqual(filters["per_page"], 5)

    def test_rpl_list_uses_server_side_views_and_pagination(self):
        args = SimpleNamespace(get=lambda key, default=None, type=None: {"view": "pending_result"}.get(key, default))
        cursor = Cursor(
            21,
            [(21, "Спартак", "Зенит", datetime(2026, 7, 18, 16, 0, tzinfo=timezone.utc), None,
              "LIVE", None, None, "Тур 1")],
        )
        result = prepare_rpl_match_list(cursor, 7, parse_rpl_match_filters(args))
        self.assertEqual(result["per_page"], 5)
        self.assertEqual(cursor.executed[-1][1][-2:], (5, 0))
        self.assertIn("COALESCE(m.status, '') <> 'FINISHED'", cursor.executed[0][0])
        self.assertIn("m.home_score IS NULL", cursor.executed[0][0])
        self.assertIn("m.tournament_id = %s", cursor.executed[0][0])
        self.assertIn("m.league = 'rpl'", cursor.executed[0][0])
        self.assertIn("LIMIT %s OFFSET %s", cursor.executed[-1][0])

    def test_rpl_upcoming_fallback_keeps_future_rpl_scope(self):
        args = SimpleNamespace(get=lambda key, default=None, type=None: {"view": "upcoming"}.get(key, default))
        cursor = SequenceCursor(
            fetchone_values=[(0,), (0,), (1,)],
            rows=[(10, "Спартак", "Зенит", datetime(2027, 8, 1, 16, 0, tzinfo=timezone.utc), None,
                   "SCHEDULED", None, None, "Тур 1")],
        )
        result = prepare_rpl_match_list(cursor, 7, parse_rpl_match_filters(args))
        self.assertTrue(result["fallback_notice"])
        self.assertIn("m.kickoff_time > %s", cursor.executed[-1][0])
        self.assertIn("m.league = 'rpl'", cursor.executed[-1][0])
        self.assertIn("LIMIT %s OFFSET %s", cursor.executed[-1][0])
        self.assertEqual(cursor.executed[-1][1][-2:], (5, 0))

    def test_rpl_upcoming_has_separate_pending_preview_count_and_limit(self):
        args = SimpleNamespace(get=lambda key, default=None, type=None: {"view": "upcoming"}.get(key, default))
        cursor = Cursor(
            2,
            [(10, "Спартак", "Зенит", datetime(2026, 7, 18, 16, 0, tzinfo=timezone.utc), None,
              "SCHEDULED", None, None, "Тур 1")],
        )
        result = prepare_rpl_match_list(cursor, 7, parse_rpl_match_filters(args))
        self.assertEqual(result["pending_preview_total"], 2)
        self.assertEqual(len(result["pending_preview"]), 1)
        self.assertTrue(result["pending_preview"][0]["pending_result"])
        self.assertGreaterEqual(len(cursor.executed), 4)
        pending_sql, pending_params = cursor.executed[2]
        future_sql = cursor.executed[-1][0]
        self.assertIn("m.kickoff_time <= %s", pending_sql)
        self.assertIn("m.home_score IS NULL", pending_sql)
        self.assertIn("m.away_score IS NULL", pending_sql)
        self.assertIn("m.tournament_id = %s", pending_sql)
        self.assertIn("m.league = 'rpl'", pending_sql)
        self.assertIn("LIMIT %s", pending_sql)
        self.assertEqual(pending_params[-1], 5)
        self.assertIn("m.kickoff_time > %s", future_sql)

    def test_rpl_pending_preview_only_loads_on_first_upcoming_page(self):
        args = SimpleNamespace(get=lambda key, default=None, type=None: {"view": "upcoming", "page": 2}.get(key, default))
        cursor = Cursor(6, [])
        result = prepare_rpl_match_list(cursor, 7, parse_rpl_match_filters(args))
        self.assertEqual(result["page"], 2)
        self.assertEqual(result["pending_preview"], [])
        self.assertEqual(len(cursor.executed), 3)
        self.assertIn("LIMIT %s OFFSET %s", cursor.executed[-1][0])

    def test_russian_cup_defaults_to_upcoming_with_five_matches(self):
        args = SimpleNamespace(get=lambda key, default=None, type=None: {}.get(key, default))
        filters = parse_russian_cup_match_filters(args)
        self.assertEqual(filters["view"], "upcoming")
        self.assertEqual(filters["per_page"], 5)

    def test_russian_cup_view_limits_and_pending_condition_are_server_side(self):
        args = SimpleNamespace(get=lambda key, default=None, type=None: {"view": "pending_result"}.get(key, default))
        cursor = Cursor(
            21,
            [(21, "Спартак", "Зенит", datetime(2026, 7, 18, 16, 0, tzinfo=timezone.utc), None,
              "LIVE", None, None, "Групповой этап")],
        )
        result = prepare_russian_cup_match_list(cursor, 7, parse_russian_cup_match_filters(args))
        self.assertEqual(result["per_page"], 5)
        self.assertEqual(cursor.executed[-1][1][-2:], (5, 0))
        self.assertIn("COALESCE(m.status, '') <> 'FINISHED'", cursor.executed[0][0])
        self.assertIn("m.home_score IS NULL", cursor.executed[0][0])
        self.assertIn("m.tournament_id = %s", cursor.executed[0][0])
        self.assertIn("LIMIT %s OFFSET %s", cursor.executed[-1][0])

    def test_russian_cup_template_has_no_full_edit_form_in_match_list(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "templates" / "admin_russian_cup.html").read_text(encoding="utf-8")
        self.assertIn("admin_russian_cup_edit_form", html)
        self.assertIn("admin_russian_cup_result", html)
        self.assertNotIn('action="{{ url_for(\'admin_matches.admin_russian_cup_edit\') }}"', html)
        self.assertNotIn("disabled", html)
        self.assertIn("rc-technical-operations", html)

    def test_rpl_and_russian_cup_metadata_do_not_load_match_rows(self):
        rpl_cursor = SequenceCursor([(2, "Чемпионат России 🇷🇺", 1, None)])
        rpl_data = prepare_rpl_admin_page_data(rpl_cursor)
        self.assertEqual(rpl_data["rpl_tournament"]["id"], 2)
        self.assertEqual(len(rpl_cursor.executed), 2)

        cup_cursor = SequenceCursor([(3, "Кубок России", 1, None), (4, 1)])
        cup_data = prepare_russian_cup_admin_page_data(cup_cursor)
        self.assertEqual(cup_data["russian_cup_tournament"]["id"], 3)
        self.assertEqual(len(cup_cursor.executed), 2)
        self.assertNotIn("SELECT id,", cup_cursor.executed[1][0])

    def test_wc_page_has_only_count_and_paginated_select(self):
        args = SimpleNamespace(get=lambda key, default=None, type=None: {"page": 2}.get(key, default))
        cursor = Cursor(
            31,
            [(31, "A", "B", datetime(2026, 7, 1, 16, 0, tzinfo=timezone.utc), None,
              "SCHEDULED", None, None, "wc2026", 0, 0, 0, "final", None,
              "api-31", None, 0)],
        )
        data = prepare_wc_playoff_page_data(cursor, parse_admin_match_filters(args))
        self.assertEqual(data["wc_playoff_list"]["page"], 2)
        self.assertEqual(len(cursor.executed), 2)
        self.assertIn("COUNT(*)", cursor.executed[0][0])
        self.assertIn("LIMIT %s OFFSET %s", cursor.executed[1][0])
        self.assertEqual(cursor.executed[1][1][-1], 30)
        self.assertEqual(cursor.executed[1][1][-2], 30)

    def test_admin_match_templates_do_not_use_grouping_accordions(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("admin_russia_2027.html", "admin_russian_cup.html"):
            html = (root / "templates" / name).read_text(encoding="utf-8")
            html = re.sub(r"<style.*?</style>", "", html, flags=re.DOTALL)
            for marker in ("toggleMonth", "toggleDay", "month-content", "day-content", "league-content", "rc-stage-group", "rc-date-group", "rpl-match-group", "rpl-match-section"):
                self.assertNotIn(marker, html, f"{marker} remains in {name}")

    def test_common_admin_css_has_mobile_overflow_protection(self):
        root = Path(__file__).resolve().parents[1]
        css = (root / "static" / "css" / "admin-match-list.css").read_text(encoding="utf-8")
        self.assertIn("max-width:430px", css)
        self.assertIn("overflow-x:auto", css)

    def test_filters_parse_supported_controls_and_default_page_size(self):
        args = SimpleNamespace(
            get=lambda key, default=None, type=None: {
                "view": "attention",
                "q": "Зенит",
                "tournament_id": 5,
                "status": "scheduled",
                "period": "7",
                "page": 2,
            }.get(key, default)
        )
        filters = parse_admin_match_filters(args)
        self.assertEqual(filters["view"], "attention")
        self.assertEqual(filters["q"], "Зенит")
        self.assertEqual(filters["status"], "SCHEDULED")
        self.assertEqual(filters["page"], 2)
        self.assertEqual(filters["per_page"], 30)

    def test_rpl_and_russian_cup_use_five_for_every_view(self):
        for parser in (parse_rpl_match_filters, parse_russian_cup_match_filters):
            for view in ("upcoming", "pending_result", "finished", "all"):
                args = SimpleNamespace(get=lambda key, default=None, type=None, view=view: {"view": view}.get(key, default))
                self.assertEqual(parser(args)["per_page"], 5)

    def test_tournament_parser_ignores_legacy_filter_parameters(self):
        args = SimpleNamespace(get=lambda key, default=None, type=None: {
            "view": "finished", "page": 2, "q": "Зенит", "status": "SCHEDULED", "period": "7",
        }.get(key, default))
        for parser in (parse_rpl_match_filters, parse_russian_cup_match_filters):
            filters = parser(args)
            self.assertEqual(filters, {"view": "finished", "page": 2, "per_page": 5})

    def test_tournament_sql_ignores_legacy_filter_parameters(self):
        args = SimpleNamespace(get=lambda key, default=None, type=None: {
            "view": "all", "q": "Зенит", "status": "SCHEDULED", "period": "7",
        }.get(key, default))
        cursor = Cursor(6, [])
        prepare_rpl_match_list(cursor, 7, parse_rpl_match_filters(args))
        sql = "\n".join(query for query, _ in cursor.executed)
        self.assertNotIn("ILIKE", sql)
        self.assertNotIn("m.status = %s", sql)

    def test_rpl_and_russian_cup_queries_use_limit_five_for_every_view(self):
        for prepare, parser in ((prepare_rpl_match_list, parse_rpl_match_filters), (prepare_russian_cup_match_list, parse_russian_cup_match_filters)):
            for view in ("upcoming", "pending_result", "finished", "all"):
                args = SimpleNamespace(get=lambda key, default=None, type=None, view=view: {"view": view}.get(key, default))
                cursor = Cursor(6, [])
                prepare(cursor, 7, parser(args))
                self.assertEqual(cursor.executed[-1][1][-2:], (5, 0))

    def test_tournament_second_page_uses_offset_five(self):
        args = SimpleNamespace(get=lambda key, default=None, type=None: {
            "view": "finished", "q": "Зенит", "status": "FINISHED", "period": "all", "page": 2,
        }.get(key, default))
        for prepare, parser in ((prepare_rpl_match_list, parse_rpl_match_filters), (prepare_russian_cup_match_list, parse_russian_cup_match_filters)):
            cursor = Cursor(6, [])
            result = prepare(cursor, 7, parser(args))
            self.assertEqual(result["pages"], 2)
            self.assertEqual(result["page"], 2)
            self.assertEqual(cursor.executed[-1][1][-2:], (5, 5))

    def test_admin_date_formatter_uses_moscow_dd_mm_yyyy(self):
        value = datetime(2026, 7, 25, 18, 30, tzinfo=timezone.utc)
        self.assertEqual(format_admin_match_date(value), "25.07.2026")

    def test_rpl_and_russian_cup_cards_use_display_date_not_iso(self):
        root = Path(__file__).resolve().parents[1]
        rpl = (root / "templates" / "admin_russia_2027.html").read_text(encoding="utf-8")
        rcup = (root / "templates" / "admin_russian_cup.html").read_text(encoding="utf-8")
        self.assertIn("m.date_label", rpl)
        self.assertIn("m.date_label", rcup)
        self.assertNotIn("{{ m.match_date_msk }} · Чемпионат России", rpl)
        self.assertNotIn("{{ m.match_date_msk }}{% if m.stage %}", rcup)

    def test_rpl_pending_preview_markup_replaces_old_alert(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "templates" / "admin_russia_2027.html").read_text(encoding="utf-8")
        css = (root / "static" / "css" / "tournaments" / "rpl-admin.css").read_text(encoding="utf-8")
        self.assertIn("pending_preview", html)
        self.assertIn("Ожидают результата", html)
        self.assertIn("ОЖИДАЕТ РЕЗУЛЬТАТА", html)
        self.assertIn("rpl-match-card--pending", html)
        self.assertIn("render_rpl_card(m, admin_return_to, true)", html)
        self.assertIn("admin_match_filters.view == 'pending_result'", html)
        self.assertIn("Все ожидающие результата", html)
        self.assertNotIn("Открыть", html)
        self.assertNotIn("rpl-pending-note", html)
        self.assertNotIn("rpl-pending-note", css)
        self.assertNotIn("rgba(255,183,77", css)
        self.assertIn("#D52B1E", css)
        self.assertIn("rgba(4,18,53,0.94)", css)
        self.assertIn("rgba(0,57,166,0.78)", css)
        self.assertIn("rgba(255,255,255,0.98)", css)
        self.assertIn("rgba(220,234,255,0.72)", css)
        self.assertIn("linear-gradient(135deg, #D52B1E, #b51f16)", css)

    def test_general_matches_page_and_navigation_are_removed(self):
        root = Path(__file__).resolve().parents[1]
        self.assertFalse((root / "templates" / "admin_matches.html").exists())
        for name in ("admin.html", "admin_russia_2027.html", "admin_russian_cup.html", "admin_wc_playoff.html", "admin_users.html", "admin_tournaments.html"):
            html = (root / "templates" / name).read_text(encoding="utf-8")
            self.assertNotIn("admin.admin_matches", html, name)
        for name in ("admin_russia_2027.html", "admin_russian_cup.html"):
            html = (root / "templates" / name).read_text(encoding="utf-8")
            self.assertNotIn("request.args.to_dict()", html)


if __name__ == "__main__":
    unittest.main()
