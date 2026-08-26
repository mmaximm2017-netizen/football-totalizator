"""Read-only API for the TOTISH Custom GPT integration."""

from __future__ import annotations

import hmac
import logging
import os
from datetime import datetime
from functools import wraps

from flask import Blueprint, jsonify, request

from app.db import close_db, get_db


logger = logging.getLogger(__name__)
gpt_api_bp = Blueprint("gpt_api", __name__, url_prefix="/api/gpt")

MAX_LIMIT = 500
DEFAULT_LIMIT = 100
MAX_OFFSET = 100_000
FINISHED_STATUSES_SQL = "('FINISHED', 'COMPLETE', 'COMPLETED')"
MOSCOW_MATCH_DATE_SQL = "(m.kickoff_time AT TIME ZONE 'Europe/Moscow')::date"


def _response(payload, status=200):
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _configured_token():
    return (os.getenv("TOTISH_GPT_API_KEY") or "").strip()


def _provided_token():
    header = request.headers.get("Authorization") or ""
    if not header.startswith("Bearer "):
        return ""
    return header[7:].strip()


def gpt_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        expected = _configured_token()
        provided = _provided_token()
        if not expected or not provided or not hmac.compare_digest(expected, provided):
            return _response({"ok": False, "error": "unauthorized"}, 401)
        return view(*args, **kwargs)

    return wrapped


def _parse_positive_int(name, *, required=False):
    raw_value = request.args.get(name)
    if raw_value is None:
        if required:
            raise ValueError(f"{name}_required")
        return None
    if not raw_value.isdigit() or int(raw_value) < 1:
        raise ValueError(f"invalid_{name}")
    return int(raw_value)


def _parse_limit_offset():
    raw_limit = request.args.get("limit", str(DEFAULT_LIMIT))
    raw_offset = request.args.get("offset", "0")
    if not raw_limit.isdigit() or not raw_offset.isdigit():
        raise ValueError("invalid_pagination")

    limit = int(raw_limit)
    offset = int(raw_offset)
    if not 1 <= limit <= MAX_LIMIT or offset > MAX_OFFSET:
        raise ValueError("invalid_pagination")
    return limit, offset


def _parse_date(name):
    raw_value = request.args.get(name)
    if raw_value is None:
        return None
    try:
        return datetime.strptime(raw_value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"invalid_{name}") from exc


def _parse_date_range():
    date_from = _parse_date("date_from")
    date_to = _parse_date("date_to")
    if date_from and date_to and date_from > date_to:
        raise ValueError("invalid_date_range")
    return date_from, date_to


def _parse_finished():
    raw_value = request.args.get("finished")
    if raw_value is None:
        return None
    if raw_value == "true":
        return True
    if raw_value == "false":
        return False
    raise ValueError("invalid_finished")


def _read_rows(query, params=()):
    conn = None
    cur = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(query, params)
        return cur.fetchall()
    except Exception:
        logger.exception("gpt_api_read_failed")
        return None
    finally:
        if conn is not None and cur is not None:
            close_db(conn, cur)


def _read_row(query, params=()):
    conn = None
    cur = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(query, params)
        return cur.fetchone()
    except Exception:
        logger.exception("gpt_api_read_failed")
        return None
    finally:
        if conn is not None and cur is not None:
            close_db(conn, cur)


def _isoformat(value):
    return value.isoformat() if value is not None else None


def _bad_request(error):
    return _response({"ok": False, "error": error}, 400)


def _database_error():
    return _response({"ok": False, "error": "service_unavailable"}, 503)


@gpt_api_bp.get("/health")
@gpt_required
def health():
    return _response({"ok": True, "service": "totish-gpt-api"})


@gpt_api_bp.get("/tournaments")
@gpt_required
def tournaments():
    rows = _read_rows("SELECT id, name FROM tournaments ORDER BY id")
    if rows is None:
        return _database_error()
    return _response({"tournaments": [{"id": row[0], "name": row[1]} for row in rows]})


@gpt_api_bp.get("/users")
@gpt_required
def users():
    rows = _read_rows(
        """
        SELECT id, username
        FROM users
        WHERE is_admin = 0
          AND COALESCE(is_deleted, 0) = 0
        ORDER BY username ASC, id ASC
        """
    )
    if rows is None:
        return _database_error()
    return _response({"users": [{"id": row[0], "username": row[1]} for row in rows]})


def _match_filters():
    tournament_id = _parse_positive_int("tournament_id")
    date_from, date_to = _parse_date_range()
    finished = _parse_finished()
    limit, offset = _parse_limit_offset()

    conditions = []
    params = []
    if tournament_id is not None:
        conditions.append("m.tournament_id = %s")
        params.append(tournament_id)
    if date_from is not None:
        conditions.append(f"{MOSCOW_MATCH_DATE_SQL} >= %s::date")
        params.append(date_from.isoformat())
    if date_to is not None:
        conditions.append(f"{MOSCOW_MATCH_DATE_SQL} <= %s::date")
        params.append(date_to.isoformat())
    if finished is True:
        conditions.append(f"UPPER(m.status) IN {FINISHED_STATUSES_SQL}")
    elif finished is False:
        conditions.append(f"(m.status IS NULL OR UPPER(m.status) NOT IN {FINISHED_STATUSES_SQL})")

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    return where_clause, tuple(params + [limit, offset])


@gpt_api_bp.get("/matches")
@gpt_required
def matches():
    try:
        where_clause, params = _match_filters()
    except ValueError as exc:
        return _bad_request(str(exc))

    rows = _read_rows(
        f"""
        SELECT m.id, m.tournament_id, m.home_team, m.away_team, m.kickoff_time,
               m.home_score, m.away_score, m.status
        FROM matches m
        {where_clause}
        ORDER BY m.kickoff_time ASC NULLS LAST, m.id ASC
        LIMIT %s OFFSET %s
        """,
        params,
    )
    if rows is None:
        return _database_error()
    return _response(
        {
            "matches": [
                {
                    "match_id": row[0],
                    "tournament_id": row[1],
                    "home_team": row[2],
                    "away_team": row[3],
                    "match_datetime": _isoformat(row[4]),
                    "home_score": row[5],
                    "away_score": row[6],
                    "status": row[7],
                }
                for row in rows
            ],
            "limit": params[-2],
            "offset": params[-1],
        }
    )


@gpt_api_bp.get("/predictions")
@gpt_required
def predictions():
    try:
        match_id = _parse_positive_int("match_id")
        user_id = _parse_positive_int("user_id")
        tournament_id = _parse_positive_int("tournament_id")
        date_from, date_to = _parse_date_range()
        limit, offset = _parse_limit_offset()
    except ValueError as exc:
        return _bad_request(str(exc))

    conditions = ["u.is_admin = 0", "COALESCE(u.is_deleted, 0) = 0"]
    params = []
    for column, value in (("p.match_id", match_id), ("p.user_id", user_id), ("p.tournament_id", tournament_id)):
        if value is not None:
            conditions.append(f"{column} = %s")
            params.append(value)
    if date_from is not None:
        conditions.append(f"{MOSCOW_MATCH_DATE_SQL} >= %s::date")
        params.append(date_from.isoformat())
    if date_to is not None:
        conditions.append(f"{MOSCOW_MATCH_DATE_SQL} <= %s::date")
        params.append(date_to.isoformat())

    rows = _read_rows(
        f"""
        SELECT p.match_id, p.user_id, p.tournament_id, u.username,
               p.home_goals, p.away_goals, m.status,
               CASE WHEN UPPER(m.status) IN {FINISHED_STATUSES_SQL} THEN m.home_score END,
               CASE WHEN UPPER(m.status) IN {FINISHED_STATUSES_SQL} THEN m.away_score END,
               CASE WHEN UPPER(m.status) IN {FINISHED_STATUSES_SQL} THEN p.points END
        FROM predictions p
        JOIN matches m ON m.id = p.match_id AND m.tournament_id = p.tournament_id
        JOIN users u ON u.id = p.user_id
        WHERE {' AND '.join(conditions)}
        ORDER BY m.kickoff_time ASC NULLS LAST, p.user_id ASC
        LIMIT %s OFFSET %s
        """,
        tuple(params + [limit, offset]),
    )
    if rows is None:
        return _database_error()
    return _response(
        {
            "predictions": [
                {
                    "match_id": row[0],
                    "user_id": row[1],
                    "tournament_id": row[2],
                    "username": row[3],
                    "predicted_home": row[4],
                    "predicted_away": row[5],
                    "status": row[6],
                    "actual_home": row[7],
                    "actual_away": row[8],
                    "points": row[9],
                }
                for row in rows
            ],
            "prediction_id_available": False,
            "limit": limit,
            "offset": offset,
        }
    )


@gpt_api_bp.get("/player-stats")
@gpt_required
def player_stats():
    try:
        user_id = _parse_positive_int("user_id", required=True)
        tournament_id = _parse_positive_int("tournament_id")
        date_from, date_to = _parse_date_range()
    except ValueError as exc:
        return _bad_request(str(exc))

    eligible_users = _read_rows(
        """
        SELECT id
        FROM users
        WHERE id = %s
          AND is_admin = 0
          AND COALESCE(is_deleted, 0) = 0
        """,
        (user_id,),
    )
    if eligible_users is None:
        return _database_error()
    if not eligible_users:
        return _response({"ok": False, "error": "not_found"}, 404)

    conditions = [f"UPPER(m.status) IN {FINISHED_STATUSES_SQL}", "p.user_id = %s"]
    params = [user_id]
    if tournament_id is not None:
        conditions.append("p.tournament_id = %s")
        params.append(tournament_id)
    if date_from is not None:
        conditions.append(f"{MOSCOW_MATCH_DATE_SQL} >= %s::date")
        params.append(date_from.isoformat())
    if date_to is not None:
        conditions.append(f"{MOSCOW_MATCH_DATE_SQL} <= %s::date")
        params.append(date_to.isoformat())

    stats = _read_row(
        f"""
        SELECT COUNT(*), COALESCE(SUM(p.points), 0), COALESCE(AVG(p.points), 0),
               COALESCE(SUM(CASE WHEN p.points IN (10, 11) THEN 1 ELSE 0 END), 0),
               COALESCE(SUM(CASE WHEN p.points = 0 THEN 1 ELSE 0 END), 0),
               COALESCE(SUM(CASE WHEN p.points >= 7 THEN 1 ELSE 0 END), 0)
        FROM predictions p
        JOIN matches m ON m.id = p.match_id AND m.tournament_id = p.tournament_id
        WHERE {' AND '.join(conditions)}
        """,
        tuple(params),
    )
    if stats is None:
        return _database_error()
    return _response(
        {
            "user_id": user_id,
            "matches_count": stats[0],
            "total_points": stats[1],
            "average_points": float(stats[2]),
            "points_10_or_11": stats[3],
            "zero_points": stats[4],
            "points_7_or_more": stats[5],
        }
    )
