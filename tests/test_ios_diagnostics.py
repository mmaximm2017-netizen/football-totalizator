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

    def test_authenticated_diagnostics_request_does_not_need_csrf(self):
        app = self.create_diagnostics_app(True)
        with app.test_client() as client:
            with client.session_transaction() as current_session:
                current_session["user_id"] = 11
            response = client.post("/__diagnostics/client", json={"event": "test"})

        self.assertEqual(response.status_code, 204)

    def test_diagnostics_request_rejects_foreign_origin(self):
        app = self.create_diagnostics_app(True)
        with app.test_client() as client:
            with client.session_transaction() as current_session:
                current_session["user_id"] = 11
            response = client.post(
                "/__diagnostics/client",
                json={"event": "test"},
                headers={"Origin": "https://attacker.example"},
            )

        self.assertEqual(response.status_code, 403)

    def test_diagnostics_request_rejects_cross_site_fetch_metadata(self):
        app = self.create_diagnostics_app(True)
        with app.test_client() as client:
            with client.session_transaction() as current_session:
                current_session["user_id"] = 11
            response = client.post(
                "/__diagnostics/client",
                json={"event": "test"},
                headers={"Sec-Fetch-Site": "cross-site"},
            )

        self.assertEqual(response.status_code, 403)

    def test_other_post_routes_remain_csrf_protected(self):
        app = self.create_diagnostics_app(True)
        with app.test_client() as client:
            response = client.post("/login", data={"username": "user", "password": "password"})

        self.assertEqual(response.status_code, 400)

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
                    "pendingResourceCount": 2,
                    "pendingResources": "img /static/slow.png complete=false",
                    "brokenResourceCount": 1,
                    "brokenResources": "img /static/broken.png natural=0x0",
                    "resourceStateSummary": "images total=2 pending=1 broken=1",
                    "slowResources": "link /static/site.css duration=201",
                    "fontStatus": "loading",
                    "unknown": "discarded",
                    "password": "secret",
                    "csrf": "secret",
                    "cookie": "secret",
                })

        self.assertEqual(response.status_code, 204)
        output = next(line for line in logs.output if "ios_client_diagnostics" in line)
        self.assertIn('"event": "timer-4s"', output)
        self.assertIn('"pathname": "/"', output)
        self.assertIn('"pendingResourceCount": 2', output)
        self.assertIn('"fontStatus": "loading"', output)
        self.assertNotIn("unknown", output)
        self.assertNotIn("password", output)
        self.assertNotIn("csrf", output)
        self.assertNotIn('"cookie"', output)

    def test_resource_diagnostic_strings_are_limited(self):
        app = self.create_diagnostics_app(True)
        with app.test_client() as client:
            with client.session_transaction() as current_session:
                current_session["user_id"] = 11
            with self.assertLogs("app", level="INFO") as logs:
                response = client.post("/__diagnostics/client", json={
                    "event": "resource-snapshot",
                    "pendingResources": "x" * 900,
                    "slowResources": "y" * 900,
                })

        self.assertEqual(response.status_code, 204)
        output = next(line for line in logs.output if "ios_client_diagnostics" in line)
        self.assertIn('"pendingResources": "' + "x" * 700 + '"', output)
        self.assertNotIn("x" * 701, output)
        self.assertIn('"slowResources": "' + "y" * 700 + '"', output)

    def test_base_template_uses_early_add_event_listener_diagnostics(self):
        with open("templates/base.html", encoding="utf-8") as template_file:
            template = template_file.read()

        for event in ("script-start", "DOMContentLoaded", "load", "pageshow", "timer-4s", "timer-10s", "timer-20s", "resource-snapshot"):
            self.assertIn(event, template)
        for selector in ("document.images", "link[rel~=\"stylesheet\"]", "script[src]", "iframe", "video, audio"):
            self.assertIn(selector, template)
        self.assertIn("window.addEventListener('load', recordResourceEvent, true)", template)
        self.assertIn("window.addEventListener('error'", template)
        self.assertIn("window.addEventListener('unhandledrejection'", template)
        self.assertIn("new URL(source, window.location.origin).pathname", template)
        self.assertNotIn("window.onerror =", template)
        self.assertNotIn("window.stop", template)
        self.assertNotIn("location.reload", template)
