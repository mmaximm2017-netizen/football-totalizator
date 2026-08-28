"""Read-only query boundary for the host Telegram admin panel."""

import argparse
import json
import os
from datetime import datetime
from urllib.request import urlopen

from app.db import close_db, get_db
from app.services.morning_digest_service import (
    collect_worker_issues,
    count_points_mismatches,
    find_finished_matches_without_result,
    latest_sync_has_problem,
    worker_heartbeat_statuses,
)
from app.services.ranking_service import get_tournament_ranking
from app.utils import MSK, is_before_deadline

MAX_CALENDAR_MATCHES = 10


def _iso(value):
    return value.isoformat() if value is not None else None


def _today_msk():
    return datetime.now(MSK).date()


def _fetch_json(url):
    with urlopen(url, timeout=5) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        return json.loads(response.read().decode("utf-8"))


def _visible_participants(cur):
    cur.execute(
        """
        SELECT id, username
        FROM users
        WHERE is_admin = 0
          AND COALESCE(is_deleted, 0) = 0
        ORDER BY username ASC, id ASC
        """
    )
    return [{"user_id": row[0], "username": row[1]} for row in cur.fetchall()]


def _match_dict(row):
    return {
        "match_id": row[0],
        "tournament_id": row[1],
        "tournament_name": row[2] or "Турнир не указан",
        "home_team": row[3] or "Команда не указана",
        "away_team": row[4] or "Команда не указана",
        "kickoff_time": _iso(row[5]),
        "deadline": _iso(row[6]),
        "status": row[7],
    }


def today_matches():
    conn = cur = None
    try:
        conn = get_db()
        cur = conn.cursor()
        today = _today_msk().isoformat()
        cur.execute(
            """
            SELECT m.id, m.tournament_id, t.name, m.home_team, m.away_team,
                   m.kickoff_time, m.deadline, m.status,
                   (SELECT COUNT(*) FROM users u
                    WHERE u.is_admin = 0 AND COALESCE(u.is_deleted, 0) = 0),
                   (SELECT COUNT(DISTINCT p.user_id)
                    FROM predictions p
                    JOIN users u ON u.id = p.user_id
                    WHERE p.match_id = m.id
                      AND p.tournament_id = m.tournament_id
                      AND u.is_admin = 0
                      AND COALESCE(u.is_deleted, 0) = 0)
            FROM matches m
            LEFT JOIN tournaments t ON t.id = m.tournament_id
            WHERE (m.kickoff_time AT TIME ZONE 'Europe/Moscow')::date = %s
            ORDER BY t.name ASC NULLS LAST, m.kickoff_time ASC NULLS LAST, m.id ASC
            """,
            (today,),
        )
        matches = []
        for row in cur.fetchall():
            item = _match_dict(row[:8])
            item["predicted_count"] = row[9]
            item["participant_count"] = row[8]
            matches.append(item)
        return {"ok": True, "matches": matches}
    finally:
        if conn is not None:
            close_db(conn, cur)


def _relevant_match(cur, match_id=None):
    params = ()
    where = "COALESCE(m.status, '') NOT IN ('FINISHED', 'POSTPONED', 'CANCELLED') AND COALESCE(t.is_active, 0) = 1"
    if match_id is not None:
        where += " AND m.id = %s"
        params = (match_id,)
    cur.execute(
        f"""
        SELECT m.id, m.tournament_id, t.name, m.home_team, m.away_team,
               m.kickoff_time, m.deadline, m.status
        FROM matches m
        LEFT JOIN tournaments t ON t.id = m.tournament_id
        WHERE {where}
        ORDER BY CASE WHEN m.deadline >= CURRENT_TIMESTAMP THEN 0 ELSE 1 END,
                 m.kickoff_time ASC NULLS LAST, m.id ASC
        LIMIT 1
        """,
        params,
    )
    row = cur.fetchone()
    return _match_dict(row) if row else None


def prediction_status(match_id=None, now=None):
    conn = cur = None
    try:
        conn = get_db()
        cur = conn.cursor()
        match = _relevant_match(cur, match_id)
        if not match:
            return {"ok": True, "match": None, "participants": []}
        deadline_open = is_before_deadline({"deadline": match["deadline"]}, now=now)
        participants = _visible_participants(cur)
        # This query intentionally selects only participation existence, never scores.
        cur.execute(
            """
            SELECT DISTINCT p.user_id
            FROM predictions p
            WHERE p.match_id = %s AND p.tournament_id = %s
            """,
            (match["match_id"], match["tournament_id"]),
        )
        predicted_ids = {row[0] for row in cur.fetchall()}
        for participant in participants:
            participant["has_prediction"] = participant["user_id"] in predicted_ids
        return {
            "ok": True,
            "match": match,
            "deadline_open": deadline_open,
            "participants": participants,
        }
    finally:
        if conn is not None:
            close_db(conn, cur)


def prediction_scores(match_id=None, now=None):
    conn = cur = None
    try:
        conn = get_db()
        cur = conn.cursor()
        match = _relevant_match(cur, match_id)
        if not match:
            return {"ok": True, "match": None, "deadline_open": False, "predictions": []}
        if is_before_deadline({"deadline": match["deadline"]}, now=now):
            return {"ok": True, "match": match, "deadline_open": True, "predictions": []}
        cur.execute(
            """
            SELECT u.username, p.home_goals, p.away_goals
            FROM users u
            LEFT JOIN predictions p
              ON p.user_id = u.id
             AND p.match_id = %s
             AND p.tournament_id = %s
            WHERE u.is_admin = 0
              AND COALESCE(u.is_deleted, 0) = 0
            ORDER BY u.username ASC, u.id ASC
            """,
            (match["match_id"], match["tournament_id"]),
        )
        return {
            "ok": True,
            "match": match,
            "deadline_open": False,
            "predictions": [
                {"username": row[0], "home_goals": row[1], "away_goals": row[2]}
                for row in cur.fetchall()
            ],
        }
    finally:
        if conn is not None:
            close_db(conn, cur)


def table_tournaments():
    conn = cur = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name FROM tournaments WHERE is_active = 1 ORDER BY name ASC, id ASC"
        )
        return {"ok": True, "tournaments": [{"id": row[0], "name": row[1]} for row in cur.fetchall()]}
    finally:
        if conn is not None:
            close_db(conn, cur)


def ranking(kind):
    names = {
        "rpl": "Чемпионат России 🇷🇺",
        "cup": "Кубок России",
    }
    if kind not in names:
        raise ValueError("invalid_tournament_kind")
    conn = cur = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id, name FROM tournaments WHERE name = %s ORDER BY id DESC LIMIT 1", (names[kind],))
        tournament = cur.fetchone()
    finally:
        if conn is not None:
            close_db(conn, cur)
    if not tournament:
        return {"ok": True, "tournament": None, "ranking": []}
    return {"ok": True, "tournament": {"id": tournament[0], "name": tournament[1]}, "ranking": get_tournament_ranking(tournament[0])}


def calendar():
    conn = cur = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT m.id, t.name, m.home_team, m.away_team, m.kickoff_time
            FROM matches m
            LEFT JOIN tournaments t ON t.id = m.tournament_id
            WHERE m.kickoff_time >= CURRENT_TIMESTAMP
              AND COALESCE(m.status, '') NOT IN ('POSTPONED', 'CANCELLED')
            ORDER BY m.kickoff_time ASC, m.id ASC
            LIMIT %s
            """,
            (MAX_CALENDAR_MATCHES,),
        )
        return {
            "ok": True,
            "matches": [
                {"match_id": row[0], "tournament_name": row[1] or "Турнир не указан", "home_team": row[2] or "Команда не указана", "away_team": row[3] or "Команда не указана", "kickoff_time": _iso(row[4])}
                for row in cur.fetchall()
            ],
        }
    finally:
        if conn is not None:
            close_db(conn, cur)


def problems(now_epoch, deadline_mtime, result_mtime):
    conn = cur = None
    try:
        conn = get_db()
        cur = conn.cursor()
        issues = [
            f"🔴 {item['home_team']} — {item['away_team']} завершён, но результат не внесён."
            for item in find_finished_matches_without_result(cur)
        ]
        mismatch_count = count_points_mismatches(cur)
        if mismatch_count:
            issues.append(f"🟠 Обнаружено расхождение в начисленных очках: {mismatch_count} прогнозов.")
        if latest_sync_has_problem(cur):
            issues.append("🟠 Последняя синхронизация матчей завершилась с ошибками.")
        issues.extend(collect_worker_issues(now_epoch, deadline_mtime, result_mtime))
        return {"ok": True, "issues": issues}
    finally:
        if conn is not None:
            close_db(conn, cur)


def system(now_epoch, deadline_mtime, result_mtime, container_state):
    result = {"container": "unknown", "local": "unknown", "db": "unknown", "public": "unknown"}
    result["container"] = "ok" if container_state == "running|true|false" else "problem"
    for key, url in (("local", "http://127.0.0.1:8000/health"), ("db", "http://127.0.0.1:8000/health/db")):
        try:
            payload = _fetch_json(url)
            result[key] = "ok" if (payload.get("status") == "ok" if key == "local" else all(payload.get(field) == "ok" for field in ("db", "active_tournament", "ranking"))) else "problem"
        except Exception:  # noqa: BLE001 - health probe failures are rendered as unavailable.
            result[key] = "problem"
    try:
        result["public"] = "ok" if _fetch_json("https://totish.ru/health").get("status") == "ok" else "problem"
    except Exception:  # noqa: BLE001 - public health failures are rendered as unavailable.
        result["public"] = "problem"
    return {"ok": True, "system": result, "worker_statuses": worker_heartbeat_statuses(now_epoch, deadline_mtime, result_mtime)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", required=True, choices=("today", "prediction-status", "prediction-scores", "table-tournaments", "ranking", "calendar", "problems", "system"))
    parser.add_argument("--kind", choices=("rpl", "cup"))
    parser.add_argument("--match-id", type=lambda value: int(value) if value.isdigit() and int(value) > 0 else (_ for _ in ()).throw(argparse.ArgumentTypeError("match_id must be a positive integer")))
    args = parser.parse_args()
    now_epoch = os.getenv("TOTISH_DIGEST_HOST_NOW_EPOCH")
    deadline_mtime = os.getenv("TOTISH_DEADLINE_WORKER_MTIME")
    result_mtime = os.getenv("TOTISH_RESULT_WORKER_MTIME")
    container_state = os.getenv("TOTISH_CONTAINER_STATE")
    try:
        actions = {
            "today": today_matches,
            "prediction-status": lambda: prediction_status(args.match_id),
            "prediction-scores": lambda: prediction_scores(args.match_id),
            "table-tournaments": table_tournaments,
            "calendar": calendar,
        }
        if args.action == "ranking":
            payload = ranking(args.kind)
        elif args.action == "problems":
            payload = problems(now_epoch, deadline_mtime, result_mtime)
        elif args.action == "system":
            payload = system(now_epoch, deadline_mtime, result_mtime, container_state)
        else:
            payload = actions[args.action]()
        print(json.dumps(payload, ensure_ascii=False, default=str))
        return 0
    except Exception:  # noqa: BLE001 - CLI must not expose internal traceback.
        print(json.dumps({"ok": False, "error": "query_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
