#!/usr/bin/env python3

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = Path.home() / ".local" / "state" / "totish"
STATE_FILE = STATE_DIR / "production-monitor-dedupe.json"

sys.path.insert(0, str(ROOT / "scripts"))
from host_telegram_notifier import send_message


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

    message = (
        "🚨 TOTISH ERROR\n"
        "Источник: production_monitor\n"
        f"{details}"
    )

    send_message(message)

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
            "Контейнер приложения недоступен.\n"
            f"{(result.stderr or result.stdout).strip()[:800]}",
        )
        return False

    state = result.stdout.strip()

    if state != "running|true|false":
        alert(
            f"container:{state}",
            f"Состояние контейнера: {state}",
        )
        return False

    return True


def check_local_health():
    try:
        health = fetch_json("http://127.0.0.1:8000/health")

        if health.get("status") != "ok":
            raise RuntimeError(
                f"unexpected response: {health}"
            )

        return True

    except Exception as exc:
        alert(
            "health:local",
            "Flask/Gunicorn не отвечает на локальный /health.\n"
            f"{type(exc).__name__}: {exc}",
        )
        return False


def check_db_health():
    try:
        health = fetch_json("http://127.0.0.1:8000/health/db")

        bad = {
            key: health.get(key)
            for key in ("db", "active_tournament", "ranking")
            if health.get(key) != "ok"
        }

        if bad:
            raise RuntimeError(
                f"bad fields: {bad}; full={health}"
            )

        return True

    except Exception as exc:
        alert(
            "health:db",
            "Проблема БД/application health.\n"
            f"{type(exc).__name__}: {exc}",
        )
        return False


def check_public_health():
    try:
        health = fetch_json("https://totish.ru/health")

        if health.get("status") != "ok":
            raise RuntimeError(
                f"unexpected response: {health}"
            )

        return True

    except Exception as exc:
        alert(
            "health:public",
            "Публичный сайт недоступен через nginx/TLS "
            "(включая 502/504).\n"
            f"{type(exc).__name__}: {exc}",
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

    if not check_public_health():
        ok = False

    if ok:
        print("MONITOR: OK")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
