import unittest
import unicodedata
from pathlib import Path
from unittest.mock import patch

from flask import Flask, session

from app.routes.admin_actions import (
    ALLOWED_TITLES,
    handle_award_title,
    handle_remove_title,
    handle_replace_title,
)
from app.routes.admin_common import admin_required


ROOT = Path(__file__).resolve().parents[1]


class TitleCursor:
    def __init__(self, is_admin=0):
        self.is_admin = is_admin
        self.executed = []
        self.rowcount = 0

    def execute(self, query, params=None):
        self.executed.append((query, params))
        if query.lstrip().startswith("INSERT"):
            self.rowcount = 1
        elif query.lstrip().startswith("DELETE"):
            self.rowcount = 1

    def fetchone(self):
        return (self.is_admin, 0)


class TitleConnection:
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


class UniqueTitleCursor(TitleCursor):
    def __init__(self):
        super().__init__()
        self.titles = set()

    def execute(self, query, params=None):
        super().execute(query, params)
        if query.lstrip().startswith("INSERT"):
            title = params[1]
            if title in self.titles:
                self.rowcount = 0
            else:
                self.titles.add(title)


class AdminTitleTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "title-test"
        self.app.add_url_rule("/admin/users", "admin.admin_users", lambda: "users")
        self.app.add_url_rule("/", "main.index", lambda: "index")
        self.app.add_url_rule("/login", "auth.login", lambda: "login")

    def call_handler(self, handler, data, cursor=None):
        cursor = cursor or TitleCursor()
        conn = TitleConnection(cursor)
        with self.app.test_request_context("/admin", method="POST", data=data):
            session["user_id"] = 99
            response = handler(conn, cursor)
        return response, conn, cursor

    def inserted_title(self, cursor):
        inserts = [params for query, params in cursor.executed if query.lstrip().startswith("INSERT")]
        return inserts[-1][1] if inserts else None

    def test_admin_awards_allowed_title(self):
        _, conn, cursor = self.call_handler(
            handle_award_title,
            {"user_id": "7", "title": ALLOWED_TITLES[0]},
        )
        self.assertEqual(self.inserted_title(cursor), ALLOWED_TITLES[0])
        self.assertEqual(conn.commits, 1)

    def test_admin_awards_custom_title(self):
        _, conn, cursor = self.call_handler(
            handle_award_title,
            {"user_id": "7", "title": "", "custom_title": "Герой финала"},
        )
        self.assertEqual(self.inserted_title(cursor), "Герой финала")
        self.assertEqual(conn.commits, 1)

    def test_custom_title_has_priority_over_selected_title(self):
        _, _, cursor = self.call_handler(
            handle_award_title,
            {"user_id": "7", "title": ALLOWED_TITLES[0], "custom_title": "Особый титул"},
        )
        self.assertEqual(self.inserted_title(cursor), "Особый титул")

    def test_whitespace_only_custom_title_is_rejected_without_selected_title(self):
        _, conn, cursor = self.call_handler(
            handle_award_title,
            {"user_id": "7", "title": "", "custom_title": "   "},
        )
        self.assertIsNone(self.inserted_title(cursor))
        self.assertEqual(conn.commits, 0)

    def test_custom_title_longer_than_40_characters_is_rejected(self):
        _, conn, cursor = self.call_handler(
            handle_award_title,
            {"user_id": "7", "custom_title": "x" * 41},
        )
        self.assertIsNone(self.inserted_title(cursor))
        self.assertEqual(conn.commits, 0)

    def test_unicode_symbols_and_html_like_text_are_allowed(self):
        titles = (
            "🥉 Бронзовый призёр",
            "🏆 Победитель турнира",
            "Лучший прогнозист ⭐",
            "🔥 Король камбэков",
            "Zenit 💙🤍",
            "🥇🥈🥉",
            "3-е место | Кубок №1 🏆",
            "Лучший <прогнозист>",
            "© Чемпион™",
            "勝者 🏆",
            "الفائز 🥇",
            "👨‍👩‍👦 Семейный чемпион",
            "🥉 3-е место | Кубок №1 🏆",
        )
        for title in titles:
            with self.subTest(title=title):
                _, conn, cursor = self.call_handler(
                    handle_award_title,
                    {"user_id": "7", "custom_title": title},
                )
                self.assertEqual(self.inserted_title(cursor), title)
                self.assertEqual(conn.commits, 1)

    def test_html_like_title_is_stored_as_text(self):
        _, conn, cursor = self.call_handler(
            handle_award_title,
            {"user_id": "7", "custom_title": "<script>alert(1)</script>"},
        )
        self.assertEqual(self.inserted_title(cursor), "<script>alert(1)</script>")
        self.assertEqual(conn.commits, 1)

    def test_title_rejects_control_and_line_separator_characters(self):
        for suffix in ("\n", "\r", "\t", "\x00", "\u2028", "\u2029"):
            with self.subTest(repr=suffix):
                _, conn, cursor = self.call_handler(
                    handle_award_title,
                    {"user_id": "7", "custom_title": "Титул" + suffix},
                )
                self.assertIsNone(self.inserted_title(cursor))
                self.assertEqual(conn.commits, 0)

    def test_title_is_nfc_normalized_and_zero_width_joiner_is_preserved(self):
        title = "Cafe\u0301 👨\u200d👩\u200d👦"
        _, conn, cursor = self.call_handler(
            handle_award_title,
            {"user_id": "7", "custom_title": title},
        )
        saved = self.inserted_title(cursor)
        self.assertEqual(saved, unicodedata.normalize("NFC", title))
        self.assertIn("👨\u200d👩\u200d👦", saved)
        self.assertEqual(conn.commits, 1)

    def test_repeated_nfc_normalized_title_is_not_inserted_twice(self):
        cursor = UniqueTitleCursor()
        first = "Cafe\u0301 🏆"
        second = "Café 🏆"
        self.call_handler(handle_award_title, {"user_id": "7", "custom_title": first}, cursor)
        _, conn, _ = self.call_handler(handle_award_title, {"user_id": "7", "custom_title": second}, cursor)
        self.assertEqual(len([query for query, _ in cursor.executed if query.lstrip().startswith("INSERT")]), 2)
        self.assertEqual(conn.rollbacks, 1)

    def test_administrator_cannot_receive_custom_title(self):
        _, conn, cursor = self.call_handler(
            handle_award_title,
            {"user_id": "7", "custom_title": "🏆 Админ"},
            TitleCursor(is_admin=1),
        )
        self.assertIsNone(self.inserted_title(cursor))
        self.assertEqual(conn.commits, 0)

    def test_title_templates_use_escaping_for_user_titles(self):
        admin_template = (ROOT / "templates" / "admin_users.html").read_text(encoding="utf-8")
        profile_template = (ROOT / "templates" / "profile.html").read_text(encoding="utf-8")
        self.assertIn("{{ title }}</span>", admin_template)
        self.assertIn("{{ t.title }}", profile_template)
        self.assertNotIn("{{ title|safe }}", admin_template)
        self.assertNotIn("{{ t.title|safe }}", profile_template)

    def test_admin_can_replace_title(self):
        _, conn, cursor = self.call_handler(
            handle_replace_title,
            {"user_id": "7", "old_title": ALLOWED_TITLES[0], "custom_title": "Новый титул"},
        )
        self.assertTrue(any(query.lstrip().startswith("DELETE") for query, _ in cursor.executed))
        self.assertEqual(self.inserted_title(cursor), "Новый титул")
        self.assertEqual(conn.commits, 1)

    def test_admin_can_remove_title(self):
        _, conn, cursor = self.call_handler(
            handle_remove_title,
            {"user_id": "7", "title": ALLOWED_TITLES[0]},
        )
        self.assertTrue(any(query.lstrip().startswith("DELETE") for query, _ in cursor.executed))
        self.assertEqual(conn.commits, 1)

    def test_non_admin_cannot_access_admin_endpoint(self):
        cursor = TitleCursor(is_admin=0)
        conn = TitleConnection(cursor)

        @self.app.route("/protected")
        @admin_required
        def protected():
            return "allowed"

        with patch("app.routes.admin_common.get_db", return_value=conn), patch("app.routes.admin_common.close_db"):
            with self.app.test_client() as client:
                with client.session_transaction() as current_session:
                    current_session["user_id"] = 7
                response = client.get("/protected")

        self.assertEqual(response.status_code, 302)
        self.assertNotEqual(response.data, b"allowed")
