# app/utils.py
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from functools import lru_cache
import app.models.team_data as team_data

MSK = ZoneInfo("Europe/Moscow")


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
    except Exception:
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
    except Exception:
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
        "Monday": "Понедельник",
        "Tuesday": "Вторник",
        "Wednesday": "Среда",
        "Thursday": "Четверг",
        "Friday": "Пятница",
        "Saturday": "Суббота",
        "Sunday": "Воскресенье",
    }
    weekday = weekdays_ru.get(dt_msk.strftime("%A"), dt_msk.strftime("%A"))
    return dt_msk.strftime("%d.%m %H:%M МСК") + f" ({weekday})"


def format_date_ru(date_str):
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        months = {
            1: "января",
            2: "февраля",
            3: "марта",
            4: "апреля",
            5: "мая",
            6: "июня",
            7: "июля",
            8: "августа",
            9: "сентября",
            10: "октября",
            11: "ноября",
            12: "декабря",
        }
        return f"{dt.day} {months[dt.month]} {dt.year} г."
    except Exception:
        return date_str


def get_flag(name):
    """Возвращает флаг из папки /static/flags/"""
    translated = team_data.TEAM_NAMES.get(name, name)
    code = team_data.TEAM_FLAGS.get(translated)
    if code:
        return f'<img src="/static/flags/{code}.svg" class="flag-icon" alt="{translated}">'
    return ""


def get_club_logo(name):
    """Возвращает эмблему клуба из /static/clubs/"""
    logo_url = team_data.CLUB_LOGOS.get(name)
    if logo_url:
        return f'<img src="{logo_url}" width="24" height="24" style="vertical-align: middle; border-radius: 4px;" alt="{name}">'
    return ""


def translate_name(name):
    return team_data.TEAM_NAMES.get(name, name)
