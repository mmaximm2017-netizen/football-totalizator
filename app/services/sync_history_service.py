import json
import logging
from datetime import datetime, timezone

from psycopg2.extras import RealDictCursor

from app.db import close_db, get_db


logger = logging.getLogger(__name__)
STALE_SYNC_TIMEOUT_MINUTES = 30
SYNC_HEALTH_MAX_AGE_HOURS = 6
HEALTHY_SYNC_STATUSES = ("success", "partial_success")
PROBLEM_SYNC_STATUSES = ("failed", "lock_error", "abandoned")


def _ensure_sync_runs_table(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sync_runs (
            id SERIAL PRIMARY KEY,
            started_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
            finished_at TIMESTAMP WITH TIME ZONE DEFAULT NULL,
            status TEXT NOT NULL,
            matches_inserted INTEGER DEFAULT 0,
            matches_updated INTEGER DEFAULT 0,
            matches_finished INTEGER DEFAULT 0,
            predictions_recalculated INTEGER DEFAULT 0,
            errors_count INTEGER DEFAULT 0,
            summary_json TEXT
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sync_runs_started ON sync_runs(started_at);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sync_runs_status ON sync_runs(status);")


def _utc_now():
    return datetime.now(timezone.utc)


def _as_utc(value):
    if not value:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _minutes_since(value):
    value = _as_utc(value)
    if not value:
        return None
    return int((_utc_now() - value).total_seconds() // 60)


def _isoformat(value):
    value = _as_utc(value)
    return value.isoformat() if value else None


def _count_errors(summary):
    if not summary:
        return 0

    errors = len(summary.get("errors") or [])
    sync_errors = len((summary.get("sync") or {}).get("errors") or [])
    return errors + sync_errors


def _history_payload(summary):
    summary = summary or {}
    sync_summary = summary.get("sync") or {}
    scoring_summary = summary.get("scoring") or {}
    changed_finished_count = sync_summary.get("changed_finished_matches_count")
    if changed_finished_count is None:
        changed_finished_count = len(sync_summary.get("matches_became_finished") or [])

    return {
        "matches_inserted": sync_summary.get("matches_inserted", 0),
        "matches_updated": sync_summary.get("matches_updated", 0),
        "matches_finished": changed_finished_count,
        "predictions_recalculated": scoring_summary.get("predictions_recalculated", 0),
        "errors_count": _count_errors(summary),
        "summary_json": json.dumps(summary, ensure_ascii=False, default=str),
    }


def create_sync_run(summary=None):
    conn = cur = None
    try:
        conn = get_db()
        cur = conn.cursor()
        _ensure_sync_runs_table(cur)
        payload = _history_payload(summary)
        cur.execute(
            """
            INSERT INTO sync_runs (
                status,
                matches_inserted,
                matches_updated,
                matches_finished,
                predictions_recalculated,
                errors_count,
                summary_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                "started",
                payload["matches_inserted"],
                payload["matches_updated"],
                payload["matches_finished"],
                payload["predictions_recalculated"],
                payload["errors_count"],
                payload["summary_json"],
            ),
        )
        sync_run_id = cur.fetchone()[0]
        conn.commit()
        logger.info("Created sync history run id=%s", sync_run_id)
        return sync_run_id
    except Exception as e:
        if conn:
            conn.rollback()
        logger.exception("Failed to create sync history run: %s", e)
        return None
    finally:
        close_db(conn, cur)


def recover_stale_syncs(timeout_minutes=STALE_SYNC_TIMEOUT_MINUTES):
    conn = cur = None
    stale_rows = []
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_sync_runs_table(cur)
        conn.commit()
        cur.execute(
            """
            SELECT id, started_at, summary_json
            FROM sync_runs
            WHERE status = %s
              AND started_at < now() - (%s * interval '1 minute')
            ORDER BY started_at ASC, id ASC
            FOR UPDATE SKIP LOCKED
            """,
            ("started", timeout_minutes),
        )
        stale_rows = cur.fetchall()
        stale_ids = [row["id"] for row in stale_rows]
        logger.info("Stale sync recovery found %s run(s): %s", len(stale_ids), stale_ids)

        if not stale_ids:
            conn.commit()
            return []

        for row in stale_rows:
            previous_summary = row.get("summary_json")
            try:
                previous_summary = json.loads(previous_summary) if previous_summary else None
            except (TypeError, ValueError):
                pass

            reason = (
                f"sync run was still started after {timeout_minutes} minutes; "
                "marking as abandoned before a new sync"
            )
            summary = {
                "status": "abandoned",
                "reason": reason,
                "started_at": row.get("started_at"),
                "recovered_by": "recover_stale_syncs",
                "previous_summary": previous_summary,
                "errors": [reason],
            }
            cur.execute(
                """
                UPDATE sync_runs
                SET
                    finished_at = now(),
                    status = %s,
                    errors_count = GREATEST(COALESCE(errors_count, 0), 1),
                    summary_json = %s
                WHERE id = %s
                """,
                (
                    "abandoned",
                    json.dumps(summary, ensure_ascii=False, default=str),
                    row["id"],
                ),
            )

        conn.commit()
        logger.warning(
            "Stale sync recovery marked run ids abandoned: %s",
            stale_ids,
        )
        return stale_ids
    except Exception as e:
        if conn:
            conn.rollback()
        logger.exception("Failed to recover stale sync history runs: %s", e)
        return []
    finally:
        close_db(conn, cur)


def finish_sync_run(sync_run_id, status, summary=None):
    if not sync_run_id:
        return False

    conn = cur = None
    try:
        conn = get_db()
        cur = conn.cursor()
        _ensure_sync_runs_table(cur)
        payload = _history_payload(summary)
        cur.execute(
            """
            UPDATE sync_runs
            SET
                finished_at = now(),
                status = %s,
                matches_inserted = %s,
                matches_updated = %s,
                matches_finished = %s,
                predictions_recalculated = %s,
                errors_count = %s,
                summary_json = %s
            WHERE id = %s
            """,
            (
                status,
                payload["matches_inserted"],
                payload["matches_updated"],
                payload["matches_finished"],
                payload["predictions_recalculated"],
                payload["errors_count"],
                payload["summary_json"],
                sync_run_id,
            ),
        )
        updated = cur.rowcount
        conn.commit()
        if updated:
            logger.info("Finished sync history run id=%s status=%s", sync_run_id, status)
            return True

        logger.warning("Sync history run id=%s was not found while finishing status=%s", sync_run_id, status)
        return False
    except Exception as e:
        if conn:
            conn.rollback()
        logger.exception("Failed to finish sync history run %s: %s", sync_run_id, e)
        return False
    finally:
        close_db(conn, cur)


def get_last_sync():
    conn = cur = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_sync_runs_table(cur)
        conn.commit()
        cur.execute(
            """
            SELECT *
            FROM sync_runs
            ORDER BY started_at DESC, id DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
        return dict(row) if row else None
    except Exception as e:
        if conn:
            conn.rollback()
        logger.exception("Failed to load last sync history run: %s", e)
        return None
    finally:
        close_db(conn, cur)


def get_recent_sync_runs(limit=5):
    conn = cur = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_sync_runs_table(cur)
        conn.commit()
        cur.execute(
            """
            SELECT
                id,
                started_at,
                finished_at,
                status,
                matches_inserted,
                matches_updated,
                matches_finished,
                predictions_recalculated,
                errors_count,
                summary_json
            FROM sync_runs
            ORDER BY started_at DESC, id DESC
            LIMIT %s
            """,
            (limit,),
        )
        return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        if conn:
            conn.rollback()
        logger.exception("Failed to load recent sync history runs: %s", e)
        return []
    finally:
        close_db(conn, cur)


def get_sync_health(max_age_hours=SYNC_HEALTH_MAX_AGE_HOURS):
    conn = cur = None
    max_age_minutes = max_age_hours * 60
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_sync_runs_table(cur)
        conn.commit()
        cur.execute(
            """
            SELECT *
            FROM sync_runs
            ORDER BY started_at DESC, id DESC
            LIMIT 1
            """
        )
        last_sync = cur.fetchone()

        if not last_sync:
            return {
                "last_sync_id": None,
                "last_status": None,
                "last_started_at": None,
                "last_finished_at": None,
                "minutes_since_last_finished": None,
                "errors_count": 0,
                "is_healthy": False,
                "health_reason": "no_sync_runs",
            }

        cur.execute(
            """
            SELECT finished_at
            FROM sync_runs
            WHERE status = ANY(%s)
              AND finished_at IS NOT NULL
            ORDER BY finished_at DESC, id DESC
            LIMIT 1
            """,
            (list(HEALTHY_SYNC_STATUSES),),
        )
        last_success = cur.fetchone()
        last_status = last_sync.get("status")
        last_finished_at = last_sync.get("finished_at")
        minutes_since_last_finished = _minutes_since(last_finished_at)

        health = {
            "last_sync_id": last_sync.get("id"),
            "last_status": last_status,
            "last_started_at": _isoformat(last_sync.get("started_at")),
            "last_finished_at": _isoformat(last_finished_at),
            "minutes_since_last_finished": minutes_since_last_finished,
            "errors_count": last_sync.get("errors_count") or 0,
            "is_healthy": False,
            "health_reason": "unknown_status",
        }

        last_success_finished_at = last_success.get("finished_at") if last_success else None
        minutes_since_last_success = _minutes_since(last_success_finished_at)

        if last_status in HEALTHY_SYNC_STATUSES:
            if minutes_since_last_finished is None:
                health["health_reason"] = "last_sync_not_finished"
            elif minutes_since_last_finished > max_age_minutes:
                health["health_reason"] = "last_success_too_old"
            else:
                health["is_healthy"] = True
                health["health_reason"] = last_status
            return health

        if last_status == "skipped_already_running":
            if minutes_since_last_success is None:
                health["health_reason"] = "skipped_already_running_no_success"
            elif minutes_since_last_success > max_age_minutes:
                health["health_reason"] = "skipped_already_running_last_success_too_old"
            else:
                health["is_healthy"] = True
                health["health_reason"] = "skipped_already_running_recent_success"
            return health

        if last_status in PROBLEM_SYNC_STATUSES:
            health["health_reason"] = last_status
            return health

        if last_status == "started":
            health["health_reason"] = "sync_running_or_unfinished"
            return health

        return health
    except Exception as e:
        if conn:
            conn.rollback()
        logger.exception("Failed to load sync health: %s", e)
        return {
            "last_sync_id": None,
            "last_status": None,
            "last_started_at": None,
            "last_finished_at": None,
            "minutes_since_last_finished": None,
            "errors_count": 0,
            "is_healthy": False,
            "health_reason": "health_query_failed",
        }
    finally:
        close_db(conn, cur)
