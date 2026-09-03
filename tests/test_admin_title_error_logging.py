import unittest
from unittest.mock import patch

from flask import Flask, get_flashed_messages, session

from app.routes.admin_actions import handle_award_title


class FailingCursor:
    rowcount = 0

    def __init__(self):
        self.calls = 0

    def execute(self, query, params=None):
        self.calls += 1
        if "SELECT is_admin" in query:
            return
        raise RuntimeError("database detail must stay server-side")

    def fetchone(self):
        return (0,)


class Connection:
    def __init__(self):
        self.rollbacks = 0

    def rollback(self):
        self.rollbacks += 1

    def commit(self):
        raise AssertionError("commit should not be reached")


class AdminTitleErrorLoggingTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "admin-title-error-test"
        self.app.add_url_rule("/admin/users", "admin.admin_users", lambda: "users")

    def test_database_failure_is_logged_without_flashing_exception_text(self):
        conn = Connection()
        cur = FailingCursor()
        with self.app.test_request_context(
            "/admin",
            method="POST",
            data={"user_id": "7", "custom_title": "Тест"},
        ):
            session["user_id"] = 99
            with patch("app.routes.admin_actions.logger.exception") as log_exception:
                response = handle_award_title(conn, cur)
                messages = get_flashed_messages(with_categories=True)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(conn.rollbacks, 1)
        log_exception.assert_called_once_with("admin_title_award_failed user_id=%s", 7)
        self.assertEqual(
            messages,
            [("error", "Не удалось выдать титул. Ошибка записана в журнал.")],
        )
        self.assertNotIn("database detail", messages[0][1])


if __name__ == "__main__":
    unittest.main()
