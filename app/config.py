# app/config.py
from datetime import datetime

API_KEY = "3c1f32333b1c4b5eacb45b01dd83170c"
LEAGUE_IDS = [2000]  # ЧМ-2026
INVITE_CODE = "FIFA2026"
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"
MSK_OFFSET = 3
START_DATE = datetime(2026, 5, 6)

DATABASE_URL = "postgresql://neondb_owner:npg_1LCchKjFH4fN@ep-falling-block-alh8f3s4-pooler.c-3.eu-central-1.aws.neon.tech/neondb?sslmode=require"
SECRET_KEY = "fifa2026-totalizator-secret-key-dont-change"

WEEKDAYS = {
    "Monday": "Понедельник", "Tuesday": "Вторник", "Wednesday": "Среда",
    "Thursday": "Четверг", "Friday": "Пятница", "Saturday": "Суббота", "Sunday": "Воскресенье"
}