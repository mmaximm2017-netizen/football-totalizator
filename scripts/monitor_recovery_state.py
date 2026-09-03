import json
from datetime import datetime
from pathlib import Path


def load_active_incidents(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in data.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def save_active_incidents(path: Path, incidents: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(incidents, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def incident_family(key: str) -> str:
    if key.startswith("container:"):
        return "container"
    if key.startswith("health:local"):
        return "health:local"
    if key.startswith("health:db"):
        return "health:db"
    if key.startswith("health:public"):
        return "health:public"
    if key.startswith("control_plane:"):
        return "control_plane"
    if key.startswith("backup:"):
        return "backup"
    return key.split(":", 1)[0]


def remember_incident(path: Path, key: str) -> None:
    incidents = load_active_incidents(path)
    incidents[incident_family(key)] = key
    save_active_incidents(path, incidents)


def recover_incident(path: Path, family: str, send_message) -> bool:
    incidents = load_active_incidents(path)
    previous = incidents.pop(family, None)
    if previous is None:
        return False

    now_text = datetime.now().astimezone().strftime("%d.%m.%Y %H:%M:%S %Z")
    send_message(
        "✅ ТОТИШ: работа восстановлена\n\n"
        "ТОТИШ снова работает штатно. Ранее обнаруженная неисправность устранена.\n\n"
        f"Время: {now_text}\n\n"
        "Технические детали:\n"
        f"production_monitor / recovered:{family}\n"
        f"Предыдущая ошибка: {previous}"
    )
    save_active_incidents(path, incidents)
    return True
