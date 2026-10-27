#!/usr/bin/env python3
"""Owner-operated personal-data export/anonymization. Never expose passwords/keys.

Run inside the application container. Anonymization preserves anonymous match
history and points; execute only for a verified request from the account owner.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import get_db, close_db
from werkzeug.security import generate_password_hash


def export_user(cur, user_id):
    cur.execute("SELECT id, username, last_seen, is_deleted FROM users WHERE id = %s", (user_id,))
    user = cur.fetchone()
    if not user:
        raise ValueError("user_not_found")
    result = {"user": dict(zip(("id", "username", "last_seen", "deactivated"), user))}
    for name, query, columns in (
        ("predictions", "SELECT match_id, tournament_id, home_goals, away_goals, points FROM predictions WHERE user_id = %s ORDER BY match_id", ("match_id", "tournament_id", "home_goals", "away_goals", "points")),
        ("titles", "SELECT title, awarded_at FROM user_titles WHERE user_id = %s ORDER BY awarded_at", ("title", "awarded_at")),
        ("notifications", "SELECT endpoint, enabled FROM push_subscriptions WHERE user_id = %s ORDER BY id", ("endpoint", "enabled")),
    ):
        cur.execute(query, (user_id,))
        result[name] = [dict(zip(columns, row)) for row in cur.fetchall()]
    return result


def anonymize_user(cur, user_id):
    cur.execute("SELECT is_admin FROM users WHERE id = %s FOR UPDATE", (user_id,))
    user = cur.fetchone()
    if not user or user[0]:
        raise ValueError("active_non_admin_target_required")
    for table in ("auth_sessions", "push_subscriptions", "push_test_cooldowns", "user_titles"):
        cur.execute(f"DELETE FROM {table} WHERE user_id = %s", (user_id,))
    cur.execute(
        "UPDATE users SET username = %s, password = %s, last_seen = NULL, is_deleted = 1 WHERE id = %s",
        (f"deleted_{user_id}_{secrets.token_hex(8)}", generate_password_hash(secrets.token_urlsafe(48)), user_id),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("export", "anonymize"))
    parser.add_argument("user_id", type=int)
    parser.add_argument("--output")
    parser.add_argument("--confirm")
    args = parser.parse_args()
    if args.user_id <= 0:
        parser.error("positive user id required")
    if args.action == "export" and not args.output:
        parser.error("export requires --output; private data is never printed")
    if args.action == "anonymize" and args.confirm != f"ANONYMIZE {args.user_id}":
        parser.error("explicit --confirm 'ANONYMIZE USER_ID' required")
    conn = get_db()
    cur = conn.cursor()
    try:
        if args.action == "export":
            conn.set_session(readonly=True)
            result = export_user(cur, args.user_id)
            content = json.dumps(result, ensure_ascii=False, default=str, indent=2)
            fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
            conn.rollback()
        else:
            anonymize_user(cur, args.user_id)
            conn.commit()
        print("USER DATA OPERATION OK; external analytics and backup retention require separate follow-through")
        return 0
    except Exception:
        conn.rollback()
        print("USER DATA OPERATION FAILED; no private data printed", file=sys.stderr)
        return 1
    finally:
        close_db(conn, cur)


if __name__ == "__main__":
    raise SystemExit(main())
