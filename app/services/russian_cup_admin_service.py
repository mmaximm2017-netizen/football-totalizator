from zoneinfo import ZoneInfo

from app.db import RUSSIAN_CUP_TOURNAMENT_NAME
from app.utils import parse_datetime


MSK = ZoneInfo("Europe/Moscow")

RUSSIAN_CUP_STAGES = (
    ("Групповой этап", "Групповой этап"),
    ("Плей-офф", "Плей-офф"),
    ("1/4 финала", "1/4 финала"),
    ("1/2 финала", "1/2 финала"),
    ("Финал", "Финал"),
    ("Другое", "Другое вручную"),
)


def normalize_russian_cup_stage(value, custom_value=None):
    value = (value or "").strip()
    custom_value = (custom_value or "").strip()
    known = {stage for stage, _ in RUSSIAN_CUP_STAGES}
    if value == "Другое":
        return custom_value or value
    return value if value in known else (custom_value or value)


def get_russian_cup_tournament(cur):
    cur.execute(
        """
        SELECT id, name, is_active, start_date
        FROM tournaments
        WHERE name = %s
        ORDER BY id DESC
        LIMIT 1
        """,
        (RUSSIAN_CUP_TOURNAMENT_NAME,),
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


def prepare_russian_cup_admin_data(cur):
    tournament = get_russian_cup_tournament(cur)
    matches = []

    if tournament:
        cur.execute(
            """
            SELECT id,
                   home_team,
                   away_team,
                   kickoff_time,
                   deadline,
                   status,
                   home_score,
                   away_score,
                   playoff_stage_manual
            FROM matches
            WHERE tournament_id = %s
              AND league = 'rcup'
            ORDER BY kickoff_time NULLS LAST, id
            """,
            (tournament["id"],),
        )
        for row in cur.fetchall():
            kickoff = parse_datetime(row[3])
            deadline = parse_datetime(row[4])
            matches.append({
                "id": row[0],
                "home_team": row[1],
                "away_team": row[2],
                "kickoff_time": kickoff,
                "deadline": deadline,
                "status": row[5],
                "home_score": row[6],
                "away_score": row[7],
                "stage": row[8] or "",
                "is_hidden": row[5] == "CANCELLED",
                "match_date_msk": kickoff.astimezone(MSK).strftime("%Y-%m-%d") if kickoff else "",
                "match_time_msk": kickoff.astimezone(MSK).strftime("%H:%M") if kickoff else "",
                "deadline_date_msk": deadline.astimezone(MSK).strftime("%Y-%m-%d") if deadline else "",
                "deadline_time_msk": deadline.astimezone(MSK).strftime("%H:%M") if deadline else "",
            })

    return {
        "russian_cup_tournament": tournament,
        "russian_cup_matches": matches,
        "russian_cup_matches_count": len(matches),
        "russian_cup_statuses": ("SCHEDULED", "TIMED", "LIVE", "FINISHED"),
        "russian_cup_stages": RUSSIAN_CUP_STAGES,
    }
