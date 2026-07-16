import unittest
from unittest.mock import patch

from flask import Flask, session

from app.routes.admin_actions import (
    ALLOWED_TITLES,
    handle_award_title,
    handle_remove_title,
    handle_replace_title,
)
from app.routes.admin_common import admin_required


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

    def test_html_title_is_rejected(self):
        _, conn, cursor = self.call_handler(
            handle_award_title,
            {"user_id": "7", "custom_title": "<script>alert(1)</script>"},
        )
        self.assertIsNone(self.inserted_title(cursor))
        self.assertEqual(conn.commits, 0)

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
