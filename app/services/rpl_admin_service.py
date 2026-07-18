import re
from datetime import datetime
from zoneinfo import ZoneInfo

from app.utils import parse_datetime


MSK = ZoneInfo("Europe/Moscow")
RPL_TOURNAMENT_NAME = "Чемпионат России 🇷🇺"

RPL_MATCH_CATEGORIES = (
    ("rpl", "Чемпионат России"),
    ("supercup", "Суперкубок России"),
    ("national_team", "Сборная России"),
)

RPL_MATCH_CATEGORY_LABELS = dict(RPL_MATCH_CATEGORIES)
RUSSIAN_MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def normalize_rpl_match_category(value):
    value = (value or "").strip().lower()
    return value if value in RPL_MATCH_CATEGORY_LABELS else "rpl"


def infer_rpl_match_category(home_team, away_team, stage, value=None):
    normalized = normalize_rpl_match_category(value)
    if value:
        return normalized

    teams = {str(home_team or "").strip().lower(), str(away_team or "").strip().lower()}
    if "россия" in teams or "russia" in teams:
        return "national_team"

    stage_text = str(stage or "").strip().lower()
    if "суперкуб" in stage_text or "supercup" in stage_text or "super cup" in stage_text:
        return "supercup"

    return "rpl"


def _tour_number(stage):
    match = re.search(r"(?:тур|round)\s*(\d+)", str(stage or ""), re.IGNORECASE)
    return int(match.group(1)) if match else None


def _match_local_date(match):
    kickoff = match.get("kickoff_time")
    return kickoff.astimezone(MSK).date() if kickoff else None


def _match_sort_key(match, reverse=False):
    kickoff = match.get("kickoff_time")
    return (kickoff is None, kickoff or datetime.max.replace(tzinfo=MSK), match.get("id", 0))


def _group_matches_within_section(matches, reverse_dates=False):
    tours = {}
    dates = {}
    for match in matches:
        tour = _tour_number(match.get("stage"))
        if tour is not None:
            tours.setdefault(tour, []).append(match)
            continue
        local_date = _match_local_date(match)
        dates.setdefault(local_date, []).append(match)

    sections = []
    for tour, tour_matches in sorted(tours.items()):
        sections.append({
            "kind": "tour",
            "label": f"Тур {tour}",
            "matches": tour_matches,
            "date_groups": [],
        })

    months = {}
    for local_date, date_matches in dates.items():
        if local_date:
            months.setdefault((local_date.year, local_date.month), []).append((local_date, date_matches))
        else:
            months.setdefault((9999, 12), []).append((local_date, date_matches))
    for (year, month), month_dates in sorted(months.items(), reverse=reverse_dates):
        date_groups = []
        for local_date, date_matches in sorted(
            month_dates,
            key=lambda item: item[0] or datetime.max.date(),
            reverse=reverse_dates,
        ):
            label = (
                f"{local_date.day} {RUSSIAN_MONTHS[local_date.month - 1]}"
                if local_date else "Дата не указана"
            )
            date_groups.append({"label": label, "matches": date_matches})
        month_label = f"{RUSSIAN_MONTHS[month - 1].capitalize()} {year}" if year != 9999 else "Без даты"
        sections.append({"kind": "month", "label": month_label, "matches": [], "date_groups": date_groups})
    return sections


def build_rpl_match_groups(matches, today=None):
    today = today or datetime.now(MSK).date()
    buckets = {"today": [], "upcoming": [], "finished": []}
    for match in matches:
        local_date = _match_local_date(match)
        if local_date == today:
            buckets["today"].append(match)
        elif str(match.get("status") or "").upper() == "FINISHED":
            buckets["finished"].append(match)
        else:
            buckets["upcoming"].append(match)

    buckets["today"].sort(key=_match_sort_key)
    buckets["upcoming"].sort(key=_match_sort_key)
    buckets["finished"].sort(key=_match_sort_key, reverse=True)

    definitions = (
        ("today", "Сегодня"),
        ("upcoming", "Предстоящие"),
        ("finished", "Завершённые"),
    )
    groups = []
    for key, label in definitions:
        matches_in_group = buckets[key]
        if not matches_in_group:
            continue
        groups.append({
            "key": key,
            "label": label,
            "count": len(matches_in_group),
            "open": key == "today" or (key == "upcoming" and not buckets["today"]),
            "sections": _group_matches_within_section(matches_in_group, reverse_dates=key == "finished"),
        })
    return groups


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


def prepare_rpl_admin_data(cur):
    tournament = get_rpl_tournament(cur)
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
                   playoff_stage_manual,
                   match_category,
                   league
            FROM matches
            WHERE tournament_id = %s
              AND league = 'rpl'
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
                "match_category": infer_rpl_match_category(row[1], row[2], row[8], row[9]),
                "match_category_label": RPL_MATCH_CATEGORY_LABELS.get(infer_rpl_match_category(row[1], row[2], row[8], row[9]), "Чемпионат России"),
                "league": row[10],
                "is_hidden": row[5] == "CANCELLED",
                "match_date_msk": kickoff.astimezone(MSK).strftime("%Y-%m-%d") if kickoff else "",
                "match_time_msk": kickoff.astimezone(MSK).strftime("%H:%M") if kickoff else "",
                "deadline_date_msk": deadline.astimezone(MSK).strftime("%Y-%m-%d") if deadline else "",
                "deadline_time_msk": deadline.astimezone(MSK).strftime("%H:%M") if deadline else "",
            })

    return {
        "rpl_tournament": tournament,
        "rpl_matches": matches,
        "rpl_match_groups": build_rpl_match_groups(matches),
        "rpl_matches_count": len(matches),
        "rpl_statuses": ("SCHEDULED", "TIMED", "LIVE", "FINISHED"),
        "rpl_match_categories": RPL_MATCH_CATEGORIES,
    }


def prepare_rpl_admin_page_data(cur):
    """Load RPL page metadata without loading the full match list."""
    tournament = get_rpl_tournament(cur)
    return {
        "rpl_tournament": tournament,
        "rpl_statuses": ("SCHEDULED", "TIMED", "LIVE", "FINISHED"),
        "rpl_match_categories": RPL_MATCH_CATEGORIES,
    }
