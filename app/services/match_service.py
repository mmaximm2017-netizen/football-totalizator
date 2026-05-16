# app/services/match_service.py
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import requests
from app.config import API_KEY, LEAGUE_IDS
from app.db import get_db, close_db
from app.utils import translate_name, parse_utc_time, utc_now

logger = logging.getLogger(__name__)

MSK = ZoneInfo("Europe/Moscow")

# Пробуем импортировать Understat
try:
    from understatapi import UnderstatClient
    UNDERSTAT_AVAILABLE = True
except ImportError:
    UNDERSTAT_AVAILABLE = False
    logger.info("Understat не установлен. РПЛ будет недоступна.")

# Кеширование матчей
_last_update_time = 0
_cache_duration = 60  # секунд (1 минута)

def fetch_matches():
    headers = {'X-Auth-Token': API_KEY}
    all_matches = []
    for league_id in LEAGUE_IDS:
        url = f"https://api.football-data.org/v4/competitions/{league_id}/matches"
        params = {'status': 'SCHEDULED,TIMED,FINISHED,IN_PLAY,PAUSED,POSTPONED,CANCELLED'}
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            if resp.status_code == 200:
                for m in resp.json().get('matches', []):
                    m['league'] = 'wc2026'; all_matches.append(m)
        except Exception as e: logger.error(f"API error: {e}")
    return all_matches

def create_match_from_understat(match, prefix, league_tag):
    return {'id': f"{prefix}_{match['id']}", 'home_team': match['h']['title'], 'away_team': match['a']['title'],
            'utcDate': match['datetime'], 'status': 'SCHEDULED', 'score': {'fullTime': {'home': None, 'away': None}}, 'league': league_tag}

def resolve_rpl_season():
    env_season = os.getenv("RPL_SEASON")
    if env_season:
        return str(env_season).strip()

    now_msk = datetime.now(MSK)
    # Russian league season usually starts in summer and is labeled by start year.
    season_start_year = now_msk.year if now_msk.month >= 7 else now_msk.year - 1
    return str(season_start_year)

def fetch_rpl_matches():
    if not UNDERSTAT_AVAILABLE:
        return []

    season = resolve_rpl_season()
    max_attempts = 3
    retry_delay_sec = 1.5

    logger.info("RPL sync start")
    logger.info("RPL selected season: %s", season)

    all_matches = []

    for attempt in range(1, max_attempts + 1):
        try:
            understat = UnderstatClient()
            league_data = understat.league(league="RFPL").get_match_data(season=season)
            for match in league_data:
                all_matches.append(create_match_from_understat(match, "rpl", "rpl"))

            logger.info("RPL matches fetched: %s", len(all_matches))
            return all_matches

        except Exception as e:
            logger.warning("RPL attempt failed (%s/%s): %s", attempt, max_attempts, e)
            if attempt < max_attempts:
                time.sleep(retry_delay_sec)

    logger.error("RPL sync failed")
    return all_matches

def should_update():
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("SELECT MAX(kickoff_time) FROM matches")
        last = cur.fetchone()[0]
        if last:
            last_update = parse_utc_time(last)
            if last_update and utc_now() - last_update <= timedelta(minutes=55): return False
    except: pass
    finally: close_db(conn, cur)
    return True

def update_matches():
    matches_data = fetch_matches()
    try:
        rpl_matches = fetch_rpl_matches()
        if rpl_matches: matches_data.extend(rpl_matches)
    except: pass
    if not matches_data: return
    conn = get_db(); cur = conn.cursor()
    try:
        for match in matches_data:
            api_id = match['id']
            raw_home = match.get('homeTeam', {}).get('name') or match.get('home_team', 'Unknown')
            raw_away = match.get('awayTeam', {}).get('name') or match.get('away_team', 'Unknown')
            home_team = translate_name(raw_home); away_team = translate_name(raw_away)
            utc_time = match.get('utcDate', match.get('datetime', '')).replace('Z', '')
            status = match.get('status', 'SCHEDULED'); league = match.get('league', 'other')
            home_score = away_score = None
            if status == 'FINISHED':
                score = match.get('score', {}); ft = score.get('fullTime') or score.get('extraTime') or {}
                home_score = ft.get('home'); away_score = ft.get('away')
            
            if ' ' in utc_time:
                kickoff_utc = datetime.strptime(utc_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            else:
                kickoff_utc = datetime.fromisoformat(utc_time)
                if kickoff_utc.tzinfo is None:
                    kickoff_utc = kickoff_utc.replace(tzinfo=timezone.utc)
            
            kickoff_msk = kickoff_utc.astimezone(MSK)
            
            if league == 'wc2026':
                deadline_msk = kickoff_msk - timedelta(hours=2)
            else:
                deadline_msk = kickoff_msk.replace(hour=11, minute=0, second=0, microsecond=0)
                if deadline_msk >= kickoff_msk:
                    deadline_msk = kickoff_msk - timedelta(hours=1)
            
            deadline_utc = deadline_msk.astimezone(timezone.utc)
            
            cur.execute("SELECT id FROM matches WHERE api_match_id = %s", (str(api_id),))
            if not cur.fetchone():
                cur.execute("""INSERT INTO matches (api_match_id, home_team, away_team, kickoff_time, deadline, status, home_score, away_score, league)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (str(api_id), home_team, away_team,
                    kickoff_utc, deadline_utc, status, home_score, away_score, league))
            else:
                cur.execute("SELECT status, home_score, away_score FROM matches WHERE api_match_id = %s", (str(api_id),))
                existing_match = cur.fetchone()
                existing_status = existing_match[0] if existing_match else None
                existing_home = existing_match[1] if existing_match else None
                existing_away = existing_match[2] if existing_match else None
                
                is_locked_completed = (
                    existing_status == 'FINISHED'
                    and existing_home is not None
                    and existing_away is not None
                )

                if is_locked_completed:
                    home_score = existing_home
                    away_score = existing_away

                # Do not downgrade already finished matches with known score.
                if (
                    existing_status == 'FINISHED'
                    and existing_home is not None
                    and existing_away is not None
                    and status != 'FINISHED'
                ):
                    status = 'FINISHED'

                # Keep completed matches immutable on sync:
                # do not overwrite status/kickoff/deadline/league once both scores are known.
                if is_locked_completed:
                    continue
                
                if status == 'FINISHED' and home_score is not None and away_score is not None:
                    cur.execute("""UPDATE matches SET status=%s, home_score=%s, away_score=%s, kickoff_time=%s, deadline=%s, league=%s WHERE api_match_id=%s""",
                        (status, home_score, away_score, kickoff_utc, deadline_utc, league, str(api_id)))
                else:
                    cur.execute("""UPDATE matches SET status=%s, kickoff_time=%s, deadline=%s, league=%s WHERE api_match_id=%s""",
                        (status, kickoff_utc, deadline_utc, league, str(api_id)))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally: close_db(conn, cur)

def update_matches_safe():
    global _last_update_time
    if not should_update():
        logger.info("Обновление не требуется")
        return
    if time.time() - _last_update_time < _cache_duration:
        logger.info("Обновление не требуется (кеш)")
        return
    _last_update_time = time.time()
    update_matches()
    from app.services import point_service
    point_service.calculate_all_points()
