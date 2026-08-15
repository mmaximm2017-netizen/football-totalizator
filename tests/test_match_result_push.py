import inspect
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.services import match_result_push_service as service
from app.services import scoring_recalculation_service


ROOT = Path(__file__).resolve().parents[1]


class Cursor:
    def __init__(self, rows=None, claims=None):
        self.rows = rows or []
        self.claims = list(claims or [])
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.claims.pop(0) if self.claims else None


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


def candidate(**overrides):
    value = {
        "match_id": 423,
        "tournament_id": 5,
        "home_team": "Зенит",
        "away_team": "Динамо",
        "home_score": 2,
        "away_score": 1,
        "user_id": 2,
        "predicted_home": 1,
        "predicted_away": 0,
        "points": 5,
        "event_type": "match_result",
        "event_key": "match:423",
    }
    value.update(overrides)
    return value


class MatchResultPushTests(unittest.TestCase):
    def test_normalize_now_requires_timezone(self):
        with self.assertRaises(ValueError):
            service.normalize_now(datetime(2026, 8, 15, 10, 0))

    def test_normalize_since_accepts_zulu(self):
        result = service.normalize_since("2026-08-15T10:00:00Z")
        self.assertEqual(result.tzinfo, timezone.utc)

    def test_candidate_query_requires_finished_state(self):
        cursor = Cursor()
        service.select_match_result_candidates(cursor, datetime.now(timezone.utc))
        query = cursor.executed[0][0]
        self.assertIn("UPPER(COALESCE(m.status, '')) = 'FINISHED'", query)
        self.assertIn("m.home_score BETWEEN 0 AND 99", query)
        self.assertIn("m.away_score BETWEEN 0 AND 99", query)

    def test_candidate_query_requires_scored_prediction(self):
        cursor = Cursor()
        service.select_match_result_candidates(cursor, datetime.now(timezone.utc))
        query = cursor.executed[0][0]
        self.assertIn("p.points IS NOT NULL", query)
        self.assertIn("push_delivery_log", query)

    def test_candidate_query_excludes_admin_deleted_and_inactive_users(self):
        cursor = Cursor()
        service.select_match_result_candidates(cursor, datetime.now(timezone.utc))
        query = cursor.executed[0][0]
        self.assertIn("COALESCE(u.is_admin, 0) = 0", query)
        self.assertIn("COALESCE(u.is_deleted, 0) = 0", query)
        self.assertIn("ps.enabled = TRUE", query)

    def test_candidate_query_has_one_user_match_group(self):
        cursor = Cursor()
        service.select_match_result_candidates(cursor, datetime.now(timezone.utc))
        query = cursor.executed[0][0]
        self.assertIn("GROUP BY d.match_id", query)
        self.assertIn("p.user_id", query)

    def test_candidate_query_supports_bootstrap_cutoff(self):
        cursor = Cursor()
        since = datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc)
        service.select_match_result_candidates(cursor, since, since=since)
        query, params = cursor.executed[0]
        self.assertIn("d.sent_at >= %s", query)
        self.assertEqual(params[-1], since)

    def test_candidate_mapping_contains_real_scores_prediction_and_points(self):
        cursor = Cursor(rows=[(423, 5, "Зенит", "Динамо", 2, 1, 2, 1, 0, 5)])
        result = service.select_match_result_candidates(cursor, datetime.now(timezone.utc))
        self.assertEqual(result, [candidate()])

    def test_unfinished_match_is_not_a_candidate_by_contract(self):
        source = inspect.getsource(service.select_match_result_candidates)
        self.assertIn("= 'FINISHED'", source)

    def test_no_prediction_is_not_a_candidate_by_join(self):
        source = inspect.getsource(service.select_match_result_candidates)
        self.assertIn("JOIN predictions p", source)

    def test_no_active_subscription_is_not_a_candidate_by_join(self):
        source = inspect.getsource(service.select_match_result_candidates)
        self.assertIn("JOIN push_subscriptions ps", source)

    def test_already_sent_event_is_not_retryable(self):
        source = inspect.getsource(service.select_match_result_candidates)
        self.assertNotIn("d.status = 'sent'", source)

    def test_multiple_subscriptions_are_grouped_to_one_event(self):
        source = inspect.getsource(service.select_match_result_candidates)
        self.assertIn("GROUP BY d.match_id", source)
        self.assertIn("ps.enabled = TRUE", source)

    def test_claim_is_atomic_and_retryable(self):
        cursor = Cursor(claims=[(1,)])
        self.assertTrue(service.claim_delivery(cursor, candidate(), datetime.now(timezone.utc)))
        query = cursor.executed[0][0]
        self.assertIn("UPDATE push_delivery_log", query)
        self.assertIn("status = 'ready'", query)
        self.assertIn("status IN ('failed', 'pending')", query)
        self.assertIn("RETURNING id", query)

    def test_duplicate_concurrent_claim_is_rejected(self):
        cursor = Cursor(claims=[None])
        self.assertFalse(service.claim_delivery(cursor, candidate(), datetime.now(timezone.utc)))

    def test_fixed_payload_uses_database_values(self):
        payload = service.build_match_result_payload(candidate())
        self.assertEqual(payload["title"], "ТОТИШ")
        self.assertIn("Зенит — Динамо", payload["body"])
        self.assertIn("2:1", payload["body"])
        self.assertIn("1:0", payload["body"])
        self.assertIn("5 очков", payload["body"])

    def test_payload_has_safe_existing_internal_url(self):
        payload = service.build_match_result_payload(candidate())
        self.assertEqual(payload["url"], "/?tid=5")
        self.assertTrue(payload["url"].startswith("/"))
        self.assertNotIn("://", payload["url"])

    def test_payload_tag_is_match_scoped(self):
        self.assertEqual(service.build_match_result_payload(candidate())["tag"], "result-423")

    def test_points_pluralization(self):
        self.assertEqual(service.points_word(0), "очков")
        self.assertEqual(service.points_word(1), "очко")
        self.assertEqual(service.points_word(2), "очка")
        self.assertEqual(service.points_word(4), "очка")
        self.assertEqual(service.points_word(5), "очков")
        self.assertEqual(service.points_word(11), "очков")
        self.assertEqual(service.points_word(21), "очко")

    def test_dry_run_does_not_claim_send_or_write(self):
        now = datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc)
        conn = Connection(Cursor(rows=[(423, 5, "Зенит", "Динамо", 2, 1, 2, 1, 0, 5)]))
        with patch.object(service, "get_db", return_value=conn), patch.object(service, "close_db"), patch.object(service, "send_push") as sender:
            result = service.run_once(now=now, since=now - timedelta(minutes=1), dry_run=True)
        sender.assert_not_called()
        self.assertEqual(result["would_send"], 1)
        self.assertEqual(conn.commits, 0)

    def test_missing_bootstrap_cutoff_fails_closed_before_db_access(self):
        with patch.dict("os.environ", {}, clear=True), patch.object(service, "get_db") as get_db:
            with self.assertRaises(RuntimeError):
                service.run_once(dry_run=True)
        get_db.assert_not_called()

    def test_partial_success_is_event_sent_by_design(self):
        source = inspect.getsource(service.run_once)
        self.assertIn("if user_sent:", source)
        self.assertIn('mark_delivery(cur, candidate, "sent", now)', source)

    def test_all_failures_remain_retryable(self):
        source = inspect.getsource(service.run_once)
        self.assertIn('mark_delivery(', source)
        self.assertIn('"failed"', source)

    def test_expired_status_disables_only_endpoint(self):
        source = inspect.getsource(service.run_once)
        self.assertIn("delivery_error_status(exc) in (404, 410)", source)
        self.assertIn("disable_expired_subscription", source)

    def test_scoring_outbox_is_created_after_points_update(self):
        source = inspect.getsource(scoring_recalculation_service.recalc_match_points)
        self.assertLess(source.index("SET points = %s"), source.index("_enqueue_result_event"))

    def test_outbox_event_is_unique_per_user_match(self):
        source = inspect.getsource(scoring_recalculation_service._enqueue_result_event)
        self.assertIn("ON CONFLICT (user_id, event_type, event_key)", source)
        self.assertIn("'ready'", source)

    def test_bootstrap_script_suppresses_historical_events(self):
        source = (ROOT / "scripts" / "bootstrap_match_result_pushes.py").read_text(encoding="utf-8")
        self.assertIn("status = 'suppressed'", source)
        self.assertIn("WHERE event_type = 'match_result'", source)
        self.assertIn("sent_at < %s", source)

    def test_correction_does_not_create_second_event(self):
        source = inspect.getsource(scoring_recalculation_service._enqueue_result_event)
        self.assertIn("DO NOTHING", source)


if __name__ == "__main__":
    unittest.main()
