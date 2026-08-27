import hashlib
import logging
import os
import re
import threading
import time
import traceback
import uuid
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

TELEGRAM_MESSAGE_LIMIT = 3900
ERROR_DEDUP_SECONDS = 300
DEFAULT_OUTBOX_DIR = "/app/runtime/telegram-outbox"
_recent_errors = {}
_recent_errors_lock = threading.Lock()


def _outbox_dir():
    value = os.getenv("TELEGRAM_ERROR_OUTBOX_DIR", DEFAULT_OUTBOX_DIR).strip()
    return Path(value) if value else None

def telegram_monitor_configured():
    outbox = _outbox_dir()
    if outbox is not None and outbox.is_dir():
        return True
    return bool(os.getenv("TELEGRAM_ERROR_BOT_TOKEN") and os.getenv("TELEGRAM_ERROR_CHAT_ID"))

def _redact(value):
    text = str(value or "")
    for env_name in ("TELEGRAM_ERROR_BOT_TOKEN","DATABASE_URL","TOTISH_AGENT_TOKEN","SECRET_KEY"):
        secret = os.getenv(env_name)
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?i)(postgres(?:ql)?://[^:\s/]+:)[^@\s]+@", r"\1[REDACTED]@", text)
    return text

def _fingerprint(exc, source, method, path):
    raw = "|".join((str(source or ""), str(method or ""), str(path or ""), type(exc).__name__, str(exc)))
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()

def _should_send(fingerprint, now=None):
    now = time.monotonic() if now is None else now
    with _recent_errors_lock:
        previous = _recent_errors.get(fingerprint)
        if previous is not None and now - previous < ERROR_DEDUP_SECONDS:
            return False
        _recent_errors[fingerprint] = now
        stale_before = now - (ERROR_DEDUP_SECONDS * 2)
        for key in [k for k, seen_at in _recent_errors.items() if seen_at < stale_before]:
            _recent_errors.pop(key, None)
    return True

def _build_message(exc, *, source, method=None, path=None):
    trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
    trace = _redact(trace)
    lines = [
        "🚨 TOTISH ERROR",
        f"Источник: {_redact(source)}",
        f"Ошибка: {type(exc).__name__}: {_redact(exc)}",
    ]
    if method or path:
        lines.append(f"Запрос: {_redact(method or '-')} {_redact(path or '-')}")
    if trace:
        lines.extend(("", "Traceback:", trace))
    message = "\n".join(lines)
    if len(message) > TELEGRAM_MESSAGE_LIMIT:
        message = message[: TELEGRAM_MESSAGE_LIMIT - 16] + "\n...[truncated]"
    return message

def _enqueue_message(message):
    outbox = _outbox_dir()
    if outbox is None or not outbox.is_dir():
        return False

    filename = f"{time.time_ns()}-{os.getpid()}-{uuid.uuid4().hex}.msg"
    tmp_path = outbox / (filename + ".tmp")
    final_path = outbox / filename

    try:
        with tmp_path.open("x", encoding="utf-8") as handle:
            handle.write(_redact(message))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, final_path)
        return True
    except Exception:
        logger.exception("telegram_error_outbox_write_failed")
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        return False


def _send_message(message):
    token = os.getenv("TELEGRAM_ERROR_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_ERROR_CHAT_ID")
    if not token or not chat_id:
        return
    body = urlencode({"chat_id": chat_id, "text": message}).encode("utf-8")
    request = Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body, method="POST")
    with urlopen(request, timeout=3) as response:
        response.read()

def _safe_send(message):
    if _enqueue_message(message):
        return

    try:
        _send_message(message)
    except Exception:
        logger.exception("telegram_error_notification_failed")

def notify_exception(exc, *, source="flask", method=None, path=None):
    if not telegram_monitor_configured():
        return False
    fingerprint = _fingerprint(exc, source, method, path)
    if not _should_send(fingerprint):
        return False
    message = _build_message(exc, source=source, method=method, path=path)
    thread = threading.Thread(target=_safe_send, args=(message,), daemon=True)
    thread.start()
    return True
