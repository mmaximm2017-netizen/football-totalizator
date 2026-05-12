# app/utils.py
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from functools import lru_cache
from app.models.team_data import TEAM_NAMES, TEAM_FLAGS, CLUB_LOGOS

MSK = ZoneInfo("Europe/Moscow")


# =========================================================
# DATETIME HELPERS
# =========================================================

def utc_now():
    return datetime.now(timezone.utc)


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
    except:
        return None


def parse_utc_time(utc_str):
    return parse_datetime(utc_str)


def is_before_deadline(match):
    try:
        if isinstance(match, dict):
            deadline = match.get("deadline")
        else:
            deadline = match[3]
        dt = parse_datetime(deadline)
        if not dt:
            return False
        return utc_now() < dt
    except:
        return False


@lru_cache(maxsize=200)
def cached_to_msk(utc_time_str):
    if not utc_time_str:
        return "—"
    dt = parse_datetime(utc_time_str)
    if not dt:
        return "—"
    dt_msk = dt.astimezone(MSK)
    weekdays_ru = {
        "Monday": "Понедельник", "Tuesday": "Вторник", "Wednesday": "Среда",
        "Thursday": "Четверг", "Friday": "Пятница", "Saturday": "Суббота", "Sunday": "Воскресенье"
    }
    weekday = weekdays_ru.get(dt_msk.strftime("%A"), dt_msk.strftime("%A"))
    return dt_msk.strftime("%d.%m %H:%M МСК") + f" ({weekday})"


# =========================================================
# DATE FORMAT FOR DISPLAY (7 мая 2026 г.)
# =========================================================

def format_date_ru(date_str):
    """Преобразует YYYY-MM-DD в '7 мая 2026 г.'"""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        months = {
            1: 'января', 2: 'февраля', 3: 'марта', 4: 'апреля',
            5: 'мая', 6: 'июня', 7: 'июля', 8: 'августа',
            9: 'сентября', 10: 'октября', 11: 'ноября', 12: 'декабря'
        }
        return f"{dt.day} {months[dt.month]} {dt.year} г."
    except:
        return date_str


# =========================================================
# TEAM ICON (умный выбор: флаг для сборной, эмблема для клуба)
# =========================================================

def get_team_icon(name):
    """Возвращает флаг для сборной или эмблему для клуба"""
    translated = TEAM_NAMES.get(name, name)
    if translated in TEAM_FLAGS:
        return f'<img src="https://flagcdn.com/w40/{TEAM_FLAGS[translated]}.png" width="24" height="16" style="vertical-align: middle; margin-right: 4px; border-radius: 2px;" alt="">'
    else:
        logo = CLUB_LOGOS.get(translated)
        if logo:
            return f'<img src="{logo}" width="24" height="24" style="vertical-align: middle; border-radius: 4px;" alt="">'
    return ""


# =========================================================
# FLAGS (старая функция, оставлена для совместимости)
# =========================================================

def get_flag(name):
    translated = TEAM_NAMES.get(name, name)
    code = TEAM_FLAGS.get(translated)
    if code:
        return f'<img src="https://flagcdn.com/w40/{code}.png" width="24" height="16" style="vertical-align: middle; margin-right: 4px; border-radius: 2px;" alt="">'
    return ""


# =========================================================
# CLUB LOGOS (старая функция, оставлена для совместимости)
# =========================================================

def get_club_logo(name):
    logo_url = CLUB_LOGOS.get(name)
    if logo_url:
        return f'<img src="{logo_url}" width="24" height="24" style="vertical-align: middle; border-radius: 4px;" alt="">'
    return ""


# =========================================================
# TRANSLATE
# =========================================================

def translate_name(name):
    return TEAM_NAMES.get(name, name)