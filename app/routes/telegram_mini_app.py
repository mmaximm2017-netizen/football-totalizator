"""Admin-only Telegram Mini App; the shell is public, every data read is signed."""
import logging
import os
import re

from flask import Blueprint, abort, current_app, jsonify, render_template, request, send_from_directory

from app.models.team_data import CLUB_LOGOS, TEAM_FLAGS, TEAM_NAMES
from app.services.telegram_mini_app import allow_request, question_payload, screen_payload, validate_launch
from app.services.telegram_mini_app_matches import event_matches_payload, matches_payload
from app.services.telegram_round_summary import round_summary_payload

logger = logging.getLogger(__name__)
telegram_mini_app_bp = Blueprint("telegram_mini_app", __name__, url_prefix="/telegram-app")


@telegram_mini_app_bp.after_request
def private_response(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Totish-Release"] = os.getenv("TOTISH_RELEASE", "development")
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' https://telegram.org; "
        "style-src 'self'; img-src 'self' data:; connect-src 'self'; "
        "object-src 'none'; base-uri 'none'; form-action 'self'; "
        "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org"
    )
    return response


@telegram_mini_app_bp.get("")
@telegram_mini_app_bp.get("/")
def index():
    logos = {**CLUB_LOGOS, **{name: f"/static/flags/{code}.svg" for name, code in TEAM_FLAGS.items()}}
    for alias, name in TEAM_NAMES.items():
        if name in logos:
            logos[alias] = logos[name]
    return render_template("telegram_mini_app.html", release=os.getenv("TOTISH_RELEASE", "development"), club_logos=logos)


@telegram_mini_app_bp.get("/assets/<release>/<path:filename>")
def asset(release, filename):
    # Bypass nginx's long-lived /static/ cache for this small private app shell.
    # The version is a URL namespace, never a filesystem path. Old namespaces
    # intentionally receive current files so an old document can recover.
    allowed = {"app.js", "app.css", "design.css", "fonts/manrope-variable.woff"}
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", release) or filename not in allowed:
        abort(404)
    return send_from_directory(os.path.join(current_app.static_folder, "telegram-app"),
                               filename, conditional=False, etag=False, max_age=0)


@telegram_mini_app_bp.post("/api")
def screen():
    request.max_content_length = 32768
    header = request.headers.get("Authorization", "")
    try:
        uid = validate_launch(header[4:] if header.startswith("tma ") else "")
    except PermissionError:
        return jsonify(error="Откройте приложение заново из личного чата с ботом. Доступ только владельцу."), 401
    if not request.is_json:
        return jsonify(error="Ожидается JSON."), 415
    if not allow_request():
        response = jsonify(error="Слишком много запросов. Повторите через минуту.")
        response.headers["Retry-After"] = "60"
        return response, 429
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(error="Неверный запрос."), 400
    try:
        if payload.get("action") == "question":
            result = question_payload(payload.get("text"), payload.get("state"), uid, current_app.secret_key)
        elif payload.get("action") == "matches":
            result = matches_payload(payload.get("kind"), payload.get("round_number"))
        elif payload.get("action") == "extra_matches":
            result = event_matches_payload(payload.get("event"), payload.get("year"))
        elif payload.get("action") == "round_summary":
            result = round_summary_payload(payload.get("kind"), payload.get("round_number"))
        elif payload.get("action", "screen") == "screen":
            result = screen_payload(payload.get("callback"), payload.get("text"))
        else:
            raise ValueError("invalid_action")
        return jsonify(result)
    except ValueError:
        return jsonify(error="Запрос устарел или заполнен неверно. Откройте раздел заново."), 400
    except Exception as exc:
        # Never include signed launch data, questions, tokens or DB details.
        logger.warning("telegram_mini_app_failed type=%s", type(exc).__name__)
        return jsonify(error="Не удалось загрузить данные. Попробуйте ещё раз."), 503
