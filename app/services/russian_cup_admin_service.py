from zoneinfo import ZoneInfo

from app.db import RUSSIAN_CUP_TOURNAMENT_NAME
from app.utils import format_admin_match_date, parse_datetime


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


def build_russian_cup_match_form_data(form, normalize_status, fallback_status="SCHEDULED"):
    return {
        "home_team": (form.get("home_team") or "").strip(),
        "away_team": (form.get("away_team") or "").strip(),
        "match_date": (form.get("match_date") or "").strip(),
        "match_time": (form.get("match_time") or "").strip(),
        "deadline_date": form.get("deadline_date", "").strip(),
        "deadline_time": form.get("deadline_time", "").strip(),
        "stage": normalize_russian_cup_stage(
            form.get("stage"),
            form.get("stage_custom"),
        ),
        "status": normalize_status(form.get("status"), fallback_status),
    }


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
    stage_map = {}

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
                   playoff_stage_manual,
                   api_match_id,
                   league
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
            match = {
                "id": row[0],
                "home_team": row[1],
                "away_team": row[2],
                "kickoff_time": kickoff,
                "deadline": deadline,
                "status": row[5],
                "home_score": row[6],
                "away_score": row[7],
                "stage": row[8] or "",
                "api_match_id": row[9] or "",
                "league": row[10] or "rcup",
                "round_label": row[8] or "Раунд не указан",
                "extra_time_home": "",
                "extra_time_away": "",
                "penalty_home": "",
                "penalty_away": "",
                "is_hidden": row[5] == "CANCELLED",
                "match_date_msk": kickoff.astimezone(MSK).strftime("%Y-%m-%d") if kickoff else "",
                "date_label": format_admin_match_date(kickoff) if kickoff else "Дата не указана",
                "match_time_msk": kickoff.astimezone(MSK).strftime("%H:%M") if kickoff else "",
                "deadline_date_msk": deadline.astimezone(MSK).strftime("%Y-%m-%d") if deadline else "",
                "deadline_time_msk": deadline.astimezone(MSK).strftime("%H:%M") if deadline else "",
            }
            matches.append(match)

            stage_key = match["stage"] or "Стадия не указана"
            date_key = match["match_date_msk"] or "Дата не указана"
            stage_group = stage_map.setdefault(stage_key, {
                "stage": stage_key,
                "matches_count": 0,
                "finished_count": 0,
                "date_map": {},
                "dates": [],
            })
            date_group = stage_group["date_map"].setdefault(date_key, {
                "date": date_key,
                "matches": [],
                "matches_count": 0,
                "finished_count": 0,
            })
            date_group["matches"].append(match)
            date_group["matches_count"] += 1
            stage_group["matches_count"] += 1
            if match["status"] == "FINISHED":
                date_group["finished_count"] += 1
                stage_group["finished_count"] += 1

    stage_groups = list(stage_map.values())
    for group in stage_groups:
        group["dates"] = list(group["date_map"].values())
        group.pop("date_map", None)

    return {
        "russian_cup_tournament": tournament,
        "russian_cup_matches": matches,
        "russian_cup_stage_groups": stage_groups,
        "russian_cup_matches_count": len(matches),
        "russian_cup_finished_count": sum(1 for match in matches if match["status"] == "FINISHED"),
        "russian_cup_statuses": ("SCHEDULED", "TIMED", "LIVE", "FINISHED", "POSTPONED", "CANCELLED"),
        "russian_cup_stages": RUSSIAN_CUP_STAGES,
        "russian_cup_stage_values": [stage for stage, _ in RUSSIAN_CUP_STAGES],
    }


def prepare_russian_cup_admin_page_data(cur):
    """Load Russian Cup metadata and counters without loading matches."""
    tournament = get_russian_cup_tournament(cur)
    matches_count = 0
    finished_count = 0
    if tournament:
        cur.execute(
            """
            SELECT COUNT(*), COUNT(*) FILTER (WHERE status = 'FINISHED')
            FROM matches
            WHERE tournament_id = %s AND league = 'rcup'
            """,
            (tournament["id"],),
        )
        counters = cur.fetchone() or (0, 0)
        matches_count, finished_count = counters[0] or 0, counters[1] or 0
    return {
        "russian_cup_tournament": tournament,
        "russian_cup_matches_count": matches_count,
        "russian_cup_finished_count": finished_count,
        "russian_cup_statuses": ("SCHEDULED", "TIMED", "LIVE", "FINISHED", "POSTPONED", "CANCELLED"),
        "russian_cup_stages": RUSSIAN_CUP_STAGES,
        "russian_cup_stage_values": [stage for stage, _ in RUSSIAN_CUP_STAGES],
    }
