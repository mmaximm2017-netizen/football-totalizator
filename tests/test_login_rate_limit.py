from datetime import datetime, timedelta, timezone

from app.services import login_rate_limit_service as service


class FakeCursor:
    def __init__(self):
        self.rows = {}
        self.current = None

    def execute(self, query, params=None):
        params = params or ()
        normalized = " ".join(query.split())

        if "pg_advisory_xact_lock" in normalized:
            self.current = (None,)
            return

        if normalized.startswith("SELECT blocked_until"):
            row = self.rows.get(params[0])
            self.current = (row["blocked_until"],) if row else None
            return

        if normalized.startswith("SELECT failure_count"):
            row = self.rows.get(params[0])
            self.current = (
                row["failure_count"],
                row["window_started_at"],
                row["blocked_until"],
            ) if row else None
            return

        if normalized.startswith("INSERT INTO login_rate_limits"):
            key_hash, failure_count, window_started_at, blocked_until, updated_at = params
            self.rows[key_hash] = {
                "failure_count": failure_count,
                "window_started_at": window_started_at,
                "blocked_until": blocked_until,
                "updated_at": updated_at,
            }
            self.current = None
            return

        if normalized.startswith("UPDATE login_rate_limits"):
            failure_count, window_started_at, blocked_until, updated_at, key_hash = params
            self.rows[key_hash] = {
                "failure_count": failure_count,
                "window_started_at": window_started_at,
                "blocked_until": blocked_until,
                "updated_at": updated_at,
            }
            self.current = None
            return

        if normalized.startswith("DELETE FROM login_rate_limits"):
            self.rows.pop(params[0], None)
            self.current = None
            return

        raise AssertionError(f"Unexpected SQL: {normalized}")

    def fetchone(self):
        return self.current


BASE = datetime(2026, 9, 3, 9, 0, tzinfo=timezone.utc)


def test_blocks_after_five_failures():
    cur = FakeCursor()

    for index in range(service.MAX_FAILED_ATTEMPTS - 1):
        assert service.record_login_failure(
            cur,
            "203.0.113.1",
            "Max",
            now=BASE + timedelta(seconds=index),
        ) is False

    assert service.record_login_failure(
        cur,
        "203.0.113.1",
        "Max",
        now=BASE + timedelta(seconds=4),
    ) is True
    assert service.is_login_blocked(
        cur,
        "203.0.113.1",
        "Max",
        now=BASE + timedelta(seconds=5),
    ) is True


def test_block_expires():
    cur = FakeCursor()

    for index in range(service.MAX_FAILED_ATTEMPTS):
        service.record_login_failure(
            cur,
            "203.0.113.1",
            "Max",
            now=BASE + timedelta(seconds=index),
        )

    assert service.is_login_blocked(
        cur,
        "203.0.113.1",
        "Max",
        now=BASE + timedelta(seconds=4 + service.BLOCK_SECONDS + 1),
    ) is False


def test_success_clears_failures():
    cur = FakeCursor()

    service.record_login_failure(cur, "203.0.113.1", "Max", now=BASE)
    service.clear_login_failures(cur, "203.0.113.1", "Max")

    assert service.is_login_blocked(
        cur,
        "203.0.113.1",
        "Max",
        now=BASE + timedelta(seconds=1),
    ) is False


def test_different_users_do_not_block_each_other():
    cur = FakeCursor()

    for index in range(service.MAX_FAILED_ATTEMPTS):
        service.record_login_failure(
            cur,
            "203.0.113.1",
            "Max",
            now=BASE + timedelta(seconds=index),
        )

    assert service.is_login_blocked(cur, "203.0.113.1", "Max", now=BASE + timedelta(seconds=5))
    assert not service.is_login_blocked(cur, "203.0.113.1", "Alex", now=BASE + timedelta(seconds=5))


def test_different_addresses_do_not_block_each_other():
    cur = FakeCursor()

    for index in range(service.MAX_FAILED_ATTEMPTS):
        service.record_login_failure(
            cur,
            "203.0.113.1",
            "Max",
            now=BASE + timedelta(seconds=index),
        )

    assert service.is_login_blocked(cur, "203.0.113.1", "Max", now=BASE + timedelta(seconds=5))
    assert not service.is_login_blocked(cur, "203.0.113.2", "Max", now=BASE + timedelta(seconds=5))


def test_old_failures_are_forgotten():
    cur = FakeCursor()

    service.record_login_failure(cur, "203.0.113.1", "Max", now=BASE)
    later = BASE + timedelta(seconds=service.FAILURE_WINDOW_SECONDS + 1)

    for offset in range(service.MAX_FAILED_ATTEMPTS - 1):
        assert service.record_login_failure(
            cur,
            "203.0.113.1",
            "Max",
            now=later + timedelta(seconds=offset),
        ) is False

    assert not service.is_login_blocked(
        cur,
        "203.0.113.1",
        "Max",
        now=later + timedelta(seconds=service.MAX_FAILED_ATTEMPTS),
    )


def test_key_is_hashed_before_storage():
    cur = FakeCursor()
    service.record_login_failure(cur, "203.0.113.1", "Max", now=BASE)

    stored_key = next(iter(cur.rows))
    assert stored_key != "203.0.113.1"
    assert "max" not in stored_key
    assert len(stored_key) == 64
