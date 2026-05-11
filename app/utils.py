from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from functools import lru_cache

MSK = ZoneInfo("Europe/Moscow")


# =========================================================
# DATETIME SAFE PARSING
# =========================================================

def parse_datetime(value):

    if not value:
        return None

    try:

        if isinstance(value, datetime):
            dt = value
        else:
            dt = datetime.fromisoformat(
                str(value).replace("Z", "+00:00")
            )

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt

    except Exception:
        return None


# =========================================================
# MSK FORMAT
# =========================================================

@lru_cache(maxsize=2048)
def to_msk(value):

    dt = parse_datetime(value)

    if not dt:
        return ""

    return dt.astimezone(MSK).strftime("%d.%m.%Y %H:%M")


# =========================================================
# DEADLINE CHECK (FIXED LOGIC)
# =========================================================

def is_before_deadline(match):

    try:

        if not match:
            return False

        # поддержка dict и tuple
        deadline = None

        if isinstance(match, dict):
            deadline = match.get("deadline")
        else:
            # fallback под твои старые tuple запросы
            deadline = match[3] if len(match) > 3 else None

        dt = parse_datetime(deadline)

        if not dt:
            return False

        return datetime.now(timezone.utc) < dt

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
        team_name.lower()
        .replace(" ", "-")
        .replace(".", "")
        .replace("'", "")
    )

    return f"{slug}-footballlogos-org.png"