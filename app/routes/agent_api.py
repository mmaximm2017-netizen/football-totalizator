"""Narrow, token-authenticated admin API for trusted agents.

V1 supports only Russian Premier League administration:
- read tournaments, teams and matches;
- validate/preview a batch of matches;
- create a validated batch atomically;
- set a result once and trigger the existing points recalculation.

No delete, arbitrary SQL, user management or direct points editing exists here.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
from datetime import datetime
from functools import wraps
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify, request

from app.db import close_db, get_db
from app.models.scoring import has_valid_finished_score
from app.services.manual_match_creation_service import (
    ManualMatchCreateData,
    ManualMatchValidationError,
    build_manual_deadline_utc,
    create_manual_match,
)
from app.services.rpl_admin_service import get_rpl_tournament
from app.services.rpl_screenshot_import_service import validate_confirmed_fields
from app.services.rpl_team_catalog import RPL_CANONICAL_TEAMS, match_rpl_team
from app.services.russian_cup_admin_service import get_russian_cup_tournament
from app.services.russian_cup_team_catalog import (
    RUSSIAN_CUP_CANONICAL_TEAMS,
    match_russian_cup_team,
)
from app.services.scoring_recalculation_service import recalc_match_points


logger = logging.getLogger(__name__)
audit_logger = logging.getLogger("totish.agent_audit")
MSK = ZoneInfo("Europe/Moscow")

agent_api_bp = Blueprint("agent_api", __name__, url_prefix="/api/agent/v1")

MAX_BATCH_MATCHES = 32
MAX_MATCH_LIST = 200


def _response(payload, status=200):
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


def _configured_token():
    return (os.getenv("TOTISH_AGENT_TOKEN") or "").strip()


def _provided_token():
    header = request.headers.get("Authorization") or ""
    if not header.startswith("Bearer "):
        return ""
    return header[7:].strip()


def _token_is_valid():
    expected = _configured_token()
    provided = _provided_token()
    return bool(expected and provided and hmac.compare_digest(expected, provided))


def agent_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not _configured_token():
            logger.error("Agent API disabled: TOTISH_AGENT_TOKEN is not configured")
            return _response({"ok": False, "error": "agent_api_disabled"}, 503)
        if not _token_is_valid():
            auth_header = request.headers.get("Authorization") or ""
            scheme = auth_header.split(" ", 1)[0] if auth_header else ""
            token_len = len(auth_header.split(" ", 1)[1].strip()) if " " in auth_header else 0
            audit_logger.warning(
                "agent_request_denied method=%s path=%s remote=%s auth_present=%s scheme=%s token_len=%s",
                request.method,
                request.path,
                request.remote_addr,
                bool(auth_header),
                scheme,
                token_len,
            )
            return _response({"ok": False, "error": "unauthorized"}, 401)
        return view(*args, **kwargs)

    return wrapped


def _audit(action, *, status="success", details=None):
    safe_details = details if isinstance(details, dict) else {}
    audit_logger.info(
        "agent_action action=%s status=%s remote=%s details=%s",
        action,
        status,
        request.remote_addr,
        json.dumps(safe_details, ensure_ascii=False, sort_keys=True, default=str),
    )


def _require_json_object():
    if not request.is_json:
        return None, _response({"ok": False, "error": "json_required"}, 415)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return None, _response({"ok": False, "error": "invalid_json_object"}, 400)
    return payload, None


def _rpl_or_error(cur):
    tournament = get_rpl_tournament(cur)
    if not tournament:
        return None, _response({"ok": False, "error": "rpl_tournament_not_found"}, 404)
    return tournament, None


def _rcup_or_error(cur):
    tournament = get_russian_cup_tournament(cur)
    if not tournament:
        return None, _response({"ok": False, "error": "russian_cup_tournament_not_found"}, 404)
    return tournament, None


def _normalize_rcup_batch_item(raw, index):
    if not isinstance(raw, dict):
        return None, [f"Матч {index}: ожидается JSON-объект"]

    home_raw = str(raw.get("home_team") or "").strip()
    away_raw = str(raw.get("away_team") or "").strip()
    date_value = str(raw.get("date") or raw.get("match_date") or "").strip()
    time_value = str(raw.get("time") or raw.get("match_time") or "").strip()

    errors = []
    home_team, home_status = match_russian_cup_team(home_raw)
    away_team, away_status = match_russian_cup_team(away_raw)

    if home_status != "ready" or not home_team:
        errors.append(f"Матч {index}: домашняя команда не входит в каталог Кубка России")
    if away_status != "ready" or not away_team:
        errors.append(f"Матч {index}: гостевая команда не входит в каталог Кубка России")
    if home_team and away_team and home_team == away_team:
        errors.append(f"Матч {index}: команды должны отличаться")

    try:
        datetime.strptime(date_value, "%Y-%m-%d")
    except ValueError:
        errors.append(f"Матч {index}: некорректная дата")

    try:
        datetime.strptime(time_value, "%H:%M")
    except ValueError:
        errors.append(f"Матч {index}: некорректное время")

    stage = str(raw.get("stage") or "").strip()
    round_value = raw.get("round")
    if round_value not in (None, ""):
        try:
            round_number = int(round_value)
            if round_number <= 0:
                raise ValueError
            if not stage:
                stage = f"Групповой этап — Тур {round_number}"
            elif "тур" not in stage.lower():
                stage = f"{stage} — Тур {round_number}"
        except (TypeError, ValueError):
            errors.append(f"Матч {index}: некорректный номер тура")

    if errors:
        return None, errors

    return {
        "home_team": home_team,
        "away_team": away_team,
        "date": date_value,
        "time": time_value,
        "stage": stage,
        "match_category": "russian_cup",
    }, []


def _prepare_rcup_batch(cur, tournament_id, raw_matches):
    if not isinstance(raw_matches, list) or not raw_matches:
        return [], ["Поле matches должно быть непустым массивом"]
    if len(raw_matches) > MAX_BATCH_MATCHES:
        return [], [f"За один запрос разрешено не более {MAX_BATCH_MATCHES} матчей"]

    prepared = []
    errors = []
    seen = set()
    for index, raw in enumerate(raw_matches, start=1):
        item, item_errors = _normalize_rcup_batch_item(raw, index)
        if item_errors:
            errors.extend(item_errors)
            continue
        try:
            kickoff_utc, _ = build_manual_deadline_utc(
                item["date"], item["time"], reject_early_auto=True,
            )
        except ValueError as exc:
            errors.append(f"Матч {index}: {exc}")
            continue
        duplicate_key = (item["home_team"], item["away_team"], kickoff_utc)
        if duplicate_key in seen:
            errors.append(f"Матч {index}: дубликат внутри запроса")
            continue
        seen.add(duplicate_key)
        cur.execute(
            """
            SELECT id FROM matches
            WHERE tournament_id = %s
              AND league = 'rcup'
              AND home_team = %s
              AND away_team = %s
              AND kickoff_time = %s
            """,
            (tournament_id, item["home_team"], item["away_team"], kickoff_utc),
        )
        existing = cur.fetchone()
        if existing:
            errors.append(f"Матч {index}: уже существует (id={existing[0]})")
            continue
        item["kickoff_time_utc"] = kickoff_utc.isoformat()
        prepared.append(item)
    return prepared, errors


def _find_rcup_matches(cur, tournament_id, home_team, away_team, date_filter=""):
    clauses = [
        "tournament_id = %s",
        "league = 'rcup'",
        "home_team = %s",
        "away_team = %s",
    ]
    params = [tournament_id, home_team, away_team]
    if date_filter:
        clauses.append("(kickoff_time AT TIME ZONE 'Europe/Moscow')::date = %s::date")
        params.append(date_filter)
    cur.execute(
        f"""
        SELECT id, home_team, away_team, kickoff_time, deadline, status,
               home_score, away_score, playoff_stage_manual, match_category,
               league, tournament_id
        FROM matches
        WHERE {' AND '.join(clauses)}
        ORDER BY kickoff_time DESC NULLS LAST, id DESC
        LIMIT 10
        """,
        tuple(params),
    )
    return [_match_json(row) for row in cur.fetchall()]



def _reconcile_candidate_batch(cur, tournament_id, league, raw_matches, normalizer):
    if not isinstance(raw_matches, list) or not raw_matches:
        return None, _response({"ok": False, "error": "matches_required"}, 422)
    if len(raw_matches) > MAX_BATCH_MATCHES:
        return None, _response({"ok": False, "error": "too_many_matches", "max": MAX_BATCH_MATCHES}, 422)

    missing, existing, invalid, seen = [], [], [], set()
    for index, raw in enumerate(raw_matches, start=1):
        item, item_errors = normalizer(raw, index)
        if item_errors:
            invalid.append({"index": index, "errors": item_errors, "input": raw})
            continue
        try:
            kickoff_utc, _ = build_manual_deadline_utc(item["date"], item["time"], reject_early_auto=True)
        except ValueError as exc:
            invalid.append({"index": index, "errors": [str(exc)], "input": raw})
            continue

        key = (item["home_team"], item["away_team"], kickoff_utc)
        if key in seen:
            invalid.append({"index": index, "errors": ["дубликат внутри переданного списка"], "input": raw})
            continue
        seen.add(key)

        cur.execute(
            """
            SELECT id, status, home_score, away_score
            FROM matches
            WHERE tournament_id = %s AND league = %s
              AND home_team = %s AND away_team = %s AND kickoff_time = %s
            LIMIT 1
            """,
            (tournament_id, league, item["home_team"], item["away_team"], kickoff_utc),
        )
        row = cur.fetchone()
        candidate = dict(item)
        candidate["kickoff_time_utc"] = kickoff_utc.isoformat()
        if row:
            existing.append({"id": row[0], "status": row[1], "home_score": row[2], "away_score": row[3], **candidate})
        else:
            missing.append(candidate)

    return {
        "ok": not invalid, "dry_run": True, "scope": league,
        "candidate_count": len(raw_matches),
        "missing_count": len(missing), "existing_count": len(existing), "invalid_count": len(invalid),
        "missing": missing, "existing": existing, "invalid": invalid,
    }, None


EDITABLE_SCHEDULE_STATUSES = {"SCHEDULED", "TIMED", "POSTPONED"}


def _schedule_update_preview(cur, *, tournament_id, league, match_id, date_value, time_value):
    try:
        kickoff_utc, deadline_utc = build_manual_deadline_utc(
            date_value,
            time_value,
            reject_early_auto=True,
        )
    except (ValueError, ManualMatchValidationError) as exc:
        return None, _response({"ok": False, "error": "invalid_schedule", "details": str(exc)}, 422)

    cur.execute(
        """
        SELECT id, home_team, away_team, kickoff_time, deadline, status,
               home_score, away_score, playoff_stage_manual, match_category,
               league, tournament_id
        FROM matches
        WHERE id = %s AND tournament_id = %s AND league = %s
        """,
        (match_id, tournament_id, league),
    )
    row = cur.fetchone()
    if not row:
        return None, _response({"ok": False, "error": "match_not_found"}, 404)

    current = _match_json(row)
    if current["status"] not in EDITABLE_SCHEDULE_STATUSES:
        return None, _response({
            "ok": False,
            "error": "schedule_update_not_allowed_for_status",
            "status": current["status"],
            "allowed_statuses": sorted(EDITABLE_SCHEDULE_STATUSES),
        }, 409)

    cur.execute(
        """
        SELECT id
        FROM matches
        WHERE tournament_id = %s
          AND league = %s
          AND home_team = %s
          AND away_team = %s
          AND kickoff_time = %s
          AND id <> %s
        LIMIT 1
        """,
        (
            tournament_id,
            league,
            current["home_team"],
            current["away_team"],
            kickoff_utc,
            match_id,
        ),
    )
    duplicate = cur.fetchone()
    if duplicate:
        return None, _response({
            "ok": False,
            "error": "schedule_update_would_create_duplicate",
            "duplicate_match_id": duplicate[0],
        }, 409)

    changed = current["kickoff_time"] != kickoff_utc.isoformat()
    preview = {
        "ok": True,
        "dry_run": True,
        "changed": changed,
        "scope": league,
        "match_id": match_id,
        "home_team": current["home_team"],
        "away_team": current["away_team"],
        "status": current["status"],
        "current": {
            "kickoff_time": current["kickoff_time"],
            "kickoff_time_msk": current["kickoff_time_msk"],
            "deadline": current["deadline"],
        },
        "requested": {
            "date": date_value,
            "time": time_value,
            "kickoff_time_utc": kickoff_utc.isoformat(),
            "kickoff_time_msk": kickoff_utc.astimezone(MSK).isoformat(),
            "deadline_utc": deadline_utc.isoformat(),
            "deadline_msk": deadline_utc.astimezone(MSK).isoformat(),
        },
    }
    return preview, None


def _apply_schedule_update(cur, conn, *, tournament_id, league, match_id, date_value, time_value, audit_action):
    preview, error = _schedule_update_preview(
        cur,
        tournament_id=tournament_id,
        league=league,
        match_id=match_id,
        date_value=date_value,
        time_value=time_value,
    )
    if error:
        return error

    if not preview["changed"]:
        conn.rollback()
        return _response({
            "ok": True,
            "changed": False,
            "scope": league,
            "match_id": match_id,
            "message": "schedule_already_matches",
            "match": preview,
        })

    kickoff_utc = datetime.fromisoformat(preview["requested"]["kickoff_time_utc"])
    deadline_utc = datetime.fromisoformat(preview["requested"]["deadline_utc"])

    cur.execute(
        """
        UPDATE matches
        SET kickoff_time = %s,
            deadline = %s
        WHERE id = %s
          AND tournament_id = %s
          AND league = %s
          AND status IN ('SCHEDULED', 'TIMED', 'POSTPONED')
        """,
        (kickoff_utc, deadline_utc, match_id, tournament_id, league),
    )
    if getattr(cur, "rowcount", 1) == 0:
        conn.rollback()
        return _response({"ok": False, "error": "schedule_update_conflict"}, 409)

    conn.commit()
    _audit(
        audit_action,
        details={
            "match_id": match_id,
            "teams": [preview["home_team"], preview["away_team"]],
            "before": preview["current"],
            "after": preview["requested"],
        },
    )
    return _response({
        "ok": True,
        "changed": True,
        "scope": league,
        "match_id": match_id,
        "home_team": preview["home_team"],
        "away_team": preview["away_team"],
        "before": preview["current"],
        "after": preview["requested"],
        "points_recalculated": False,
    })

def _match_json(row):
    kickoff = row[3]
    deadline = row[4]
    return {
        "id": row[0],
        "home_team": row[1],
        "away_team": row[2],
        "kickoff_time": kickoff.isoformat() if kickoff else None,
        "kickoff_time_msk": kickoff.astimezone(MSK).isoformat() if kickoff else None,
        "deadline": deadline.isoformat() if deadline else None,
        "status": row[5],
        "home_score": row[6],
        "away_score": row[7],
        "stage": row[8] or "",
        "match_category": row[9] or "rpl",
        "league": row[10],
        "tournament_id": row[11],
    }


def _normalize_batch_item(raw, index):
    if not isinstance(raw, dict):
        return None, [f"Матч {index}: ожидается JSON-объект"]

    date_value = str(raw.get("date") or raw.get("match_date") or "").strip()
    time_value = str(raw.get("time") or raw.get("match_time") or "").strip()
    checked = validate_confirmed_fields({
        "home_team": raw.get("home_team"),
        "away_team": raw.get("away_team"),
        "date": date_value,
        "time": time_value,
    })
    errors = [f"Матч {index}: {reason}" for reason in checked.get("reasons", [])]

    round_value = raw.get("round")
    stage = str(raw.get("stage") or "").strip()
    if not stage and round_value not in (None, ""):
        try:
            round_number = int(round_value)
            if round_number <= 0:
                raise ValueError
            stage = f"Тур {round_number}"
        except (TypeError, ValueError):
            errors.append(f"Матч {index}: некорректный номер тура")

    if errors:
        return None, errors

    return {
        "home_team": checked["home_team"],
        "away_team": checked["away_team"],
        "date": date_value,
        "time": time_value,
        "stage": stage,
        "match_category": "rpl",
    }, []


def _prepare_batch(cur, tournament_id, raw_matches):
    if not isinstance(raw_matches, list) or not raw_matches:
        return [], ["Поле matches должно быть непустым массивом"]
    if len(raw_matches) > MAX_BATCH_MATCHES:
        return [], [f"За один запрос разрешено не более {MAX_BATCH_MATCHES} матчей"]

    prepared = []
    errors = []
    seen = set()

    for index, raw in enumerate(raw_matches, start=1):
        item, item_errors = _normalize_batch_item(raw, index)
        if item_errors:
            errors.extend(item_errors)
            continue

        kickoff_utc, _ = build_manual_deadline_utc(
            item["date"], item["time"], reject_early_auto=True,
        )
        duplicate_key = (item["home_team"], item["away_team"], kickoff_utc)
        if duplicate_key in seen:
            errors.append(f"Матч {index}: дубликат внутри запроса")
            continue
        seen.add(duplicate_key)

        cur.execute(
            """
            SELECT id
            FROM matches
            WHERE tournament_id = %s
              AND league = 'rpl'
              AND home_team = %s
              AND away_team = %s
              AND kickoff_time = %s
            """,
            (tournament_id, item["home_team"], item["away_team"], kickoff_utc),
        )
        existing = cur.fetchone()
        if existing:
            errors.append(f"Матч {index}: уже существует (id={existing[0]})")
            continue

        item["kickoff_time_utc"] = kickoff_utc.isoformat()
        prepared.append(item)

    return prepared, errors

def _find_matches(cur, tournament_id, home_team, away_team, date_filter=""):
    clauses = [
        "tournament_id = %s",
        "league = 'rpl'",
        "home_team = %s",
        "away_team = %s",
    ]
    params = [tournament_id, home_team, away_team]
    if date_filter:
        clauses.append("(kickoff_time AT TIME ZONE 'Europe/Moscow')::date = %s::date")
        params.append(date_filter)

    cur.execute(
        f"""
        SELECT id, home_team, away_team, kickoff_time, deadline, status,
               home_score, away_score, playoff_stage_manual, match_category,
               league, tournament_id
        FROM matches
        WHERE {' AND '.join(clauses)}
        ORDER BY kickoff_time DESC NULLS LAST, id DESC
        LIMIT 10
        """,
        tuple(params),
    )
    return [_match_json(row) for row in cur.fetchall()]


def _openapi_spec():
    match_schema = {
        "type": "object",
        "required": ["home_team", "away_team", "date", "time"],
        "properties": {
            "home_team": {"type": "string"},
            "away_team": {"type": "string"},
            "date": {"type": "string", "format": "date"},
            "time": {"type": "string", "pattern": "^\\\\d{2}:\\\\d{2}$"},
            "round": {"type": "integer", "minimum": 1},
            "stage": {"type": "string"},
        },
    }
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "TOTISH Agent API",
            "version": "2.0.0",
            "description": (
                "Private administration API for TOTISH RPL matches. "
                "Read operations are safe. Before create/result writes, "
                "show the intended change and obtain explicit user confirmation."
            ),
        },
        "servers": [{"url": "https://totish.ru/api/agent/v1"}],
        "paths": {
            "/capabilities": {
                "get": {
                    "operationId": "getTotishCapabilities",
                    "summary": "Get agent capabilities and safety rules.",
                    "responses": {"200": {"description": "Capabilities"}},
                }
            },
            "/teams": {
                "get": {
                    "operationId": "getRplTeams",
                    "summary": "Get canonical RPL team names.",
                    "parameters": [{
                        "name": "tournament", "in": "query",
                        "schema": {"type": "string", "default": "rpl"},
                    }],
                    "responses": {"200": {"description": "RPL teams"}},
                }
            },
            "/matches": {
                "get": {
                    "operationId": "getRplMatches",
                    "summary": "List RPL matches stored in TOTISH.",
                    "parameters": [
                        {"name": "status", "in": "query", "schema": {"type": "string"}},
                        {"name": "date", "in": "query", "schema": {"type": "string", "format": "date"}},
                        {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 200}},
                    ],
                    "responses": {"200": {"description": "Matches"}},
                },
                "post": {
                    "operationId": "createRplMatches",
                    "summary": "Create validated RPL matches.",
                    "description": (
                        "WRITE ACTION. Call previewRplMatches first and execute only "
                        "after explicit user confirmation."
                    ),
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {
                            "type": "object",
                            "required": ["matches"],
                            "properties": {"matches": {"type": "array", "maxItems": 32, "items": match_schema}},
                        }}},
                    },
                    "responses": {"201": {"description": "Created"}, "422": {"description": "Rejected"}},
                },
            },
            "/matches/upcoming": {
                "get": {
                    "operationId": "getUpcomingRplMatches",
                    "summary": "Get nearest future RPL matches in chronological order.",
                    "parameters": [
                        {"name": "limit", "in": "query",
                         "schema": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}},
                    ],
                    "responses": {"200": {"description": "Upcoming RPL matches"}},
                }
            },
            "/matches/recent": {
                "get": {
                    "operationId": "getRecentRplMatches",
                    "summary": "Get most recently finished RPL matches, newest first.",
                    "parameters": [
                        {"name": "limit", "in": "query",
                         "schema": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}},
                    ],
                    "responses": {"200": {"description": "Recently finished RPL matches"}},
                }
            },
            "/matches/find": {
                "get": {
                    "operationId": "findRplMatch",
                    "summary": "Find a match by teams and optionally date.",
                    "parameters": [
                        {"name": "home_team", "in": "query", "required": True, "schema": {"type": "string"}},
                        {"name": "away_team", "in": "query", "required": True, "schema": {"type": "string"}},
                        {"name": "date", "in": "query", "schema": {"type": "string", "format": "date"}},
                    ],
                    "responses": {"200": {"description": "Matches"}, "422": {"description": "Invalid team"}},
                }
            },
            "/matches/preview": {
                "post": {
                    "operationId": "previewRplMatches",
                    "summary": "Validate/deduplicate matches without writing.",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {
                            "type": "object",
                            "required": ["matches"],
                            "properties": {"matches": {"type": "array", "maxItems": 32, "items": match_schema}},
                        }}},
                    },
                    "responses": {"200": {"description": "Ready"}, "422": {"description": "Rejected"}},
                }
            },
            "/matches/{match_id}/result": {
                "post": {
                    "operationId": "setRplMatchResult",
                    "summary": "Set match result and recalculate points.",
                    "description": (
                        "WRITE ACTION. Find the match first and execute only after "
                        "explicit user confirmation. A different existing result is rejected."
                    ),
                    "parameters": [{
                        "name": "match_id", "in": "path", "required": True,
                        "schema": {"type": "integer"},
                    }],
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {
                            "type": "object",
                            "required": ["home_score", "away_score"],
                            "properties": {
                                "home_score": {"type": "integer", "minimum": 0, "maximum": 99},
                                "away_score": {"type": "integer", "minimum": 0, "maximum": 99},
                            },
                        }}},
                    },
                    "responses": {
                        "200": {"description": "Saved"},
                        "404": {"description": "Not found"},
                        "409": {"description": "Different result already exists"},
                    },
                }
            },
            "/russian-cup/teams": {
                "get": {
                    "operationId": "getRussianCupTeams",
                    "summary": "Get canonical Russian Cup team names.",
                    "responses": {"200": {"description": "Russian Cup teams"}},
                }
            },
            "/russian-cup/matches": {
                "get": {
                    "operationId": "getRussianCupMatches",
                    "summary": "List Russian Cup matches stored in TOTISH.",
                    "parameters": [
                        {"name": "status", "in": "query", "schema": {"type": "string"}},
                        {"name": "date", "in": "query", "schema": {"type": "string", "format": "date"}},
                        {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 200}},
                    ],
                    "responses": {"200": {"description": "Russian Cup matches"}},
                },
                "post": {
                    "operationId": "createRussianCupMatches",
                    "summary": "Create validated Russian Cup matches.",
                    "description": "WRITE ACTION. Call previewRussianCupMatches first and execute only after explicit user confirmation.",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {
                            "type": "object", "required": ["matches"],
                            "properties": {"matches": {"type": "array", "maxItems": 32, "items": match_schema}},
                        }}},
                    },
                    "responses": {"201": {"description": "Created"}, "422": {"description": "Rejected"}},
                },
            },
            "/russian-cup/matches/upcoming": {
                "get": {
                    "operationId": "getUpcomingRussianCupMatches",
                    "summary": "Get nearest future Russian Cup matches in chronological order.",
                    "parameters": [{"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}}],
                    "responses": {"200": {"description": "Upcoming Russian Cup matches"}},
                }
            },
            "/russian-cup/matches/recent": {
                "get": {
                    "operationId": "getRecentRussianCupMatches",
                    "summary": "Get most recently finished Russian Cup matches, newest first.",
                    "parameters": [{"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}}],
                    "responses": {"200": {"description": "Recently finished Russian Cup matches"}},
                }
            },
            "/russian-cup/matches/find": {
                "get": {
                    "operationId": "findRussianCupMatch",
                    "summary": "Find a Russian Cup match by teams and optionally date.",
                    "parameters": [
                        {"name": "home_team", "in": "query", "required": True, "schema": {"type": "string"}},
                        {"name": "away_team", "in": "query", "required": True, "schema": {"type": "string"}},
                        {"name": "date", "in": "query", "schema": {"type": "string", "format": "date"}},
                    ],
                    "responses": {"200": {"description": "Matches"}, "422": {"description": "Invalid team"}},
                }
            },
            "/russian-cup/matches/preview": {
                "post": {
                    "operationId": "previewRussianCupMatches",
                    "summary": "Validate/deduplicate Russian Cup matches without writing.",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {
                            "type": "object", "required": ["matches"],
                            "properties": {"matches": {"type": "array", "maxItems": 32, "items": match_schema}},
                        }}},
                    },
                    "responses": {"200": {"description": "Ready"}, "422": {"description": "Rejected"}},
                }
            },
            "/russian-cup/matches/{match_id}/result": {
                "post": {
                    "operationId": "setRussianCupMatchResult",
                    "summary": "Set Russian Cup match result and recalculate points.",
                    "description": "WRITE ACTION. Find the match first and execute only after explicit user confirmation. A different existing result is rejected.",
                    "parameters": [{"name": "match_id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {
                            "type": "object", "required": ["home_score", "away_score"],
                            "properties": {
                                "home_score": {"type": "integer", "minimum": 0, "maximum": 99},
                                "away_score": {"type": "integer", "minimum": 0, "maximum": 99},
                            },
                        }}},
                    },
                    "responses": {
                        "200": {"description": "Saved"},
                        "404": {"description": "Not found"},
                        "409": {"description": "Different result already exists"},
                    },
                }
            },

            "/matches/reconcile": {
                "post": {
                    "operationId": "reconcileRplMatches",
                    "summary": "Compare a candidate RPL schedule with TOTISH without writing.",
                    "description": "READ-ONLY. Split a full candidate round into missing, existing and invalid matches. Use before creating matches collected from external sources.",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {
                        "type": "object", "required": ["matches"],
                        "properties": {"matches": {"type": "array", "maxItems": 32, "items": match_schema}}
                    }}}},
                    "responses": {"200": {"description": "Reconciliation result"}}
                }
            },
            "/russian-cup/matches/reconcile": {
                "post": {
                    "operationId": "reconcileRussianCupMatches",
                    "summary": "Compare a candidate Russian Cup schedule with TOTISH without writing.",
                    "description": "READ-ONLY. Split a full candidate round into missing, existing and invalid matches. Use before creating matches collected from external sources.",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {
                        "type": "object", "required": ["matches"],
                        "properties": {"matches": {"type": "array", "maxItems": 32, "items": match_schema}}
                    }}}},
                    "responses": {"200": {"description": "Reconciliation result"}}
                }
            },

            "/matches/{match_id}/schedule/preview": {
                "post": {
                    "operationId": "previewRplMatchScheduleUpdate",
                    "summary": "Preview a date/time change for an existing RPL match without writing.",
                    "description": (
                        "READ-ONLY. Find the match first. Show the current and requested schedule. "
                        "Do not call the write action until the user explicitly confirms."
                    ),
                    "parameters": [{"name": "match_id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {
                            "type": "object", "required": ["date", "time"],
                            "properties": {
                                "date": {"type": "string", "format": "date"},
                                "time": {"type": "string", "pattern": "^([01]\\\\d|2[0-3]):[0-5]\\\\d$"},
                            },
                        }}},
                    },
                    "responses": {
                        "200": {"description": "Preview"},
                        "404": {"description": "Match not found"},
                        "409": {"description": "Unsafe/conflicting update"},
                        "422": {"description": "Invalid schedule"},
                    },
                }
            },
            "/matches/{match_id}/schedule": {
                "post": {
                    "operationId": "updateRplMatchSchedule",
                    "summary": "Update date/time of an existing RPL match.",
                    "description": (
                        "WRITE ACTION. Call previewRplMatchScheduleUpdate first and execute only "
                        "after explicit user confirmation. Only SCHEDULED, TIMED and POSTPONED "
                        "matches may be changed. The deadline is recalculated automatically."
                    ),
                    "parameters": [{"name": "match_id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {
                            "type": "object", "required": ["date", "time"],
                            "properties": {
                                "date": {"type": "string", "format": "date"},
                                "time": {"type": "string", "pattern": "^([01]\\\\d|2[0-3]):[0-5]\\\\d$"},
                            },
                        }}},
                    },
                    "responses": {
                        "200": {"description": "Updated"},
                        "404": {"description": "Match not found"},
                        "409": {"description": "Unsafe/conflicting update"},
                        "422": {"description": "Invalid schedule"},
                    },
                }
            },
            "/russian-cup/matches/{match_id}/schedule/preview": {
                "post": {
                    "operationId": "previewRussianCupMatchScheduleUpdate",
                    "summary": "Preview a date/time change for an existing Russian Cup match without writing.",
                    "description": (
                        "READ-ONLY. Find the match first. Show the current and requested schedule. "
                        "Do not call the write action until the user explicitly confirms."
                    ),
                    "parameters": [{"name": "match_id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {
                            "type": "object", "required": ["date", "time"],
                            "properties": {
                                "date": {"type": "string", "format": "date"},
                                "time": {"type": "string", "pattern": "^([01]\\\\d|2[0-3]):[0-5]\\\\d$"},
                            },
                        }}},
                    },
                    "responses": {
                        "200": {"description": "Preview"},
                        "404": {"description": "Match not found"},
                        "409": {"description": "Unsafe/conflicting update"},
                        "422": {"description": "Invalid schedule"},
                    },
                }
            },
            "/russian-cup/matches/{match_id}/schedule": {
                "post": {
                    "operationId": "updateRussianCupMatchSchedule",
                    "summary": "Update date/time of an existing Russian Cup match.",
                    "description": (
                        "WRITE ACTION. Call previewRussianCupMatchScheduleUpdate first and execute "
                        "only after explicit user confirmation. Only SCHEDULED, TIMED and POSTPONED "
                        "matches may be changed. The deadline is recalculated automatically."
                    ),
                    "parameters": [{"name": "match_id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {
                            "type": "object", "required": ["date", "time"],
                            "properties": {
                                "date": {"type": "string", "format": "date"},
                                "time": {"type": "string", "pattern": "^([01]\\\\d|2[0-3]):[0-5]\\\\d$"},
                            },
                        }}},
                    },
                    "responses": {
                        "200": {"description": "Updated"},
                        "404": {"description": "Match not found"},
                        "409": {"description": "Unsafe/conflicting update"},
                        "422": {"description": "Invalid schedule"},
                    },
                }
            },
        },
        "components": {
            "schemas": {},
            "securitySchemes": {"BearerAuth": {"type": "http", "scheme": "bearer"}}
        },
        "security": [{"BearerAuth": []}],
    }


@agent_api_bp.after_request
def _agent_no_store(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@agent_api_bp.get("/openapi.json")
def openapi_schema():
    response = jsonify(_openapi_spec())
    response.headers["Cache-Control"] = "public, max-age=300"
    return response


@agent_api_bp.get("/health")
@agent_required
def health():
    return _response({"ok": True, "service": "totish-agent-api", "version": 2})


@agent_api_bp.get("/capabilities")
@agent_required
def capabilities():
    return _response({
        "ok": True,
        "version": 2,
        "scope": "rpl",
        "scopes": ["rpl", "rcup"],
        "russian_cup": {
            "read": ["get_teams", "get_matches", "get_upcoming_matches", "get_recent_matches", "find_match"],
            "write": ["preview_matches", "create_matches", "set_match_result"],
        },
        "read": [
            "get_teams",
            "get_matches",
            "get_upcoming_matches",
            "get_recent_matches",
            "find_match",
        ],
        "write": ["preview_matches", "create_matches", "set_match_result"],
        "forbidden": [
            "delete_match", "delete_prediction", "edit_user",
            "edit_points_directly", "arbitrary_sql",
            "overwrite_existing_different_result",
        ],
        "safety": {
            "preview_before_create": True,
            "explicit_confirmation_before_write": True,
            "existing_different_result_requires_manual_review": True,
        },
    })


@agent_api_bp.get("/tournaments")
@agent_required
def tournaments():
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT id, name, is_active, start_date, end_date
            FROM tournaments
            ORDER BY id DESC
            """
        )
        rows = cur.fetchall()
        return _response({
            "ok": True,
            "tournaments": [
                {
                    "id": row[0],
                    "name": row[1],
                    "is_active": bool(row[2]),
                    "start_date": row[3],
                    "end_date": row[4],
                }
                for row in rows
            ],
        })
    finally:
        close_db(conn, cur)


@agent_api_bp.get("/teams")
@agent_required
def teams():
    tournament = (request.args.get("tournament") or "rpl").strip().lower()
    if tournament != "rpl":
        return _response({"ok": False, "error": "unsupported_tournament"}, 400)
    return _response({
        "ok": True,
        "tournament": "rpl",
        "teams": list(RPL_CANONICAL_TEAMS),
    })


def _read_limit(default=20, maximum=100):
    try:
        return min(max(int(request.args.get("limit", default)), 1), maximum), None
    except (TypeError, ValueError):
        return None, _response({"ok": False, "error": "invalid_limit"}, 400)


@agent_api_bp.get("/matches/upcoming")
@agent_required
def upcoming_matches():
    limit, error = _read_limit()
    if error:
        return error

    conn = get_db()
    cur = conn.cursor()
    try:
        tournament, error = _rpl_or_error(cur)
        if error:
            return error

        cur.execute(
            """
            SELECT id, home_team, away_team, kickoff_time, deadline, status,
                   home_score, away_score, playoff_stage_manual, match_category,
                   league, tournament_id
            FROM matches
            WHERE tournament_id = %s
              AND league = 'rpl'
              AND kickoff_time >= NOW()
            ORDER BY kickoff_time ASC, id ASC
            LIMIT %s
            """,
            (tournament["id"], limit),
        )
        rows = cur.fetchall()
        return _response({
            "ok": True,
            "kind": "upcoming",
            "tournament": tournament,
            "count": len(rows),
            "matches": [_match_json(row) for row in rows],
        })
    finally:
        close_db(conn, cur)


@agent_api_bp.get("/matches/recent")
@agent_required
def recent_matches():
    limit, error = _read_limit()
    if error:
        return error

    conn = get_db()
    cur = conn.cursor()
    try:
        tournament, error = _rpl_or_error(cur)
        if error:
            return error

        cur.execute(
            """
            SELECT id, home_team, away_team, kickoff_time, deadline, status,
                   home_score, away_score, playoff_stage_manual, match_category,
                   league, tournament_id
            FROM matches
            WHERE tournament_id = %s
              AND league = 'rpl'
              AND status = 'FINISHED'
              AND kickoff_time <= NOW()
            ORDER BY kickoff_time DESC, id DESC
            LIMIT %s
            """,
            (tournament["id"], limit),
        )
        rows = cur.fetchall()
        return _response({
            "ok": True,
            "kind": "recent",
            "tournament": tournament,
            "count": len(rows),
            "matches": [_match_json(row) for row in rows],
        })
    finally:
        close_db(conn, cur)


@agent_api_bp.get("/matches/find")
@agent_required
def find_match():
    home_raw = (request.args.get("home_team") or "").strip()
    away_raw = (request.args.get("away_team") or "").strip()
    date_filter = (request.args.get("date") or "").strip()

    if not home_raw or not away_raw:
        return _response({"ok": False, "error": "home_team_and_away_team_required"}, 400)

    home_team, home_status = match_rpl_team(home_raw)
    away_team, away_status = match_rpl_team(away_raw)
    if home_status != "ready" or not home_team:
        return _response({"ok": False, "error": "unknown_home_team", "value": home_raw}, 422)
    if away_status != "ready" or not away_team:
        return _response({"ok": False, "error": "unknown_away_team", "value": away_raw}, 422)
    if home_team == away_team:
        return _response({"ok": False, "error": "teams_must_differ"}, 422)

    if date_filter:
        try:
            datetime.strptime(date_filter, "%Y-%m-%d")
        except ValueError:
            return _response({"ok": False, "error": "invalid_date"}, 400)

    conn = get_db()
    cur = conn.cursor()
    try:
        tournament, error = _rpl_or_error(cur)
        if error:
            return error
        found = _find_matches(cur, tournament["id"], home_team, away_team, date_filter)
        return _response({
            "ok": True,
            "query": {"home_team": home_team, "away_team": away_team, "date": date_filter or None},
            "count": len(found),
            "matches": found,
            "unique": len(found) == 1,
        })
    finally:
        close_db(conn, cur)


@agent_api_bp.get("/matches")
@agent_required
def matches():
    status = (request.args.get("status") or "").strip().upper()
    date_filter = (request.args.get("date") or "").strip()
    try:
        limit = min(max(int(request.args.get("limit", 100)), 1), MAX_MATCH_LIST)
    except (TypeError, ValueError):
        return _response({"ok": False, "error": "invalid_limit"}, 400)

    if date_filter:
        try:
            datetime.strptime(date_filter, "%Y-%m-%d")
        except ValueError:
            return _response({"ok": False, "error": "invalid_date"}, 400)

    conn = get_db()
    cur = conn.cursor()
    try:
        tournament, error = _rpl_or_error(cur)
        if error:
            return error

        clauses = ["tournament_id = %s", "league = 'rpl'"]
        params = [tournament["id"]]
        if status:
            clauses.append("status = %s")
            params.append(status)
        if date_filter:
            clauses.append("(kickoff_time AT TIME ZONE 'Europe/Moscow')::date = %s::date")
            params.append(date_filter)

        params.append(limit)
        cur.execute(
            f"""
            SELECT id, home_team, away_team, kickoff_time, deadline, status,
                   home_score, away_score, playoff_stage_manual, match_category,
                   league, tournament_id
            FROM matches
            WHERE {' AND '.join(clauses)}
            ORDER BY kickoff_time NULLS LAST, id
            LIMIT %s
            """,
            tuple(params),
        )
        return _response({
            "ok": True,
            "tournament": tournament,
            "matches": [_match_json(row) for row in cur.fetchall()],
        })
    finally:
        close_db(conn, cur)


@agent_api_bp.post("/matches/preview")
@agent_required
def preview_matches():
    payload, error = _require_json_object()
    if error:
        return error

    raw_matches = payload.get("matches")
    if not isinstance(raw_matches, list) or not raw_matches:
        return _response(
            {"ok": False, "dry_run": True, "ready_count": 0, "error_count": 1,
             "matches": [], "errors": ["Поле matches должно быть непустым массивом"]},
            422,
        )

    conn = get_db()
    cur = conn.cursor()
    try:
        tournament, error = _rpl_or_error(cur)
        if error:
            return error
        prepared, errors = _prepare_batch(cur, tournament["id"], raw_matches)
        result = {
            "ok": not errors,
            "dry_run": True,
            "ready_count": len(prepared),
            "error_count": len(errors),
            "matches": prepared,
            "errors": errors,
        }
        _audit(
            "preview_matches",
            status="success" if not errors else "rejected",
            details={"ready_count": len(prepared), "error_count": len(errors)},
        )
        return _response(result, 200 if not errors else 422)
    finally:
        close_db(conn, cur)


@agent_api_bp.post("/matches")
@agent_required
def create_matches():
    payload, error = _require_json_object()
    if error:
        return error

    conn = get_db()
    cur = conn.cursor()
    try:
        tournament, error = _rpl_or_error(cur)
        if error:
            return error

        prepared, errors = _prepare_batch(cur, tournament["id"], payload.get("matches"))
        if errors:
            conn.rollback()
            _audit("create_matches", status="rejected", details={"errors": errors})
            return _response(
                {"ok": False, "created_count": 0, "errors": errors},
                422,
            )

        created = []
        for item in prepared:
            match_id = create_manual_match(
                cur,
                ManualMatchCreateData(
                    tournament_id=tournament["id"],
                    league="rpl",
                    home_team=item["home_team"],
                    away_team=item["away_team"],
                    match_date=item["date"],
                    match_time=item["time"],
                    status="SCHEDULED",
                    stage=item["stage"],
                    match_category="rpl",
                    reject_early_auto_deadline=True,
                ),
            )
            created.append({
                "id": match_id,
                "home_team": item["home_team"],
                "away_team": item["away_team"],
                "date": item["date"],
                "time": item["time"],
                "stage": item["stage"],
            })

        conn.commit()
        _audit("create_matches", details={"created": created})
        return _response({
            "ok": True,
            "created_count": len(created),
            "matches": created,
        }, 201)
    except ManualMatchValidationError as exc:
        conn.rollback()
        _audit("create_matches", status="rejected", details={"error": str(exc)})
        return _response({"ok": False, "error": str(exc)}, 422)
    except Exception:
        conn.rollback()
        logger.exception("Agent API create_matches failed")
        _audit("create_matches", status="error")
        return _response({"ok": False, "error": "internal_error"}, 500)
    finally:
        close_db(conn, cur)


@agent_api_bp.post("/matches/<int:match_id>/result")
@agent_required
def set_match_result(match_id):
    payload, error = _require_json_object()
    if error:
        return error

    try:
        home_score = int(payload.get("home_score"))
        away_score = int(payload.get("away_score"))
    except (TypeError, ValueError):
        return _response({"ok": False, "error": "invalid_score"}, 422)

    if not has_valid_finished_score("FINISHED", home_score, away_score):
        return _response({"ok": False, "error": "invalid_score"}, 422)

    conn = get_db()
    cur = conn.cursor()
    try:
        tournament, error = _rpl_or_error(cur)
        if error:
            return error

        cur.execute(
            """
            SELECT id, home_team, away_team, status, home_score, away_score
            FROM matches
            WHERE id = %s
              AND tournament_id = %s
              AND league = 'rpl'
            FOR UPDATE
            """,
            (match_id, tournament["id"]),
        )
        row = cur.fetchone()
        if not row:
            conn.rollback()
            return _response({"ok": False, "error": "match_not_found"}, 404)

        previous = {
            "status": row[3],
            "home_score": row[4],
            "away_score": row[5],
        }
        if row[4] is not None or row[5] is not None:
            if row[4] == home_score and row[5] == away_score and row[3] == "FINISHED":
                conn.rollback()
                return _response({
                    "ok": True,
                    "changed": False,
                    "match_id": match_id,
                    "home_score": home_score,
                    "away_score": away_score,
                })
            conn.rollback()
            _audit(
                "set_match_result",
                status="rejected",
                details={
                    "match_id": match_id,
                    "reason": "existing_result_requires_manual_review",
                    "previous": previous,
                    "requested": [home_score, away_score],
                },
            )
            return _response({
                "ok": False,
                "error": "existing_result_requires_manual_review",
                "current": previous,
            }, 409)

        cur.execute(
            """
            UPDATE matches
            SET home_score = %s,
                away_score = %s,
                status = 'FINISHED',
                manual_result_override = 1
            WHERE id = %s
              AND tournament_id = %s
              AND league = 'rpl'
            """,
            (home_score, away_score, match_id, tournament["id"]),
        )
        recalc_match_points(
            match_id,
            tournament_id=tournament["id"],
            conn=conn,
            cur=cur,
        )
        conn.commit()
        _audit(
            "set_match_result",
            details={
                "match_id": match_id,
                "teams": [row[1], row[2]],
                "previous": previous,
                "new": {
                    "status": "FINISHED",
                    "home_score": home_score,
                    "away_score": away_score,
                },
            },
        )
        return _response({
            "ok": True,
            "changed": True,
            "match_id": match_id,
            "home_team": row[1],
            "away_team": row[2],
            "home_score": home_score,
            "away_score": away_score,
            "points_recalculated": True,
        })
    except Exception:
        conn.rollback()
        logger.exception("Agent API set_match_result failed match_id=%s", match_id)
        _audit("set_match_result", status="error", details={"match_id": match_id})
        return _response({"ok": False, "error": "internal_error"}, 500)
    finally:
        close_db(conn, cur)

# ---------------------------------------------------------------------------
# Russian Cup agent actions
# ---------------------------------------------------------------------------

@agent_api_bp.get("/russian-cup/teams")
@agent_required
def russian_cup_teams():
    return _response({"ok": True, "tournament": "rcup", "teams": list(RUSSIAN_CUP_CANONICAL_TEAMS)})


@agent_api_bp.get("/russian-cup/matches/upcoming")
@agent_required
def upcoming_russian_cup_matches():
    limit, error = _read_limit()
    if error:
        return error
    conn = get_db()
    cur = conn.cursor()
    try:
        tournament, error = _rcup_or_error(cur)
        if error:
            return error
        cur.execute(
            """
            SELECT id, home_team, away_team, kickoff_time, deadline, status,
                   home_score, away_score, playoff_stage_manual, match_category,
                   league, tournament_id
            FROM matches
            WHERE tournament_id = %s AND league = 'rcup' AND kickoff_time >= NOW()
            ORDER BY kickoff_time ASC, id ASC
            LIMIT %s
            """,
            (tournament["id"], limit),
        )
        rows = cur.fetchall()
        return _response({"ok": True, "kind": "upcoming", "scope": "rcup", "tournament": tournament, "count": len(rows), "matches": [_match_json(row) for row in rows]})
    finally:
        close_db(conn, cur)


@agent_api_bp.get("/russian-cup/matches/recent")
@agent_required
def recent_russian_cup_matches():
    limit, error = _read_limit()
    if error:
        return error
    conn = get_db()
    cur = conn.cursor()
    try:
        tournament, error = _rcup_or_error(cur)
        if error:
            return error
        cur.execute(
            """
            SELECT id, home_team, away_team, kickoff_time, deadline, status,
                   home_score, away_score, playoff_stage_manual, match_category,
                   league, tournament_id
            FROM matches
            WHERE tournament_id = %s AND league = 'rcup'
              AND status = 'FINISHED' AND kickoff_time <= NOW()
            ORDER BY kickoff_time DESC, id DESC
            LIMIT %s
            """,
            (tournament["id"], limit),
        )
        rows = cur.fetchall()
        return _response({"ok": True, "kind": "recent", "scope": "rcup", "tournament": tournament, "count": len(rows), "matches": [_match_json(row) for row in rows]})
    finally:
        close_db(conn, cur)


@agent_api_bp.get("/russian-cup/matches/find")
@agent_required
def find_russian_cup_match():
    home_raw = (request.args.get("home_team") or "").strip()
    away_raw = (request.args.get("away_team") or "").strip()
    date_filter = (request.args.get("date") or "").strip()
    if not home_raw or not away_raw:
        return _response({"ok": False, "error": "home_team_and_away_team_required"}, 400)
    home_team, home_status = match_russian_cup_team(home_raw)
    away_team, away_status = match_russian_cup_team(away_raw)
    if home_status != "ready" or not home_team:
        return _response({"ok": False, "error": "unknown_home_team", "value": home_raw}, 422)
    if away_status != "ready" or not away_team:
        return _response({"ok": False, "error": "unknown_away_team", "value": away_raw}, 422)
    if home_team == away_team:
        return _response({"ok": False, "error": "teams_must_differ"}, 422)
    if date_filter:
        try:
            datetime.strptime(date_filter, "%Y-%m-%d")
        except ValueError:
            return _response({"ok": False, "error": "invalid_date"}, 400)
    conn = get_db()
    cur = conn.cursor()
    try:
        tournament, error = _rcup_or_error(cur)
        if error:
            return error
        found = _find_rcup_matches(cur, tournament["id"], home_team, away_team, date_filter)
        return _response({"ok": True, "scope": "rcup", "query": {"home_team": home_team, "away_team": away_team, "date": date_filter or None}, "count": len(found), "matches": found, "unique": len(found) == 1})
    finally:
        close_db(conn, cur)


@agent_api_bp.get("/russian-cup/matches")
@agent_required
def russian_cup_matches():
    status = (request.args.get("status") or "").strip().upper()
    date_filter = (request.args.get("date") or "").strip()
    try:
        limit = min(max(int(request.args.get("limit", 100)), 1), MAX_MATCH_LIST)
    except (TypeError, ValueError):
        return _response({"ok": False, "error": "invalid_limit"}, 400)
    if date_filter:
        try:
            datetime.strptime(date_filter, "%Y-%m-%d")
        except ValueError:
            return _response({"ok": False, "error": "invalid_date"}, 400)
    conn = get_db()
    cur = conn.cursor()
    try:
        tournament, error = _rcup_or_error(cur)
        if error:
            return error
        clauses = ["tournament_id = %s", "league = 'rcup'"]
        params = [tournament["id"]]
        if status:
            clauses.append("status = %s")
            params.append(status)
        if date_filter:
            clauses.append("(kickoff_time AT TIME ZONE 'Europe/Moscow')::date = %s::date")
            params.append(date_filter)
        params.append(limit)
        cur.execute(
            f"""
            SELECT id, home_team, away_team, kickoff_time, deadline, status,
                   home_score, away_score, playoff_stage_manual, match_category,
                   league, tournament_id
            FROM matches
            WHERE {' AND '.join(clauses)}
            ORDER BY kickoff_time NULLS LAST, id
            LIMIT %s
            """,
            tuple(params),
        )
        rows = cur.fetchall()
        return _response({"ok": True, "scope": "rcup", "tournament": tournament, "count": len(rows), "matches": [_match_json(row) for row in rows]})
    finally:
        close_db(conn, cur)


@agent_api_bp.post("/russian-cup/matches/preview")
@agent_required
def preview_russian_cup_matches():
    payload, error = _require_json_object()
    if error:
        return error
    raw_matches = payload.get("matches")
    if not isinstance(raw_matches, list) or not raw_matches:
        return _response({"ok": False, "dry_run": True, "scope": "rcup", "ready_count": 0, "error_count": 1, "matches": [], "errors": ["Поле matches должно быть непустым массивом"]}, 422)
    conn = get_db()
    cur = conn.cursor()
    try:
        tournament, error = _rcup_or_error(cur)
        if error:
            return error
        prepared, errors = _prepare_rcup_batch(cur, tournament["id"], raw_matches)
        result = {"ok": not errors, "dry_run": True, "scope": "rcup", "ready_count": len(prepared), "error_count": len(errors), "matches": prepared, "errors": errors}
        _audit("preview_russian_cup_matches", status="success" if not errors else "rejected", details={"ready_count": len(prepared), "error_count": len(errors)})
        return _response(result, 200 if not errors else 422)
    finally:
        close_db(conn, cur)


@agent_api_bp.post("/russian-cup/matches")
@agent_required
def create_russian_cup_matches():
    payload, error = _require_json_object()
    if error:
        return error
    conn = get_db()
    cur = conn.cursor()
    try:
        tournament, error = _rcup_or_error(cur)
        if error:
            return error
        prepared, errors = _prepare_rcup_batch(cur, tournament["id"], payload.get("matches"))
        if errors:
            conn.rollback()
            _audit("create_russian_cup_matches", status="rejected", details={"errors": errors})
            return _response({"ok": False, "scope": "rcup", "created_count": 0, "errors": errors}, 422)
        created = []
        for item in prepared:
            match_id = create_manual_match(
                cur,
                ManualMatchCreateData(
                    tournament_id=tournament["id"], league="rcup",
                    home_team=item["home_team"], away_team=item["away_team"],
                    match_date=item["date"], match_time=item["time"],
                    status="SCHEDULED", stage=item["stage"],
                    match_category="russian_cup", reject_early_auto_deadline=True,
                ),
            )
            created.append({"id": match_id, "home_team": item["home_team"], "away_team": item["away_team"], "date": item["date"], "time": item["time"], "stage": item["stage"]})
        conn.commit()
        _audit("create_russian_cup_matches", details={"created": created})
        return _response({"ok": True, "scope": "rcup", "created_count": len(created), "matches": created}, 201)
    except ManualMatchValidationError as exc:
        conn.rollback()
        _audit("create_russian_cup_matches", status="rejected", details={"error": str(exc)})
        return _response({"ok": False, "scope": "rcup", "error": str(exc)}, 422)
    except Exception:
        conn.rollback()
        logger.exception("Agent API create_russian_cup_matches failed")
        _audit("create_russian_cup_matches", status="error")
        return _response({"ok": False, "scope": "rcup", "error": "internal_error"}, 500)
    finally:
        close_db(conn, cur)


@agent_api_bp.post("/russian-cup/matches/<int:match_id>/result")
@agent_required
def set_russian_cup_match_result(match_id):
    payload, error = _require_json_object()
    if error:
        return error
    try:
        home_score = int(payload.get("home_score"))
        away_score = int(payload.get("away_score"))
    except (TypeError, ValueError):
        return _response({"ok": False, "error": "invalid_score"}, 422)
    if not has_valid_finished_score("FINISHED", home_score, away_score):
        return _response({"ok": False, "error": "invalid_score"}, 422)

    conn = get_db()
    cur = conn.cursor()
    try:
        tournament, error = _rcup_or_error(cur)
        if error:
            return error
        cur.execute(
            """
            SELECT id, home_team, away_team, status, home_score, away_score
            FROM matches
            WHERE id = %s AND tournament_id = %s AND league = 'rcup'
            FOR UPDATE
            """,
            (match_id, tournament["id"]),
        )
        row = cur.fetchone()
        if not row:
            conn.rollback()
            return _response({"ok": False, "error": "match_not_found"}, 404)
        previous = {"status": row[3], "home_score": row[4], "away_score": row[5]}
        if row[4] is not None or row[5] is not None:
            if row[4] == home_score and row[5] == away_score and row[3] == "FINISHED":
                conn.rollback()
                return _response({"ok": True, "scope": "rcup", "changed": False, "match_id": match_id, "home_score": home_score, "away_score": away_score})
            conn.rollback()
            _audit("set_russian_cup_match_result", status="rejected", details={"match_id": match_id, "reason": "existing_result_requires_manual_review", "previous": previous, "requested": [home_score, away_score]})
            return _response({"ok": False, "scope": "rcup", "error": "existing_result_requires_manual_review", "current": previous}, 409)

        cur.execute(
            """
            UPDATE matches
            SET home_score = %s, away_score = %s, status = 'FINISHED', manual_result_override = 1
            WHERE id = %s AND tournament_id = %s AND league = 'rcup'
            """,
            (home_score, away_score, match_id, tournament["id"]),
        )
        recalc_match_points(match_id, tournament_id=tournament["id"], conn=conn, cur=cur)
        conn.commit()
        _audit("set_russian_cup_match_result", details={"match_id": match_id, "teams": [row[1], row[2]], "previous": previous, "new": {"status": "FINISHED", "home_score": home_score, "away_score": away_score}})
        return _response({"ok": True, "scope": "rcup", "changed": True, "match_id": match_id, "home_team": row[1], "away_team": row[2], "home_score": home_score, "away_score": away_score, "points_recalculated": True})
    except Exception:
        conn.rollback()
        logger.exception("Agent API set_russian_cup_match_result failed match_id=%s", match_id)
        _audit("set_russian_cup_match_result", status="error", details={"match_id": match_id})
        return _response({"ok": False, "scope": "rcup", "error": "internal_error"}, 500)
    finally:
        close_db(conn, cur)

@agent_api_bp.post("/matches/reconcile")
@agent_required
def reconcile_rpl_matches():
    payload, error = _require_json_object()
    if error:
        return error
    conn = get_db()
    cur = conn.cursor()
    try:
        tournament, error = _rpl_or_error(cur)
        if error:
            return error
        result, error = _reconcile_candidate_batch(cur, tournament["id"], "rpl", payload.get("matches"), _normalize_batch_item)
        if error:
            return error
        _audit("reconcile_rpl_matches", details={k: result[k] for k in ("candidate_count","missing_count","existing_count","invalid_count")})
        return _response(result)
    finally:
        close_db(conn, cur)


@agent_api_bp.post("/russian-cup/matches/reconcile")
@agent_required
def reconcile_russian_cup_matches():
    payload, error = _require_json_object()
    if error:
        return error
    conn = get_db()
    cur = conn.cursor()
    try:
        tournament, error = _rcup_or_error(cur)
        if error:
            return error
        result, error = _reconcile_candidate_batch(cur, tournament["id"], "rcup", payload.get("matches"), _normalize_rcup_batch_item)
        if error:
            return error
        _audit("reconcile_russian_cup_matches", details={k: result[k] for k in ("candidate_count","missing_count","existing_count","invalid_count")})
        return _response(result)
    finally:
        close_db(conn, cur)

@agent_api_bp.post("/matches/<int:match_id>/schedule/preview")
@agent_required
def preview_rpl_match_schedule_update(match_id):
    payload, error = _require_json_object()
    if error:
        return error
    date_value = str(payload.get("date") or "").strip()
    time_value = str(payload.get("time") or "").strip()

    conn = get_db()
    cur = conn.cursor()
    try:
        tournament, error = _rpl_or_error(cur)
        if error:
            return error
        preview, error = _schedule_update_preview(
            cur,
            tournament_id=tournament["id"],
            league="rpl",
            match_id=match_id,
            date_value=date_value,
            time_value=time_value,
        )
        if error:
            return error
        _audit(
            "preview_rpl_match_schedule_update",
            details={"match_id": match_id, "changed": preview["changed"]},
        )
        return _response(preview)
    finally:
        close_db(conn, cur)


@agent_api_bp.post("/matches/<int:match_id>/schedule")
@agent_required
def update_rpl_match_schedule(match_id):
    payload, error = _require_json_object()
    if error:
        return error
    date_value = str(payload.get("date") or "").strip()
    time_value = str(payload.get("time") or "").strip()

    conn = get_db()
    cur = conn.cursor()
    try:
        tournament, error = _rpl_or_error(cur)
        if error:
            return error
        return _apply_schedule_update(
            cur,
            conn,
            tournament_id=tournament["id"],
            league="rpl",
            match_id=match_id,
            date_value=date_value,
            time_value=time_value,
            audit_action="update_rpl_match_schedule",
        )
    except Exception:
        conn.rollback()
        logger.exception("Agent API update_rpl_match_schedule failed match_id=%s", match_id)
        _audit("update_rpl_match_schedule", status="error", details={"match_id": match_id})
        return _response({"ok": False, "error": "internal_error"}, 500)
    finally:
        close_db(conn, cur)


@agent_api_bp.post("/russian-cup/matches/<int:match_id>/schedule/preview")
@agent_required
def preview_russian_cup_match_schedule_update(match_id):
    payload, error = _require_json_object()
    if error:
        return error
    date_value = str(payload.get("date") or "").strip()
    time_value = str(payload.get("time") or "").strip()

    conn = get_db()
    cur = conn.cursor()
    try:
        tournament, error = _rcup_or_error(cur)
        if error:
            return error
        preview, error = _schedule_update_preview(
            cur,
            tournament_id=tournament["id"],
            league="rcup",
            match_id=match_id,
            date_value=date_value,
            time_value=time_value,
        )
        if error:
            return error
        _audit(
            "preview_russian_cup_match_schedule_update",
            details={"match_id": match_id, "changed": preview["changed"]},
        )
        return _response(preview)
    finally:
        close_db(conn, cur)


@agent_api_bp.post("/russian-cup/matches/<int:match_id>/schedule")
@agent_required
def update_russian_cup_match_schedule(match_id):
    payload, error = _require_json_object()
    if error:
        return error
    date_value = str(payload.get("date") or "").strip()
    time_value = str(payload.get("time") or "").strip()

    conn = get_db()
    cur = conn.cursor()
    try:
        tournament, error = _rcup_or_error(cur)
        if error:
            return error
        return _apply_schedule_update(
            cur,
            conn,
            tournament_id=tournament["id"],
            league="rcup",
            match_id=match_id,
            date_value=date_value,
            time_value=time_value,
            audit_action="update_russian_cup_match_schedule",
        )
    except Exception:
        conn.rollback()
        logger.exception("Agent API update_russian_cup_match_schedule failed match_id=%s", match_id)
        _audit("update_russian_cup_match_schedule", status="error", details={"match_id": match_id})
        return _response({"ok": False, "error": "internal_error"}, 500)
    finally:
        close_db(conn, cur)
