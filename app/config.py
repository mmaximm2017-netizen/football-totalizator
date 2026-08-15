# app/config.py

import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()


def require_env(name):
    value = os.getenv(name)

    if not value:
        raise RuntimeError(
            f"Не найдена переменная окружения: {name}"
        )

    return value


API_KEY = require_env("API_KEY")

LEAGUE_IDS = [2000]  # ЧМ-2026

WC2026_API_SYNC_ENABLED = os.getenv("WC2026_API_SYNC_ENABLED", "false").lower() in ("1", "true", "yes", "on")

INVITE_CODE = require_env("INVITE_CODE")

ADMIN_USERNAME = require_env("ADMIN_USERNAME")
ADMIN_PASSWORD = require_env("ADMIN_PASSWORD")

DATABASE_URL = require_env("DATABASE_URL")

SECRET_KEY = require_env("SECRET_KEY")

# Web Push configuration is intentionally optional during the staged rollout.
# Public key can be exposed by the API; the private key must remain server-side.
WEB_PUSH_VAPID_PUBLIC_KEY = os.getenv("WEB_PUSH_VAPID_PUBLIC_KEY", "")
WEB_PUSH_VAPID_PRIVATE_KEY = os.getenv("WEB_PUSH_VAPID_PRIVATE_KEY", "")
WEB_PUSH_VAPID_SUBJECT = os.getenv("WEB_PUSH_VAPID_SUBJECT", "")

MSK_OFFSET = 3

START_DATE = datetime(2026, 5, 6)

WEEKDAYS = {
    "Monday": "Понедельник",
    "Tuesday": "Вторник",
    "Wednesday": "Среда",
    "Thursday": "Четверг",
    "Friday": "Пятница",
    "Saturday": "Суббота",
    "Sunday": "Воскресенье"
}
