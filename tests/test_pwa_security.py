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

        self.assertIn("serviceWorkerPath(worker.scriptURL) === '/static/service-worker.js'", template)
        self.assertIn("scopePath === '/static/'", template)

    def test_service_worker_recovery_runs_before_window_load(self):
        template = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        recovery = template.split("const SW_RECOVERY_MARKER", 1)[1].split("</script>", 1)[0]

        self.assertIn("document.readyState !== 'loading'", recovery)
        self.assertIn("document.addEventListener('DOMContentLoaded'", recovery)
        self.assertNotIn("window.addEventListener('load'", recovery)

    def test_recovery_deletes_all_legacy_and_current_totish_caches(self):
        template = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        recovery = template.split("const SW_RECOVERY_MARKER", 1)[1].split("</script>", 1)[0]

        self.assertIn("name === 'totish-cache-v5'", recovery)
        self.assertIn("name.indexOf('totish-static-') === 0", recovery)
        self.assertIn("name.indexOf('totish-ios-rca-') === 0", recovery)
        self.assertNotIn("name !== 'totish-static-v6'", recovery)

    def test_recovery_marker_follows_successful_recovery_only(self):
        template = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        recovery = template.split("const SW_RECOVERY_MARKER", 1)[1].split("</script>", 1)[0]

        self.assertLess(recovery.index("await recoverServiceWorker();"), recovery.index("if (!writeRecoveryMarker())"))
        self.assertIn("localStorage.setItem(SW_RECOVERY_MARKER, SW_RECOVERY_COMPLETE)", recovery)
        self.assertIn("localStorage.setItem(probeKey, '1')", recovery)
        self.assertIn("if (!writeRecoveryMarker())", recovery)
        catch_block = recovery.split("} catch (error) {\n                    console.warn('Service Worker recovery failed'", 1)[1]
        self.assertNotIn("writeRecoveryMarker", catch_block)

    def test_unavailable_marker_storage_uses_safe_normal_registration_only(self):
        template = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        recovery = template.split("const SW_RECOVERY_MARKER", 1)[1].split("</script>", 1)[0]
        unavailable_mode = recovery.split("if (!marker.available) {", 1)[1].split("}\n                    if (marker.complete)", 1)[0]

        self.assertIn("await registerProductionWorker();", unavailable_mode)
        self.assertNotIn("recoverServiceWorker", unavailable_mode)

    def test_normal_mode_only_registers_the_production_worker(self):
        template = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        recovery = template.split("const SW_RECOVERY_MARKER", 1)[1].split("</script>", 1)[0]
        normal_mode = recovery.split("if (marker.complete) {", 1)[1].split("}\n\n                    await recoverServiceWorker", 1)[0]

        self.assertIn("await registerProductionWorker();", normal_mode)
        self.assertNotIn("unregister", normal_mode)
        self.assertNotIn("caches.delete", normal_mode)

    def test_recovery_has_bounded_operations_and_no_reload(self):
        template = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        recovery = template.split("const SW_RECOVERY_MARKER", 1)[1].split("</script>", 1)[0]

        for operation in ("getRegistrations", "unregister", "caches.keys", "caches.delete", "register"):
            self.assertIn("'" + operation + "'", recovery)
        self.assertIn("const SW_OPERATION_TIMEOUT_MS = 8000", recovery)
        self.assertIn("scope: '/'", recovery)
        self.assertIn("updateViaCache: 'none'", recovery)
        self.assertIn("console.warn('Service Worker recovery failed'", recovery)
        self.assertNotIn("location.reload", recovery)

    def test_manifest_uses_root_standalone_scope(self):
        manifest = json.loads((ROOT / "static" / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["start_url"], "/")
        self.assertEqual(manifest["scope"], "/")
        self.assertEqual(manifest["display"], "standalone")
