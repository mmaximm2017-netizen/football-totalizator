from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from functools import lru_cache
import os

# =========================================================
# TIME HELPERS
# =========================================================

MSK = ZoneInfo("Europe/Moscow")


def parse_datetime(value):
    if not value:
        return None

    try:
        if isinstance(value, datetime):
            dt = value
        else:
            clean = str(value).replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt

    except Exception:
        return None


def parse_utc_time(value):
    return parse_datetime(value)


@lru_cache(maxsize=2048)
def cached_to_msk(value):
    dt = parse_datetime(value)
    if not dt:
        return ""
    return dt.astimezone(MSK).strftime("%d.%m.%Y %H:%M")


def utc_now():
    return datetime.now(timezone.utc)


def is_before_deadline(match):
    try:
        deadline = None

        if isinstance(match, dict):
            deadline = match.get("deadline")
        else:
            deadline = match[3]

        dt = parse_datetime(deadline)
        if not dt:
            return False

        return utc_now() < dt

    except Exception:
        return False


# =========================================================
# FLAGS
# =========================================================

FLAGS = {
    "Россия": "🇷🇺",
    "Англия": "🏴",
    "Испания": "🇪🇸",
    "Италия": "🇮🇹",
    "Германия": "🇩🇪",
    "Франция": "🇫🇷"
}


def get_flag(country):
    return FLAGS.get(country, "⚽")


# =========================================================
# CLUB LOGOS (FLEXIBLE MATCHING SYSTEM)
# =========================================================

# Загружаем список файлов логотипов один раз при старте
CLUB_LOGO_FILES = set()

LOGO_DIR = "static/clubs"

if os.path.exists(LOGO_DIR):
    CLUB_LOGO_FILES = set(os.listdir(LOGO_DIR))


def normalize_team(name: str):
    if not name:
        return ""

    return (
        name.lower()
        .replace("fc ", "")
        .replace("fk ", "")
        .replace(" ", "-")
        .replace(".", "")
        .replace("'", "")
        .strip()
    )


def find_logo_by_fuzzy(name: str):
    """
    Умный поиск логотипа:
    1. пробует полное совпадение по нормализованному имени
    2. пробует частичное совпадение
    3. fallback по ключевым словам
    """

    if not name:
        return "/static/clubs/default.png"

    norm = normalize_team(name)

    # 1. точное вхождение
    for file in CLUB_LOGO_FILES:
        if norm in file:
            return f"/static/clubs/{file}"

    # 2. fallback по ключевым словам
    keywords = norm.split("-")
    for file in CLUB_LOGO_FILES:
        if any(k in file for k in keywords if len(k) > 3):
            return f"/static/clubs/{file}"

    # 3. дефолт
    return "/static/clubs/default.png"


def get_club_logo(team_name):
    return find_logo_by_fuzzy(team_name)


# =========================================================
# TRANSLATE (заглушка)
# =========================================================

def translate_name(name):
    return name