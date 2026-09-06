#!/usr/bin/env python3

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = Path.home() / ".local" / "state" / "totish"
STATE_FILE = STATE_DIR / "production-monitor-dedupe.json"
RECOVERY_STATE_FILE = STATE_DIR / "production-monitor-recovery.json"

sys.path.insert(0, str(ROOT / "scripts"))
from host_telegram_notifier import send_message


RECOVERY_MESSAGES = {
    "container": (
        "✅ ТОТИШ: приложение снова работает",
        "Основной контейнер приложения снова находится в нормальном состоянии.",
    ),
    "health_local": (
        "✅ ТОТИШ: приложение снова отвечает",
        "Внутренняя проверка приложения на VPS снова проходит успешно.",
    ),
    "health_db": (
        "✅ ТОТИШ: база данных снова доступна",
        "Проверка базы данных снова проходит успешно.",
    ),
    "control_plane": (
        "✅ ТОТИШ: служебные файлы снова синхронизированы",
        "Версия служебных production-файлов снова совпадает с версией приложения.",
    ),
    "backup": (
        "✅ ТОТИШ: резервное копирование снова в норме",
        "Проверка последней резервной копии базы снова проходит успешно.",
    ),
    "auto_results": (
        "✅ ТОТИШ: автоматическая проверка результатов восстановилась",
        "Монитор автоматического ввода результатов снова завершается успешно.",
    ),
    "health_public": (
        "✅ ТОТИШ: сайт снова доступен",
        "Публичная проверка totish.ru снова проходит успешно.",
    ),
}


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


def load_recovery_state():
    try:
        data = json.loads(RECOVERY_STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_recovery_state(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = RECOVERY_STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    tmp.replace(RECOVERY_STATE_FILE)


def failure_family(key):
    if key.startswith("container:"):
        return "container"
    if key.startswith("health:local"):
        return "health_local"
    if key.startswith("health:db"):
        return "health_db"
    if key.startswith("control_plane:"):
        return "control_plane"
    if key.startswith("backup:"):
        return "backup"
    if key.startswith("auto_results:"):
        return "auto_results"
    if key.startswith("health:public"):
        return "health_public"
    return None


def mark_failure(key):
    family = failure_family(key)
    if not family:
        return
    state = load_recovery_state()
    state[family] = {
        "key": key,
        "since": int(time.time()),
    }
    save_recovery_state(state)


def recover(family):
    state = load_recovery_state()
    previous = state.get(family)
    if not isinstance(previous, dict):
        return False

    message_parts = RECOVERY_MESSAGES.get(family)
    if not message_parts:
        return False

    title, human_text = message_parts
    now_text = datetime.now().astimezone().strftime("%d.%m.%Y %H:%M:%S %Z")
    previous_key = previous.get("key", "unknown")
    message = (
        f"{title}\n\n"
        f"{human_text}\n\n"
        f"Время: {now_text}\n\n"
        "Технические детали:\n"
        f"production_monitor / recovered_from={previous_key}"
    )
    send_message(message)
    state.pop(family, None)
    save_recovery_state(state)
    print(f"RECOVERY SENT: {family}")
    return True


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
    mark_failure(key)

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

    recover("container")
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

        recover("health_local")
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
        recover("health_db")
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
    # scheduled run after rollout, do not emit a false alert or recovery.
    if not backup_dir.exists():
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

    recover("backup")
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

    recover("control_plane")
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

        recover("health_public")
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


def safe_output(value, limit=600):
    if isinstance(value, bytes):
        value = value.decode('utf-8', errors='replace')
    text = str(value or '')
    for key, secret in os.environ.items():
        if secret and re.search(r'TOKEN|PASSWORD|SECRET|DSN|DATABASE_URL|API_KEY', key, re.I):
            text = text.replace(secret, '[redacted]')
    text = re.sub(r'\b[a-zA-Z][a-zA-Z0-9+.-]*://\S+', '[URL redacted]', text)
    text = re.sub(r'(?im)^.*(?:password|passwd|token|secret|dsn|database_url|authorization|api[_-]?key|\bhost\s*=|\bdbname\s*=|\buser\s*=).*$','[sensitive line redacted]',text)
    text = re.sub(r'(?m)^\s*(?:export\s+)?[A-Z_][A-Z0-9_]*=.*$', '[environment redacted]', text)
    text = re.sub(r'\b\d{6,}:[A-Za-z0-9_-]{20,}\b', '[token redacted]', text)
    text = re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', text)
    return text if len(text) <= limit else text[:limit//3] + '\n… [truncated] …\n' + text[-(limit-limit//3):]


def failure_details(code=None, stdout='', stderr='', *, kind=None):
    if kind is None:
        combined = str(stdout) + str(stderr)
        kind = ('timeout' if code in {124, 137} else 'DB failure'
                if re.search(r'psycopg|OperationalError|InterfaceError|PostgreSQL', combined, re.I)
                else 'shell failure' if code in {126, 127} or 'AUTO_RESULTS_REFUSED' in combined
                else 'unknown failure')
    return (f'Тип: {kind}; exit_code={code if code is not None else "unknown"}. '
            'Состояние результата может быть неизвестно.\n'
            f'stdout: {safe_output(stdout) or "(empty)"}\n'
            f'stderr: {safe_output(stderr) or "(empty)"}')


def check_auto_results():
    # Independent of the five-minute result cron: can detect its disappearance.
    try:
        result = run(["/bin/bash", "scripts/run_auto_results.sh", "--monitor"], timeout=45)
    except subprocess.TimeoutExpired as exc:
        details = failure_details(stdout=exc.stdout, stderr=exc.stderr, kind="timeout")
    except OSError as exc:
        details = failure_details(stderr=type(exc).__name__, kind="shell failure")
    except Exception as exc:
        details = failure_details(stderr=type(exc).__name__, kind="unknown failure")
    else:
        if result.returncode == 0:
            recover("auto_results")
            return True
        details = failure_details(result.returncode, result.stdout, result.stderr)
    print(f"auto_results:monitor_failed\n{details}")
    alert("auto_results:monitor_failed", details)
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

    if not check_auto_results():
        ok = False

    if not check_public_health():
        ok = False

    if ok:
        print("MONITOR: OK")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
