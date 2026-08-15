#!/usr/bin/env python3
"""Explicit idempotent migration for business push delivery idempotency."""

from pathlib import Path
import logging
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import close_db, get_db


DDL = (
    """
    CREATE TABLE IF NOT EXISTS push_delivery_log (
        id BIGSERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        match_id INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
        event_type TEXT NOT NULL,
        event_key TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        sent_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        delivered_at TIMESTAMPTZ NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_error TEXT NULL,
        CONSTRAINT push_delivery_log_event_key UNIQUE (user_id, event_type, event_key)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_push_delivery_log_match ON push_delivery_log(match_id)",
    "CREATE INDEX IF NOT EXISTS idx_push_delivery_log_user ON push_delivery_log(user_id)",
)


def migrate():
    conn = get_db()
    cur = conn.cursor()
    try:
        for statement in DDL:
            cur.execute(statement)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        close_db(conn, cur)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        migrate()
    except Exception as exc:
        logging.error("push_delivery_log migration failed: %s", type(exc).__name__)
        sys.exit(1)
    print("push_delivery_log migration completed")
