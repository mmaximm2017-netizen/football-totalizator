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
MAX_QUERY_ROWS = 500
MAX_QUERY_CHARS = 12_000
ALLOWED_QUERY_PREFIXES = ("select", "with")
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
    for column, value in (("m.match_id", match_id), ("m.tournament_id", tournament_id), ("m.status", status)):
        if value is not None:
            conditions.append(f"{column} = %s")
            params.append(value)
    if team is not None:
        conditions.append("(m.home_team ILIKE %s OR m.away_team ILIKE %s)")
        params.extend([f"%{team}%", f"%{team}%"])
    if date_from is not None:
        conditions.append("(m.kickoff_time AT TIME ZONE 'Europe/Moscow')::date >= %s::date")
        params.append(date_from.isoformat())
    if date_to is not None:
        conditions.append("(m.kickoff_time AT TIME ZONE 'Europe/Moscow')::date <= %s::date")
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
    rows = _read("SELECT tournament_id, tournament_name, is_active, start_date, end_date FROM gpt_safe.tournaments ORDER BY tournament_id")
    if rows is None:
        return _database_error()
    return _response({"tournaments": [{"tournament_id": row[0], "name": row[1], "is_active": bool(row[2]), "start_date": row[3], "end_date": row[4]} for row in rows]})


@gpt_api_bp.get("/users")
@gpt_required
def users():
    rows = _read("SELECT user_id, username FROM gpt_safe.users ORDER BY username ASC, user_id ASC")
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
        SELECT m.match_id, m.tournament_id, m.tournament_name,
               m.home_team, m.away_team, m.kickoff_time,
               m.deadline, m.status, m.home_score, m.away_score,
               m.stage, m.league
        FROM gpt_safe.matches m
        {_where(conditions)}
        ORDER BY m.kickoff_time ASC NULLS LAST, m.match_id ASC LIMIT %s OFFSET %s
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
    conditions = []
    params = []
    for column, value in (("p.match_id", match_id), ("p.user_id", user_id), ("p.tournament_id", tournament_id)):
        if value is not None:
            conditions.append(f"{column} = %s")
            params.append(value)
    if date_from is not None:
        conditions.append("(p.kickoff_time AT TIME ZONE 'Europe/Moscow')::date >= %s::date")
        params.append(date_from.isoformat())
    if date_to is not None:
        conditions.append("(p.kickoff_time AT TIME ZONE 'Europe/Moscow')::date <= %s::date")
        params.append(date_to.isoformat())
    if finished is True:
        conditions.append(f"UPPER(p.status) IN {FINISHED_STATUSES_SQL}")
    elif finished is False:
        conditions.append(f"(p.status IS NULL OR UPPER(p.status) NOT IN {FINISHED_STATUSES_SQL})")
    return conditions, params


def _prediction_rows():
    try:
        conditions, params = _prediction_conditions()
        limit, offset = _parse_limit_offset()
    except ValueError as exc:
        return None, _bad_request(str(exc))
    rows = _read(
        f"""
        SELECT p.match_id, p.user_id, p.tournament_id,
               p.username, p.tournament_name,
               p.predicted_home, p.predicted_away,
               p.home_team, p.away_team, p.kickoff_time, p.status,
               p.actual_home, p.actual_away, p.points,
               p.stage, p.league
        FROM gpt_safe.predictions p
        {_where(conditions)}
        ORDER BY p.kickoff_time ASC NULLS LAST, p.user_id ASC LIMIT %s OFFSET %s
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
    conditions, params = [f"UPPER(p.status) IN {FINISHED_STATUSES_SQL}", "p.user_id = %s"], [user_id]
    if tournament_id is not None:
        conditions.append("p.tournament_id = %s")
        params.append(tournament_id)
    for date_value, operator in ((date_from, ">="), (date_to, "<=")):
        if date_value is not None:
            conditions.append(f"(p.kickoff_time AT TIME ZONE 'Europe/Moscow')::date {operator} %s::date")
            params.append(date_value.isoformat())
    row = _read(
        f"""
        SELECT COUNT(*), COALESCE(SUM(p.points), 0), COALESCE(AVG(p.points), 0),
               COALESCE(SUM(CASE WHEN p.points = 0 THEN 1 ELSE 0 END), 0),
               COALESCE(SUM(CASE WHEN p.points = 5 THEN 1 ELSE 0 END), 0),
               COALESCE(SUM(CASE WHEN p.points = 7 THEN 1 ELSE 0 END), 0),
               COALESCE(SUM(CASE WHEN p.points IN (10, 11) THEN 1 ELSE 0 END), 0)
        FROM gpt_safe.predictions p
        WHERE {' AND '.join(conditions)}
        """,
        tuple(params),
        one=True,
    )
    if row is None:
        return _database_error()
    return _response({"user_id": user_id, "matches_count": row[0], "total_points": row[1], "average_points": float(row[2]), "points_0": row[3], "points_5": row[4], "points_7": row[5], "points_10_or_11": row[6]})


def _validate_analytics_sql(sql):
    """Accept one read-only SELECT/CTE query for the isolated analytics schema."""
    if not isinstance(sql, str):
        raise ValueError("invalid_sql")

    sql = sql.strip()
    if not sql:
        raise ValueError("invalid_sql")

    if len(sql) > MAX_QUERY_CHARS:
        raise ValueError("sql_too_long")

    # Permit an optional final semicolon, but never multiple statements.
    body = sql[:-1].rstrip() if sql.endswith(";") else sql
    if ";" in body:
        raise ValueError("multiple_statements_not_allowed")

    first_word = body.lstrip().split(None, 1)[0].lower() if body.strip() else ""
    if first_word not in ALLOWED_QUERY_PREFIXES:
        raise ValueError("select_only")

    lowered = body.lower()

    # Quoted SQL identifiers can bypass simple schema-name checks, e.g.
    # "public"."users". The analytics endpoint does not need quoted
    # identifiers, so reject them before PostgreSQL sees the query.
    if '"' in body:
        raise ValueError("quoted_identifiers_not_allowed")

    # The database role will ultimately have SELECT only on gpt_safe views.
    # These checks additionally stop obvious attempts to reach metadata or
    # explicitly-qualified non-safe schemas before PostgreSQL sees the query.
    forbidden_fragments = (
        "information_schema",
        "pg_catalog",
        "pg_toast",
        "pg_temp",
        "public.",
    )
    if any(fragment in lowered for fragment in forbidden_fragments):
        raise ValueError("unsafe_relation")

    import re

    forbidden_keywords = {
        "insert", "update", "delete", "merge",
        "create", "alter", "drop", "truncate",
        "copy", "grant", "revoke",
        "call", "do", "execute",
        "reset",
        "vacuum", "analyze", "refresh",
        "lock", "listen", "notify",
    }

    words = set(re.findall(r"\b[a-z_]+\b", lowered))
    if words & forbidden_keywords:
        raise ValueError("select_only")

    # PostgreSQL implicitly exposes pg_catalog even when it is not present
    # in search_path. Block unqualified system objects such as pg_tables,
    # pg_class, pg_roles, pg_settings, etc.
    if re.search(r"\bpg_[a-z0-9_]*\b", lowered):
        raise ValueError("unsafe_relation")

    return body


@gpt_api_bp.post("/query")
@gpt_required
def analytics_query():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _bad_request("invalid_json")

    try:
        sql = _validate_analytics_sql(payload.get("sql"))
    except ValueError as exc:
        return _bad_request(str(exc))

    conn = cur = None
    try:
        conn = gpt_db.get_gpt_db()
        cur = conn.cursor()

        # Resolve unqualified names only inside the isolated safe schema.
        cur.execute("SET LOCAL search_path = gpt_safe")
        cur.execute(sql)

        if cur.description is None:
            return _bad_request("query_must_return_rows")

        columns = [
            item.name if hasattr(item, "name") else item[0]
            for item in cur.description
        ]

        rows = cur.fetchmany(MAX_QUERY_ROWS + 1)
        truncated = len(rows) > MAX_QUERY_ROWS
        rows = rows[:MAX_QUERY_ROWS]

        result = []
        for row in rows:
            item = {}
            for key, value in zip(columns, row):
                if hasattr(value, "isoformat"):
                    value = value.isoformat()
                item[key] = value
            result.append(item)

        return _response({
            "rows": result,
            "row_count": len(result),
            "truncated": truncated,
            "max_rows": MAX_QUERY_ROWS,
        })

    except gpt_db.GPTDatabaseUnavailable:
        return _database_error()
    except Exception as exc:
        logger.warning("gpt_analytics_query_failed type=%s", type(exc).__name__)
        return _bad_request("query_failed")
    finally:
        gpt_db.close_gpt_db(conn, cur)
