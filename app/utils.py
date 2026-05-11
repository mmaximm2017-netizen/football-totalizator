from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from functools import lru_cache

MSK = ZoneInfo("Europe/Moscow")


# =========================================================
# DATETIME HELPERS
# =========================================================

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


@lru_cache(maxsize=2048)
def cached_to_msk(value):
    dt = parse_datetime(value)

    if not dt:
        return ""

    return dt.astimezone(MSK).strftime("%d.%m.%Y %H:%M")


def utc_now():
    return datetime.now(timezone.utc)


def is_before_deadline(match):
    """
    match может быть:
    - tuple из БД (deadline на index 3)
    - или dict (fallback через ключи)
    """
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
# CLUB LOGOS
# =========================================================

def get_club_logo(team_name):
    if not team_name:
        return "default.png"

    slug = (
        team_name
        .lower()
        .replace(" ", "-")
        .replace(".", "")
        .replace("'", "")
    )

    return f"{slug}-footballlogos-org.png"


# =========================================================
# TRANSLATION (ВАЖНО: чтобы не падал import)
# =========================================================

def translate_name(name):
    """
    Заглушка, чтобы не падал импорт.
    Если у тебя есть словарь перевода — сюда вставишь.
    """
    return name