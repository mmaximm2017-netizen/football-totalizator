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

INVITE_CODE = require_env("INVITE_CODE")

ADMIN_USERNAME = require_env("ADMIN_USERNAME")
ADMIN_PASSWORD = require_env("ADMIN_PASSWORD")

DATABASE_URL = require_env("DATABASE_URL")

SECRET_KEY = require_env("SECRET_KEY")

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