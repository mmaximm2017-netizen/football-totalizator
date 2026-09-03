# app/services/match_service.py
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import requests
from app.config import API_KEY, LEAGUE_IDS, WC2026_API_SYNC_ENABLED
from app.db import get_db, close_db
from app.models.scoring import has_valid_finished_score
from app.services.sync_history_service import (
    create_sync_run,
    finish_sync_run,
    recover_stale_syncs,
)
from app.utils import translate_name, parse_utc_time, utc_now
from app.services.wc_playoff_service import infer_playoff_stage_from_api, is_wc2026_playoff_match

logger = logging.getLogger(__name__)

MSK = ZoneInfo("Europe/Moscow")
SYNC_LOCK_KEY = 88422031
# Кеширование матчей
_last_update_time = 0
_cache_duration = 60  # секунд (1 минута)

def _empty_sync_summary():
    return {
        "football_data_matches": 0,
        "matches_inserted": 0,
        "matches_updated": 0,
        "matches_skipped_finished": 0,
        "matches_skipped_missing_tournament": 0,
        "matches_skipped_invalid_finished_score": 0,
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


def _score_side(score_node, side):
    if not score_node:
        return None
    return score_node.get(side) if side in score_node else score_node.get(f"{side}Team")


def _score_pair(score_node):
    home = _score_side(score_node, "home")
    away = _score_side(score_node, "away")
    if home is None or away is None:
        return None, None
    return home, away


def _sum_score_pairs(first, second):
    first_home, first_away = _score_pair(first)
    second_home, second_away = _score_pair(second)
    if None in (first_home, first_away, second_home, second_away):
        return None, None
    return first_home + second_home, first_away + second_away


def extract_api_match_score(match, is_playoff_match=False):
    if match.get('status') != 'FINISHED':
        return None, None, "not_finished"

    score = match.get('score', {}) or {}

    if not is_playoff_match:
        full_time = score.get('fullTime') or {}
        home, away = _score_pair(full_time)
        if home is not None and away is not None:
            return home, away, "score.fullTime"
        home, away = _score_pair(score.get('extraTime') or {})
        return home, away, "score.extraTime"

    duration = (score.get('duration') or 'REGULAR').upper()
    if duration == 'REGULAR':
        home, away = _score_pair(score.get('fullTime') or {})
        return home, away, "score.fullTime"

    if duration == 'EXTRA_TIME':
        home, away = _score_pair(score.get('fullTime') or {})
        if home is not None and away is not None:
            return home, away, "score.fullTime.extra_time"
        home, away = _sum_score_pairs(score.get('regularTime'), score.get('extraTime'))
        return home, away, "score.regularTime+extraTime"

    if duration == 'PENALTY_SHOOTOUT':
        home, away = _sum_score_pairs(score.get('regularTime'), score.get('extraTime'))
        if home is None or away is None:
            return None, None, "penalty_unreliable_120min_score"
        return home, away, "score.regularTime+extraTime"

    home, away = _score_pair(score.get('fullTime') or {})
    return home, away, "score.fullTime"


def apply_manual_result_override(api_status, api_home_score, api_away_score, existing_status, existing_home_score, existing_away_score, manual_result_override, manual_override_allowed):
    if manual_result_override and manual_override_allowed:
        return existing_status, existing_home_score, existing_away_score
    return api_status, api_home_score, api_away_score


def fetch_matches(errors=None):
    headers = {'X-Auth-Token': API_KEY}
    all_matches = []
    for league_id in LEAGUE_IDS:
        if league_id == 2000 and not WC2026_API_SYNC_ENABLED:
            logger.info("[SYNC_SKIP] WC2026 API sync disabled")
            continue
        url = f"https://api.football-data.org/v4/competitions/{league_id}/matches"
        params = {'status': 'SCHEDULED,TIMED,FINISHED,IN_PLAY,PAUSED,POSTPONED,CANCELLED'}
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            if resp.status_code == 200:
                for m in resp.json().get('matches', []):
                    m['league'] = 'wc2026' if league_id == 2000 else 'other'; all_matches.append(m)
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

def get_tournament_id_by_name(cur, name):
    cur.execute(
        "SELECT id FROM tournaments WHERE name = %s ORDER BY id DESC LIMIT 1",
        (name,),
    )
    row = cur.fetchone()
    return row[0] if row else None

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

    if not matches_data:
        logger.warning("Match sync received no matches from external sources")
        return summary
    conn = get_db(); cur = conn.cursor()
    try:
        cup_tournament_id = get_tournament_id_by_name(cur, "Кубок Матч-премьер")
        wc_tournament_id = get_tournament_id_by_name(cur, "ЧМ-2026")
        missing_tournament_leagues = set()

        for match in matches_data:
            api_id = match['id']
            raw_home = match.get('homeTeam', {}).get('name') or match.get('home_team', 'Unknown')
            raw_away = match.get('awayTeam', {}).get('name') or match.get('away_team', 'Unknown')
            home_team = translate_name(raw_home); away_team = translate_name(raw_away)
            utc_time = match.get('utcDate', match.get('datetime', '')).replace('Z', '')
            status = match.get('status', 'SCHEDULED'); league = match.get('league', 'other')
            match_category = match.get('match_category')
            if league == 'wc2026' and not WC2026_API_SYNC_ENABLED:
                logger.info("[SYNC_SKIP] WC2026 API sync disabled")
                continue
            if league == 'wc2026':
                tournament_id = wc_tournament_id
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
            api_tournament_name = "ЧМ-2026" if tournament_id == wc_tournament_id else None
            is_playoff_api_match = is_wc2026_playoff_match(api_tournament_name, league, kickoff_utc)
            home_score, away_score, score_source = extract_api_match_score(match, is_playoff_api_match)
            if status == 'FINISHED' and not has_valid_finished_score(status, home_score, away_score):
                logger.warning(
                    "Skipping incomplete API result api_match_id=%s status=%s home_score=%r away_score=%r",
                    api_id,
                    status,
                    home_score,
                    away_score,
                )
                summary["matches_skipped_invalid_finished_score"] += 1
                status = 'SCHEDULED'
            if is_playoff_api_match and score_source == "penalty_unreliable_120min_score":
                logger.warning(
                    "[API_SCORE_WARNING] penalty shootout without reliable 120min score api_match_id=%s stage=%s home_team=%s away_team=%s raw_score=%s",
                    api_id,
                    match.get('stage'),
                    raw_home,
                    raw_away,
                    json.dumps(match.get('score', {}), ensure_ascii=False, default=str),
                )
            playoff_stage_auto = None
            if is_playoff_api_match:
                playoff_stage_auto = infer_playoff_stage_from_api(match)
                score = match.get('score', {}) or {}
                logger.info(
                    "[API_MATCH_RAW] api_match_id=%s stage=%s status=%s home_team=%s away_team=%s raw_score=%s full_time_score=%s regular_time_score=%s extra_time_score=%s penalty_score=%s winner=%s all_score_fields=%s current_code_score_source=%s current_code_home_score=%s current_code_away_score=%s",
                    api_id,
                    match.get('stage'),
                    status,
                    raw_home,
                    raw_away,
                    json.dumps(score, ensure_ascii=False, default=str),
                    json.dumps(score.get('fullTime'), ensure_ascii=False, default=str),
                    json.dumps(score.get('regularTime'), ensure_ascii=False, default=str),
                    json.dumps(score.get('extraTime'), ensure_ascii=False, default=str),
                    json.dumps(score.get('penalties'), ensure_ascii=False, default=str),
                    score.get('winner'),
                    json.dumps(score, ensure_ascii=False, default=str),
                    score_source,
                    home_score,
                    away_score,
                )
            
            cur.execute("SELECT id, tournament_id FROM matches WHERE api_match_id = %s", (str(api_id),))
            existing_row = cur.fetchone()
            if existing_row and existing_row[1] != tournament_id:
                msg = (
                    f"api_match_id={api_id} belongs to tournament_id={existing_row[1]}, "
                    f"cannot update for tournament_id={tournament_id}; skipping"
                )
                logger.warning(msg)
                summary["errors"].append(msg)
                summary["matches_skipped_missing_tournament"] += 1
                continue
            if not existing_row:
                cur.execute("""INSERT INTO matches (api_match_id, home_team, away_team, kickoff_time, deadline, status, home_score, away_score, league, tournament_id, playoff_stage_auto, match_category)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id""", (str(api_id), home_team, away_team,
                    kickoff_utc, deadline_utc, status, home_score, away_score, league, tournament_id, playoff_stage_auto, match_category))
                match_id = cur.fetchone()[0]
                logger.info(
                    "[PLAYOFF_CHECK] match_id=%s api_match_id=%s is_playoff_match=%s raw_stage=%s round_raw=%s matchday=%s round_number=%s effective_stage=%s css_class=%s",
                    match_id,
                    api_id,
                    is_playoff_api_match,
                    match.get('stage'),
                    match.get('round') or match.get('roundName') or match.get('round_name'),
                    match.get('matchday'),
                    match.get('round_number'),
                    playoff_stage_auto or ("playoff" if is_playoff_api_match else None),
                    "match-card--playoff" if is_playoff_api_match else "",
                )
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
                           COALESCE(m.manual_kickoff_override, 0),
                           t.name,
                           m.league,
                           m.kickoff_time,
                           m.deadline,
                           m.playoff_stage_manual,
                           m.playoff_stage_auto
                    FROM matches m
                    LEFT JOIN tournaments t ON t.id = m.tournament_id
                    WHERE m.api_match_id = %s AND m.tournament_id = %s
                    """,
                    (str(api_id), tournament_id),
                )
                existing_match = cur.fetchone()
                match_id = existing_match[0] if existing_match else None
                logger.info(
                    "[PLAYOFF_CHECK] match_id=%s api_match_id=%s is_playoff_match=%s raw_stage=%s round_raw=%s matchday=%s round_number=%s effective_stage=%s css_class=%s",
                    match_id,
                    api_id,
                    is_playoff_api_match,
                    match.get('stage'),
                    match.get('round') or match.get('roundName') or match.get('round_name'),
                    match.get('matchday'),
                    match.get('round_number'),
                    playoff_stage_auto or ("playoff" if is_playoff_api_match else None),
                    "match-card--playoff" if is_playoff_api_match else "",
                )
                existing_status = existing_match[1] if existing_match else None
                existing_home = existing_match[2] if existing_match else None
                existing_away = existing_match[3] if existing_match else None
                existing_home_team = existing_match[4] if existing_match else None
                existing_away_team = existing_match[5] if existing_match else None
                manual_teams_override = bool(existing_match and existing_match[6])
                manual_result_override = bool(existing_match and existing_match[7])
                manual_kickoff_override = bool(existing_match and existing_match[8])
                existing_tournament_name = existing_match[9] if existing_match else None
                existing_league = existing_match[10] if existing_match else None
                existing_kickoff = existing_match[11] if existing_match else None
                existing_deadline = existing_match[12] if existing_match else None
                existing_stage_manual = existing_match[13] if existing_match else None

                manual_override_allowed = is_wc2026_playoff_match(
                    existing_tournament_name,
                    existing_league,
                    existing_kickoff,
                )

                api_conflicts = []

                if (
                    manual_teams_override
                    and manual_override_allowed
                    and (home_team != existing_home_team or away_team != existing_away_team)
                ):
                    api_conflicts.append("API прислал другие команды")

                if (
                    manual_result_override
                    and manual_override_allowed
                    and status == 'FINISHED'
                    and home_score is not None
                    and away_score is not None
                    and (home_score != existing_home or away_score != existing_away)
                ):
                    api_conflicts.append("API прислал другой результат")

                if (
                    existing_stage_manual
                    and manual_override_allowed
                    and playoff_stage_auto
                    and playoff_stage_auto != existing_stage_manual
                ):
                    api_conflicts.append("API прислал другую стадию")

                if (
                    manual_kickoff_override
                    and manual_override_allowed
                    and existing_kickoff
                    and kickoff_utc != existing_kickoff
                ):
                    api_conflicts.append("API прислал другое время матча")

                api_conflict_note = "; ".join(api_conflicts) if api_conflicts else None

                cur.execute(
                    """
                    UPDATE matches
                    SET api_conflict_note = %s
                    WHERE id = %s
                    """,
                    (api_conflict_note, match_id),
                )

                if manual_teams_override and manual_override_allowed:
                    home_team = existing_home_team
                    away_team = existing_away_team

                status, home_score, away_score = apply_manual_result_override(
                    status,
                    home_score,
                    away_score,
                    existing_status,
                    existing_home,
                    existing_away,
                    manual_result_override,
                    manual_override_allowed,
                )

                if manual_kickoff_override and manual_override_allowed:
                    kickoff_utc = existing_kickoff
                    deadline_utc = existing_deadline
                
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

                if status == 'FINISHED' and home_score is not None and away_score is not None:
                    cur.execute("""UPDATE matches SET home_team=%s, away_team=%s, status=%s, home_score=%s, away_score=%s, kickoff_time=%s, deadline=%s, league=%s, tournament_id=%s, playoff_stage_auto=%s, match_category=%s WHERE api_match_id=%s AND tournament_id=%s""",
                        (home_team, away_team, status, home_score, away_score, kickoff_utc, deadline_utc, league, tournament_id, playoff_stage_auto, match_category, str(api_id), tournament_id))
                    if match_id is not None and (existing_status != 'FINISHED' or finished_score_changed):
                        summary["matches_became_finished"].append(match_id)
                        _add_changed_finished_match(summary, match_id)
                else:
                    cur.execute("""UPDATE matches SET home_team=%s, away_team=%s, status=%s, kickoff_time=%s, deadline=%s, league=%s, tournament_id=%s, playoff_stage_auto=%s, match_category=%s WHERE api_match_id=%s AND tournament_id=%s""",
                        (home_team, away_team, status, kickoff_utc, deadline_utc, league, tournament_id, playoff_stage_auto, match_category, str(api_id), tournament_id))
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
        logger.info("Sync lock key=%s acquired=%s", lock_key, acquired)
        return conn, cur, acquired, None
    except Exception as e:
        logger.error("Sync lock acquisition failed for key=%s: %s", lock_key, e)
        close_db(conn, cur)
        return None, None, False, str(e)

def release_sync_lock(conn, cur, lock_key=SYNC_LOCK_KEY):
    if not conn or not cur:
        return
    try:
        cur.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))
        logger.info("Sync lock key=%s released", lock_key)
        close_db(conn, cur)
    except Exception as e:
        logger.error("Failed to release sync lock key=%s: %s", lock_key, e)
        if cur is not None and not cur.closed:
            try:
                cur.close()
            except Exception:
                pass
        from app.db import db_pool
        if db_pool is not None:
            try:
                db_pool.putconn(conn, close=True)
                return
            except Exception:
                pass
        try:
            conn.close()
        except Exception:
            pass


def _sync_history_status(summary):
    if summary.get("lock_error"):
        return "lock_error"
    if summary.get("status") == "scoring_failed":
        return "scoring_failed"
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
        summary["lock_acquired"] = acquired
        summary["lock_error"] = lock_error

        if lock_error:
            summary["status"] = "lock_error"
            summary["errors"].append(lock_error)
            logger.error("Sync lock acquisition failed: %s", lock_error)
            finish_sync_run(sync_run_id, "lock_error", summary)
            return summary

        if not acquired:
            summary["status"] = "skipped_already_running"
            logger.info("Sync lock busy — already running")
            finish_sync_run(sync_run_id, "skipped_already_running", summary)
            return summary

        logger.info("Sync lock acquired key=%s", SYNC_LOCK_KEY)

        sync_summary = update_matches()
        summary["sync"] = sync_summary

        try:
            summary["scoring"] = _recalculate_points_after_sync(sync_summary)
            logger.info("sync scoring summary: %s", summary["scoring"])
        except Exception as scoring_error:
            msg = f"Scoring recalculation failed after match sync: {scoring_error}"
            logger.exception(msg)
            summary["errors"].append(msg)
            summary["scoring"] = {
                "scoring_mode": "failed",
                "matches_recalculated": 0,
                "predictions_recalculated": 0,
                "error": str(scoring_error),
            }
            summary["status"] = "scoring_failed"
            logger.info("sync end with scoring failure: %s", summary)
            finish_sync_run(sync_run_id, "scoring_failed", summary)
            return summary

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
