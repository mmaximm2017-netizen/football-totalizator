"""Strictly read-only analytics API for the TOTISH Custom GPT integration."""

from __future__ import annotations

import hmac
import logging
import os
from datetime import date
from functools import wraps

from flask import Blueprint, jsonify, request

from app import gpt_db

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
    return header[7:].strip() if header.startswith("Bearer ") else ""


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
    value = request.args.get(name)
    if value is None:
        if required:
            raise ValueError(f"{name}_required")
        return None
    if not value.isdigit() or int(value) < 1:
        raise ValueError(f"invalid_{name}")
    return int(value)


def _parse_limit_offset():
    limit = request.args.get("limit", str(DEFAULT_LIMIT))
    offset = request.args.get("offset", "0")
    if not limit.isdigit() or not offset.isdigit():
        raise ValueError("invalid_pagination")
    limit, offset = int(limit), int(offset)
    if not 1 <= limit <= MAX_LIMIT or offset > MAX_OFFSET:
        raise ValueError("invalid_pagination")
    return limit, offset


def _parse_date(name):
    value = request.args.get(name)
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid_{name}") from exc


def _parse_date_range():
    date_from, date_to = _parse_date("date_from"), _parse_date("date_to")
    if date_from and date_to and date_from > date_to:
        raise ValueError("invalid_date_range")
    return date_from, date_to


def _parse_finished():
    value = request.args.get("finished")
    if value is None:
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError("invalid_finished")


def _parse_text(name, max_length=100):
    value = request.args.get(name)
    if value is None:
        return None
    value = value.strip()
    if not value or len(value) > max_length:
        raise ValueError(f"invalid_{name}")
    return value


def _read(query, params=(), *, one=False):
    conn = cur = None
    try:
        conn = gpt_db.get_gpt_db()
        cur = conn.cursor()
        cur.execute(query, params)
        return cur.fetchone() if one else cur.fetchall()
    except gpt_db.GPTDatabaseUnavailable:
        return None
    except Exception:
        logger.exception("gpt_api_read_failed")
        return None
    finally:
        gpt_db.close_gpt_db(conn, cur)


def _bad_request(error):
    return _response({"ok": False, "error": error}, 400)


def _database_error():
    return _response({"ok": False, "error": "service_unavailable"}, 503)


def _isoformat(value):
    return value.isoformat() if value is not None else None


def _match_conditions():
    match_id = _parse_positive_int("match_id")
    tournament_id = _parse_positive_int("tournament_id")
    date_from, date_to = _parse_date_range()
    finished = _parse_finished()
    status, team = _parse_text("status", 64), _parse_text("team")
    conditions, params = [], []
    for column, value in (("m.id", match_id), ("m.tournament_id", tournament_id), ("m.status", status)):
        if value is not None:
            conditions.append(f"{column} = %s")
            params.append(value)
    if team is not None:
        conditions.append("(m.home_team ILIKE %s OR m.away_team ILIKE %s)")
        params.extend([f"%{team}%", f"%{team}%"])
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
    return conditions, params


def _where(conditions):
    return f"WHERE {' AND '.join(conditions)}" if conditions else ""


@gpt_api_bp.get("/health")
@gpt_required
def health():
    row = _read("SHOW transaction_read_only", one=True)
    if row is None or str(row[0]).lower() != "on":
        return _database_error()
    return _response({"ok": True, "service": "totish-gpt-api", "database": "ok", "read_only": True})


@gpt_api_bp.get("/tournaments")
@gpt_required
def tournaments():
    rows = _read("SELECT id, name, is_active, start_date, end_date FROM tournaments ORDER BY id")
    if rows is None:
        return _database_error()
    return _response({"tournaments": [{"tournament_id": row[0], "name": row[1], "is_active": bool(row[2]), "start_date": row[3], "end_date": row[4]} for row in rows]})


@gpt_api_bp.get("/users")
@gpt_required
def users():
    rows = _read("SELECT id, username FROM users WHERE is_admin = 0 AND COALESCE(is_deleted, 0) = 0 ORDER BY username ASC, id ASC")
    if rows is None:
        return _database_error()
    return _response({"users": [{"user_id": row[0], "username": row[1]} for row in rows]})


@gpt_api_bp.get("/matches")
@gpt_required
def matches():
    try:
        conditions, params = _match_conditions()
        limit, offset = _parse_limit_offset()
    except ValueError as exc:
        return _bad_request(str(exc))
    rows = _read(
        f"""
        SELECT m.id, m.tournament_id, t.name, m.home_team, m.away_team, m.kickoff_time,
               m.deadline, m.status, m.home_score, m.away_score, m.playoff_stage, m.league
        FROM matches m LEFT JOIN tournaments t ON t.id = m.tournament_id
        {_where(conditions)}
        ORDER BY m.kickoff_time ASC NULLS LAST, m.id ASC LIMIT %s OFFSET %s
        """,
        tuple(params + [limit, offset]),
    )
    if rows is None:
        return _database_error()
    return _response({"matches": [{"match_id": row[0], "tournament_id": row[1], "tournament_name": row[2], "home_team": row[3], "away_team": row[4], "kickoff_time": _isoformat(row[5]), "deadline": _isoformat(row[6]), "status": row[7], "home_score": row[8], "away_score": row[9], "stage": row[10], "league": row[11]} for row in rows], "limit": limit, "offset": offset})


def _prediction_conditions():
    match_id = _parse_positive_int("match_id")
    user_id = _parse_positive_int("user_id")
    tournament_id = _parse_positive_int("tournament_id")
    date_from, date_to = _parse_date_range()
    finished = _parse_finished()
    conditions = ["u.is_admin = 0", "COALESCE(u.is_deleted, 0) = 0", "m.deadline <= CURRENT_TIMESTAMP"]
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
    if finished is True:
        conditions.append(f"UPPER(m.status) IN {FINISHED_STATUSES_SQL}")
    elif finished is False:
        conditions.append(f"(m.status IS NULL OR UPPER(m.status) NOT IN {FINISHED_STATUSES_SQL})")
    return conditions, params


def _prediction_rows():
    try:
        conditions, params = _prediction_conditions()
        limit, offset = _parse_limit_offset()
    except ValueError as exc:
        return None, _bad_request(str(exc))
    rows = _read(
        f"""
        SELECT p.match_id, p.user_id, p.tournament_id, u.username, t.name, p.home_goals,
               p.away_goals, m.home_team, m.away_team, m.kickoff_time, m.status,
               CASE WHEN UPPER(m.status) IN {FINISHED_STATUSES_SQL} THEN m.home_score END,
               CASE WHEN UPPER(m.status) IN {FINISHED_STATUSES_SQL} THEN m.away_score END,
               CASE WHEN UPPER(m.status) IN {FINISHED_STATUSES_SQL} THEN p.points END,
               m.playoff_stage, m.league
        FROM predictions p
        JOIN matches m ON m.id = p.match_id AND m.tournament_id = p.tournament_id
        JOIN users u ON u.id = p.user_id
        LEFT JOIN tournaments t ON t.id = p.tournament_id
        {_where(conditions)}
        ORDER BY m.kickoff_time ASC NULLS LAST, p.user_id ASC LIMIT %s OFFSET %s
        """,
        tuple(params + [limit, offset]),
    )
    if rows is None:
        return None, _database_error()
    items = [{"match_id": row[0], "user_id": row[1], "tournament_id": row[2], "username": row[3], "tournament_name": row[4], "predicted_home": row[5], "predicted_away": row[6], "home_team": row[7], "away_team": row[8], "kickoff_time": _isoformat(row[9]), "status": row[10], "actual_home": row[11], "actual_away": row[12], "points": row[13], "stage": row[14], "league": row[15]} for row in rows]
    return (items, limit, offset), None


@gpt_api_bp.get("/predictions")
@gpt_required
def predictions():
    result, error = _prediction_rows()
    if error:
        return error
    items, limit, offset = result
    return _response({"predictions": items, "limit": limit, "offset": offset})


@gpt_api_bp.get("/analytics/predictions")
@gpt_required
def analytics_predictions():
    result, error = _prediction_rows()
    if error:
        return error
    items, limit, offset = result
    return _response({"analytics_predictions": items, "limit": limit, "offset": offset})


@gpt_api_bp.get("/analytics/player-summary")
@gpt_api_bp.get("/player-stats")
@gpt_required
def player_summary():
    try:
        user_id = _parse_positive_int("user_id", required=True)
        tournament_id = _parse_positive_int("tournament_id")
        date_from, date_to = _parse_date_range()
    except ValueError as exc:
        return _bad_request(str(exc))
    conditions, params = [f"UPPER(m.status) IN {FINISHED_STATUSES_SQL}", "p.user_id = %s"], [user_id]
    if tournament_id is not None:
        conditions.append("p.tournament_id = %s")
        params.append(tournament_id)
    for date_value, operator in ((date_from, ">="), (date_to, "<=")):
        if date_value is not None:
            conditions.append(f"{MOSCOW_MATCH_DATE_SQL} {operator} %s::date")
            params.append(date_value.isoformat())
    row = _read(
        f"""
        SELECT COUNT(*), COALESCE(SUM(p.points), 0), COALESCE(AVG(p.points), 0),
               COALESCE(SUM(CASE WHEN p.points = 0 THEN 1 ELSE 0 END), 0),
               COALESCE(SUM(CASE WHEN p.points = 5 THEN 1 ELSE 0 END), 0),
               COALESCE(SUM(CASE WHEN p.points = 7 THEN 1 ELSE 0 END), 0),
               COALESCE(SUM(CASE WHEN p.points IN (10, 11) THEN 1 ELSE 0 END), 0)
        FROM predictions p JOIN matches m ON m.id = p.match_id AND m.tournament_id = p.tournament_id
        JOIN users u ON u.id = p.user_id
        WHERE u.is_admin = 0 AND COALESCE(u.is_deleted, 0) = 0 AND {' AND '.join(conditions)}
        """,
        tuple(params),
        one=True,
    )
    if row is None:
        return _database_error()
    return _response({"user_id": user_id, "matches_count": row[0], "total_points": row[1], "average_points": float(row[2]), "points_0": row[3], "points_5": row[4], "points_7": row[5], "points_10_or_11": row[6]})
