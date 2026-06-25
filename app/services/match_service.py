# app/services/match_service.py
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import requests
from app.config import API_KEY, LEAGUE_IDS
from app.db import get_db, close_db
from app.services.sync_history_service import (
    create_sync_run,
    finish_sync_run,
    recover_stale_syncs,
)
from app.utils import translate_name, parse_utc_time, utc_now
from app.services.wc_playoff_service import is_wc2026_playoff_match

logger = logging.getLogger(__name__)

MSK = ZoneInfo("Europe/Moscow")
SYNC_LOCK_KEY = 88422031
RPL_TOURNAMENT_NAME = "РПЛ 2026/27"

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

def _empty_sync_summary():
    return {
        "football_data_matches": 0,
        "understat_matches": 0,
        "matches_inserted": 0,
        "matches_updated": 0,
        "matches_skipped_finished": 0,
        "matches_skipped_missing_tournament": 0,
        "matches_became_finished": [],
        "changed_finished_match_ids": [],
        "changed_finished_matches_count": 0,
        "errors": [],
    }


def _add_changed_finished_match(summary, match_id):
    if match_id is None:
        return
    if match_id not in summary["changed_finished_match_ids"]:
        summary["changed_finished_match_ids"].append(match_id)
        summary["changed_finished_matches_count"] = len(summary["changed_finished_match_ids"])


def fetch_matches(errors=None):
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
            else:
                msg = f"football-data {league_id} returned status {resp.status_code}"
                logger.warning(msg)
                if errors is not None:
                    errors.append(msg)
        except Exception as e:
            msg = f"football-data {league_id} API error: {e}"
            logger.error(msg)
            if errors is not None:
                errors.append(msg)
    return all_matches

def parse_optional_int(value):
    try:
        if value is None:
            return None
        if isinstance(value, str):
            value = value.strip()
            if value == "":
                return None
        return int(value)
    except (TypeError, ValueError):
        return None


def get_tournament_id_by_name(cur, name):
    cur.execute("SELECT id FROM tournaments WHERE name = %s ORDER BY id DESC LIMIT 1", (name,))
    row = cur.fetchone()
    if row:
        return row[0]

    # Fallback for legacy mojibake/default names in old DB snapshots.
    if name == "ЧМ-2026":
        cur.execute(
            """
            SELECT id
            FROM tournaments
            WHERE name ILIKE '%2026%'
            ORDER BY is_active DESC, id DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
        return row[0] if row else None

    if name == "Кубок Матч-премьер":
        cur.execute(
            """
            SELECT id
            FROM tournaments
            WHERE name <> 'ЧМ-2026'
            ORDER BY
                CASE WHEN name ILIKE '%матч%' THEN 0 ELSE 1 END,
                is_active DESC,
                id DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
        return row[0] if row else None

    return None

def create_match_from_understat(match, prefix, league_tag):
    goals = match.get('goals', {}) or {}
    home_goal = parse_optional_int(goals.get('h'))
    away_goal = parse_optional_int(goals.get('a'))

    is_result = bool(match.get('isResult'))
    is_finished = is_result and home_goal is not None and away_goal is not None

    status = 'FINISHED' if is_finished else 'SCHEDULED'
    score = {
        'fullTime': {
            'home': home_goal if is_finished else None,
            'away': away_goal if is_finished else None
        }
    }

    return {'id': f"{prefix}_{match['id']}", 'home_team': match['h']['title'], 'away_team': match['a']['title'],
            'utcDate': match['datetime'], 'status': status, 'score': score, 'league': league_tag}

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

            finished_mapped = 0
            scheduled_mapped = 0
            invalid_score_rows = 0

            for match in league_data:
                mapped = create_match_from_understat(match, "rpl", "rpl")
                all_matches.append(mapped)

                if mapped.get('status') == 'FINISHED':
                    finished_mapped += 1
                else:
                    scheduled_mapped += 1

                if (
                    match.get('isResult')
                    and mapped.get('status') != 'FINISHED'
                ):
                    invalid_score_rows += 1

            logger.info("RPL matches fetched: %s", len(all_matches))
            logger.info(
                "RPL mapping summary: finished=%s scheduled=%s invalid_score_rows=%s",
                finished_mapped,
                scheduled_mapped,
                invalid_score_rows
            )
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
    except Exception as e:
        logger.warning("Failed to check whether match sync is needed: %s", e)
    finally: close_db(conn, cur)
    return True

def update_matches():
    summary = _empty_sync_summary()

    matches_data = fetch_matches(summary["errors"])
    summary["football_data_matches"] = len(matches_data)

    try:
        rpl_matches = fetch_rpl_matches()
        summary["understat_matches"] = len(rpl_matches)
        if rpl_matches: matches_data.extend(rpl_matches)
    except Exception as e:
        msg = f"Understat sync failed during update_matches: {e}"
        logger.warning(msg)
        summary["errors"].append(msg)
    if not matches_data:
        logger.warning("Match sync received no matches from external sources")
        return summary
    conn = get_db(); cur = conn.cursor()
    try:
        cup_tournament_id = get_tournament_id_by_name(cur, "Кубок Матч-премьер")
        wc_tournament_id = get_tournament_id_by_name(cur, "ЧМ-2026")
        rpl_tournament_id = get_tournament_id_by_name(cur, RPL_TOURNAMENT_NAME)
        missing_tournament_leagues = set()

        for match in matches_data:
            api_id = match['id']
            raw_home = match.get('homeTeam', {}).get('name') or match.get('home_team', 'Unknown')
            raw_away = match.get('awayTeam', {}).get('name') or match.get('away_team', 'Unknown')
            home_team = translate_name(raw_home); away_team = translate_name(raw_away)
            utc_time = match.get('utcDate', match.get('datetime', '')).replace('Z', '')
            status = match.get('status', 'SCHEDULED'); league = match.get('league', 'other')
            if league == 'wc2026':
                tournament_id = wc_tournament_id
            elif league == 'rpl':
                tournament_id = rpl_tournament_id
            else:
                tournament_id = cup_tournament_id

            if not tournament_id:
                summary["matches_skipped_missing_tournament"] += 1
                if league not in missing_tournament_leagues:
                    msg = f"No tournament configured for league={league}; skipping source matches"
                    logger.error(msg)
                    summary["errors"].append(msg)
                    missing_tournament_leagues.add(league)
                continue

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
                deadline_msk = kickoff_msk - timedelta(hours=6)
            else:
                eleven_am_msk = kickoff_msk.replace(hour=11, minute=0, second=0, microsecond=0)
                kickoff_minus_5m_msk = kickoff_msk - timedelta(minutes=5)
                deadline_msk = min(eleven_am_msk, kickoff_minus_5m_msk)
            
            deadline_utc = deadline_msk.astimezone(timezone.utc)
            
            cur.execute("SELECT id FROM matches WHERE api_match_id = %s", (str(api_id),))
            existing_row = cur.fetchone()
            if not existing_row:
                cur.execute("""INSERT INTO matches (api_match_id, home_team, away_team, kickoff_time, deadline, status, home_score, away_score, league, tournament_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id""", (str(api_id), home_team, away_team,
                    kickoff_utc, deadline_utc, status, home_score, away_score, league, tournament_id))
                match_id = cur.fetchone()[0]
                summary["matches_inserted"] += 1
                if status == 'FINISHED' and home_score is not None and away_score is not None:
                    summary["matches_became_finished"].append(match_id)
                    _add_changed_finished_match(summary, match_id)
            else:
                cur.execute(
                    """
                    SELECT m.id,
                           m.status,
                           m.home_score,
                           m.away_score,
                           m.home_team,
                           m.away_team,
                           COALESCE(m.manual_teams_override, 0),
                           COALESCE(m.manual_result_override, 0),
                           t.name,
                           m.league,
                           m.kickoff_time
                    FROM matches m
                    LEFT JOIN tournaments t ON t.id = m.tournament_id
                    WHERE m.api_match_id = %s
                    """,
                    (str(api_id),),
                )
                existing_match = cur.fetchone()
                match_id = existing_match[0] if existing_match else None
                existing_status = existing_match[1] if existing_match else None
                existing_home = existing_match[2] if existing_match else None
                existing_away = existing_match[3] if existing_match else None
                existing_home_team = existing_match[4] if existing_match else None
                existing_away_team = existing_match[5] if existing_match else None
                manual_teams_override = bool(existing_match and existing_match[6])
                manual_result_override = bool(existing_match and existing_match[7])
                existing_tournament_name = existing_match[8] if existing_match else None
                existing_league = existing_match[9] if existing_match else None
                existing_kickoff = existing_match[10] if existing_match else None

                manual_override_allowed = is_wc2026_playoff_match(
                    existing_tournament_name,
                    existing_league,
                    existing_kickoff,
                )

                if manual_teams_override and manual_override_allowed:
                    home_team = existing_home_team
                    away_team = existing_away_team

                if manual_result_override and manual_override_allowed:
                    status = existing_status
                    home_score = existing_home
                    away_score = existing_away
                
                is_locked_completed = (
                    existing_status == 'FINISHED'
                    and existing_home is not None
                    and existing_away is not None
                )
                finished_score_changed = (
                    not (manual_result_override and manual_override_allowed)
                    and
                    is_locked_completed
                    and status == 'FINISHED'
                    and home_score is not None
                    and away_score is not None
                    and (existing_home != home_score or existing_away != away_score)
                )

                if is_locked_completed and not finished_score_changed:
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
                if is_locked_completed and not finished_score_changed:
                    summary["matches_skipped_finished"] += 1
                    continue

                if finished_score_changed:
                    cur.execute("""UPDATE matches SET status=%s, home_score=%s, away_score=%s WHERE api_match_id=%s""",
                        (status, home_score, away_score, str(api_id)))
                    _add_changed_finished_match(summary, match_id)
                    summary["matches_updated"] += 1
                    continue
                
                if status == 'FINISHED' and home_score is not None and away_score is not None:
                    cur.execute("""UPDATE matches SET status=%s, home_score=%s, away_score=%s, kickoff_time=%s, deadline=%s, league=%s, tournament_id=%s WHERE api_match_id=%s""",
                        (status, home_score, away_score, kickoff_utc, deadline_utc, league, tournament_id, str(api_id)))
                    if existing_status != 'FINISHED' and match_id is not None:
                        summary["matches_became_finished"].append(match_id)
                        _add_changed_finished_match(summary, match_id)
                else:
                    cur.execute("""UPDATE matches SET status=%s, kickoff_time=%s, deadline=%s, league=%s, tournament_id=%s WHERE api_match_id=%s""",
                        (status, kickoff_utc, deadline_utc, league, tournament_id, str(api_id)))
                summary["matches_updated"] += 1
        conn.commit()
        return summary
    except Exception:
        conn.rollback()
        raise
    finally: close_db(conn, cur)

def try_acquire_sync_lock(lock_key=SYNC_LOCK_KEY):
    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (lock_key,))
        row = cur.fetchone()
        acquired = bool(row and row[0])
        return conn, cur, acquired, None
    except Exception as e:
        logger.warning("Sync lock unavailable: %s", e)
        close_db(conn, cur)
        return None, None, True, str(e)

def release_sync_lock(conn, cur, lock_key=SYNC_LOCK_KEY):
    if not conn or not cur:
        return
    try:
        cur.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))
    except Exception as e:
        logger.warning("Failed to release sync lock: %s", e)
    finally:
        close_db(conn, cur)


def _sync_history_status(summary):
    if summary.get("lock_error"):
        return "lock_error"
    if summary.get("errors") or (summary.get("sync") or {}).get("errors"):
        return "partial_success"
    return "success"


def _recalculate_points_after_sync(sync_summary):
    if not isinstance(sync_summary, dict):
        logger.warning("sync summary is unavailable; using broad scoring fallback")
        from app.services.scoring_recalculation_service import recalc_all_points
        scoring_summary = recalc_all_points()
        return {
            "scoring_mode": "broad_fallback",
            "matches_recalculated": scoring_summary.get("matches", 0),
            "predictions_recalculated": scoring_summary.get("updated", 0),
        }

    if "changed_finished_match_ids" not in sync_summary:
        logger.warning("sync summary has no changed_finished_match_ids; using broad scoring fallback")
        from app.services.scoring_recalculation_service import recalc_all_points
        scoring_summary = recalc_all_points()
        return {
            "scoring_mode": "broad_fallback",
            "matches_recalculated": scoring_summary.get("matches", 0),
            "predictions_recalculated": scoring_summary.get("updated", 0),
        }

    changed_match_ids = sync_summary.get("changed_finished_match_ids")
    if not isinstance(changed_match_ids, list):
        logger.warning("sync summary changed_finished_match_ids is invalid; using broad scoring fallback")
        from app.services.scoring_recalculation_service import recalc_all_points
        scoring_summary = recalc_all_points()
        return {
            "scoring_mode": "broad_fallback",
            "matches_recalculated": scoring_summary.get("matches", 0),
            "predictions_recalculated": scoring_summary.get("updated", 0),
        }

    if not changed_match_ids:
        return {
            "scoring_mode": "skipped_no_finished_changes",
            "matches_recalculated": 0,
            "predictions_recalculated": 0,
        }

    from app.services.scoring_recalculation_service import recalc_match_points
    total_updated = 0
    unique_match_ids = []
    for match_id in changed_match_ids:
        if match_id not in unique_match_ids:
            unique_match_ids.append(match_id)

    for match_id in unique_match_ids:
        result = recalc_match_points(match_id)
        total_updated += result.get("updated", 0)

    return {
        "scoring_mode": "changed_matches",
        "matches_recalculated": len(unique_match_ids),
        "predictions_recalculated": total_updated,
    }


def run_sync_with_lock(strict_lock=False):
    lock_conn = lock_cur = None
    sync_run_id = None
    summary = {
        "status": "started",
        "sync_run_id": None,
        "strict_lock": strict_lock,
        "lock_acquired": False,
        "lock_error": None,
        "sync": _empty_sync_summary(),
        "scoring": {
            "scoring_mode": None,
            "matches_recalculated": 0,
            "predictions_recalculated": 0,
        },
        "errors": [],
    }

    logger.info("sync start")
    recover_stale_syncs()
    sync_run_id = create_sync_run(summary)
    summary["sync_run_id"] = sync_run_id

    try:
        lock_conn, lock_cur, acquired, lock_error = try_acquire_sync_lock()
        summary["lock_acquired"] = acquired and not lock_error
        summary["lock_error"] = lock_error

        if not acquired:
            summary["status"] = "skipped_already_running"
            logger.info("sync lock skipped: already running")
            finish_sync_run(sync_run_id, "skipped_already_running", summary)
            return summary

        if lock_error:
            summary["status"] = "lock_error"
            summary["errors"].append(lock_error)
            if strict_lock:
                logger.error("sync lock unavailable; strict mode stops sync: %s", lock_error)
                finish_sync_run(sync_run_id, "lock_error", summary)
                return summary
            logger.warning("sync lock unavailable; continuing without lock in admin/manual mode")
        else:
            logger.info("sync lock acquired")

        sync_summary = update_matches()
        summary["sync"] = sync_summary

        summary["scoring"] = _recalculate_points_after_sync(sync_summary)
        logger.info("sync scoring summary: %s", summary["scoring"])

        summary["status"] = "completed"
        logger.info("sync end: %s", summary)
        finish_sync_run(sync_run_id, _sync_history_status(summary), summary)
        return summary
    except Exception as e:
        summary["status"] = "error"
        summary["errors"].append(str(e))
        logger.exception("sync failed: %s", summary)
        finish_sync_run(sync_run_id, "failed", summary)
        raise
    finally:
        release_sync_lock(lock_conn, lock_cur)

def update_matches_safe():
    global _last_update_time
    if not should_update():
        logger.info("Обновление не требуется")
        summary = _empty_sync_summary()
        summary["skipped"] = "not_needed"
        return summary
    if time.time() - _last_update_time < _cache_duration:
        logger.info("Обновление не требуется (кеш)")
        summary = _empty_sync_summary()
        summary["skipped"] = "cache"
        return summary
    _last_update_time = time.time()
    return run_sync_with_lock()
