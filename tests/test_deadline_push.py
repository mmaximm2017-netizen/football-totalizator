import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.services import deadline_push_service as service
from app.services import web_push_service
from scripts.send_deadline_pushes import parse_now


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

    @property
    def closed(self):
        return False

    def close(self):
        pass


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

    @property
    def closed(self):
        return False


class DeadlinePushTests(unittest.TestCase):
    def test_normalize_now_converts_to_utc(self):
        value = datetime(2026, 8, 15, 13, 0, tzinfo=timezone(timedelta(hours=3)))
        self.assertEqual(service.normalize_now(value), datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc))

    def test_naive_now_is_rejected(self):
        with self.assertRaises(ValueError):
            service.normalize_now(datetime(2026, 8, 15, 10, 0))

    def test_window_is_exactly_115_to_120_minutes(self):
        now = datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc)
        cursor = Cursor(rows=[])
        service.select_deadline_candidates(cursor, now)
        params = cursor.executed[0][1]
        self.assertEqual(params[0], now + timedelta(minutes=115))
        self.assertEqual(params[1], now + timedelta(minutes=120))

    def test_window_has_five_minute_width(self):
        self.assertEqual(service.WINDOW_END_MINUTES - service.WINDOW_MINUTES, 5)

    def test_window_excludes_late_match_by_query_bounds(self):
        now = datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc)
        cursor = Cursor(rows=[])
        service.select_deadline_candidates(cursor, now)
        params = cursor.executed[0][1]
        self.assertGreater(now + timedelta(minutes=121), params[1])

    def test_window_excludes_early_match_by_query_bounds(self):
        now = datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc)
        cursor = Cursor(rows=[])
        service.select_deadline_candidates(cursor, now)
        params = cursor.executed[0][1]
        self.assertLess(now + timedelta(minutes=114), params[0])

    def test_fixed_payload_and_safe_internal_url(self):
        candidate = {"match_id": 123, "home_team": "Зенит", "away_team": "Спартак"}
        payload = service.build_deadline_payload(candidate)
        self.assertEqual(payload["title"], "ТОТИШ")
        self.assertEqual(payload["url"], "/")
        self.assertEqual(payload["tag"], "deadline-2h-123")
        self.assertIn("Зенит — Спартак", payload["body"])

    def test_payload_body_has_fixed_missing_prediction_text(self):
        payload = service.build_deadline_payload({"match_id": 1, "home_team": "A", "away_team": "B"})
        self.assertTrue(payload["body"].endswith("Прогноз ещё не сделан ⚽"))

    def test_payload_tag_is_match_scoped(self):
        payload = service.build_deadline_payload({"match_id": 77, "home_team": "A", "away_team": "B"})
        self.assertEqual(payload["tag"], "deadline-2h-77")

    def test_dry_run_does_not_claim_or_send(self):
        now = datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc)
        row = (123, "Зенит", "Спартак", now + timedelta(minutes=117), 7)
        cursor = Cursor(rows=[row])
        conn = Connection(cursor)
        with patch.object(service, "get_db", return_value=conn), patch.object(service, "close_db"), \
             patch.object(service, "send_push") as sender:
            result = service.run_once(now=now, dry_run=True)
        sender.assert_not_called()
        self.assertEqual(result["would_send"], 1)
        self.assertEqual(conn.commits, 0)

    def test_candidate_query_excludes_prediction_finished_and_delivered(self):
        cursor = Cursor()
        service.select_deadline_candidates(cursor, datetime.now(timezone.utc))
        query = cursor.executed[0][0]
        self.assertIn("NOT EXISTS", query)
        self.assertIn("predictions", query)
        self.assertIn("push_delivery_log", query)
        self.assertIn("FINISHED", query)
        self.assertIn("is_admin", query)
        self.assertIn("updated_at", query)
        self.assertIn("GROUP BY m.id", query)
        self.assertIn("ps.enabled = TRUE", query)
        self.assertIn("t.is_active = 1", query)
        self.assertIn("COALESCE(u.is_deleted, 0) = 0", query)

    def test_candidate_query_excludes_cancelled_and_live_states(self):
        cursor = Cursor()
        service.select_deadline_candidates(cursor, datetime.now(timezone.utc))
        query = cursor.executed[0][0]
        self.assertIn("CANCELLED", query)
        self.assertIn("LIVE", query)
        self.assertIn("ABANDONED", query)

    def test_candidate_query_retries_only_stale_pending_claims(self):
        cursor = Cursor()
        service.select_deadline_candidates(cursor, datetime.now(timezone.utc))
        query = cursor.executed[0][0]
        self.assertIn("d.status = 'sent'", query)
        self.assertIn("d.status = 'pending'", query)
        self.assertIn("d.status = 'failed'", query)

    def test_duplicate_claim_uses_unique_event_key(self):
        cursor = Cursor(claims=[None, None])
        candidate = {"user_id": 7, "match_id": 123, "event_type": "deadline_2h", "event_key": "match:123"}
        self.assertFalse(service.claim_delivery(cursor, candidate, datetime.now(timezone.utc)))
        self.assertIn("ON CONFLICT (user_id, event_type, event_key)", cursor.executed[0][0])
        self.assertIn("status IN ('failed', 'pending')", cursor.executed[1][0])

    def test_claim_insert_is_pending(self):
        cursor = Cursor(claims=[(9,)])
        candidate = {"user_id": 7, "match_id": 123, "event_type": "deadline_2h", "event_key": "match:123"}
        self.assertTrue(service.claim_delivery(cursor, candidate, datetime.now(timezone.utc)))
        self.assertIn("'pending'", cursor.executed[0][0])

    def test_mark_sent_records_delivery_time(self):
        cursor = Cursor()
        candidate = {"user_id": 7, "event_type": "deadline_2h", "event_key": "match:123"}
        service.mark_delivery(cursor, candidate, "sent", datetime.now(timezone.utc))
        self.assertIn("delivered_at", cursor.executed[0][0])

    def test_mark_failed_keeps_event_retryable(self):
        cursor = Cursor()
        candidate = {"user_id": 7, "event_type": "deadline_2h", "event_key": "match:123"}
        service.mark_delivery(cursor, candidate, "failed", datetime.now(timezone.utc), "failed=1")
        self.assertIn("last_error", cursor.executed[0][0])

    def test_enabled_subscription_batch_groups_by_user(self):
        cursor = Cursor(rows=[(7, "https://one", "p1", "a1"), (7, "https://two", "p2", "a2")])
        result = web_push_service.get_enabled_subscriptions_for_users(cursor, {7})
        self.assertEqual(len(result[7]), 2)
        self.assertEqual(result[7][0]["keys"]["p256dh"], "p1")

    def test_enabled_subscription_batch_empty_ids_does_not_query(self):
        cursor = Cursor()
        self.assertEqual(web_push_service.get_enabled_subscriptions_for_users(cursor, set()), {})
        self.assertEqual(cursor.executed, [])

    def test_dry_run_reports_match_and_user_counts(self):
        now = datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc)
        rows = [(1, "A", "B", now + timedelta(minutes=117), 7), (2, "C", "D", now + timedelta(minutes=118), 7)]
        conn = Connection(Cursor(rows=rows))
        with patch.object(service, "get_db", return_value=conn), patch.object(service, "close_db"):
            result = service.run_once(now=now, dry_run=True)
        self.assertEqual(result["matches"], 2)
        self.assertEqual(result["would_send"], 2)

    def test_dry_run_does_not_disable_or_claim(self):
        now = datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc)
        conn = Connection(Cursor(rows=[]))
        with patch.object(service, "get_db", return_value=conn), patch.object(service, "close_db"), \
             patch.object(service, "disable_expired_subscription") as disable:
            service.run_once(now=now, dry_run=True)
        disable.assert_not_called()
        self.assertEqual(conn.commits, 0)

    def test_parse_now_accepts_explicit_timezone(self):
        self.assertEqual(parse_now("2026-08-15T13:00:00+03:00").utcoffset(), timedelta(hours=3))

    def test_parse_now_accepts_zulu(self):
        self.assertEqual(parse_now("2026-08-15T10:00:00Z").tzinfo, timezone.utc)

    def test_migration_has_idempotent_table_and_indexes(self):
        from scripts import migrate_push_delivery_log
        self.assertIn("CREATE TABLE IF NOT EXISTS", migrate_push_delivery_log.DDL[0])
        self.assertIn("UNIQUE (user_id, event_type, event_key)", migrate_push_delivery_log.DDL[0])
        self.assertTrue(all("IF NOT EXISTS" in statement for statement in migrate_push_delivery_log.DDL))


if __name__ == "__main__":
    unittest.main()
