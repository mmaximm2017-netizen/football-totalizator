#!/usr/bin/env python3
"""One-shot, admin-only Telegram inline-keyboard poller for TOTISH."""

import argparse
import json
import logging
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
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
CONTAINER_NAME = "football-totalizator-app-1"
MSK = ZoneInfo("Europe/Moscow")

logger = logging.getLogger("totish.telegram_admin")


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


def _telegram_request(token, method, payload, timeout=POLL_TIMEOUT):
    body = urlencode(payload).encode("utf-8")
    request = Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=body,
        method="POST",
    )
    with urlopen(request, timeout=timeout + 10) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict) or value.get("ok") is not True:
        raise RuntimeError("telegram_api_error")
    return value.get("result")


def _answer_callback_best_effort(token, callback_id):
    try:
        _telegram_request(
            token,
            "answerCallbackQuery",
            {"callback_query_id": callback_id},
            timeout=10,
        )
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace").lower()
        error_text = f"{exc.reason} {error_body}".lower()
        expired_markers = (
            "query is too old",
            "query id is invalid",
            "response timeout expired",
        )
        if exc.code == 400 and any(marker in error_text for marker in expired_markers):
            logger.warning("telegram_admin_callback_ack_expired")
            return False
        raise
    return True


def _keyboard(rows):
    return json.dumps({"inline_keyboard": rows}, ensure_ascii=False)


def main_keyboard():
    return _keyboard(
        [
            [{"text": "⚽ Сегодня", "callback_data": "adm:today"}, {"text": "🚨 Проблемы", "callback_data": "adm:problems"}],
            [{"text": "🎯 Прогнозы", "callback_data": "adm:predictions"}, {"text": "🏆 Таблица", "callback_data": "adm:table"}],
            [{"text": "📅 Календарь", "callback_data": "adm:calendar"}, {"text": "⚙️ Система", "callback_data": "adm:system"}],
        ]
    )


def _menu_button():
    return [{"text": "⬅️ Меню", "callback_data": "adm:main"}]


def _safe_text(text):
    return text[:MAX_MESSAGE_LENGTH]


def _friendly_tournament(name):
    return {"Чемпионат России 🇷🇺": "РПЛ", "Кубок России": "Кубок России"}.get(name, name)


def _format_today(payload):
    matches = payload.get("matches", [])
    lines = ["⚽ Сегодня"]
    if not matches:
        lines.extend(["", "Сегодня матчей нет."])
        return "\n".join(lines), _keyboard([[{"text": "🔄 Обновить", "callback_data": "adm:today:refresh"}], _menu_button()])
    groups = {}
    for match in matches:
        groups.setdefault(_friendly_tournament(match["tournament_name"]), []).append(match)
    for tournament, group in sorted(groups.items()):
        lines.extend(["", tournament])
        for match in group:
            kickoff = datetime.fromisoformat(match["kickoff_time"]).astimezone(MSK).strftime("%H:%M")
            lines.append(f"{kickoff} — {match['home_team']} — {match['away_team']}")
            lines.append(f"Прогнозы: {match['predicted_count']}/{match['participant_count']}")
    return "\n".join(lines), _keyboard([[{"text": "🎯 Прогнозы", "callback_data": "adm:predictions"}, {"text": "🔄 Обновить", "callback_data": "adm:today:refresh"}], _menu_button()])


def _format_prediction_status(payload):
    match = payload.get("match")
    lines = ["🎯 Прогнозы"]
    if not match:
        lines.extend(["", "Нет ближайшего матча."])
        return "\n".join(lines), _keyboard([[{"text": "🔄 Обновить", "callback_data": "adm:predictions:refresh"}], _menu_button()])
    kickoff = datetime.fromisoformat(match["kickoff_time"]).astimezone(MSK).strftime("%d.%m %H:%M")
    lines.extend(["", f"{match['home_team']} — {match['away_team']}", f"{kickoff}", ""])
    participants = payload.get("participants", [])
    placed = sum(1 for participant in participants if participant.get("has_prediction"))
    lines.append(f"Поставили: {placed}/{len(participants)}")
    for participant in participants:
        lines.append(f"{'✅' if participant.get('has_prediction') else '❌'} {participant['username']}")
    match_id = match["match_id"]
    rows = [[{"text": "🔄 Обновить", "callback_data": f"adm:pred:refresh:{match_id}"}]]
    if not payload.get("deadline_open"):
        rows[0].insert(0, {"text": "👁 Показать прогнозы", "callback_data": f"adm:pred:show:{match_id}"})
    if payload.get("deadline_open"):
        lines.extend(["", "🔒 Сами прогнозы будут доступны после дедлайна."])
    rows.append(_menu_button())
    return _safe_text("\n".join(lines)), _keyboard(rows)


def _format_prediction_scores(payload):
    match = payload.get("match")
    if not match:
        return "🎯 Прогнозы\n\nНет ближайшего матча.", _keyboard([_menu_button()])
    if payload.get("deadline_open"):
        return "🔒 Прогнозы пока закрыты.", _keyboard([[{"text": "🔄 Обновить", "callback_data": f"adm:pred:refresh:{match['match_id']}"}], _menu_button()])
    lines = ["🎯 Прогнозы", "", f"{match['home_team']} — {match['away_team']}", "Дедлайн прошёл", ""]
    for prediction in payload.get("predictions", []):
        score = "нет прогноза" if prediction["home_goals"] is None or prediction["away_goals"] is None else f"{prediction['home_goals']}:{prediction['away_goals']}"
        lines.append(f"{prediction['username']} — {score}")
    return _safe_text("\n".join(lines)), _keyboard([[{"text": "🔄 Обновить", "callback_data": f"adm:pred:show:{match['match_id']}"}], _menu_button()])


def _format_table(payload):
    tournament = payload.get("tournament")
    if not tournament:
        return "🏆 Таблица\n\nТурнир не найден.", _keyboard([_menu_button()])
    lines = [f"🏆 {_friendly_tournament(tournament['name'])}", ""]
    for row in payload.get("ranking", [])[:20]:
        lines.append(f"{row['place']}. {row['username']} — {row['points']}")
    return _safe_text("\n".join(lines)), _keyboard([_menu_button()])


def _format_calendar(payload):
    lines = ["📅 Ближайшие матчи"]
    matches = payload.get("matches", [])
    if not matches:
        lines.extend(["", "Ближайших матчей нет."])
    else:
        for match in matches:
            kickoff = datetime.fromisoformat(match["kickoff_time"]).astimezone(MSK)
            lines.extend(["", kickoff.strftime("%d %B"), _friendly_tournament(match["tournament_name"]), f"{kickoff.strftime('%H:%M')} — {match['home_team']} — {match['away_team']}"])
    return _safe_text("\n".join(lines)), _keyboard([[{"text": "🔄 Обновить", "callback_data": "adm:calendar:refresh"}], _menu_button()])


def _format_problems(payload):
    issues = payload.get("issues", [])
    text = "🚨 Проблемы\n\n" + ("\n".join(issues) if issues else "✅ Проблем не обнаружено.")
    return _safe_text(text), _keyboard([[{"text": "🔄 Проверить снова", "callback_data": "adm:problems:refresh"}], _menu_button()])


def _format_system(payload):
    system = payload.get("system", {})
    labels = (("container", "Сайт"), ("local", "Local health"), ("db", "База данных"), ("public", "Public health"))
    lines = ["⚙️ ТОТИШ — система", ""]
    for key, label in labels:
        value = system.get(key, "unknown")
        icon = "🟢" if value == "ok" else "🔴" if value == "problem" else "⚪"
        lines.append(f"{icon} {label}")
    for worker in payload.get("worker_statuses", []):
        if worker["state"] == "ok":
            lines.append(f"🟢 {worker['label']} — {worker['minutes']} мин назад")
        elif worker["state"] == "stale":
            lines.append(f"🟠 {worker['label']} — {worker['minutes']} мин назад")
        else:
            lines.append(f"🔴 {worker['label']} — нет данных")
    lines.append("⚪ Telegram relay — нет данных")
    return _safe_text("\n".join(lines)), _keyboard([[{"text": "🔄 Обновить", "callback_data": "adm:system:refresh"}, {"text": "🚨 Проблемы", "callback_data": "adm:problems"}], _menu_button()])


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


def _docker_query(action, kind=None, match_id=None, timeout=QUERY_TIMEOUT):
    docker_bin = os.getenv("TOTISH_DOCKER_BIN", "docker")
    command = [docker_bin, "exec"]
    for key, value in _host_metadata(docker_bin).items():
        command.extend(["-e", f"{key}={value}"])
    command.extend([CONTAINER_NAME, "python", "-m", "app.services.telegram_admin_service", "--action", action])
    if kind:
        command.extend(["--kind", kind])
    if match_id is not None:
        command.extend(["--match-id", str(match_id)])
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=timeout, check=False)
    if completed.returncode != 0:
        raise RuntimeError("container_query_failed")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("container_query_invalid_json") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError("container_query_failed")
    return payload


def _edit_or_send(token, chat_id, text, markup, message_id=None):
    payload = {"chat_id": chat_id, "text": _safe_text(text), "reply_markup": markup}
    if message_id is None:
        return _telegram_request(token, "sendMessage", payload, timeout=10)
    payload["message_id"] = message_id
    return _telegram_request(token, "editMessageText", payload, timeout=10)


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
        return "🤖 ТОТИШ — Администратор", main_keyboard()
    if action == "adm:today":
        return _format_today(_docker_query("today"))
    if action == "adm:predictions":
        return _format_prediction_status(_docker_query("prediction-status"))
    if action == "adm:table":
        return "🏆 Турнирная таблица", _keyboard([[{"text": "🇷🇺 РПЛ", "callback_data": "adm:table:rpl"}], [{"text": "🏆 Кубок России", "callback_data": "adm:table:cup"}], _menu_button()])
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
        if callback_id:
            _answer_callback_best_effort(token, callback_id)
        try:
            rendered = _render_callback(callback.get("data", ""))
        except Exception as exc:  # noqa: BLE001 - Telegram must receive a safe fallback message.
            logger.error("telegram_admin_query_failed type=%s", type(exc).__name__)
            _edit_or_send(
                token,
                chat_id,
                "⚠️ Не удалось получить данные ТОТИШа.\nПопробуйте обновить позже.",
                _keyboard([[{"text": "⬅️ Меню", "callback_data": "adm:main"}]]),
                callback.get("message", {}).get("message_id"),
            )
            return True
        if rendered is None:
            return True
        text, markup = rendered
        _edit_or_send(token, chat_id, text, markup, callback.get("message", {}).get("message_id"))
        return True
    message = update.get("message")
    if isinstance(message, dict) and message.get("text") in {"/start", "/menu"}:
        _edit_or_send(token, chat_id, "🤖 ТОТИШ — Администратор", main_keyboard())
    return True


def run_once():
    token, admin_chat_id = _config()
    lock = _lock()
    if lock is None:
        return 0
    try:
        offset = _state()
        payload = {"limit": 25, "timeout": POLL_TIMEOUT, "allowed_updates": json.dumps(["message", "callback_query"])}
        if offset is not None:
            payload["offset"] = offset
        updates = _telegram_request(token, "getUpdates", payload, timeout=POLL_TIMEOUT)
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
        return 0
    except Exception as exc:  # noqa: BLE001 - one-shot poller reports controlled failure.
        logger.error("telegram_admin_poller_failed type=%s", type(exc).__name__)
        return 1
    finally:
        lock.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--send-menu", action="store_true")
    args = parser.parse_args()
    token, admin_chat_id = _config()
    if args.send_menu:
        _edit_or_send(token, admin_chat_id, "🤖 ТОТИШ — Администратор", main_keyboard())
        return 0
    return run_once()


if __name__ == "__main__":
    raise SystemExit(main())
