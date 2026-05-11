# app/utils.py
from functools import lru_cache
from datetime import datetime, timedelta, timezone
from app.config import MSK_OFFSET, WEEKDAYS
from app.models import team_data

@lru_cache(maxsize=500)
def translate_name(name):
    return team_data.TEAM_NAMES.get(name, name)

@lru_cache(maxsize=200)
def cached_to_msk(utc_time_str):
    if not utc_time_str: return "—"
    clean_str = utc_time_str.replace('Z', '').replace('+00:00', '').replace('-00:00', '')
    try: dt_utc = datetime.fromisoformat(clean_str)
    except:
        try: dt_utc = datetime.strptime(utc_time_str, "%Y-%m-%d %H:%M:%S")
        except: dt_utc = datetime.fromisoformat(utc_time_str)
    dt_msk = dt_utc + timedelta(hours=MSK_OFFSET)
    weekday = WEEKDAYS.get(dt_msk.strftime("%A"), dt_msk.strftime("%A"))
    return dt_msk.strftime("%d.%m %H:%M МСК") + f" ({weekday})"

def parse_utc_time(utc_str):
    if not utc_str: return None
    clean_str = utc_str.replace('Z', '').replace('+00:00', '').replace('-00:00', '')
    try: return datetime.fromisoformat(clean_str)
    except:
        try: return datetime.strptime(utc_str, "%Y-%m-%d %H:%M:%S")
        except: return datetime.fromisoformat(utc_str)

def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

def is_before_deadline(match_tuple):
    deadline_str = match_tuple[3]
    if not deadline_str: return False
    deadline = parse_utc_time(deadline_str)
    if deadline is None: return False
    return utc_now() < deadline

def get_flag(name):
    translated = team_data.TEAM_NAMES.get(name, name)
    code = team_data.TEAM_FLAGS.get(translated)
    if code:
        return f'<img src="https://flagcdn.com/w40/{code}.png" width="24" height="16" style="vertical-align: middle; margin-right: 4px; border-radius: 2px;" alt="">'
    return ""

def get_club_logo(name):
    logo_url = team_data.CLUB_LOGOS.get(name)
    if logo_url:
        return f'<img src="{logo_url}" width="24" height="24" style="vertical-align: middle; border-radius: 4px;" alt="">'
    return ""