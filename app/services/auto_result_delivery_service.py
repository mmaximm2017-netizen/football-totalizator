"""Durable diagnostics and Telegram outbox, separate from user result pushes."""
import hashlib
import json
import uuid
from contextlib import contextmanager
from pathlib import Path

from app.db import close_db, get_db

WORKER_LOCK_KEY = 847_202_610


def match_identity(match):
    # Include all fields used by the finalizer; old observations cannot explain
    # a rescheduled or renamed match. JSON state is never a write authority.
    values = [match[key] for key in (
        "id", "tournament_id", "league", "home_team", "away_team",
        "kickoff_time", "match_category",
    )]
    return hashlib.sha256(json.dumps(values, default=str).encode()).hexdigest()


@contextmanager
def worker_lock():
    conn = get_db()
    cur = conn.cursor()
    try:
        # Transaction-scoped lock also works through transaction-mode PgBouncer.
        cur.execute("SELECT pg_try_advisory_xact_lock(%s)", (WORKER_LOCK_KEY,))
        yield bool(cur.fetchone()[0])
    finally:
        close_db(conn, cur)


def enqueue(cur, key, message):
    cur.execute(
        "INSERT INTO auto_result_notifications (event_key, message) VALUES (%s, %s) "
        "ON CONFLICT (event_key) DO NOTHING", (key, message),
    )


def notify(key, message):
    conn = get_db()
    cur = conn.cursor()
    try:
        enqueue(cur, key, message)
        conn.commit()
    finally:
        close_db(conn, cur)



def notify_pending(match, key, message):
    """Do not request manual input if an admin/worker already saved the match."""
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id, tournament_id, league, home_team, away_team, kickoff_time, "
            "COALESCE(match_category, '') FROM matches WHERE id=%s "
            "AND status IN ('SCHEDULED', 'TIMED', 'LIVE') "
            "AND home_score IS NULL AND away_score IS NULL FOR SHARE", (match["id"],),
        )
        row = cur.fetchone()
        if row:
            current = dict(zip(("id", "tournament_id", "league", "home_team", "away_team",
                                "kickoff_time", "match_category"), row))
            if match_identity(current) == match_identity(match):
                enqueue(cur, key, message)
                conn.commit()
    finally:
        close_db(conn, cur)

def record_check(match, now, detail):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO auto_result_checks (match_id, identity, checked_at, detail) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (match_id) DO UPDATE SET "
            "identity=EXCLUDED.identity, checked_at=EXCLUDED.checked_at, detail=EXCLUDED.detail",
            (match["id"], match_identity(match), now, detail),
        )
        conn.commit()
    finally:
        close_db(conn, cur)


def last_check(match):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT checked_at, detail FROM auto_result_checks "
            "WHERE match_id=%s AND identity=%s", (match["id"], match_identity(match)),
        )
        return cur.fetchone()
    finally:
        close_db(conn, cur)


def enabled_since(enabled):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE auto_result_monitor SET enabled=%s, enabled_since=clock_timestamp() "
            "WHERE id=1 AND enabled IS DISTINCT FROM %s RETURNING enabled_since", (enabled, enabled),
        )
        changed = cur.fetchone()
        if changed:
            message = ("▶️ ТОТИШ: автоматическая проверка результатов снова включена."
                       if enabled else "⏸️ ТОТИШ: автоматическая проверка результатов отключена.")
            enqueue(cur, f"enabled:{enabled}:{changed[0].isoformat()}", message)
        cur.execute("SELECT enabled_since FROM auto_result_monitor WHERE id=1")
        row = cur.fetchone()
        if not row:
            raise RuntimeError("auto_result_monitor_missing")
        conn.commit()
        return row[0]
    finally:
        close_db(conn, cur)


def flush_notifications(outbox: Path | None):
    """At-least-once publication; a crash can duplicate a notice, never a result.

    Rows are committed only after atomic file publication. Relay failures leave
    the file for the existing host relay. No Telegram network calls here.
    """
    if outbox is None:
        return
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT event_key, message FROM auto_result_notifications "
            "WHERE queued_at IS NULL ORDER BY created_at LIMIT 100 FOR UPDATE SKIP LOCKED"
        )
        rows = cur.fetchall()
        outbox.mkdir(parents=True, exist_ok=True)
        for key, message in rows:
            name = hashlib.sha256(key.encode()).hexdigest()
            target = outbox / f"auto-results-{name}.msg"
            tmp = target.with_suffix(f".{uuid.uuid4().hex}.tmp")
            tmp.write_text(message, encoding="utf-8")
            tmp.replace(target)
            cur.execute(
                "UPDATE auto_result_notifications SET queued_at=clock_timestamp() WHERE event_key=%s",
                (key,),
            )
        conn.commit()
    finally:
        close_db(conn, cur)
