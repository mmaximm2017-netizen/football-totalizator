import json
import threading
import unittest
from unittest.mock import MagicMock, patch

from app.services.match_service import (
    SYNC_LOCK_KEY,
    try_acquire_sync_lock,
    release_sync_lock,
    run_sync_with_lock,
)


def _silence_loggers(test_case, *logger_paths):
    """Silence specific loggers during a test, auto-restored on cleanup."""
    for path in logger_paths:
        p = patch(path)
        p.start()
        test_case.addCleanup(p.stop)


class TestTryAcquireSyncLock(unittest.TestCase):
    """Tests for try_acquire_sync_lock() — direct unit tests."""

    def setUp(self):
        _silence_loggers(self, "app.services.match_service.logger")
        self.get_db_patcher = patch("app.services.match_service.get_db")
        self.mock_get_db = self.get_db_patcher.start()
        self.close_db_patcher = patch("app.services.match_service.close_db")
        self.mock_close_db = self.close_db_patcher.start()
        self.addCleanup(self.close_db_patcher.stop)
        self.addCleanup(self.get_db_patcher.stop)

    def _make_fake_conn(self, fetchone_result=(True,)):
        cur = MagicMock()
        cur.fetchone.return_value = fetchone_result
        conn = MagicMock()
        conn.cursor.return_value = cur
        return conn, cur

    def test_acquire_success(self):
        fake_conn, fake_cur = self._make_fake_conn((True,))
        self.mock_get_db.return_value = fake_conn

        conn, cur, acquired, error = try_acquire_sync_lock()

        self.assertTrue(acquired)
        self.assertIsNone(error)
        fake_cur.execute.assert_called_once_with(
            "SELECT pg_try_advisory_lock(%s)", (SYNC_LOCK_KEY,),
        )

    def test_acquire_busy(self):
        fake_conn, fake_cur = self._make_fake_conn((False,))
        self.mock_get_db.return_value = fake_conn

        conn, cur, acquired, error = try_acquire_sync_lock()

        self.assertFalse(acquired)
        self.assertIsNone(error)

    def test_acquire_error_fail_closed(self):
        fake_conn, fake_cur = self._make_fake_conn()
        fake_cur.execute.side_effect = RuntimeError("connection lost")
        self.mock_get_db.return_value = fake_conn

        conn, cur, acquired, error = try_acquire_sync_lock()

        self.assertFalse(acquired)
        self.assertIsNotNone(error)
        self.assertIn("connection lost", error)
        self.mock_close_db.assert_called_once_with(fake_conn, fake_cur)


class TestReleaseSyncLock(unittest.TestCase):
    """Tests for release_sync_lock() — direct unit tests."""

    def setUp(self):
        _silence_loggers(self, "app.services.match_service.logger")

    def test_release_noop_when_no_conn(self):
        release_sync_lock(None, None)

    def test_release_success(self):
        fake_cur = MagicMock()
        fake_conn = MagicMock()
        close_db_patcher = patch("app.services.match_service.close_db")
        mock_close_db = close_db_patcher.start()

        release_sync_lock(fake_conn, fake_cur)

        fake_cur.execute.assert_called_once_with(
            "SELECT pg_advisory_unlock(%s)", (SYNC_LOCK_KEY,),
        )
        mock_close_db.assert_called_once_with(fake_conn, fake_cur)
        close_db_patcher.stop()

    def test_release_unlock_error_putconn_close_true(self):
        fake_cur = MagicMock()
        fake_cur.closed = False
        fake_cur.execute.side_effect = RuntimeError("unlock failed")
        fake_conn = MagicMock()
        mock_pool = MagicMock()

        with patch("app.db.db_pool", mock_pool):
            release_sync_lock(fake_conn, fake_cur)

        fake_cur.close.assert_called_once()
        mock_pool.putconn.assert_called_once_with(fake_conn, close=True)
        fake_conn.close.assert_not_called()

    def test_release_unlock_error_pool_none_fallback_close(self):
        fake_cur = MagicMock()
        fake_cur.closed = False
        fake_cur.execute.side_effect = RuntimeError("unlock failed")
        fake_conn = MagicMock()

        with patch("app.db.db_pool", None):
            release_sync_lock(fake_conn, fake_cur)

        fake_cur.close.assert_called_once()
        fake_conn.close.assert_called_once()


class TestRunSyncWithLock(unittest.TestCase):
    """Tests for run_sync_with_lock() behavior with mocked lock."""

    def setUp(self):
        _silence_loggers(self, "app.services.match_service.logger")
        self.patches = {}
        patchers = {
            "recover_stale_syncs": patch("app.services.match_service.recover_stale_syncs"),
            "create_sync_run": patch("app.services.match_service.create_sync_run", return_value=1),
            "finish_sync_run": patch("app.services.match_service.finish_sync_run"),
            "update_matches": patch("app.services.match_service.update_matches"),
            "_recalculate_points_after_sync": patch(
                "app.services.match_service._recalculate_points_after_sync"
            ),
            "release_sync_lock": patch("app.services.match_service.release_sync_lock"),
        }
        for name, p in patchers.items():
            self.patches[name] = p.start()
        self.patches["update_matches"].return_value = {
            "matches_inserted": 2, "matches_updated": 3,
        }
        self.patches["_recalculate_points_after_sync"].return_value = {
            "scoring_mode": "changed_matches",
            "matches_recalculated": 1,
            "predictions_recalculated": 5,
        }

    def tearDown(self):
        for p in self.patches.values():
            p.stop()

    # --- Test 1: Lock free ---
    def test_lock_free_sync_completes(self):
        acquire_patcher = patch(
            "app.services.match_service.try_acquire_sync_lock",
            return_value=(MagicMock(), MagicMock(), True, None),
        )
        mock_acquire = acquire_patcher.start()
        try:
            result = run_sync_with_lock()
            self.assertEqual(result["status"], "completed")
            self.assertTrue(result["lock_acquired"])
            self.assertIsNone(result["lock_error"])
            self.patches["update_matches"].assert_called_once()
            self.patches["_recalculate_points_after_sync"].assert_called_once()
            self.patches["release_sync_lock"].assert_called_once()
        finally:
            acquire_patcher.stop()

    # --- Test 2: Lock busy ---
    def test_lock_busy_skips_sync(self):
        acquire_patcher = patch(
            "app.services.match_service.try_acquire_sync_lock",
            return_value=(MagicMock(), MagicMock(), False, None),
        )
        mock_acquire = acquire_patcher.start()
        try:
            result = run_sync_with_lock()
            self.assertEqual(result["status"], "skipped_already_running")
            self.assertFalse(result["lock_acquired"])
            self.patches["update_matches"].assert_not_called()
            self.patches["_recalculate_points_after_sync"].assert_not_called()
            self.patches["release_sync_lock"].assert_called_once()
        finally:
            acquire_patcher.stop()

    # --- Test 3: Lock error -> fail-closed ---
    def test_lock_error_fail_closed(self):
        acquire_patcher = patch(
            "app.services.match_service.try_acquire_sync_lock",
            return_value=(None, None, False, "connection lost"),
        )
        mock_acquire = acquire_patcher.start()
        try:
            result = run_sync_with_lock()
            self.assertEqual(result["status"], "lock_error")
            self.assertFalse(result["lock_acquired"])
            self.assertEqual(result["lock_error"], "connection lost")
            self.patches["update_matches"].assert_not_called()
            self.patches["_recalculate_points_after_sync"].assert_not_called()
            self.patches["release_sync_lock"].assert_called_once()
        finally:
            acquire_patcher.stop()

    # --- Test 4: Error inside sync -> lock released in finally ---
    def test_sync_error_releases_lock(self):
        acquire_patcher = patch(
            "app.services.match_service.try_acquire_sync_lock",
            return_value=(MagicMock(), MagicMock(), True, None),
        )
        mock_acquire = acquire_patcher.start()
        self.patches["update_matches"].side_effect = RuntimeError("DB crash")
        try:
            with self.assertRaises(RuntimeError):
                run_sync_with_lock()
            self.patches["release_sync_lock"].assert_called_once()
        finally:
            acquire_patcher.stop()

    # --- Test 7: Subsequent run can acquire lock after completion ---
    def test_subsequent_run_acquires_lock(self):
        acquire_patcher = patch(
            "app.services.match_service.try_acquire_sync_lock",
            side_effect=[
                (MagicMock(), MagicMock(), True, None),
                (MagicMock(), MagicMock(), True, None),
            ],
        )
        mock_acquire = acquire_patcher.start()
        self.patches["update_matches"].side_effect = None
        self.patches["update_matches"].return_value = {"matches_inserted": 1}
        try:
            r1 = run_sync_with_lock()
            self.assertEqual(r1["status"], "completed")
            r2 = run_sync_with_lock()
            self.assertEqual(r2["status"], "completed")
            self.assertEqual(mock_acquire.call_count, 2)
        finally:
            acquire_patcher.stop()


class TestSyncLockConcurrency(unittest.TestCase):
    """Test 6: Two concurrent runs — only one proceeds."""

    def setUp(self):
        _silence_loggers(self, "app.services.match_service.logger")

    def test_concurrent_runs_only_one_proceeds(self):
        proceed = threading.Event()
        concurrency_lock = threading.Lock()
        call_index = 0
        results = []

        def blocking_update(*args, **kwargs):
            proceed.wait(timeout=5)
            return {"matches_inserted": 1}

        def acquire_side_effect(*args, **kwargs):
            nonlocal call_index
            with concurrency_lock:
                call_index += 1
                if call_index == 1:
                    return (MagicMock(), MagicMock(), True, None)
                return (MagicMock(), MagicMock(), False, None)

        with patch.multiple(
            "app.services.match_service",
            try_acquire_sync_lock=MagicMock(side_effect=acquire_side_effect),
            recover_stale_syncs=MagicMock(),
            create_sync_run=MagicMock(return_value=1),
            finish_sync_run=MagicMock(),
            update_matches=MagicMock(side_effect=blocking_update),
            _recalculate_points_after_sync=MagicMock(),
            release_sync_lock=MagicMock(),
        ):
            def target():
                r = run_sync_with_lock()
                results.append(r)

            t1 = threading.Thread(target=target)
            t2 = threading.Thread(target=target)

            t1.start()
            t2.start()

            t2.join(timeout=5)
            self.assertFalse(
                t2.is_alive(), "Second thread should finish immediately (lock busy)",
            )

            proceed.set()
            t1.join(timeout=5)
            self.assertFalse(
                t1.is_alive(), "First thread should finish after unblock",
            )

        self.assertEqual(len(results), 2)
        statuses = {r["status"] for r in results}
        self.assertIn("completed", statuses)
        self.assertIn("skipped_already_running", statuses)


class TestAdminSyncRoutes(unittest.TestCase):
    """Tests 8-9: Admin route handler behaves correctly for sync lock states."""

    def setUp(self):
        _silence_loggers(
            self,
            "app.routes.admin_sync.logger",
            "app.routes.admin_matches.logger",
        )
        self.patchers = [
            patch("app.services.match_service.run_sync_with_lock"),
            patch("app.services.sync_history_service.get_sync_health",
                  return_value={"is_healthy": True}),
        ]
        for p in self.patchers:
            p.start()

        self.app = _create_test_app()
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()
        for p in self.patchers:
            p.stop()

    def _resolve(self, result):
        """Unpack Flask view return value (may be tuple)."""
        if isinstance(result, tuple):
            return result[0], result[1]
        return result, result.status_code

    def _call_sync_handler(self, headers=None):
        from app.routes.admin_sync import handle_manual_sync_update
        from app.services.match_service import run_sync_with_lock
        run_sync_with_lock.return_value = self._sync_result

        with patch("app.routes.admin_sync.url_for", return_value="/admin"):
            with self.app.test_request_context("/admin", method="POST",
                                               headers=headers or {}):
                return self._resolve(handle_manual_sync_update())

    # --- Test 8: Browser busy -> warning ---
    def test_browser_busy_shows_warning(self):
        self._sync_result = {"status": "skipped_already_running"}
        response, code = self._call_sync_handler()
        self.assertEqual(code, 302)

    # --- Test 8: Browser success -> normal ---
    def test_browser_success_redirects(self):
        self._sync_result = {
            "status": "completed",
            "sync": {"matches_inserted": 2, "matches_updated": 3, "errors": []},
            "scoring": {"predictions_recalculated": 5},
        }
        response, code = self._call_sync_handler()
        self.assertEqual(code, 302)

    # --- Test 8: Browser exception -> safe error ---
    def test_browser_exception_shows_safe_error(self):
        from app.services.match_service import run_sync_with_lock
        run_sync_with_lock.side_effect = RuntimeError("unexpected")

        with patch("app.routes.admin_sync.url_for", return_value="/admin"):
            with self.app.test_request_context("/admin", method="POST"):
                from app.routes.admin_sync import handle_manual_sync_update
                response, code = self._resolve(handle_manual_sync_update())
                self.assertEqual(code, 302)

    # --- Test 9: AJAX busy -> 423 JSON ---
    def test_ajax_busy_returns_423_json(self):
        self._sync_result = {"status": "skipped_already_running"}
        response, code = self._call_sync_handler(
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(code, 423)
        self.assertTrue(response.is_json)
        data = response.get_json()
        self.assertFalse(data["ok"])
        self.assertIn("Синхронизация", data["message"])

    # --- Test 9: AJAX lock_error -> 423 JSON ---
    def test_ajax_lock_error_returns_423_json(self):
        self._sync_result = {
            "status": "lock_error", "lock_error": "connection lost",
        }
        response, code = self._call_sync_handler(
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(code, 423)
        self.assertTrue(response.is_json)
        data = response.get_json()
        self.assertFalse(data["ok"])

    # --- Test 9: AJAX success -> 200 JSON ---
    def test_ajax_success_returns_200_json(self):
        self._sync_result = {
            "status": "completed",
            "sync": {"matches_inserted": 2, "matches_updated": 3, "errors": []},
            "scoring": {"predictions_recalculated": 5},
        }
        response, code = self._call_sync_handler(
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(code, 200)
        self.assertTrue(response.is_json)
        data = response.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["inserted"], 2)
        self.assertEqual(data["updated"], 3)

    # --- Test 9: AJAX exception -> 500 JSON ---
    def test_ajax_exception_returns_500_json(self):
        from app.services.match_service import run_sync_with_lock
        run_sync_with_lock.side_effect = RuntimeError("unexpected")

        with self.app.test_request_context(
            "/admin", method="POST",
            headers={"X-Requested-With": "XMLHttpRequest"},
        ):
            from app.routes.admin_sync import handle_manual_sync_update
            response, code = self._resolve(handle_manual_sync_update())
            self.assertEqual(code, 500)
            self.assertTrue(response.is_json)
            data = response.get_json()
            self.assertFalse(data["ok"])


def _create_test_app():
    from flask import Flask
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.config["TESTING"] = True
    return app


if __name__ == "__main__":
    unittest.main()
