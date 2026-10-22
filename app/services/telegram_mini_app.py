"""Telegram launch validation and stateless adapter for existing read-only screens."""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from dataclasses import asdict
from urllib.parse import parse_qsl

from itsdangerous import BadData, URLSafeTimedSerializer

from app.services import telegram_sql_assistant as assistant
from app.services.telegram_bot_ui_service import render
from scripts.telegram_bot_ui import local_screen, parse

AUTH_TTL = 3600
_assistant_lock = threading.RLock()
_rate_lock = threading.Lock()
_requests = []


def validate_launch(raw, now=None):
    """Check HMAC, freshness, and the same explicit admin identity as the bot."""
    token = os.getenv("TELEGRAM_ERROR_BOT_TOKEN", "")
    admin = os.getenv("TELEGRAM_ADMIN_USER_ID", "")
    if not token or not admin.isdigit() or int(admin) <= 0:
        raise PermissionError("unconfigured")
    if not isinstance(raw, str) or not raw or len(raw) > 8192:
        raise PermissionError("invalid_launch")
    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True, max_num_fields=32)
        fields = dict(pairs)
        if len(fields) != len(pairs):
            raise ValueError("duplicate_field")
        signature = fields.pop("hash")
        check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
        key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        expected = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("bad_signature")
        age = (time.time() if now is None else now) - int(fields["auth_date"])
        if age < -30 or age > AUTH_TTL:
            raise ValueError("expired")
        user = json.loads(fields["user"])
        if not isinstance(user, dict) or type(user.get("id")) is not int or user["id"] != int(admin) or user.get("is_bot") is True:
            raise ValueError("wrong_user")
        return user["id"]
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise PermissionError("invalid_launch") from None


def allow_request():
    """Single authorized admin: bounded per-worker burst limit, no background job."""
    now = time.monotonic()
    with _rate_lock:
        _requests[:] = [stamp for stamp in _requests if now - stamp < 60]
        if len(_requests) >= 60:
            return False
        _requests.append(now)
    return True


def screen_payload(data, text=None):
    if not isinstance(data, str) or len(data) > 100:
        raise ValueError("invalid_callback")
    # No administrative host commands or generic SQL endpoint are exposed here.
    if data == "ui:tools" or not data.startswith("ui:"):
        raise ValueError("invalid_callback")
    if text is not None and (not isinstance(text, str) or not 1 <= len(text) <= 1000):
        raise ValueError("invalid_input")
    payload = local_screen(data)
    while payload and "next" in payload:
        data = payload["next"]
        payload = local_screen(data)
    if payload is None or text is not None:
        parse(data)
        payload = render(data, text=text)
    return payload


def question_payload(text, state, uid, signing_key):
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 1000:
        raise ValueError("invalid_question")
    if state is not None and (not isinstance(state, str) or len(state) > 20000):
        raise ValueError("invalid_state")
    serializer = URLSafeTimedSerializer(signing_key, salt="totish-mini-app-question-v1")
    previous = None
    if state:
        try:
            previous = serializer.loads(state, max_age=assistant.TTL)
            if previous["uid"] != uid:
                raise ValueError("wrong_user")
        except (BadData, ValueError, KeyError, TypeError):
            raise ValueError("expired_state") from None
    # Existing parser sessions are process-local. Carry a signed state between
    # requests so follow-ups work across Gunicorn workers without DB writes.
    scope = "mini:" + secrets.token_hex(16)
    with _assistant_lock:
        try:
            if previous:
                assistant.SESSIONS[scope] = (time.monotonic(), assistant.Question(**previous["question"]), previous["pending"])
            result = assistant.answer(text, scope)
            saved = assistant.SESSIONS.get(scope)
            result["state"] = serializer.dumps({"uid": uid, "question": asdict(saved[1]), "pending": saved[2]}) if saved else None
            return result
        finally:
            assistant.SESSIONS.pop(scope, None)
