import importlib
import sys
import unittest
from unittest.mock import patch


class ControlledDbInitializationEntrypointTests(unittest.TestCase):
    def test_entrypoint_calls_initialization_once_on_success(self):
        entrypoint = importlib.import_module("app.init_db")

        with (
            patch.object(entrypoint, "init_db") as init_db,
            patch("app.create_app") as create_app,
            self.assertLogs(level="INFO") as logs,
        ):
            result = entrypoint.main()

        self.assertEqual(result, 0)
        init_db.assert_called_once_with()
        create_app.assert_not_called()
        self.assertIn("Database initialization completed", "\n".join(logs.output))

    def test_entrypoint_returns_failure_without_success_log(self):
        entrypoint = importlib.import_module("app.init_db")

        with patch.object(entrypoint, "init_db", side_effect=RuntimeError("migration failed")), self.assertLogs(level="ERROR") as logs:
            result = entrypoint.main()

        self.assertEqual(result, 1)
        output = "\n".join(logs.output)
        self.assertIn("Database initialization failed", output)
        self.assertNotIn("Database initialization completed", output)

    def test_wsgi_import_does_not_run_database_initialization_or_external_requests(self):
        sys.modules.pop("wsgi", None)

        with patch("app.db.init_db") as init_db, patch("requests.get") as requests_get:
            importlib.import_module("wsgi")

        init_db.assert_not_called()
        requests_get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
