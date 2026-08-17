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
from app.services.rpl_team_catalog import RPL_CANONICAL_TEAMS
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
            audit_logger.warning(
                "agent_request_denied method=%s path=%s remote=%s",
                request.method, request.path, request.remote_addr,
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


@agent_api_bp.after_request
def _agent_no_store(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@agent_api_bp.get("/health")
@agent_required
def health():
    return _response({"ok": True, "service": "totish-agent-api", "version": 1})


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
                status = 'FINISHED'
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
