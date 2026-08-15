"""Selection and delivery workflow for scored match-result notifications."""

import os
from datetime import datetime, timedelta, timezone

from app.db import close_db, get_db
from app.services.web_push_service import (
    delivery_error_status,
    disable_expired_subscription,
    get_enabled_subscriptions_for_users,
    send_push,
)


EVENT_TYPE = "match_result"
RETRY_AFTER_MINUTES = 15
BOOTSTRAP_CUTOFF_ENV = "TOTISH_MATCH_RESULT_PUSH_SINCE"


def normalize_now(value=None):
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("--now must include a timezone offset")
    return value.astimezone(timezone.utc)


def normalize_since(value=None):
    if value is None:
        value = os.getenv(BOOTSTRAP_CUTOFF_ENV)
    if not value:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("--since must include a timezone offset")
    return value.astimezone(timezone.utc)


def select_match_result_candidates(cur, now, since=None):
    """Return one candidate per user/match from the scored-result outbox."""
    stale_cutoff = now - timedelta(minutes=RETRY_AFTER_MINUTES)
    since_clause = ""
    params = [EVENT_TYPE, stale_cutoff]
    if since is not None:
        since_clause = "AND d.sent_at >= %s"
        params.append(since)

    cur.execute(
        f"""
        SELECT d.match_id,
               m.tournament_id,
               m.home_team,
               m.away_team,
               m.home_score,
               m.away_score,
               p.user_id,
               p.home_goals,
               p.away_goals,
               p.points
        FROM push_delivery_log d
        JOIN matches m
          ON m.id = d.match_id
        JOIN predictions p
          ON p.match_id = m.id
         AND p.tournament_id = m.tournament_id
         AND p.user_id = d.user_id
        JOIN users u
          ON u.id = p.user_id
         AND COALESCE(u.is_admin, 0) = 0
         AND COALESCE(u.is_deleted, 0) = 0
        JOIN push_subscriptions ps
          ON ps.user_id = p.user_id
         AND ps.enabled = TRUE
        WHERE d.event_type = %s
          AND d.event_key = 'match:' || m.id::text
          AND (
                d.status = 'ready'
                OR (
                    d.status IN ('failed', 'pending')
                    AND d.updated_at <= %s
                   )
              )
          AND UPPER(COALESCE(m.status, '')) = 'FINISHED'
          AND m.home_score BETWEEN 0 AND 99
          AND m.away_score BETWEEN 0 AND 99
          AND p.points IS NOT NULL
          {since_clause}
        GROUP BY d.match_id, m.tournament_id, m.home_team, m.away_team,
                 m.home_score, m.away_score, p.user_id, p.home_goals,
                 p.away_goals, p.points
        ORDER BY d.match_id, p.user_id
        """,
        tuple(params),
    )
    return [
        {
            "match_id": row[0],
            "tournament_id": row[1],
            "home_team": row[2],
            "away_team": row[3],
            "home_score": row[4],
            "away_score": row[5],
            "user_id": row[6],
            "predicted_home": row[7],
            "predicted_away": row[8],
            "points": row[9],
            "event_type": EVENT_TYPE,
            "event_key": f"match:{row[0]}",
        }
        for row in cur.fetchall()
    ]


def claim_delivery(cur, candidate, now):
    """Atomically move one ready/retryable outbox event to pending."""
    stale_cutoff = now - timedelta(minutes=RETRY_AFTER_MINUTES)
    cur.execute(
        """
        UPDATE push_delivery_log
        SET status = 'pending', sent_at = %s, updated_at = %s, last_error = NULL
        WHERE user_id = %s
          AND match_id = %s
          AND event_type = %s
          AND event_key = %s
          AND (
                status = 'ready'
                OR (
                    status IN ('failed', 'pending')
                    AND updated_at <= %s
                   )
              )
        RETURNING id
        """,
        (
            now,
            now,
            candidate["user_id"],
            candidate["match_id"],
            candidate["event_type"],
            candidate["event_key"],
            stale_cutoff,
        ),
    )
    return bool(cur.fetchone())


def mark_delivery(cur, candidate, status, now, error=None):
    cur.execute(
        """
        UPDATE push_delivery_log
        SET status = %s,
            updated_at = %s,
            delivered_at = CASE WHEN %s = 'sent' THEN %s ELSE delivered_at END,
            last_error = %s
        WHERE user_id = %s
          AND match_id = %s
          AND event_type = %s
          AND event_key = %s
        """,
        (
            status,
            now,
            status,
            now,
            error,
            candidate["user_id"],
            candidate["match_id"],
            candidate["event_type"],
            candidate["event_key"],
        ),
    )


def points_word(points):
    points = abs(int(points))
    if 11 <= points % 100 <= 14:
        return "очков"
    last_digit = points % 10
    if last_digit == 1:
        return "очко"
    if last_digit in (2, 3, 4):
        return "очка"
    return "очков"


def build_match_result_payload(candidate):
    points = int(candidate["points"])
    return {
        "title": "ТОТИШ",
        "body": (
            f"«{candidate['home_team']} — {candidate['away_team']}» завершён: "
            f"{candidate['home_score']}:{candidate['away_score']}. "
            f"Ваш прогноз: {candidate['predicted_home']}:{candidate['predicted_away']}. "
            f"Начислено: {points} {points_word(points)} ⚽"
        ),
        "url": f"/?tid={candidate['tournament_id']}",
        "tag": f"result-{candidate['match_id']}",
    }


def run_once(*, now=None, since=None, dry_run=False, sender=None):
    now = normalize_now(now)
    since = normalize_since(since)
    if since is None:
        raise RuntimeError(f"{BOOTSTRAP_CUTOFF_ENV} must be configured")
    if sender is None:
        sender = send_push

    conn = get_db()
    cur = conn.cursor()
    try:
        candidates = select_match_result_candidates(cur, now, since)
    finally:
        close_db(conn, cur)

    if dry_run:
        return {
            "candidates": candidates,
            "matches": len({c["match_id"] for c in candidates}),
            "users": len({c["user_id"] for c in candidates}),
            "would_send": len(candidates),
        }

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
        return {
            "candidates": candidates,
            "matches": len({c["match_id"] for c in candidates}),
            "claimed": 0,
            "sent": 0,
            "expired": 0,
            "failed": 0,
        }

    conn = get_db()
    cur = conn.cursor()
    try:
        subscriptions = get_enabled_subscriptions_for_users(cur, user_ids)
    finally:
        close_db(conn, cur)

    expired_endpoints = []
    outcomes = []
    expired = 0
    sent = 0
    failed = 0
    for candidate in claimed:
        user_sent = 0
        user_expired = 0
        user_failed = 0
        for subscription in subscriptions.get(candidate["user_id"], []):
            try:
                sender(subscription, build_match_result_payload(candidate))
                user_sent += 1
            except Exception as exc:
                if delivery_error_status(exc) in (404, 410):
                    user_expired += 1
                    expired += 1
                    expired_endpoints.append(subscription["endpoint"])
                else:
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
        for candidate, user_sent, user_expired, user_failed in outcomes:
            if user_sent:
                sent += 1
                mark_delivery(cur, candidate, "sent", now)
            else:
                failed += 1
                mark_delivery(
                    cur,
                    candidate,
                    "failed",
                    now,
                    f"failed={user_failed},expired={user_expired}",
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        close_db(conn, cur)

    return {
        "candidates": candidates,
        "matches": len({c["match_id"] for c in candidates}),
        "claimed": len(claimed),
        "sent": sent,
        "expired": expired,
        "failed": failed,
    }
