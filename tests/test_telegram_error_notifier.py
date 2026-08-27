import os
import unittest
from unittest.mock import patch

from app.services import telegram_error_notifier as notifier

class ImmediateThread:
    def __init__(self, target, args=(), daemon=None):
        self.target = target
        self.args = args
    def start(self):
        self.target(*self.args)

class TelegramErrorNotifierTests(unittest.TestCase):
    def setUp(self):
        notifier._recent_errors.clear()

    def test_disabled_without_environment(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(notifier.telegram_monitor_configured())
            self.assertFalse(notifier.notify_exception(RuntimeError("boom")))

    def test_message_contains_context_and_traceback(self):
        try:
            raise ValueError("broken")
        except ValueError as exc:
            message = notifier._build_message(exc, source="flask_500", method="POST", path="/test")
        self.assertIn("TOTISH ERROR", message)
        self.assertIn("flask_500", message)
        self.assertIn("ValueError: broken", message)
        self.assertIn("POST /test", message)
        self.assertIn("Traceback:", message)

    def test_redacts_configured_secrets(self):
        with patch.dict(os.environ, {
            "TELEGRAM_ERROR_BOT_TOKEN": "bot-secret",
            "DATABASE_URL": "postgresql://u:db-secret@db.example/db",
        }, clear=True):
            value = notifier._redact("bot-secret postgresql://u:db-secret@db.example/db")
        self.assertNotIn("bot-secret", value)
        self.assertNotIn("db-secret", value)
        self.assertIn("[REDACTED]", value)

    def test_duplicate_is_suppressed_for_five_minutes(self):
        exc = RuntimeError("same")
        fingerprint = notifier._fingerprint(exc, "flask", "GET", "/")
        self.assertTrue(notifier._should_send(fingerprint, now=100))
        self.assertFalse(notifier._should_send(fingerprint, now=399))
        self.assertTrue(notifier._should_send(fingerprint, now=400))

    def test_notify_sends_once_when_configured(self):
        env = {"TELEGRAM_ERROR_BOT_TOKEN": "token", "TELEGRAM_ERROR_CHAT_ID": "123"}
        sent = []
        with patch.dict(os.environ, env, clear=True),              patch.object(notifier.threading, "Thread", ImmediateThread),              patch.object(notifier, "_send_message", side_effect=sent.append):
            exc = RuntimeError("boom")
            first = notifier.notify_exception(exc, source="flask_500", method="GET", path="/x")
            second = notifier.notify_exception(exc, source="flask_500", method="GET", path="/x")
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(len(sent), 1)

if __name__ == "__main__":
    unittest.main()
