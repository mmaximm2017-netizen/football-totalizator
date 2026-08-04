import json
import unittest
from pathlib import Path

from app import create_app


ROOT = Path(__file__).resolve().parents[1]


class PwaSecurityTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()

    def test_root_service_worker_has_root_scope_headers(self):
        with self.app.test_client() as client:
            response = client.get("/service-worker.js")

        try:
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.content_type.startswith("application/javascript"))
            self.assertEqual(response.headers["Service-Worker-Allowed"], "/")
            self.assertEqual(response.headers["Cache-Control"], "no-cache, no-store, must-revalidate")
        finally:
            response.close()

    def test_factory_keeps_secure_lax_session_cookie_settings(self):
        self.assertTrue(self.app.config["SESSION_COOKIE_SECURE"])
        self.assertTrue(self.app.config["SESSION_COOKIE_HTTPONLY"])
        self.assertEqual(self.app.config["SESSION_COOKIE_SAMESITE"], "Lax")
        self.assertIsNone(self.app.config["SESSION_COOKIE_DOMAIN"])
        self.assertEqual(self.app.config["SESSION_COOKIE_PATH"], "/")

    def test_login_and_logout_responses_are_not_stored(self):
        with self.app.test_client() as client:
            login_response = client.get("/login")
            with client.session_transaction() as current_session:
                current_session["csrf_token"] = "test-csrf-token"
            logout_response = client.post("/logout", data={"csrf_token": "test-csrf-token"})

        self.assertEqual(login_response.headers["Cache-Control"], "no-store")
        self.assertEqual(logout_response.headers["Cache-Control"], "no-store")

    def test_worker_bypasses_authenticated_navigation_requests(self):
        worker = (ROOT / "static" / "service-worker.js").read_text(encoding="utf-8")

        for condition in (
            "request.method !== 'GET'",
            "request.mode === 'navigate'",
            "url.pathname === '/login'",
            "url.pathname === '/logout'",
            "url.pathname === '/admin'",
            "url.pathname.startsWith('/admin/')",
            "url.pathname === '/api'",
            "url.pathname.startsWith('/api/')",
            "url.origin !== location.origin",
        ):
            self.assertIn(condition, worker)

        self.assertIn("!url.pathname.startsWith('/static/')", worker)
        self.assertNotIn("cache.addAll", worker)

    def test_worker_migrates_legacy_cache_without_global_cache_fallback(self):
        worker = (ROOT / "static" / "service-worker.js").read_text(encoding="utf-8")

        self.assertIn("const LEGACY_CACHE_NAMES = new Set([", worker)
        self.assertIn("'totish-cache-v5'", worker)
        self.assertIn("LEGACY_CACHE_NAMES.has(key)", worker)
        self.assertIn("const cache = await caches.open(CACHE_NAME);", worker)
        self.assertIn("const cachedResponse = await cache.match(request);", worker)
        self.assertIn("return cachedResponse || Response.error();", worker)
        self.assertNotIn("caches.match(request)", worker)

    def test_legacy_worker_registration_matches_scope_and_script(self):
        template = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")

        self.assertIn("new URL(registration.scope).pathname === '/static/'", template)
        self.assertIn("new URL(worker.scriptURL).pathname === '/static/service-worker.js'", template)

    def test_manifest_uses_root_standalone_scope(self):
        manifest = json.loads((ROOT / "static" / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["start_url"], "/")
        self.assertEqual(manifest["scope"], "/")
        self.assertEqual(manifest["display"], "standalone")
