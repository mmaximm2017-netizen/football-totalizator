"""Database-backed guard against repeated failed login attempts."""

import hashlib
from datetime import datetime, timedelta, timezone


MAX_FAILED_ATTEMPTS = 5
FAILURE_WINDOW_SECONDS = 10 * 60
BLOCK_SECONDS = 15 * 60


def _key(remote_addr, username):
    return ((remote_addr or "unknown").strip(), (username or "").strip().casefold())


def _key_hash(remote_addr, username):
    remote, normalized_username = _key(remote_addr, username)
    value = f"{remote}\0{normalized_username}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _now(value=None):
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def is_login_blocked(cur, remote_addr, username, *, now=None):
    now = _now(now)
    cur.execute(
        """
        SELECT blocked_until
        FROM login_rate_limits
        WHERE key_hash = %s
        """,
        (_key_hash(remote_addr, username),),
    )
    row = cur.fetchone()
    return bool(row and row[0] and row[0] > now)


def record_login_failure(cur, remote_addr, username, *, now=None):
    now = _now(now)
    key_hash = _key_hash(remote_addr, username)

    # Serialize updates for one login identity across all Gunicorn workers.
    cur.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (key_hash,),
    )
    cur.execute(
        """
        SELECT failure_count, window_started_at, blocked_until
        FROM login_rate_limits
        WHERE key_hash = %s
        FOR UPDATE
        """,
        (key_hash,),
    )
    row = cur.fetchone()

    if row and row[2] and row[2] > now:
        return True

    window_cutoff = now - timedelta(seconds=FAILURE_WINDOW_SECONDS)
    if not row or not row[1] or row[1] <= window_cutoff:
        failure_count = 1
        window_started_at = now
    else:
        failure_count = int(row[0] or 0) + 1
        window_started_at = row[1]

    blocked_until = (
        now + timedelta(seconds=BLOCK_SECONDS)
        if failure_count >= MAX_FAILED_ATTEMPTS
        else None
    )

    if row:
        cur.execute(
            """
            UPDATE login_rate_limits
            SET failure_count = %s,
                window_started_at = %s,
                blocked_until = %s,
                updated_at = %s
            WHERE key_hash = %s
            """,
            (
                failure_count,
                window_started_at,
                blocked_until,
                now,
                key_hash,
            ),
        )
    else:
        cur.execute(
            """
            INSERT INTO login_rate_limits
                (key_hash, failure_count, window_started_at, blocked_until, updated_at)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                key_hash,
                failure_count,
                window_started_at,
                blocked_until,
                now,
            ),
        )

    return blocked_until is not None


def clear_login_failures(cur, remote_addr, username):
    cur.execute(
        "DELETE FROM login_rate_limits WHERE key_hash = %s",
        (_key_hash(remote_addr, username),),
    )
