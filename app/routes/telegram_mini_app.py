"""Admin-only Telegram Mini App; the shell is public, every data read is signed."""
import logging
import os

from flask import Blueprint, current_app, jsonify, render_template, request

from app.services.telegram_mini_app import allow_request, question_payload, screen_payload, validate_launch
from app.services.telegram_mini_app_matches import matches_payload

logger = logging.getLogger(__name__)
telegram_mini_app_bp = Blueprint("telegram_mini_app", __name__, url_prefix="/telegram-app")


@telegram_mini_app_bp.after_request
def private_response(response):
    response.headers["Cache-Control"] = "no-store"
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
    return render_template("telegram_mini_app.html", release=os.getenv("TOTISH_RELEASE", "development"))


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
