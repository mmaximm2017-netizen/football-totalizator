#!/usr/bin/env python3
"""Explicit idempotent migration for test-push cooldown state."""

from pathlib import Path
import logging
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import close_db, get_db


DDL = """
CREATE TABLE IF NOT EXISTS push_test_cooldowns (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    last_sent_at TIMESTAMPTZ NOT NULL
)
"""


def migrate():
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(DDL)
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
        logging.error("push_test_cooldowns migration failed: %s", type(exc).__name__)
        sys.exit(1)
    print("push_test_cooldowns migration completed")
