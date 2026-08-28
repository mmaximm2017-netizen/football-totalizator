#!/usr/bin/env python3
"""One-shot, admin-only Telegram inline-keyboard poller for TOTISH."""

import argparse
import json
import logging
import os
import re
import select
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

try:
    import fcntl
except ModuleNotFoundError:  # Linux-only host lock; keeps local Windows tests importable.
    fcntl = None

try:
    from host_telegram_notifier import load_env
except ModuleNotFoundError:  # Allows unit tests to import the script as a module.
    from scripts.host_telegram_notifier import load_env


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = Path.home() / ".local" / "state" / "totish"
OFFSET_FILE = STATE_DIR / "telegram-admin-offset.json"
LOCK_FILE = STATE_DIR / "telegram-admin-poller.lock"
MAX_MESSAGE_LENGTH = 3800
POLL_TIMEOUT = 50
QUERY_TIMEOUT = 20
BACKOFF_SECONDS = (1, 2, 5, 10)
CONTAINER_NAME = "football-totalizator-app-1"
MSK = ZoneInfo("Europe/Moscow")
RU_MONTHS = (
    "",
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
CLOCK_HOURS = ("🕛", "🕐", "🕑", "🕒", "🕓", "🕔", "🕕", "🕖", "🕗", "🕘", "🕙", "🕚")
CLOCK_HALVES = ("🕧", "🕜", "🕝", "🕞", "🕟", "🕠", "🕡", "🕢", "🕣", "🕤", "🕥", "🕦")
HELPER_ACTIONS = {
    "today",
    "today-dashboard",
    "prediction-status",
    "prediction-scores",
    "table-tournaments",
    "ranking",
    "calendar",
    "problems",
    "system",
}

logger = logging.getLogger("totish.telegram_admin")
TODAY_PHOTO_ROOT = (ROOT / "runtime" / "telegram-outbox" / "admin-cards").resolve()
_today_photo_messages = {}


def _config():
    values = load_env()
    token = values.get("TELEGRAM_ERROR_BOT_TOKEN") or os.getenv("TELEGRAM_ERROR_BOT_TOKEN")
    chat_id = values.get("TELEGRAM_ERROR_CHAT_ID") or os.getenv("TELEGRAM_ERROR_CHAT_ID")
    if not token or not chat_id or not str(chat_id).lstrip("-").isdigit():
        raise RuntimeError("telegram admin configuration is unavailable")
    return token, int(chat_id)


def _state():
    try:
        value = json.loads(OFFSET_FILE.read_text(encoding="utf-8"))
        offset = value.get("next_update_id") if isinstance(value, dict) else None
        return int(offset) if isinstance(offset, int) and offset >= 0 else None
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None


def _save_offset(next_update_id):
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=STATE_DIR, delete=False) as handle:
        json.dump({"next_update_id": next_update_id}, handle)
        handle.flush()
        os.fsync(handle.fileno())
        temp_path = Path(handle.name)
    os.chmod(temp_path, 0o600)
    os.replace(temp_path, OFFSET_FILE)


def _lock():
    if fcntl is None:
        raise RuntimeError("fcntl is required on the Telegram poller host")
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle = LOCK_FILE.open("a+")
    os.chmod(LOCK_FILE, 0o600)
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def _telegram_request(token, method, payload, timeout=POLL_TIMEOUT, long_poll=False):
    body = urlencode(payload).encode("utf-8")
    request = Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=body,
        method="POST",
    )
    with urlopen(request, timeout=timeout + 10 if long_poll else timeout) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict) or value.get("ok") is not True:
        raise RuntimeError("telegram_api_error")
    return value.get("result")


def _telegram_error_text(error):
    return f"{error.reason} {error.read().decode('utf-8', errors='replace')}".lower()


def _answer_callback_best_effort(token, callback_id):
    try:
        _telegram_request(
            token,
            "answerCallbackQuery",
            {"callback_query_id": callback_id},
            timeout=1,
        )
    except HTTPError as exc:
        error_text = _telegram_error_text(exc)
        expired_markers = (
            "query is too old",
            "query id is invalid",
            "response timeout expired",
        )
        if exc.code == 400 and any(marker in error_text for marker in expired_markers):
            logger.warning("telegram_admin_callback_ack_expired")
            return False
        logger.warning("telegram_admin_callback_ack_http_error code=%s", exc.code)
        return False
    except URLError as exc:
        logger.warning(
            "telegram_admin_callback_ack_network_error type=%s",
            type(exc).__name__,
        )
        return False
    except (RuntimeError, json.JSONDecodeError, TimeoutError) as exc:
        logger.warning(
            "telegram_admin_callback_ack_error type=%s",
            type(exc).__name__,
        )
        return False
    return True


def _keyboard(rows):
    return json.dumps({"inline_keyboard": rows}, ensure_ascii=False)


def _dashboard_keyboard(refresh_callback, extra_rows=()):
    rows = list(extra_rows)
    rows.extend(
        [
            [{"text": "⚽ Сегодня", "callback_data": "adm:today"}, {"text": "🏆 Таблица", "callback_data": "adm:table"}],
            [{"text": "📅 Календарь", "callback_data": "adm:calendar"}, {"text": "🚨 Проблемы", "callback_data": "adm:problems"}],
            [{"text": "⚙️ Система", "callback_data": "adm:system"}],
            [{"text": "🔄 Обновить", "callback_data": refresh_callback}],
        ]
    )
    return _keyboard(rows)


def main_keyboard():
    return _keyboard(
        [
            [{"text": "⚽ Сегодня", "callback_data": "adm:today"}, {"text": "🏆 Таблица", "callback_data": "adm:table"}],
            [{"text": "📅 Календарь", "callback_data": "adm:calendar"}, {"text": "🚨 Проблемы", "callback_data": "adm:problems"}],
            [{"text": "⚙️ Система", "callback_data": "adm:system"}],
            [{"text": "🔄 Обновить", "callback_data": "adm:main"}],
        ]
    )


def _menu_button():
    return [{"text": "⬅️ Меню", "callback_data": "adm:main"}]


def _safe_text(text):
    return text[:MAX_MESSAGE_LENGTH]


def _friendly_tournament(name):
    return {"Чемпионат России 🇷🇺": "РПЛ", "Кубок России": "Кубок России"}.get(name, name)


def _updated_line(now=None):
    now = now or datetime.now(MSK)
    return f"🕒 Обновлено: {now.astimezone(MSK).strftime('%H:%M')}"


def _format_date(value):
    value = value.astimezone(MSK)
    return f"{value.day} {RU_MONTHS[value.month]}"


def _clock_emoji(value):
    value = value.astimezone(MSK)
    hour = value.hour % 12
    return CLOCK_HALVES[hour] if value.minute >= 30 else CLOCK_HOURS[hour]


def _tournament_heading(name, table=False):
    friendly = _friendly_tournament(name)
    if friendly == "РПЛ":
        return f"{'🇷🇺' if table else '🏆'} РПЛ"
    return f"🏆 {friendly}"


def _main_text():
    return f"🤖 ТОТИШ — Администратор\n{_updated_line()}"


def _format_today(payload):
    matches = payload.get("matches", [])
    lines = ["⚽ Сегодня", _updated_line()]
    if not matches:
        lines.extend(["", "Сегодня матчей нет."])
        return _safe_text("\n".join(lines)), _dashboard_keyboard("adm:today:refresh")
    groups = {}
    for match in matches:
        groups.setdefault(_friendly_tournament(match["tournament_name"]), []).append(match)
    for tournament, group in sorted(groups.items()):
        lines.extend(["", _tournament_heading(tournament), ""])
        for match in group:
            kickoff = datetime.fromisoformat(match["kickoff_time"]).astimezone(MSK)
            lines.append(f"{match['home_team']} — {match['away_team']}")
            lines.append(f"{_clock_emoji(kickoff)} {kickoff.strftime('%H:%M')}")
            prediction_label = "🎯 Прогнозы:" if len(group) == 1 else "🎯"
            lines.extend([f"{prediction_label} {match['predicted_count']}/{match['participant_count']}", ""])
    return _safe_text("\n".join(lines).rstrip()), _dashboard_keyboard("adm:today:refresh")


def _format_today_dashboard(payload):
    if not payload.get("matches") or payload.get("photo_error"):
        return (*_format_today(payload), payload)
    text = _safe_text(f"⚽ Сегодня\n{_updated_line()}\n\nВсе подробности находятся в графической карточке.")
    return text, _dashboard_keyboard("adm:today:refresh"), payload


def _format_prediction_status(payload):
    match = payload.get("match")
    lines = ["🎯 Прогнозы", _updated_line()]
    if not match:
        lines.extend(["", "Нет ближайшего матча."])
        return _safe_text("\n".join(lines)), _dashboard_keyboard("adm:predictions")
    kickoff = datetime.fromisoformat(match["kickoff_time"]).astimezone(MSK)
    lines.extend(["", _tournament_heading(match["tournament_name"]), "", f"{match['home_team']} — {match['away_team']}", f"📅 {_format_date(kickoff)} • {kickoff.strftime('%H:%M')}", ""])
    participants = payload.get("participants", [])
    placed = sum(1 for participant in participants if participant.get("has_prediction"))
    lines.extend([f"👥 Поставили: {placed}/{len(participants)}", ""])
    for participant in participants:
        lines.append(f"{'✅' if participant.get('has_prediction') else '❌'} {participant['username']}")
    match_id = match["match_id"]
    rows = []
    if not payload.get("deadline_open"):
        rows.append([{"text": "👁 Показать прогнозы", "callback_data": f"adm:pred:show:{match_id}"}])
    if payload.get("deadline_open"):
        lines.extend(["", "🔒 Сами прогнозы будут доступны после дедлайна."])
    return _safe_text("\n".join(lines)), _dashboard_keyboard(f"adm:pred:refresh:{match_id}", rows)


def _format_prediction_scores(payload):
    match = payload.get("match")
    if not match:
        return _safe_text(f"🎯 Прогнозы\n{_updated_line()}\n\nНет ближайшего матча."), _dashboard_keyboard("adm:predictions")
    if payload.get("deadline_open"):
        return _safe_text(f"🎯 Прогнозы\n{_updated_line()}\n\n🔒 Прогнозы пока закрыты."), _dashboard_keyboard(f"adm:pred:refresh:{match['match_id']}")
    lines = ["🎯 Прогнозы", _updated_line(), "", _tournament_heading(match["tournament_name"]), "", f"{match['home_team']} — {match['away_team']}", "🔓 Прогнозы открыты", ""]
    for prediction in payload.get("predictions", []):
        score = "нет прогноза" if prediction["home_goals"] is None or prediction["away_goals"] is None else f"{prediction['home_goals']}:{prediction['away_goals']}"
        lines.append(f"{prediction['username']} — {score}")
    return _safe_text("\n".join(lines)), _dashboard_keyboard(f"adm:pred:show:{match['match_id']}")


def _format_table(payload):
    tournament = payload.get("tournament")
    if not tournament:
        return _safe_text(f"🏆 Турнирная таблица\n{_updated_line()}\n\nТурнир не найден."), _dashboard_keyboard("adm:table:rpl")
    lines = ["🏆 Турнирная таблица", _updated_line(), "", _tournament_heading(tournament["name"], table=True), ""]
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    for row in payload.get("ranking", [])[:20]:
        place = medals.get(row["place"], f"{row['place']}.")
        lines.append(f"{place} {row['username']} — {row['points']}")
    kind = "rpl" if tournament["name"] == "Чемпионат России 🇷🇺" else "cup"
    tabs = [[{"text": "🇷🇺 РПЛ", "callback_data": "adm:table:rpl"}, {"text": "🏆 Кубок", "callback_data": "adm:table:cup"}]]
    return _safe_text("\n".join(lines)), _dashboard_keyboard(f"adm:table:{kind}", tabs)


def _format_calendar(payload):
    lines = ["📅 Календарь", _updated_line()]
    matches = payload.get("matches", [])
    if not matches:
        lines.extend(["", "Ближайших матчей нет."])
    else:
        grouped = {}
        for match in sorted(matches, key=lambda item: item["kickoff_time"]):
            kickoff = datetime.fromisoformat(match["kickoff_time"]).astimezone(MSK)
            date_group = grouped.setdefault(kickoff.date(), {})
            date_group.setdefault(match["tournament_name"], []).append((kickoff, match))
        for match_date, tournaments in grouped.items():
            lines.extend(["", f"{match_date.day} {RU_MONTHS[match_date.month]}"])
            for tournament_name, group in tournaments.items():
                lines.extend(["", _tournament_heading(tournament_name), ""])
                for kickoff, match in group:
                    lines.extend([f"{match['home_team']} — {match['away_team']}", f"{_clock_emoji(kickoff)} {kickoff.strftime('%H:%M')}", ""])
    return _safe_text("\n".join(lines)), _dashboard_keyboard("adm:calendar:refresh")


def _format_problems(payload):
    issues = payload.get("issues", [])
    title = "🚨 Обнаружена проблема" if issues else "🟢 Всё работает штатно"
    text = f"{title}\n{_updated_line()}\n\n" + ("\n".join(issues) if issues else "Проблем не обнаружено.")
    return _safe_text(text), _dashboard_keyboard("adm:problems:refresh")


def _format_system(payload):
    system = payload.get("system", {})
    labels = (("container", "Сайт"), ("local", "Local health"), ("db", "База данных"), ("public", "Public health"))
    lines = ["⚙️ ТОТИШ — система", _updated_line(), "", "🩺 Сервисы", ""]
    for key, label in labels:
        value = system.get(key, "unknown")
        icon = "🟢" if value == "ok" else "🔴" if value == "problem" else "⚪"
        lines.append(f"{icon} {label}")
    lines.extend(["", "⚙️ Workers", ""])
    worker_labels = {"deadline": "Дедлайны", "result": "Обработка результатов"}
    for worker in payload.get("worker_statuses", []):
        label = worker_labels.get(worker.get("key"), worker["label"])
        if worker["state"] == "ok":
            lines.append(f"🟢 {label} — {worker['minutes']} мин назад")
        elif worker["state"] == "stale":
            lines.append(f"🟠 {label} — {worker['minutes']} мин назад")
        else:
            lines.append(f"🔴 {label} — нет данных")
    lines.extend(["", "📡 Telegram", "", "⚪ Relay — нет данных"])
    return _safe_text("\n".join(lines)), _dashboard_keyboard("adm:system:refresh")


def _host_metadata(docker_bin):
    def stat_mtime(path):
        completed = subprocess.run(
            ["stat", "-c", "%Y", path],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
        return completed.stdout.strip() if completed.returncode == 0 else ""

    state = subprocess.run(
        [docker_bin, "inspect", "-f", "{{.State.Status}}|{{.State.Running}}|{{.State.Restarting}}", CONTAINER_NAME],
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )
    return {
        "TOTISH_DIGEST_HOST_NOW_EPOCH": str(int(datetime.now(timezone.utc).timestamp())),
        "TOTISH_DEADLINE_WORKER_MTIME": stat_mtime("/var/log/totish-deadline-push.log"),
        "TOTISH_RESULT_WORKER_MTIME": stat_mtime("/var/log/totish-match-result-push.log"),
        "TOTISH_CONTAINER_STATE": state.stdout.strip() if state.returncode == 0 else "missing",
    }


class PersistentHelper:
    def __init__(self, docker_bin=None):
        self.docker_bin = docker_bin or os.getenv("TOTISH_DOCKER_BIN", "docker")
        self.process = None
        self.next_request_id = 1

    def close(self):
        process = self.process
        self.process = None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

    def _start(self):
        if self.process is not None and self.process.poll() is None:
            return self.process
        self.close()
        self.process = subprocess.Popen(
            [
                self.docker_bin,
                "exec",
                "-i",
                CONTAINER_NAME,
                "python",
                "-u",
                "-m",
                "app.services.telegram_admin_service",
                "--server",
            ],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        return self.process

    def _readline(self, process, timeout):
        ready, _, _ = select.select([process.stdout], [], [], timeout)
        if not ready:
            raise subprocess.TimeoutExpired(process.args, timeout)
        line = process.stdout.readline()
        if not line:
            raise BrokenPipeError("telegram helper closed stdout")
        return line

    def _exchange(self, request, timeout):
        request_id = self.next_request_id
        self.next_request_id += 1
        request = {"id": request_id, **request}
        for attempt in range(2):
            process = self._start()
            try:
                process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
                process.stdin.flush()
                response = json.loads(self._readline(process, timeout))
                if not isinstance(response, dict) or response.get("id") != request_id:
                    raise RuntimeError("container_query_invalid_response")
                if response.get("ok") is not True or not isinstance(response.get("payload"), dict):
                    raise RuntimeError("container_query_failed")
                return response["payload"]
            except (BrokenPipeError, OSError, json.JSONDecodeError):
                self.close()
                if attempt == 1:
                    raise RuntimeError("container_query_failed") from None
            except subprocess.TimeoutExpired:
                self.close()
                raise
        raise RuntimeError("container_query_failed")

    def warmup(self, timeout=QUERY_TIMEOUT):
        payload = self._exchange({"command": "warmup"}, timeout)
        if payload != {"ready": True}:
            raise RuntimeError("container_warmup_failed")
        return payload

    def query(self, action, *, kind=None, match_id=None, metadata=None, timeout=QUERY_TIMEOUT):
        if action not in HELPER_ACTIONS:
            raise ValueError("invalid_helper_action")
        if kind is not None and kind not in {"rpl", "cup"}:
            raise ValueError("invalid_helper_kind")
        if match_id is not None and (isinstance(match_id, bool) or not isinstance(match_id, int) or match_id < 1):
            raise ValueError("invalid_helper_match_id")
        return self._exchange(
            {
                "action": action,
                "kind": kind,
                "match_id": match_id,
                "metadata": metadata or {},
            },
            timeout,
        )


_helper = PersistentHelper()


def _docker_query(action, kind=None, match_id=None, timeout=QUERY_TIMEOUT):
    started = time.monotonic()
    host_metadata = _host_metadata(_helper.docker_bin)
    payload = _helper.query(
        action,
        kind=kind,
        match_id=match_id,
        metadata={
            "host_now_epoch": host_metadata["TOTISH_DIGEST_HOST_NOW_EPOCH"],
            "deadline_worker_mtime": host_metadata["TOTISH_DEADLINE_WORKER_MTIME"],
            "result_worker_mtime": host_metadata["TOTISH_RESULT_WORKER_MTIME"],
            "container_state": host_metadata["TOTISH_CONTAINER_STATE"],
        },
        timeout=timeout,
    )
    logger.info(
        "telegram_admin_query action=%s duration_ms=%s",
        action,
        int((time.monotonic() - started) * 1000),
    )
    return payload


def _edit_or_send(token, chat_id, text, markup, message_id=None):
    payload = {"chat_id": chat_id, "text": _safe_text(text), "reply_markup": markup}
    if message_id is None:
        return _telegram_request(token, "sendMessage", payload, timeout=10)
    payload["message_id"] = message_id
    try:
        return _telegram_request(token, "editMessageText", payload, timeout=10)
    except HTTPError as exc:
        if exc.code == 400 and "message is not modified" in _telegram_error_text(exc):
            logger.info("telegram_admin_message_not_modified")
            return None
        raise


def _deliver_with_retry(token, chat_id, text, markup, message_id=None, sleep=None):
    sleep = sleep or time.sleep
    method = "sendMessage" if message_id is None else "editMessageText"
    for attempt in (1, 2):
        started = time.monotonic()
        try:
            result = _edit_or_send(token, chat_id, text, markup, message_id)
            logger.info(
                "telegram_admin_delivery method=%s attempt=%s duration_ms=%s",
                method,
                attempt,
                int((time.monotonic() - started) * 1000),
            )
            return True, result
        except (HTTPError, URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            transient = isinstance(exc, (URLError, TimeoutError)) or (
                isinstance(exc, HTTPError) and (exc.code == 429 or exc.code >= 500)
            )
            if transient and attempt == 1:
                logger.warning(
                    "telegram_admin_delivery_retry method=%s type=%s attempt=%s",
                    method,
                    type(exc).__name__,
                    attempt,
                )
                sleep(0.2)
                continue
            logger.error(
                "telegram_admin_delivery_failed method=%s type=%s attempts=%s duration_ms=%s",
                method,
                type(exc).__name__,
                attempt,
                int((time.monotonic() - started) * 1000),
            )
            return False, None
    return False, None


def _multipart_photo_request(token, method, fields, photo_path, timeout=15):
    boundary = f"----totish-{uuid.uuid4().hex}"
    chunks = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())
    chunks.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"totish_today.png\"\r\nContent-Type: image/png\r\n\r\n".encode()
        + photo_path.read_bytes()
        + b"\r\n"
    )
    chunks.append(f"--{boundary}--\r\n".encode())
    request = Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=b"".join(chunks),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("ok") is not True:
        raise RuntimeError("telegram_photo_api_error")
    return payload.get("result")


def _today_photo_path(relative_path):
    if not isinstance(relative_path, str) or Path(relative_path).is_absolute():
        raise ValueError("invalid_today_photo_path")
    candidate = (ROOT / "runtime" / "telegram-outbox" / relative_path).resolve()
    if not candidate.is_relative_to(TODAY_PHOTO_ROOT) or candidate.suffix.lower() != ".png":
        raise ValueError("invalid_today_photo_path")
    return candidate


def _send_today_photo(token, chat_id, path, caption):
    return _multipart_photo_request(
        token,
        "sendPhoto",
        {"chat_id": chat_id, "caption": caption},
        path,
    )


def _edit_today_photo(token, chat_id, message_id, path, caption):
    media = json.dumps({"type": "photo", "media": "attach://photo", "caption": caption}, ensure_ascii=False)
    try:
        return _multipart_photo_request(
            token,
            "editMessageMedia",
            {"chat_id": chat_id, "message_id": message_id, "media": media},
            path,
        )
    except HTTPError as exc:
        if exc.code == 400 and "message is not modified" in _telegram_error_text(exc):
            logger.info("telegram_admin_today_photo_not_modified")
            return None
        raise


def _sync_today_photos(token, chat_id, payload):
    started = time.monotonic()
    if payload.get("photo_error"):
        logger.warning("telegram_admin_today_photo_failed stage=render")
        return False
    relative_paths = payload.get("photo_paths") or []
    if not relative_paths:
        return True
    existing_ids = _today_photo_messages.get(chat_id, [])
    next_ids = []
    paths = []
    try:
        paths = [_today_photo_path(path) for path in relative_paths]
        for index, path in enumerate(paths):
            caption = f"⚽ Сегодня • {index + 1}/{len(paths)}" if len(paths) > 1 else "⚽ Сегодня"
            method = "sendPhoto"
            result = None
            if index < len(existing_ids):
                method = "editMessageMedia"
                delivery_started = time.monotonic()
                try:
                    result = _edit_today_photo(token, chat_id, existing_ids[index], path, caption)
                    logger.info(
                        "telegram_admin_today_photo_delivery method=%s duration_ms=%s",
                        method,
                        int((time.monotonic() - delivery_started) * 1000),
                    )
                    next_ids.append(existing_ids[index])
                    continue
                except Exception as exc:  # noqa: BLE001 - sendPhoto fallback keeps Today usable.
                    logger.warning("telegram_admin_today_photo_failed stage=edit type=%s", type(exc).__name__)
            method = "sendPhoto"
            delivery_started = time.monotonic()
            result = _send_today_photo(token, chat_id, path, caption)
            logger.info(
                "telegram_admin_today_photo_delivery method=%s duration_ms=%s",
                method,
                int((time.monotonic() - delivery_started) * 1000),
            )
            if isinstance(result, dict) and isinstance(result.get("message_id"), int):
                next_ids.append(result["message_id"])
        if next_ids:
            _today_photo_messages[chat_id] = next_ids
        return True
    except Exception as exc:  # noqa: BLE001 - photo failure must not fail the callback.
        logger.warning("telegram_admin_today_photo_failed stage=delivery type=%s", type(exc).__name__)
        return False
    finally:
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("telegram_admin_today_photo_failed stage=cleanup")
        logger.info(
            "telegram_admin_today_photo_total duration_ms=%s",
            int((time.monotonic() - started) * 1000),
        )


def _render_callback(callback_data):
    prediction_callback = re.fullmatch(r"adm:pred:(show|refresh):([1-9][0-9]*)", callback_data)
    if prediction_callback:
        action, match_id = prediction_callback.groups()
        payload = _docker_query(
            "prediction-scores" if action == "show" else "prediction-status",
            match_id=int(match_id),
        )
        return _format_prediction_scores(payload) if action == "show" else _format_prediction_status(payload)
    action = callback_data.replace(":refresh", "")
    if action == "adm:main":
        return _main_text(), main_keyboard()
    if action == "adm:today":
        payload = _docker_query("today-dashboard")
        logger.info("telegram_admin_today_data duration_ms=%s", payload.get("data_duration_ms", 0))
        logger.info("telegram_admin_today_render duration_ms=%s", payload.get("render_duration_ms", 0))
        return _format_today_dashboard(payload)
    if action == "adm:predictions":
        payload = _docker_query("today-dashboard")
        logger.info("telegram_admin_today_data duration_ms=%s", payload.get("data_duration_ms", 0))
        logger.info("telegram_admin_today_render duration_ms=%s", payload.get("render_duration_ms", 0))
        return _format_today_dashboard(payload)
    if action == "adm:table":
        return _format_table(_docker_query("ranking", "rpl"))
    if action == "adm:table:rpl":
        return _format_table(_docker_query("ranking", "rpl"))
    if action == "adm:table:cup":
        return _format_table(_docker_query("ranking", "cup"))
    if action == "adm:calendar":
        return _format_calendar(_docker_query("calendar"))
    if action == "adm:problems":
        return _format_problems(_docker_query("problems"))
    if action == "adm:system":
        return _format_system(_docker_query("system"))
    return None


def _chat_id(update):
    if not isinstance(update, dict):
        return None
    callback = update.get("callback_query")
    source = callback.get("message") if isinstance(callback, dict) else update.get("message")
    chat = source.get("chat") if isinstance(source, dict) else None
    chat_id = chat.get("id") if isinstance(chat, dict) else None
    return chat_id if isinstance(chat_id, int) else None


def _handle_update(token, admin_chat_id, update):
    chat_id = _chat_id(update)
    callback = update.get("callback_query")
    if chat_id != admin_chat_id:
        if isinstance(callback, dict) and callback.get("id"):
            _answer_callback_best_effort(token, callback["id"])
        logger.warning("telegram_admin_unauthorized_update chat_id=%s", chat_id)
        return True
    if isinstance(callback, dict):
        callback_id = callback.get("id")
        callback_started = time.monotonic()
        try:
            rendered = _render_callback(callback.get("data", ""))
        except Exception as exc:  # noqa: BLE001 - Telegram must receive a safe fallback message.
            logger.error("telegram_admin_query_failed type=%s", type(exc).__name__)
            _deliver_with_retry(
                token,
                chat_id,
                "⚠️ Не удалось получить данные ТОТИШа.\nПопробуйте обновить позже.",
                _keyboard([[{"text": "⬅️ Меню", "callback_data": "adm:main"}]]),
                callback.get("message", {}).get("message_id"),
            )
        else:
            if rendered is not None:
                text, markup = rendered[:2]
                _deliver_with_retry(
                    token,
                    chat_id,
                    text,
                    markup,
                    callback.get("message", {}).get("message_id"),
                )
                if len(rendered) == 3:
                    photo_payload = rendered[2]
                    if not _sync_today_photos(token, chat_id, photo_payload):
                        fallback_text, fallback_markup = _format_today(photo_payload)
                        _deliver_with_retry(
                            token,
                            chat_id,
                            fallback_text,
                            fallback_markup,
                            callback.get("message", {}).get("message_id"),
                        )
        logger.info(
            "telegram_admin_callback_delivery_ready duration_ms=%s",
            int((time.monotonic() - callback_started) * 1000),
        )
        if callback_id:
            _answer_callback_best_effort(token, callback_id)
        logger.info(
            "telegram_admin_callback duration_ms=%s",
            int((time.monotonic() - callback_started) * 1000),
        )
        return True
    message = update.get("message")
    if isinstance(message, dict) and message.get("text") in {"/start", "/menu"}:
        _deliver_with_retry(token, chat_id, _main_text(), main_keyboard())
    return True


def _poll_cycle(token, admin_chat_id):
    try:
        offset = _state()
        payload = {"limit": 25, "timeout": POLL_TIMEOUT, "allowed_updates": json.dumps(["message", "callback_query"])}
        if offset is not None:
            payload["offset"] = offset
        updates = _telegram_request(token, "getUpdates", payload, timeout=POLL_TIMEOUT, long_poll=True)
        if not isinstance(updates, list):
            raise TypeError("telegram_updates_invalid")
        for update in sorted(
            (item for item in updates if isinstance(item, dict)),
            key=lambda item: item.get("update_id", -1) if isinstance(item.get("update_id", -1), int) else -1,
        ):
            update_id = update.get("update_id")
            if not isinstance(update_id, int):
                logger.warning("telegram_admin_malformed_update_without_id")
                continue
            if _handle_update(token, admin_chat_id, update):
                _save_offset(update_id + 1)
                logger.info("telegram_admin_update_processed update_id=%s", update_id)
        return 0
    except Exception as exc:  # noqa: BLE001 - poll cycle uses bounded retry in daemon mode.
        logger.error("telegram_admin_poller_failed type=%s", type(exc).__name__)
        return 1


def run_once():
    token, admin_chat_id = _config()
    lock = _lock()
    if lock is None:
        return 0
    try:
        return _poll_cycle(token, admin_chat_id)
    finally:
        _helper.close()
        lock.close()


def run_forever(max_cycles=None, sleep=time.sleep):
    token, admin_chat_id = _config()
    lock = _lock()
    if lock is None:
        return 0
    logger.info("telegram_admin_started")
    warmup_started = time.monotonic()
    try:
        _helper.warmup(timeout=QUERY_TIMEOUT)
        logger.info(
            "telegram_admin_helper_warmup duration_ms=%s",
            int((time.monotonic() - warmup_started) * 1000),
        )
    except Exception as exc:  # noqa: BLE001 - warmup is an optional startup optimization.
        _helper.close()
        logger.warning(
            "telegram_admin_helper_warmup_failed type=%s duration_ms=%s",
            type(exc).__name__,
            int((time.monotonic() - warmup_started) * 1000),
        )
    backoff_index = 0
    cycles = 0
    try:
        while max_cycles is None or cycles < max_cycles:
            status = _poll_cycle(token, admin_chat_id)
            cycles += 1
            if status == 0:
                backoff_index = 0
                continue
            delay = BACKOFF_SECONDS[backoff_index]
            logger.warning("telegram_admin_api_backoff seconds=%s", delay)
            sleep(delay)
            backoff_index = min(backoff_index + 1, len(BACKOFF_SECONDS) - 1)
        return 0
    except KeyboardInterrupt:
        logger.info("telegram_admin_stopped")
        return 0
    finally:
        _helper.close()
        lock.close()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--send-menu", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    token, admin_chat_id = _config()
    if args.send_menu:
        _edit_or_send(token, admin_chat_id, _main_text(), main_keyboard())
        return 0
    if args.once:
        return run_once()
    return run_forever()


if __name__ == "__main__":
    raise SystemExit(main())
