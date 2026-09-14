#!/usr/bin/env python3
"""Keep 5-minute worker responsiveness only around real DB activity windows.

`plan` runs inside the app container every 15 minutes and emits a tiny JSON plan.
`run -- <command>` runs on the VPS every 5 minutes and executes the command only
when the plan says a fast cadence is useful. Every 15th minute is a fail-safe
baseline run, and missing/stale state fails open so reliability wins over savings.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

BASELINE_MINUTES = 15
PLAN_TTL_SECONDS = 30 * 60
ACTIVE_FOR_SECONDS = 25 * 60


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
                WHERE d.status IN ('ready','pending','failed')
                  AND d.event_type IN ('match_result','deadline_2h')
                  AND d.updated_at >= clock_timestamp() - INTERVAL '1 day'
              ) AS delivery_backlog
            """
        )
        auto_results, deadline, backlog = cur.fetchone()
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
    return {
        "generated_at": now,
        "active": active,
        "active_until": now + ACTIVE_FOR_SECONDS if active else 0,
        "reasons": reasons,
    }


def load_plan(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None


def should_run(path: Path, now: int | None = None) -> tuple[bool, str]:
    now = int(time.time()) if now is None else int(now)
    # Quarter-hour baseline guarantees bounded latency even when no active window
    # is known, and aligns all DB jobs so Neon can idle between bursts.
    if datetime.fromtimestamp(now).minute % BASELINE_MINUTES == 0:
        return True, "baseline"

    plan = load_plan(path)
    if not plan:
        return True, "missing_plan_fail_open"
    generated = int(plan.get("generated_at", 0) or 0)
    if generated <= 0 or now - generated > PLAN_TTL_SECONDS:
        return True, "stale_plan_fail_open"
    if bool(plan.get("active")) and int(plan.get("active_until", 0) or 0) >= now:
        return True, "active_window"
    return False, "idle"


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("plan")
    run = sub.add_parser("run")
    run.add_argument("--state", required=True)
    run.add_argument("remainder", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    if args.command == "plan":
        print(json.dumps(build_plan(), sort_keys=True))
        return 0

    command = list(args.remainder)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("run requires a command after --")

    due, reason = should_run(Path(args.state))
    if not due:
        return 0
    os.execvp(command[0], command)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
