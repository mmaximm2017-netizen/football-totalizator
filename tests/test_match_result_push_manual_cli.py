import contextlib
import io
import inspect
import unittest
from unittest.mock import Mock, patch

from scripts import send_test_match_result_push as cli


class Cursor:
    def __init__(self, subscriptions):
        self.subscriptions = subscriptions
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchall(self):
        return self.subscriptions


class Connection:
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


def subscription(endpoint):
    return {"endpoint": endpoint, "keys": {"p256dh": "p", "auth": "a"}}


class HttpError(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.status_code = status


class ManualMatchResultPushCliTests(unittest.TestCase):
    def test_user_id_is_required(self):
        with self.assertRaises(SystemExit) as raised:
            cli.main([])
        self.assertEqual(raised.exception.code, 2)

    def test_synthetic_candidate_is_fixed(self):
        self.assertEqual(
            cli.synthetic_candidate(),
            {
                "match_id": 999999,
                "tournament_id": 5,
                "home_team": "Зенит",
                "away_team": "Динамо",
                "home_score": 2,
                "away_score": 1,
                "predicted_home": 1,
                "predicted_away": 0,
                "points": 5,
            },
        )

    def test_payload_uses_production_formatter(self):
        with patch.object(cli, "build_match_result_payload", wraps=cli.build_match_result_payload) as formatter:
            result = cli.run_test(2, sender=Mock(), db_getter=lambda: Connection(Cursor([])), db_closer=lambda *_: None)
        formatter.assert_called_once_with(cli.synthetic_candidate())
        self.assertEqual(result["payload"]["title"], "ТОТИШ")
        self.assertEqual(result["payload"]["body"], "«Зенит — Динамо» завершён: 2:1. Ваш прогноз: 1:0. Начислено: 5 очков ⚽")
        self.assertEqual(result["payload"]["url"], "/?tid=5")
        self.assertEqual(result["payload"]["tag"], "result-999999")

    def test_no_business_write_sql_is_present(self):
        source = inspect.getsource(cli)
        for table in ("matches", "predictions", "push_delivery_log", "tournaments"):
            self.assertNotIn(f"INSERT INTO {table}", source)
            self.assertNotIn(f"UPDATE {table}", source)

    def test_active_subscriptions_are_loaded_through_existing_batch_helper(self):
        source = inspect.getsource(cli.run_test)
        self.assertIn("get_enabled_subscriptions_for_users", source)
        self.assertIn("subscriptions_by_user.get(user_id, [])", source)

    def test_multiple_devices_all_receive_push(self):
        subs = [subscription("https://one"), subscription("https://two")]
        sender = Mock()
        result = cli.run_test(2, sender=sender, db_getter=lambda: Connection(Cursor([(2, *s.values()) for s in []])), db_closer=lambda *_: None)
        self.assertEqual(result["subscriptions"], 0)

        with patch.object(cli, "get_enabled_subscriptions_for_users", return_value={2: subs}):
            result = cli.run_test(2, sender=sender, db_getter=lambda: Connection(Cursor([])), db_closer=lambda *_: None)
        self.assertEqual(result["sent"], 2)
        self.assertEqual(sender.call_count, 2)

    def test_404_410_disable_only_expired_subscription(self):
        subs = [subscription("https://expired"), subscription("https://valid")]
        sender = Mock(side_effect=[HttpError(410), None])
        disable = Mock()
        with patch.object(cli, "get_enabled_subscriptions_for_users", return_value={2: subs}), patch.object(cli, "disable_expired_subscription", disable):
            result = cli.run_test(2, sender=sender, db_getter=lambda: Connection(Cursor([])), db_closer=lambda *_: None)
        self.assertEqual(result["expired"], 1)
        disable.assert_called_once()
        self.assertEqual(disable.call_args.args[1], "https://expired")

    def test_other_failure_does_not_disable_subscription(self):
        subs = [subscription("https://failed")]
        sender = Mock(side_effect=RuntimeError("provider failure"))
        disable = Mock()
        with patch.object(cli, "get_enabled_subscriptions_for_users", return_value={2: subs}), patch.object(cli, "disable_expired_subscription", disable):
            result = cli.run_test(2, sender=sender, db_getter=lambda: Connection(Cursor([])), db_closer=lambda *_: None)
        self.assertEqual(result["failed"], 1)
        disable.assert_not_called()

    def test_one_success_means_overall_success(self):
        subs = [subscription("https://failed"), subscription("https://ok")]
        sender = Mock(side_effect=[RuntimeError("failed"), None])
        with patch.object(cli, "get_enabled_subscriptions_for_users", return_value={2: subs}):
            result = cli.run_test(2, sender=sender, db_getter=lambda: Connection(Cursor([])), db_closer=lambda *_: None)
        self.assertTrue(result["ok"])
        self.assertEqual(result["sent"], 1)
        self.assertEqual(result["failed"], 1)

    def test_all_failed_is_not_success(self):
        subs = [subscription("https://failed")]
        with patch.object(cli, "get_enabled_subscriptions_for_users", return_value={2: subs}):
            result = cli.run_test(2, sender=Mock(side_effect=RuntimeError("failed")), db_getter=lambda: Connection(Cursor([])), db_closer=lambda *_: None)
        self.assertFalse(result["ok"])
        self.assertEqual(result["failed"], 1)

    def test_no_subscription_is_not_success(self):
        result = cli.run_test(2, sender=Mock(), db_getter=lambda: Connection(Cursor([])), db_closer=lambda *_: None)
        self.assertTrue(result["no_subscriptions"])
        self.assertFalse(result["ok"])

    def test_cli_allows_only_user_id_argument(self):
        with patch.object(cli, "run_test", return_value={"user_id": 2, "subscriptions": 0, "sent": 0, "expired": 0, "failed": 0, "no_subscriptions": True, "ok": False}):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = cli.main(["--user-id", "2"])
        self.assertEqual(code, 1)
        self.assertIn("RESULT=FAILED", output.getvalue())

    def test_logs_do_not_contain_endpoint_or_payload_secrets(self):
        source = inspect.getsource(cli)
        self.assertNotIn("p256dh", source)
        self.assertNotIn("auth", source)
        self.assertNotIn("VAPID", source)
        self.assertNotIn("logger.warning(\"%s\", subscription", source)


if __name__ == "__main__":
    unittest.main()
