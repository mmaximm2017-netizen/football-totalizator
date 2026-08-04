import os
import unittest
from unittest.mock import patch

from app import create_app


class IosDiagnosticsTests(unittest.TestCase):
    def create_diagnostics_app(self, enabled):
        with patch.dict(os.environ, {"IOS_DIAGNOSTICS": "1" if enabled else "0"}):
            return create_app()

    def test_endpoint_is_hidden_when_diagnostics_are_disabled(self):
        app = self.create_diagnostics_app(False)
        with app.test_client() as client:
            response = client.post("/__diagnostics/client", json={"event": "test"})

        self.assertEqual(response.status_code, 404)

    def test_unauthenticated_diagnostics_request_is_rejected(self):
        app = self.create_diagnostics_app(True)
        with app.test_client() as client:
            response = client.post("/__diagnostics/client", json={"event": "test"})

        self.assertEqual(response.status_code, 401)

    def test_authenticated_allowed_fields_are_logged_without_secrets(self):
        app = self.create_diagnostics_app(True)
        with app.test_client() as client:
            with client.session_transaction() as current_session:
                current_session["user_id"] = 11
            with self.assertLogs("app", level="INFO") as logs:
                response = client.post("/__diagnostics/client", json={
                    "event": "timer-4s",
                    "pathname": "/?token=discarded",
                    "splashExists": True,
                    "unknown": "discarded",
                    "password": "secret",
                    "csrf": "secret",
                    "cookie": "secret",
                })

        self.assertEqual(response.status_code, 204)
        output = next(line for line in logs.output if "ios_client_diagnostics" in line)
        self.assertIn('"event": "timer-4s"', output)
        self.assertIn('"pathname": "/"', output)
        self.assertNotIn("unknown", output)
        self.assertNotIn("password", output)
        self.assertNotIn("csrf", output)
        self.assertNotIn('"cookie"', output)

    def test_base_template_uses_early_add_event_listener_diagnostics(self):
        with open("templates/base.html", encoding="utf-8") as template_file:
            template = template_file.read()

        for event in ("script-start", "DOMContentLoaded", "load", "pageshow", "timer-4s", "timer-10s"):
            self.assertIn(event, template)
        self.assertIn("window.addEventListener('error'", template)
        self.assertIn("window.addEventListener('unhandledrejection'", template)
        self.assertNotIn("window.onerror =", template)
