#!/usr/bin/env python3
"""Suppress pre-rollout result events before enabling the result cron."""

import argparse
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import close_db, get_db


def parse_datetime(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def bootstrap(since):
    if since.tzinfo is None:
        raise ValueError("--since must include a timezone offset")
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO push_delivery_log
                (user_id, match_id, event_type, event_key, status,
                 sent_at, delivered_at, updated_at, last_error)
            SELECT p.user_id,
                   p.match_id,
                   'match_result',
                   'match:' || p.match_id::text,
                   'suppressed',
                   CURRENT_TIMESTAMP,
                   CURRENT_TIMESTAMP,
                   CURRENT_TIMESTAMP,
                   'bootstrap_before_match_result_push_rollout'
            FROM predictions p
            JOIN matches m
              ON m.id = p.match_id
             AND m.tournament_id = p.tournament_id
            WHERE UPPER(COALESCE(m.status, '')) = 'FINISHED'
            ON CONFLICT (user_id, event_type, event_key) DO NOTHING
            """
        )
        inserted = cur.rowcount
        cur.execute(
            """
            UPDATE push_delivery_log
            SET status = 'suppressed',
                delivered_at = COALESCE(delivered_at, CURRENT_TIMESTAMP),
                updated_at = CURRENT_TIMESTAMP,
                last_error = 'bootstrap_before_match_result_push_rollout'
            WHERE event_type = 'match_result'
              AND status IN ('ready', 'pending', 'failed')
              AND sent_at < %s
            """,
            (since,),
        )
        suppressed = cur.rowcount
        conn.commit()
        return {"inserted": inserted, "suppressed": suppressed}
    except Exception:
        conn.rollback()
        raise
    finally:
        close_db(conn, cur)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", required=True, type=parse_datetime)
    args = parser.parse_args()
    result = bootstrap(args.since)
    print(f"inserted={result['inserted']} suppressed={result['suppressed']}")


if __name__ == "__main__":
    main()
