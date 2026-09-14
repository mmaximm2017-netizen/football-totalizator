#!/usr/bin/env python3
"""Keep 5-minute DB worker responsiveness only around real activity windows.

`plan` runs inside the app container and emits a tiny JSON plan. The plan includes
`next_due`, the next time when a DB-sensitive window is expected to begin.

`refresh-due` runs entirely on the VPS and decides whether the plan itself needs a
DB refresh. This lets cron check every five minutes without waking Neon. Idle plans
are refreshed at most hourly as a safety net for schedule/admin changes.

`run -- <command>` also runs entirely on the VPS. It executes DB workers every five
minutes while a planned window is active, and fails open if state is missing,
stale, or reaches `next_due` before the refresh job has updated the plan.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

ACTIVE_FOR_SECONDS = 25 * 60
ACTIVE_REFRESH_SECONDS = 15 * 60
MAX_IDLE_REFRESH_SECONDS = 60 * 60
FAIL_OPEN_GRACE_SECONDS = 10 * 60


def _as_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def build_plan() -> dict:
    from app.db import close_db, get_db

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT
              EXISTS (
                SELECT 1
                FROM matches m
                WHERE m.kickoff_time IS NOT NULL
                  AND m.home_score IS NULL
                  AND m.away_score IS NULL
                  AND UPPER(COALESCE(m.status, 'SCHEDULED')) IN
                      ('SCHEDULED','TIMED','LIVE','IN_PLAY','PAUSED','HALFTIME')
                  AND m.kickoff_time >= clock_timestamp() - INTERVAL '360 minutes'
                  AND m.kickoff_time <= clock_timestamp() - INTERVAL '100 minutes'
              ) AS auto_results_window,
              EXISTS (
                SELECT 1
                FROM matches m
                JOIN tournaments t ON t.id = m.tournament_id AND t.is_active = 1
                WHERE m.deadline IS NOT NULL
                  AND UPPER(COALESCE(m.status, 'SCHEDULED')) NOT IN
                      ('FINISHED','COMPLETE','COMPLETED','CANCELLED','POSTPONED',
                       'SUSPENDED','LIVE','IN_PLAY','PAUSED','HALFTIME','ABANDONED')
                  AND m.deadline >= clock_timestamp() + INTERVAL '95 minutes'
                  AND m.deadline <= clock_timestamp() + INTERVAL '140 minutes'
              ) AS deadline_window,
              EXISTS (
                SELECT 1
                FROM push_delivery_log d
                WHERE d.status IN ('ready','pending')
                  AND d.event_type IN ('match_result','deadline_2h')
                  AND d.updated_at >= clock_timestamp() - INTERVAL '1 day'
              ) AS delivery_backlog,
              (
                SELECT EXTRACT(EPOCH FROM MIN(m.kickoff_time + INTERVAL '100 minutes'))::bigint
                FROM matches m
                WHERE m.kickoff_time IS NOT NULL
                  AND m.home_score IS NULL
                  AND m.away_score IS NULL
                  AND UPPER(COALESCE(m.status, 'SCHEDULED')) IN
                      ('SCHEDULED','TIMED','LIVE','IN_PLAY','PAUSED','HALFTIME')
                  AND m.kickoff_time + INTERVAL '100 minutes' > clock_timestamp()
              ) AS next_auto_results_at,
              (
                SELECT EXTRACT(EPOCH FROM MIN(m.deadline - INTERVAL '140 minutes'))::bigint
                FROM matches m
                JOIN tournaments t ON t.id = m.tournament_id AND t.is_active = 1
                WHERE m.deadline IS NOT NULL
                  AND UPPER(COALESCE(m.status, 'SCHEDULED')) NOT IN
                      ('FINISHED','COMPLETE','COMPLETED','CANCELLED','POSTPONED',
                       'SUSPENDED','LIVE','IN_PLAY','PAUSED','HALFTIME','ABANDONED')
                  AND m.deadline - INTERVAL '140 minutes' > clock_timestamp()
              ) AS next_deadline_at
            """
        )
        auto_results, deadline, backlog, next_auto, next_deadline = cur.fetchone()
    finally:
        close_db(conn, cur)

    now = int(time.time())
    reasons = []
    if auto_results:
        reasons.append("auto_results_window")
    if deadline:
        reasons.append("deadline_window")
    if backlog:
        reasons.append("delivery_backlog")

    active = bool(reasons)
    future_due = [
        value for value in (_as_int(next_auto), _as_int(next_deadline))
        if value > now
    ]
    next_due = min(future_due) if future_due else 0

    return {
        "generated_at": now,
        "active": active,
        "active_until": now + ACTIVE_FOR_SECONDS if active else 0,
        "next_due": next_due,
        "reasons": reasons,
    }


def load_plan(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None


def refresh_due(path: Path, now: int | None = None) -> tuple[bool, str]:
    """Return whether refreshing the DB-derived plan is needed now."""
    now = int(time.time()) if now is None else int(now)
    plan = load_plan(path)
    if not plan:
        return True, "missing_plan"

    generated = _as_int(plan.get("generated_at"))
    if generated <= 0 or generated > now + 60:
        return True, "invalid_plan"

    age = max(0, now - generated)
    if bool(plan.get("active")):
        active_until = _as_int(plan.get("active_until"))
        if active_until < now:
            return True, "active_expired"
        if age >= ACTIVE_REFRESH_SECONDS:
            return True, "active_refresh"
        return False, "active_fresh"

    next_due = _as_int(plan.get("next_due"))
    if next_due and now >= next_due:
        return True, "next_due"
    if age >= MAX_IDLE_REFRESH_SECONDS:
        return True, "idle_watchdog"
    return False, "idle_wait"


def should_run(path: Path, now: int | None = None) -> tuple[bool, str]:
    now = int(time.time()) if now is None else int(now)
    plan = load_plan(path)
    if not plan:
        return True, "missing_plan_fail_open"

    generated = _as_int(plan.get("generated_at"))
    if generated <= 0 or generated > now + 60:
        return True, "invalid_plan_fail_open"

    if bool(plan.get("active")):
        if _as_int(plan.get("active_until")) >= now:
            return True, "active_window"
        return True, "expired_active_fail_open"

    next_due = _as_int(plan.get("next_due"))
    if next_due and now >= next_due:
        return True, "next_due_fail_open"

    if now - generated > MAX_IDLE_REFRESH_SECONDS + FAIL_OPEN_GRACE_SECONDS:
        return True, "stale_plan_fail_open"

    return False, "idle"


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("plan")

    refresh = sub.add_parser("refresh-due")
    refresh.add_argument("--state", required=True)

    run = sub.add_parser("run")
    run.add_argument("--state", required=True)
    run.add_argument("remainder", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    if args.command == "plan":
        print(json.dumps(build_plan(), sort_keys=True))
        return 0

    if args.command == "refresh-due":
        due, reason = refresh_due(Path(args.state))
        print(reason)
        return 0 if due else 3

    command = list(args.remainder)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("run requires a command after --")

    due, _ = should_run(Path(args.state))
    if not due:
        return 0
    os.execvp(command[0], command)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
