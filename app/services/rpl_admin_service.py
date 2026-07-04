from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.match_service import (
    RPL_TOURNAMENT_NAME,
    fetch_rpl_matches,
    resolve_rpl_season,
)
from app.utils import parse_datetime


MSK = ZoneInfo("Europe/Moscow")


def check_rpl_calendar():
    checked_at = datetime.now().strftime("%d.%m.%Y %H:%M")
    season = resolve_rpl_season()
    try:
        matches = fetch_rpl_matches()
        return {
            "source": "Understat",
            "season": season,
            "checked_at": checked_at,
            "matches_count": len(matches),
            "status": "available" if matches else "not_published",
            "status_label": "Календарь доступен" if matches else "Календарь ещё не опубликован",
            "matches": matches,
            "error": None,
        }
    except Exception as e:
        return {
            "source": "Understat",
            "season": season,
            "checked_at": checked_at,
            "matches_count": 0,
            "status": "error",
            "status_label": "Ошибка проверки календаря",
            "matches": [],
            "error": str(e),
        }


def get_rpl_tournament(cur):
    cur.execute(
        """
        SELECT id, name, is_active, start_date
        FROM tournaments
        WHERE name = %s
        ORDER BY id DESC
        LIMIT 1
        """,
        (RPL_TOURNAMENT_NAME,),
    )
    row = cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "name": row[1],
        "is_active": row[2],
        "start_date": row[3],
    }


def prepare_rpl_admin_data(cur, calendar_check=None):
    tournament = get_rpl_tournament(cur)
    matches = []

    if tournament:
        cur.execute(
            """
            SELECT id,
                   api_match_id,
                   home_team,
                   away_team,
                   kickoff_time,
                   deadline,
                   status,
                   home_score,
                   away_score,
                   playoff_stage_manual,
                   league
            FROM matches
            WHERE tournament_id = %s
              AND league = 'rpl'
            ORDER BY kickoff_time NULLS LAST, id
            """,
            (tournament["id"],),
        )
        for row in cur.fetchall():
            kickoff = parse_datetime(row[4])
            deadline = parse_datetime(row[5])
            source = "api" if row[1] else "manual"
            matches.append({
                "id": row[0],
                "api_match_id": row[1],
                "home_team": row[2],
                "away_team": row[3],
                "kickoff_time": kickoff,
                "deadline": deadline,
                "status": row[6],
                "home_score": row[7],
                "away_score": row[8],
                "stage": row[9] or "",
                "league": row[10],
                "source": source,
                "source_label": "API" if source == "api" else "Вручную",
                "is_hidden": row[6] == "CANCELLED",
                "match_date_msk": kickoff.astimezone(MSK).strftime("%Y-%m-%d") if kickoff else "",
                "match_time_msk": kickoff.astimezone(MSK).strftime("%H:%M") if kickoff else "",
                "deadline_date_msk": deadline.astimezone(MSK).strftime("%Y-%m-%d") if deadline else "",
                "deadline_time_msk": deadline.astimezone(MSK).strftime("%H:%M") if deadline else "",
            })

    calendar_check = calendar_check or {
        "source": "Understat",
        "season": resolve_rpl_season(),
        "checked_at": "не проверялось",
        "matches_count": 0,
        "status": "not_checked",
        "status_label": "Календарь ещё не проверялся",
        "matches": [],
        "error": None,
    }

    return {
        "rpl_tournament": tournament,
        "rpl_matches": matches,
        "rpl_matches_count": len(matches),
        "rpl_calendar": calendar_check,
        "rpl_statuses": ("SCHEDULED", "TIMED", "LIVE", "FINISHED"),
    }
