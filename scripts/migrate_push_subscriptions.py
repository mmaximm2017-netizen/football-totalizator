#!/usr/bin/env python3
"""Idempotent, explicit migration for Web Push subscriptions."""

import logging
from pathlib import Path
import sys

# Make ``python scripts/migrate_push_subscriptions.py`` work from any cwd.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import close_db, get_db


DDL = (
    """
    CREATE TABLE IF NOT EXISTS push_subscriptions (
        id BIGSERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        endpoint TEXT NOT NULL,
        p256dh TEXT NOT NULL,
        auth TEXT NOT NULL,
        user_agent TEXT NULL,
        device_label TEXT NULL,
        enabled BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT push_subscriptions_endpoint_key UNIQUE (endpoint)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_push_subscriptions_user ON push_subscriptions(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_push_subscriptions_enabled ON push_subscriptions(enabled)",
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
        logging.error("push_subscriptions migration failed: %s", type(exc).__name__)
        sys.exit(1)
    print("push_subscriptions migration completed")
