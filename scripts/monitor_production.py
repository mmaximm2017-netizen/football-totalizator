#!/usr/bin/env python3

import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = Path.home() / ".local" / "state" / "totish"
STATE_FILE = STATE_DIR / "production-monitor-dedupe.json"
INCIDENT_STATE_FILE = STATE_DIR / "production-monitor-incidents.json"

sys.path.insert(0, str(ROOT / "scripts"))
from host_telegram_notifier import send_message
from monitor_recovery_state import recover_incident, remember_incident


def load_state():
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_state(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    tmp.replace(STATE_FILE)


def alert(key, details):
    now = int(time.time())
    state = load_state()
    fingerprint = hashlib.sha256(key.encode("utf-8")).hexdigest()
    previous = int(state.get(fingerprint, 0) or 0)

    if previous and now - previous < 300:
        print(f"DEDUPED: {key}")
        return False

    descriptions = {
        "container:": (
            "🚨 ТОТИШ: приложение остановлено",
            "Контейнер приложения ТОТИШ остановлен или работает неправильно.",
        ),
        "health:local": (
            "🚨 ТОТИШ: приложение не отвечает",
            "Внутри сервера приложение ТОТИШ перестало отвечать.",
        ),
        "health:db": (
            "🚨 ТОТИШ: проблема с базой данных",
            "Возникла проблема с базой данных или внутренней проверкой сайта.",
        ),
        "health:public": (
            "🚨 ТОТИШ: сайт недоступен из интернета",
            "ТОТИШ не открывается через nginx/TLS.",
        ),
        "control_plane:": (
            "🚨 ТОТИШ: служебные файлы VPS не совпадают с версией сайта",
            "Служебные production-файлы на VPS рассинхронизированы с запущенной версией ТОТИШа.",
        ),
        "backup:": (
            "🚨 ТОТИШ: проблема с резервной копией базы",
            "Ежедневная резервная копия базы отсутствует или слишком старая.",
        ),
    }

    title = "🚨 ТОТИШ: обнаружена техническая проблема"
    human_text = "Обнаружена проблема в работе ТОТИШа."

    for prefix, value in descriptions.items():
        if key.startswith(prefix):
            title, human_text = value
            break

    now_text = datetime.now().astimezone().strftime("%d.%m.%Y %H:%M:%S %Z")

    message = (
        f"{title}\n\n"
        f"{human_text}\n\n"
        f"Время: {now_text}\n\n"
        "Технические детали:\n"
        f"production_monitor / {key}\n"
        f"{details}"
    )

    send_message(message)
    remember_incident(INCIDENT_STATE_FILE, key)

    state[fingerprint] = now
    cutoff = now - 3600
    state = {
        k: v
        for k, v in state.items()
        if int(v or 0) >= cutoff
    }
    save_state(state)

    print(f"ALERT SENT: {key}")
    return True


def run(command, timeout=8):
    return subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def fetch_json(url, timeout=5):
    with urlopen(url, timeout=timeout) as response:
        body = response.read().decode("utf-8", errors="replace")
        if response.status != 200:
            raise RuntimeError(
                f"HTTP {response.status}: {body[:300]}"
            )
    return json.loads(body)


def check_container():
    result = run([
        "docker",
        "inspect",
        "-f",
        "{{.State.Status}}|{{.State.Running}}|{{.State.Restarting}}",
        "football-totalizator-app-1",
    ])

    if result.returncode != 0:
        alert(
            "container:missing",
            "Что именно произошло:\n"
            "Docker не видит основной контейнер приложения ТОТИШ.\n"
            "Сайт, скорее всего, сейчас полностью не работает.\n\n"
            "Причина: контейнер отсутствует, удалён или Docker не смог получить его состояние.",
        )
        return False

    state = result.stdout.strip()

    if state != "running|true|false":
        alert(
            f"container:{state}",
            "Что именно произошло:\n"
            "Основной контейнер ТОТИШ существует, но находится в неправильном состоянии.\n"
            "Flask/Gunicorn могут быть остановлены или перезапускаться.\n\n"
            f"Состояние Docker: {state}",
        )
        return False

    recover_incident(INCIDENT_STATE_FILE, "container", send_message)
    return True

def check_local_health():
    try:
        health = fetch_json("http://127.0.0.1:8000/health")

        if health.get("status") != "ok":
            alert(
                "health:local:bad_status",
                "Что именно произошло:\n"
                "Flask/Gunicorn отвечает внутри VPS, но сообщает, что приложение нездорово.\n"
                "Пользователи могут получать ошибки при работе с ТОТИШем.\n\n"
                f"Ответ /health: {health}",
            )
            return False

        recover_incident(INCIDENT_STATE_FILE, "health:local", send_message)
        return True

    except Exception as exc:
        alert(
            "health:local:no_response",
            "Что именно произошло:\n"
            "Flask/Gunicorn не отвечает даже внутри самого VPS.\n"
            "Проблема находится в приложении или его внутреннем HTTP-сервере, а не только в nginx.\n\n"
            f"Причина: {type(exc).__name__}: {exc}",
        )
        return False

def check_db_health():
    health = None
    last_exc = None

    for attempt in range(1, 3):
        try:
            health = fetch_json(
                "http://127.0.0.1:8000/health/db",
                timeout=10,
            )
            break
        except Exception as exc:  # noqa: BLE001 - every probe failure gets one bounded retry.
            last_exc = exc
            if attempt == 1:
                print(f"DB HEALTH RETRY: {type(exc).__name__}: {exc}")
                time.sleep(2)

    if health is None:
        alert(
            "health:db:connection",
            "Что именно произошло:\n"
            "Проверка базы данных не ответила два раза подряд.\n"
            "Возможные функции ТОТИШа, зависящие от БД, сейчас могут не работать.\n\n"
            f"Причина последней ошибки: {type(last_exc).__name__}: {last_exc}",
        )
        return False

    bad = {
        key: health.get(key)
        for key in ("db", "active_tournament", "ranking")
        if health.get(key) != "ok"
    }

    if not bad:
        recover_incident(INCIDENT_STATE_FILE, "health:db", send_message)
        return True

    explanations = {
        "db": (
            "Основное соединение с базой данных работает неправильно. "
            "Матчи, прогнозы, пользователи и результаты могут быть недоступны."
        ),
        "active_tournament": (
            "Не прошла проверка активного турнира. "
            "ТОТИШ может неправильно определять текущий турнир."
        ),
        "ranking": (
            "Не прошла проверка турнирной таблицы. "
            "Расчёт мест или очков игроков может работать неправильно."
        ),
    }

    lines = ["Что именно произошло:"]

    for key, value in bad.items():
        lines.append(f"• {explanations.get(key, key)}")
        lines.append(f"  Состояние: {key} = {value}")

    alert(
        "health:db:" + ",".join(sorted(bad)),
        "\n".join(lines),
    )
    return False

def check_database_backup():
    backup_dir = STATE_DIR / "backups"

    # The directory is created by the backup job itself. Before the first
    # scheduled run after rollout, do not emit a false alert.
    if not backup_dir.exists():
        recover_incident(INCIDENT_STATE_FILE, "backup", send_message)
        return True

    backups = sorted(backup_dir.glob("totish-daily-*.dump"), reverse=True)
    if not backups:
        alert(
            "backup:missing",
            "Что именно произошло:\n"
            "Каталог резервных копий уже существует, но ежедневного дампа базы в нём нет.",
        )
        return False

    latest = backups[0]
    try:
        age_seconds = max(0, int(time.time() - latest.stat().st_mtime))
    except OSError as exc:
        alert(
            "backup:unreadable",
            "Что именно произошло:\n"
            "Не удалось проверить дату последней резервной копии базы.\n\n"
            f"Причина: {type(exc).__name__}: {exc}",
        )
        return False

    max_age_seconds = 36 * 60 * 60
    if age_seconds > max_age_seconds:
        age_hours = age_seconds // 3600
        alert(
            "backup:stale",
            "Что именно произошло:\n"
            "Последняя ежедневная резервная копия базы слишком старая.\n\n"
            f"Возраст копии: {age_hours} ч. Допустимо: не более 36 ч.",
        )
        return False

    checksum = latest.with_name(latest.name + ".sha256")
    if not checksum.is_file():
        alert(
            "backup:checksum_missing",
            "Что именно произошло:\n"
            "У последней резервной копии базы отсутствует файл контрольной суммы.",
        )
        return False

    recover_incident(INCIDENT_STATE_FILE, "backup", send_message)
    return True


def check_control_plane_release():
    marker = ROOT / ".totish-managed-release"

    try:
        managed_release = marker.read_text(encoding="utf-8").strip()
    except OSError as exc:
        alert(
            "control_plane:marker_missing",
            "Что именно произошло:\n"
            "Не удалось прочитать отметку версии служебных файлов production.\n\n"
            f"Причина: {type(exc).__name__}: {exc}",
        )
        return False

    result = run([
        "docker",
        "inspect",
        "--format",
        "{{range .Config.Env}}{{println .}}{{end}}",
        "football-totalizator-app-1",
    ])

    if result.returncode != 0:
        alert(
            "control_plane:image_release_unknown",
            "Что именно произошло:\n"
            "Не удалось определить commit запущенного Docker-образа.",
        )
        return False

    image_release = ""
    for line in result.stdout.splitlines():
        if line.startswith("TOTISH_RELEASE="):
            image_release = line.split("=", 1)[1].strip()
            break

    if not image_release:
        alert(
            "control_plane:image_release_missing",
            "Что именно произошло:\n"
            "Запущенный Docker-образ не сообщает свой commit.",
        )
        return False

    if managed_release != image_release:
        alert(
            "control_plane:release_mismatch",
            "Что именно произошло:\n"
            "Версия служебных файлов VPS не совпадает с версией приложения.\n\n"
            f"Файлы VPS: {managed_release}\n"
            f"Приложение: {image_release}",
        )
        return False

    recover_incident(INCIDENT_STATE_FILE, "control_plane", send_message)
    return True


def check_public_health():
    try:
        health = fetch_json("https://totish.ru/health")

        if health.get("status") != "ok":
            alert(
                "health:public:bad_status",
                "Что именно произошло:\n"
                "totish.ru открывается из интернета, но сам сайт сообщает о проблеме.\n"
                "Пользователи могут видеть ошибки или некорректную работу страниц.\n\n"
                f"Ответ публичного /health: {health}",
            )
            return False

        recover_incident(INCIDENT_STATE_FILE, "health:public", send_message)
        return True

    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"

        if "502" in str(exc):
            explanation = (
                "nginx доступен, но не смог получить ответ от приложения. "
                "Это HTTP 502."
            )
        elif "504" in str(exc):
            explanation = (
                "nginx ждал ответ приложения слишком долго. "
                "Это HTTP 504."
            )
        elif "timed out" in str(exc).lower():
            explanation = (
                "Публичный сайт не ответил вовремя. "
                "Возможен зависший nginx, приложение или сетевой сбой."
            )
        else:
            explanation = (
                "Не удалось открыть публичный health-check totish.ru. "
                "Проблема может быть в nginx, TLS, DNS, сети или самом приложении."
            )

        alert(
            "health:public:unavailable",
            "Что именно произошло:\n"
            "ТОТИШ недоступен пользователям из интернета.\n"
            f"{explanation}\n\n"
            f"Причина: {reason}",
        )
        return False

def main():
    ok = True

    if not check_container():
        return 1

    if not check_local_health():
        ok = False

    if not check_db_health():
        ok = False

    if not check_control_plane_release():
        ok = False

    if not check_database_backup():
        ok = False

    if not check_public_health():
        ok = False

    if ok:
        print("MONITOR: OK")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
