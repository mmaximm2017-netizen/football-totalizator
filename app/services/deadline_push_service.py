"""Selection and delivery workflow for two-hour deadline notifications."""

from datetime import datetime, timedelta, timezone

from app.db import close_db, get_db
from app.services.web_push_service import (
    delivery_error_status,
    disable_expired_subscription,
    get_enabled_subscriptions_for_users,
    send_push,
)


EVENT_TYPE = "deadline_2h"
WINDOW_MINUTES = 115
WINDOW_END_MINUTES = 120
RETRY_AFTER_MINUTES = 15
EXCLUDED_STATUSES = (
    "FINISHED", "COMPLETE", "COMPLETED", "CANCELLED", "POSTPONED",
    "SUSPENDED", "LIVE", "IN_PLAY", "PAUSED", "HALFTIME", "ABANDONED",
)


def normalize_now(value=None):
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("--now must include a timezone offset")
    return value.astimezone(timezone.utc)


def select_deadline_candidates(cur, now):
    """Return one row per eligible user/match pair, without sending or writing."""
    window_start = now + timedelta(minutes=WINDOW_MINUTES)
    window_end = now + timedelta(minutes=WINDOW_END_MINUTES)
    stale_claim_cutoff = now - timedelta(minutes=RETRY_AFTER_MINUTES)
    cur.execute(
        """
        SELECT m.id, m.home_team, m.away_team, m.deadline, u.id
        FROM matches m
        JOIN tournaments t ON t.id = m.tournament_id AND t.is_active = 1
        JOIN users u ON COALESCE(u.is_admin, 0) = 0
                       AND COALESCE(u.is_deleted, 0) = 0
        JOIN push_subscriptions ps ON ps.user_id = u.id AND ps.enabled = TRUE
        WHERE m.deadline > %s
          AND (
              (m.deadline >= %s AND m.deadline <= %s)
              OR EXISTS (
                  SELECT 1 FROM push_delivery_log d_retry
                  WHERE d_retry.user_id = u.id
                    AND d_retry.event_type = %s
                    AND d_retry.event_key = 'match:' || m.id::text
                    AND d_retry.status IN ('failed', 'pending')
                    AND d_retry.updated_at <= %s
              )
          )
          AND UPPER(COALESCE(m.status, 'SCHEDULED')) <> ALL(%s)
          AND NOT EXISTS (
              SELECT 1 FROM predictions p
              WHERE p.user_id = u.id
                AND p.match_id = m.id
                AND p.tournament_id = m.tournament_id
          )
          AND NOT EXISTS (
              SELECT 1 FROM push_delivery_log d
              WHERE d.user_id = u.id
                AND d.event_type = %s
                AND d.event_key = 'match:' || m.id::text
                AND (
                    d.status = 'sent'
                    OR (d.status = 'pending' AND d.updated_at > %s)
                    OR (d.status = 'failed' AND d.updated_at > %s)
                )
          )
        GROUP BY m.id, m.home_team, m.away_team, m.deadline, u.id
        ORDER BY m.deadline, m.id, u.id
        """,
        (
            now,
            window_start,
            window_end,
            EVENT_TYPE,
            stale_claim_cutoff,
            list(EXCLUDED_STATUSES),
            EVENT_TYPE,
            stale_claim_cutoff,
            stale_claim_cutoff,
        ),
    )
    return [
        {
            "match_id": row[0],
            "home_team": row[1],
            "away_team": row[2],
            "deadline": row[3],
            "user_id": row[4],
            "event_type": EVENT_TYPE,
            "event_key": f"match:{row[0]}",
        }
        for row in cur.fetchall()
    ]


def claim_delivery(cur, candidate, now):
    """Claim an event atomically; concurrent workers cannot both claim it."""
    cur.execute(
        """
        INSERT INTO push_delivery_log
            (user_id, match_id, event_type, event_key, status, sent_at, updated_at)
        VALUES (%s, %s, %s, %s, 'pending', %s, %s)
        ON CONFLICT (user_id, event_type, event_key) DO NOTHING
        RETURNING id
        """,
        (
            candidate["user_id"], candidate["match_id"], candidate["event_type"],
            candidate["event_key"], now, now,
        ),
    )
    if cur.fetchone():
        return True

    cur.execute(
        """
        UPDATE push_delivery_log
        SET status = 'pending', sent_at = %s, updated_at = %s, last_error = NULL
        WHERE user_id = %s AND event_type = %s AND event_key = %s
          AND status IN ('failed', 'pending')
          AND updated_at <= %s - (%s * INTERVAL '1 minute')
        RETURNING id
        """,
        (
            now, now, candidate["user_id"], candidate["event_type"],
            candidate["event_key"], now, RETRY_AFTER_MINUTES,
        ),
    )
    return bool(cur.fetchone())


def mark_delivery(cur, candidate, status, now, error=None):
    cur.execute(
        """
        UPDATE push_delivery_log
        SET status = %s, updated_at = %s, delivered_at = CASE WHEN %s = 'sent' THEN %s ELSE delivered_at END,
            last_error = %s
        WHERE user_id = %s AND event_type = %s AND event_key = %s
        """,
        (
            status, now, status, now, error,
            candidate["user_id"], candidate["event_type"], candidate["event_key"],
        ),
    )


def build_deadline_payload(candidate):
    return {
        "title": "ТОТИШ",
        "body": (
            f"До дедлайна матча «{candidate['home_team']} — "
            f"{candidate['away_team']}» осталось 2 часа. "
            "Прогноз ещё не сделан ⚽"
        ),
        "url": "/",
        "tag": f"deadline-2h-{candidate['match_id']}",
    }


def run_once(*, now=None, dry_run=False, sender=send_push):
    now = normalize_now(now)
    conn = get_db()
    cur = conn.cursor()
    try:
        candidates = select_deadline_candidates(cur, now)
    finally:
        close_db(conn, cur)

    if dry_run:
        return {"candidates": candidates, "matches": len({c["match_id"] for c in candidates}), "would_send": len(candidates)}

    conn = get_db()
    cur = conn.cursor()
    try:
        claimed = [candidate for candidate in candidates if claim_delivery(cur, candidate, now)]
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        close_db(conn, cur)

    user_ids = {candidate["user_id"] for candidate in claimed}
    if not user_ids:
        return {"candidates": candidates, "claimed": 0, "sent": 0, "expired": 0, "failed": 0}

    conn = get_db()
    cur = conn.cursor()
    try:
        subscriptions = get_enabled_subscriptions_for_users(cur, user_ids)
    finally:
        close_db(conn, cur)

    expired_endpoints = []
    sent = expired = failed = 0
    outcomes = []
    for candidate in claimed:
        user_sent = 0
        user_expired = 0
        user_failed = 0
        for subscription in subscriptions.get(candidate["user_id"], []):
            try:
                sender(subscription, build_deadline_payload(candidate))
                sent += 1
                user_sent += 1
            except Exception as exc:
                if delivery_error_status(exc) in (404, 410):
                    expired += 1
                    user_expired += 1
                    expired_endpoints.append(subscription["endpoint"])
                else:
                    failed += 1
                    user_failed += 1
        outcomes.append((candidate, user_sent, user_expired, user_failed))

    if expired_endpoints:
        conn = get_db()
        cur = conn.cursor()
        try:
            for endpoint in expired_endpoints:
                disable_expired_subscription(cur, endpoint)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            close_db(conn, cur)

    conn = get_db()
    cur = conn.cursor()
    try:
        for candidate, user_sent, _user_expired, user_failed in outcomes:
            if user_sent:
                mark_delivery(cur, candidate, "sent", now)
            else:
                mark_delivery(cur, candidate, "failed", now, f"failed={user_failed}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        close_db(conn, cur)

    return {
        "candidates": candidates,
        "claimed": len(claimed),
        "sent": sent,
        "expired": expired,
        "failed": failed,
    }
