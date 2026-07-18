import unittest
import re
from datetime import datetime, timezone
from types import SimpleNamespace
from pathlib import Path

from app.services.admin_view_service import (
    prepare_admin_matches_page_data,
    parse_admin_match_filters,
    parse_russian_cup_match_filters,
    prepare_admin_match_list,
    prepare_russian_cup_match_list,
    prepare_wc_playoff_page_data,
)
from app.services.rpl_admin_service import prepare_rpl_admin_page_data
from app.services.russian_cup_admin_service import prepare_russian_cup_admin_page_data


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
    def test_russian_cup_defaults_to_upcoming_with_fifteen_matches(self):
        args = SimpleNamespace(get=lambda key, default=None, type=None: {}.get(key, default))
        filters = parse_russian_cup_match_filters(args)
        self.assertEqual(filters["view"], "upcoming")
        self.assertEqual(filters["period"], "30")
        self.assertEqual(filters["per_page"], 15)

    def test_russian_cup_view_limits_and_pending_condition_are_server_side(self):
        args = SimpleNamespace(get=lambda key, default=None, type=None: {"view": "pending_result"}.get(key, default))
        cursor = Cursor(
            21,
            [(21, "Спартак", "Зенит", datetime(2026, 7, 18, 16, 0, tzinfo=timezone.utc), None,
              "LIVE", None, None, "Групповой этап")],
        )
        result = prepare_russian_cup_match_list(cursor, 7, parse_russian_cup_match_filters(args))
        self.assertEqual(result["per_page"], 20)
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

    def test_main_page_metadata_does_not_select_matches(self):
        cursor = Cursor(0, [(1, "Турнир", 1, None)])
        data = prepare_admin_matches_page_data(cursor)
        self.assertEqual(data["tournaments"][0]["id"], 1)
        self.assertEqual(len(cursor.executed), 1)
        self.assertNotIn("FROM matches", cursor.executed[0][0].upper())

    def test_rpl_and_russian_cup_metadata_do_not_load_match_rows(self):
        rpl_cursor = SequenceCursor([(2, "Чемпионат России 🇷🇺", 1, None)])
        rpl_data = prepare_rpl_admin_page_data(rpl_cursor)
        self.assertEqual(rpl_data["rpl_tournament"]["id"], 2)
        self.assertEqual(len(rpl_cursor.executed), 1)

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
        for name in ("admin_matches.html", "admin_russia_2027.html", "admin_russian_cup.html"):
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

    def test_query_contains_server_side_search_filters_and_pagination(self):
        args = SimpleNamespace(get=lambda key, default=None, type=None: {
            "view": "upcoming", "q": "10", "status": "SCHEDULED", "period": "30", "page": 1,
        }.get(key, default))
        cursor = Cursor(
            31,
            [(10, "Спартак", "Зенит", datetime(2026, 7, 18, 16, 30, tzinfo=timezone.utc),
              datetime(2026, 7, 18, 8, 0, tzinfo=timezone.utc), "SCHEDULED", None, None,
              "rpl", 5, "Чемпионат России", None, None, None)],
        )
        result = prepare_admin_match_list(cursor, parse_admin_match_filters(args))
        self.assertEqual(result["per_page"], 30)
        self.assertEqual(result["pages"], 2)
        self.assertEqual(result["matches"][0]["id"], 10)
        sql = "\n".join(query for query, _ in cursor.executed)
        self.assertIn("ILIKE", sql)
        self.assertIn("LIMIT %s OFFSET %s", sql)
        self.assertIn("m.status = %s", sql)

    def test_second_page_uses_offset_and_date_groups_are_static_data(self):
        args = SimpleNamespace(get=lambda key, default=None, type=None: {"page": 2}.get(key, default))
        cursor = Cursor(
            31,
            [(31, "А", "Б", datetime(2026, 7, 19, 16, 30, tzinfo=timezone.utc), None,
              "SCHEDULED", None, None, "rcup", 7, "Кубок России", "Финал", None, None)],
        )
        result = prepare_admin_match_list(cursor, parse_admin_match_filters(args), league="rcup")
        self.assertEqual(result["page"], 2)
        self.assertEqual(result["first"], 31)
        self.assertEqual(result["groups"][0]["matches"][0]["id"], 31)
        self.assertIn("m.league = %s", cursor.executed[0][0])

    def test_page_bounds_are_clamped_without_extra_match_load(self):
        for requested, expected_page, expected_offset in ((0, 1, 0), (999, 2, 30)):
            args = SimpleNamespace(get=lambda key, default=None, type=None, value=requested: {"page": value}.get(key, default))
            cursor = Cursor(31, [])
            result = prepare_admin_match_list(cursor, parse_admin_match_filters(args))
            self.assertEqual(result["page"], expected_page)
            self.assertEqual(cursor.executed[1][1][-1], expected_offset)


if __name__ == "__main__":
    unittest.main()
