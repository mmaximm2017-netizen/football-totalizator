from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.config import START_DATE
from app.routes.admin_actions import ALLOWED_TITLES
from app.services.wc_playoff_service import (
    WC2026_PLAYOFF_START,
    PLAYOFF_STAGES,
    determine_effective_playoff_stage,
    get_playoff_stage_label,
    get_playoff_stage_sort_order,
    is_wc2026_playoff_match,
)
from app.utils import (
    format_date_ru,
    format_month_label,
    parse_datetime,
)


MSK = ZoneInfo("Europe/Moscow")


def parse_admin_match_filters(args, forced_tournament_id=None):
    """Parse the shared server-side match list controls."""
    view = args.get("view", "all")
    if view not in {"attention", "upcoming", "finished", "all"}:
        view = "all"
    page = max(args.get("page", 1, type=int) or 1, 1)
    period = args.get("period", "all")
    if period not in {"7", "30", "all"}:
        period = "all"
    tournament_id = forced_tournament_id or args.get("tournament_id", type=int)
    return {
        "view": view,
        "q": (args.get("q") or "").strip(),
        "tournament_id": tournament_id,
        "status": (args.get("status") or "").strip().upper(),
        "period": period,
        "page": page,
        "per_page": 30,
    }


def _admin_match_where(filters, now):
    where = ["m.kickoff_time >= %s"]
    params = [START_DATE.strftime("%Y-%m-%dT%H:%M:%S")]
    view = filters["view"]
    if view == "attention":
        where.append("m.kickoff_time <= %s AND COALESCE(m.status, '') <> 'FINISHED' AND m.home_score IS NULL AND m.away_score IS NULL")
        params.append(now)
    elif view == "upcoming":
        where.append("m.kickoff_time > %s AND COALESCE(m.status, '') <> 'FINISHED'")
        params.append(now)
    elif view == "finished":
        where.append("(m.status = 'FINISHED' OR (m.home_score IS NOT NULL AND m.away_score IS NOT NULL))")
    if filters.get("tournament_id"):
        where.append("m.tournament_id = %s")
        params.append(filters["tournament_id"])
    if filters.get("status"):
        where.append("m.status = %s")
        params.append(filters["status"])
    if filters.get("q"):
        q = filters["q"]
        numeric_id = int(q) if q.isdigit() else -1
        where.append("(m.home_team ILIKE %s ESCAPE '\\' OR m.away_team ILIKE %s ESCAPE '\\' OR m.id = %s)")
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params.extend([f"%{escaped}%", f"%{escaped}%", numeric_id])
    if filters["period"] != "all":
        days = int(filters["period"])
        if view == "finished":
            where.append("m.kickoff_time >= %s AND m.kickoff_time <= %s")
            params.extend([now - timedelta(days=days), now])
        elif view in {"upcoming", "attention", "all"}:
            where.append("m.kickoff_time <= %s")
            params.append(now + timedelta(days=days))
    return " AND ".join(where), params


def prepare_admin_match_list(cur, filters, league=None, tournament_name=None):
    """Return one filtered, date-grouped, server-paginated admin match list."""
    now = datetime.now(timezone.utc)
    where, params = _admin_match_where(filters, now)
    if league:
        where += " AND m.league = %s"
        params.append(league)
    if tournament_name:
        where += " AND t.name = %s"
        params.append(tournament_name)

    count_sql = f"""
        SELECT COUNT(*)
        FROM matches m
        LEFT JOIN tournaments t ON t.id = m.tournament_id
        WHERE {where}
    """
    cur.execute(count_sql, tuple(params))
    total = int((cur.fetchone() or [0])[0] or 0)
    pages = max((total + filters["per_page"] - 1) // filters["per_page"], 1)
    page = min(filters["page"], pages)
    order = "m.kickoff_time DESC, m.id DESC" if filters["view"] == "finished" else "m.kickoff_time ASC, m.id ASC"
    offset = (page - 1) * filters["per_page"]
    cur.execute(
        f"""
        SELECT m.id, m.home_team, m.away_team, m.kickoff_time, m.deadline,
               m.status, m.home_score, m.away_score, m.league,
               m.tournament_id, t.name, m.playoff_stage_manual,
               m.playoff_stage_auto, m.api_match_id
        FROM matches m
        LEFT JOIN tournaments t ON t.id = m.tournament_id
        WHERE {where}
        ORDER BY {order}
        LIMIT %s OFFSET %s
        """,
        tuple(params + [filters["per_page"], offset]),
    )
    matches = []
    for row in cur.fetchall():
        kickoff = parse_datetime(row[3])
        deadline = parse_datetime(row[4])
        kickoff_msk = kickoff.astimezone(MSK) if kickoff else None
        deadline_msk = deadline.astimezone(MSK) if deadline else None
        matches.append({
            "id": row[0], "home_team": row[1], "away_team": row[2],
            "kickoff_time": kickoff, "deadline": deadline, "status": row[5],
            "home_score": row[6], "away_score": row[7], "league": row[8],
            "has_result": row[6] is not None and row[7] is not None,
            "tournament_id": row[9], "tournament_name": row[10] or "",
            "playoff_stage": row[11] or row[12] or "",
            "api_match_id": row[13],
            "is_manual": not row[13],
            "match_date_msk": kickoff_msk.strftime("%Y-%m-%d") if kickoff_msk else "",
            "match_time_msk": kickoff_msk.strftime("%H:%M") if kickoff_msk else "",
            "deadline_date_msk": deadline_msk.strftime("%Y-%m-%d") if deadline_msk else "",
            "deadline_time_msk": deadline_msk.strftime("%H:%M") if deadline_msk else "",
            "date_label": format_date_ru(kickoff_msk.date().isoformat()) if kickoff_msk else "Дата не указана",
        })
    grouped = []
    for match in matches:
        if not grouped or grouped[-1]["key"] != (match["kickoff_time"].astimezone(MSK).date().isoformat() if match["kickoff_time"] else "unknown"):
            key = match["kickoff_time"].astimezone(MSK).date().isoformat() if match["kickoff_time"] else "unknown"
            grouped.append({"key": key, "label": match["date_label"], "matches": []})
        grouped[-1]["matches"].append(match)
    return {
        "matches": matches,
        "groups": grouped,
        "total": total,
        "page": page,
        "pages": pages,
        "per_page": filters["per_page"],
        "first": offset + 1 if total else 0,
        "last": min(offset + len(matches), total),
    }


def prepare_admin_matches_page_data(cur):
    """Load only metadata needed by the main paginated matches page."""
    cur.execute("""
        SELECT id, name, is_active, start_date
        FROM tournaments
        ORDER BY is_active DESC, id DESC
    """)
    tournaments = [
        {"id": row[0], "name": row[1], "is_active": row[2], "start_date": row[3]}
        for row in cur.fetchall()
    ]
    return {
        "tournaments": tournaments,
        "active_tournaments": [item for item in tournaments if item.get("is_active")],
        "playoff_stages": PLAYOFF_STAGES,
    }


def parse_russian_cup_match_filters(args):
    view = args.get("view", "upcoming")
    if view not in {"upcoming", "pending_result", "finished", "all"}:
        view = "upcoming"
    page = max(args.get("page", 1, type=int) or 1, 1)
    period = args.get("period", "30" if view == "upcoming" else "all")
    if period not in {"7", "30", "all"}:
        period = "30" if view == "upcoming" else "all"
    return {
        "view": view,
        "q": (args.get("q") or "").strip(),
        "status": (args.get("status") or "").strip().upper(),
        "period": period,
        "page": page,
        "per_page": {"upcoming": 15, "pending_result": 20, "finished": 20, "all": 30}[view],
    }


def prepare_russian_cup_match_list(cur, tournament_id, filters):
    query_filters = dict(filters)
    query_filters["view"] = "attention" if filters["view"] == "pending_result" else filters["view"]
    now = datetime.now(timezone.utc)
    where, params = _admin_match_where(query_filters, now)
    where += " AND m.tournament_id = %s AND m.league = 'rcup'"
    params.append(tournament_id)

    def count_rows(current_where, current_params):
        cur.execute(
            f"""
            SELECT COUNT(*)
            FROM matches m
            LEFT JOIN tournaments t ON t.id = m.tournament_id
            WHERE {current_where}
            """,
            tuple(current_params),
        )
        return int((cur.fetchone() or [0])[0] or 0)

    total = count_rows(where, params)
    pending_count = 0
    if filters["view"] == "upcoming":
        pending_filters = dict(filters, view="attention", period="all")
        pending_where, pending_params = _admin_match_where(pending_filters, now)
        pending_where += " AND m.tournament_id = %s AND m.league = 'rcup'"
        pending_params.append(tournament_id)
        pending_count = count_rows(pending_where, pending_params)
    fallback_notice = False
    if filters["view"] == "upcoming" and total == 0 and filters["period"] == "30":
        query_filters["period"] = "all"
        where, params = _admin_match_where(query_filters, now)
        where += " AND m.tournament_id = %s AND m.league = 'rcup'"
        params.append(tournament_id)
        total = count_rows(where, params)
        fallback_notice = total > 0

    pages = max((total + filters["per_page"] - 1) // filters["per_page"], 1)
    page = min(filters["page"], pages)
    offset = (page - 1) * filters["per_page"]
    order = "m.kickoff_time DESC, m.id DESC" if filters["view"] in {"pending_result", "finished"} else "m.kickoff_time ASC, m.id ASC"
    cur.execute(
        f"""
        SELECT m.id, m.home_team, m.away_team, m.kickoff_time, m.deadline,
               m.status, m.home_score, m.away_score, m.playoff_stage_manual
        FROM matches m
        LEFT JOIN tournaments t ON t.id = m.tournament_id
        WHERE {where}
        ORDER BY {order}
        LIMIT %s OFFSET %s
        """,
        tuple(params + [filters["per_page"], offset]),
    )
    now_msk = now.astimezone(MSK).date()
    matches = []
    for row in cur.fetchall():
        kickoff = parse_datetime(row[3])
        deadline = parse_datetime(row[4])
        kickoff_msk = kickoff.astimezone(MSK) if kickoff else None
        deadline_msk = deadline.astimezone(MSK) if deadline else None
        matches.append({
            "id": row[0], "home_team": row[1], "away_team": row[2],
            "kickoff_time": kickoff, "deadline": deadline, "status": row[5],
            "home_score": row[6], "away_score": row[7], "stage": row[8] or "",
            "has_result": row[6] is not None and row[7] is not None,
            "match_date_msk": kickoff_msk.strftime("%Y-%m-%d") if kickoff_msk else "",
            "match_time_msk": kickoff_msk.strftime("%H:%M") if kickoff_msk else "",
            "deadline_date_msk": deadline_msk.strftime("%Y-%m-%d") if deadline_msk else "",
            "deadline_time_msk": deadline_msk.strftime("%H:%M") if deadline_msk else "",
        })
    groups = []
    for match in matches:
        date_value = match["kickoff_time"].astimezone(MSK).date() if match["kickoff_time"] else None
        if date_value == now_msk:
            label = "Сегодня"
        elif date_value == now_msk + timedelta(days=1):
            label = "Завтра"
        elif date_value == now_msk - timedelta(days=1):
            label = "Вчера"
        else:
            label = format_date_ru(match["match_date_msk"]) if match["match_date_msk"] else "Дата не указана"
        key = match["match_date_msk"] or "unknown"
        if not groups or groups[-1]["key"] != key:
            groups.append({"key": key, "label": label, "matches": []})
        groups[-1]["matches"].append(match)
    return {
        "matches": matches,
        "groups": groups,
        "total": total,
        "page": page,
        "pages": pages,
        "per_page": filters["per_page"],
        "first": offset + 1 if total else 0,
        "last": min(offset + len(matches), total),
        "fallback_notice": fallback_notice,
        "pending_count": pending_count,
    }


def prepare_wc_playoff_page_data(cur, filters):
    """Load only the bounded WC playoff candidate set for its admin page."""
    now = datetime.now(timezone.utc)
    where, params = _admin_match_where(filters, now)
    where += " AND t.name = %s AND m.kickoff_time >= %s"
    params.extend(["ЧМ-2026", WC2026_PLAYOFF_START])
    cur.execute(
        f"""
        SELECT COUNT(*)
        FROM matches m
        JOIN tournaments t ON t.id = m.tournament_id
        WHERE {where}
        """,
        tuple(params),
    )
    total = int((cur.fetchone() or [0])[0] or 0)
    pages = max((total + filters["per_page"] - 1) // filters["per_page"], 1)
    page = min(filters["page"], pages)
    offset = (page - 1) * filters["per_page"]
    cur.execute(
        f"""
        SELECT m.id, m.home_team, m.away_team, m.kickoff_time, m.deadline,
               m.status, m.home_score, m.away_score, m.league,
               COALESCE(m.manual_teams_override, 0),
               COALESCE(m.manual_result_override, 0),
               COALESCE(m.manual_kickoff_override, 0),
               m.playoff_stage_manual, m.playoff_stage_auto,
               m.api_match_id, m.api_conflict_note, COUNT(p.user_id)
        FROM matches m
        JOIN tournaments t ON t.id = m.tournament_id
        LEFT JOIN predictions p ON p.match_id = m.id AND p.tournament_id = m.tournament_id
        WHERE {where}
        GROUP BY m.id, t.name
        ORDER BY m.kickoff_time ASC, m.id ASC
        LIMIT %s OFFSET %s
        """,
        tuple(params + [filters["per_page"], offset]),
    )
    matches = []
    for row in cur.fetchall():
        kickoff = parse_datetime(row[3])
        kickoff_msk = kickoff.astimezone(MSK) if kickoff else None
        stage = determine_effective_playoff_stage(row[12], row[13])
        matches.append({
            "id": row[0], "home_team": row[1], "away_team": row[2],
            "kickoff_time": kickoff, "deadline": parse_datetime(row[4]),
            "status": row[5], "home_score": row[6], "away_score": row[7],
            "league": row[8], "manual_teams_override": bool(row[9]),
            "manual_result_override": bool(row[10]), "manual_kickoff_override": bool(row[11]),
            "playoff_stage_manual": row[12], "playoff_stage_auto": row[13],
            "effective_playoff_stage": stage, "playoff_stage": stage,
            "playoff_stage_label": get_playoff_stage_label(stage),
            "api_match_id": row[14], "api_conflict_note": row[15],
            "has_api_conflict": bool(row[15]), "predictions_count": row[16] or 0,
            "match_date_input": kickoff_msk.strftime("%Y-%m-%d") if kickoff_msk else "",
            "match_time_input": kickoff_msk.strftime("%H:%M") if kickoff_msk else "",
        })
    groups = []
    for match in matches:
        key = match["match_date_input"] or "unknown"
        if not groups or groups[-1]["key"] != key:
            groups.append({"key": key, "label": key or "Дата не указана", "matches": []})
        groups[-1]["matches"].append(match)
    return {
        "wc_playoff_matches": matches,
        "playoff_stages": PLAYOFF_STAGES,
        "wc_playoff_list": {
            "matches": matches, "groups": groups, "total": total,
            "page": page, "pages": pages, "per_page": filters["per_page"],
            "first": offset + 1 if total else 0,
            "last": min(offset + len(matches), total),
        },
    }


def normalize_league_key(raw_value):
    """
    Normalize legacy/manual league values (including occasional mojibake variants)
    into stable internal keys used by admin grouping.
    """
    if raw_value is None:
        return "other"

    s = str(raw_value).strip()
    if not s:
        return "other"

    lowered = s.lower()

    alias_map = {
        "rpl": "rpl",
        "rfpl": "rpl",
        "рпл": "rpl",
        "рпл 2026": "rpl",
        "wc2026": "wc2026",
        "wc-2026": "wc2026",
        "чм-2026": "wc2026",
        "чм 2026": "wc2026",
        "rcup": "rcup",
        "кубок россии": "rcup",
        "other": "other",
        "россия": "other",
    }

    if lowered in alias_map:
        return alias_map[lowered]

    # Common UTF-8/CP1251 mojibake fragments seen in legacy text fields.
    if ("ð" in lowered) or ("р" in lowered and "џ" in lowered):
        if "ð ðŸð›" in lowered or "ð¿ð»" in lowered:
            return "rpl"
        if "2026" in lowered and ("ð§ðœ" in lowered or "ñ‡ð¼" in lowered):
            return "wc2026"
        if "ðºñƒð±ð¾ðº" in lowered and "ñ€ð¾ñ" in lowered:
            return "rcup"

    if "2026" in lowered and ("чм" in lowered or "world cup" in lowered):
        return "wc2026"
    if "рпл" in lowered or "rfpl" in lowered:
        return "rpl"
    if "кубок" in lowered and "рос" in lowered:
        return "rcup"

    # Broken unicode / placeholder characters: route safely to generic bucket.
    if ("�" in s) or ("?" in s):
        return "other"

    # Safety: do not leak unknown raw values into UI labels.
    # Keep predictable buckets only.
    return "other"


def prepare_admin_view_data(cur):
    start_date_str = START_DATE.strftime("%Y-%m-%dT%H:%M:%S")

    cur.execute("""
        SELECT id, home_team, away_team, kickoff_time, status
        FROM matches
        WHERE kickoff_time >= %s
        ORDER BY kickoff_time
    """, (start_date_str,))
    raw_matches = cur.fetchall()

    free_by_day = defaultdict(list)
    finished_by_day = defaultdict(list)

    for m in raw_matches:
        kickoff_dt = parse_datetime(m[3])
        if not kickoff_dt:
            continue

        kickoff_msk = kickoff_dt.astimezone(MSK)
        day = kickoff_msk.date().isoformat()
        item = {
            'id': m[0],
            'home_team': m[1],
            'away_team': m[2],
            'kickoff_time': m[3],
            'status': m[4]
        }
        if m[4] == 'FINISHED':
            finished_by_day[day].append(item)
        else:
            free_by_day[day].append(item)

    free_months = defaultdict(list)
    for day_str in sorted(free_by_day.keys()):
        month_key = day_str[:7]
        free_months[month_key].append({
            'date': format_date_ru(day_str),
            'matches': free_by_day[day_str],
            'count': len(free_by_day[day_str])
        })

    finished_months = defaultdict(list)
    for day_str in sorted(finished_by_day.keys()):
        month_key = day_str[:7]
        finished_months[month_key].append({
            'date': format_date_ru(day_str),
            'matches': finished_by_day[day_str],
            'count': len(finished_by_day[day_str])
        })

    free_months_list = []
    for mk in sorted(free_months.keys()):
        year, month = mk.split('-')
        month_label = format_month_label(year, month)
        free_months_list.append({
            'key': mk,
            'label': month_label,
            'days': free_months[mk],
            'total_matches': sum(d['count'] for d in free_months[mk])
        })

    finished_months_list = []
    for mk in sorted(finished_months.keys()):
        year, month = mk.split('-')
        month_label = format_month_label(year, month)
        finished_months_list.append({
            'key': mk,
            'label': month_label,
            'days': finished_months[mk],
            'total_matches': sum(d['count'] for d in finished_months[mk])
        })

    cur.execute("""
        SELECT m.id,
               m.home_team,
               m.away_team,
               m.kickoff_time,
               m.deadline,
               m.status,
               m.league,
               m.playoff_stage_manual,
               m.playoff_stage_auto,
               t.name
        FROM matches m
        LEFT JOIN tournaments t ON t.id = m.tournament_id
        WHERE ((m.api_match_id IS NULL OR m.api_match_id = '') OR t.name = 'ЧМ-2026')
        AND m.kickoff_time >= %s
        ORDER BY m.kickoff_time
    """, (start_date_str,))
    manual_matches = []
    for m in cur.fetchall():
        kickoff_dt = parse_datetime(m[3])
        deadline_dt = parse_datetime(m[4])
        kickoff_msk = kickoff_dt.astimezone(MSK) if kickoff_dt else None
        deadline_msk = deadline_dt.astimezone(MSK) if deadline_dt else None
        is_playoff = is_wc2026_playoff_match(m[9], m[6], kickoff_dt)
        effective_stage = determine_effective_playoff_stage(m[7], m[8]) if is_playoff else None
        manual_matches.append({
            'id': m[0],
            'home_team': m[1],
            'away_team': m[2],
            'kickoff_time': kickoff_dt,
            'deadline': deadline_dt,
            'status': m[5],
            'league': m[6] if len(m) > 6 else 'other',
            'playoff_stage': effective_stage,
            'playoff_stage_manual': m[7],
            'playoff_stage_auto': m[8],
            'effective_playoff_stage': effective_stage,
            'playoff_stage_label': get_playoff_stage_label(effective_stage),
            'is_wc2026_playoff': is_playoff,
            'match_date_msk': kickoff_msk.strftime("%Y-%m-%d") if kickoff_msk else "",
            'match_time_msk': kickoff_msk.strftime("%H:%M") if kickoff_msk else "",
            'deadline_date_msk': deadline_msk.strftime("%Y-%m-%d") if deadline_msk else "",
            'deadline_time_msk': deadline_msk.strftime("%H:%M") if deadline_msk else ""
        })

    cur.execute("""
        SELECT id, name, is_active, start_date
        FROM tournaments
        ORDER BY is_active DESC, id DESC
    """)
    tournaments = []
    for t in cur.fetchall():
        tournaments.append({
            'id': t[0],
            'name': t[1],
            'is_active': t[2],
            'start_date': t[3]
        })
    active_tournaments = [t for t in tournaments if t.get('is_active')]

    cur.execute(
        """
        SELECT DISTINCT team_name
        FROM (
            SELECT home_team AS team_name
            FROM matches
            WHERE tournament_id IN (SELECT id FROM tournaments WHERE name = 'ЧМ-2026')
              AND home_team IS NOT NULL
              AND home_team <> ''
            UNION
            SELECT away_team AS team_name
            FROM matches
            WHERE tournament_id IN (SELECT id FROM tournaments WHERE name = 'ЧМ-2026')
              AND away_team IS NOT NULL
              AND away_team <> ''
        ) teams
        WHERE team_name <> 'Unknown'
        ORDER BY team_name
        """
    )
    wc_team_options = [r[0] for r in cur.fetchall()]

    cur.execute(
        """
        SELECT
            m.id,
            m.home_team,
            m.away_team,
            m.kickoff_time,
            m.deadline,
            m.status,
            m.home_score,
            m.away_score,
            m.league,
            t.name,
            COALESCE(m.manual_teams_override, 0),
            COALESCE(m.manual_result_override, 0),
            COALESCE(m.manual_kickoff_override, 0),
            m.playoff_stage_manual,
            m.playoff_stage_auto,
            m.api_match_id,
            m.api_conflict_note,
            COUNT(p.user_id) AS predictions_count
        FROM matches m
        JOIN tournaments t ON t.id = m.tournament_id
        LEFT JOIN predictions p ON p.match_id = m.id AND p.tournament_id = m.tournament_id
        WHERE t.name = 'ЧМ-2026'
        GROUP BY m.id, t.name
        ORDER BY m.kickoff_time
        """
    )
    wc_playoff_matches = []
    for m in cur.fetchall():
        kickoff_dt = parse_datetime(m[3])
        if not is_wc2026_playoff_match(m[9], m[8], kickoff_dt):
            continue
        kickoff_msk = kickoff_dt.astimezone(MSK) if kickoff_dt else None
        wc_playoff_matches.append({
            'id': m[0],
            'home_team': m[1],
            'away_team': m[2],
            'kickoff_time': kickoff_dt,
            'deadline': parse_datetime(m[4]),
            'status': m[5],
            'home_score': m[6],
            'away_score': m[7],
            'league': m[8],
            'manual_teams_override': bool(m[10]),
            'manual_result_override': bool(m[11]),
            'manual_kickoff_override': bool(m[12]),
            'playoff_stage_manual': m[13],
            'playoff_stage_auto': m[14],
            'effective_playoff_stage': determine_effective_playoff_stage(m[13], m[14]),
            'playoff_stage': determine_effective_playoff_stage(m[13], m[14]),
            'playoff_stage_label': get_playoff_stage_label(determine_effective_playoff_stage(m[13], m[14])),
            'api_match_id': m[15],
            'api_conflict_note': m[16],
            'has_api_conflict': bool(m[16]),
            'predictions_count': m[17] or 0,
            'match_date_msk': kickoff_msk.strftime("%d.%m.%Y %H:%M") if kickoff_msk else "",
            'match_date_input': kickoff_msk.strftime("%Y-%m-%d") if kickoff_msk else "",
            'match_time_input': kickoff_msk.strftime("%H:%M") if kickoff_msk else "",
        })
    wc_playoff_matches.sort(
        key=lambda m: (
            get_playoff_stage_sort_order(m.get('effective_playoff_stage')),
            m.get('kickoff_time'),
        )
    )

    cur.execute("""
        SELECT
            u.id,
            u.username,
            u.is_admin,
            u.last_seen,
            COALESCE(u.is_deleted, 0),
            COUNT(DISTINCT (p.user_id, p.match_id, p.tournament_id)) AS predictions_total,
            COUNT(DISTINCT (p.user_id, p.match_id, p.tournament_id)) FILTER (WHERE t.is_active = 1) AS active_predictions,
            COUNT(DISTINCT (p.user_id, p.match_id, p.tournament_id)) FILTER (WHERE t.is_active = 0) AS archive_predictions
        FROM users u
        LEFT JOIN predictions p ON p.user_id = u.id
        LEFT JOIN tournaments t ON t.id = p.tournament_id
        GROUP BY u.id, u.username, u.is_admin, u.last_seen, u.is_deleted
        ORDER BY u.username
    """)
    users = []
    title_users = []
    for u in cur.fetchall():
        users.append({
            'id': u[0],
            'username': u[1],
            'is_admin': u[2],
            'last_seen': (
                u[3].astimezone(MSK).strftime('%d.%m.%Y %H:%M')
                if u[3] else 'Никогда'
            ),
            'is_deleted': u[4] or 0,
            'predictions_total': u[5] or 0,
            'active_predictions': u[6] or 0,
            'archive_predictions': u[7] or 0,
        })
        if u[2] == 0 and (u[4] or 0) == 0:
            title_users.append({'id': u[0], 'username': u[1], 'titles': []})

    cur.execute(
        """
        SELECT user_id, title
        FROM user_titles
        ORDER BY awarded_at DESC, title
        """
    )
    titles_by_user = {}
    for user_id, title in cur.fetchall():
        titles_by_user.setdefault(user_id, []).append(title)
    for user in title_users:
        user['titles'] = titles_by_user.get(user['id'], [])

    return {
        'free_months': free_months_list,
        'finished_months': finished_months_list,
        'manual_matches': manual_matches,
        'users': users,
        'title_users': title_users,
        'allowed_titles': ALLOWED_TITLES,
        'tournaments': tournaments,
        'active_tournaments': active_tournaments,
        'playoff_stages': PLAYOFF_STAGES,
        'wc_team_options': wc_team_options,
        'wc_playoff_matches': wc_playoff_matches,
    }


def prepare_admin_matches_data(cur):
    data = prepare_admin_view_data(cur)

    manual_matches = data.get('manual_matches', [])
    for month_block in data.get('free_months', []):
        key = month_block.get('key', '')
        if '-' in key:
            year, month = key.split('-', 1)
            month_block['label'] = format_month_label(year, month)

    for month_block in data.get('finished_months', []):
        key = month_block.get('key', '')
        if '-' in key:
            year, month = key.split('-', 1)
            month_block['label'] = format_month_label(year, month)
    league_names = {
        'rpl': 'РПЛ',
        'wc2026': 'ЧМ-2026',
        'rcup': 'Кубок России',
        'other': 'Россия',
        None: 'Россия',
        '': 'Россия',
    }

    manual_grouped_map = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for m in manual_matches:
        kickoff_dt = parse_datetime(m.get('kickoff_time'))
        if not kickoff_dt:
            continue
        kickoff_msk = kickoff_dt.astimezone(MSK)
        league_key = normalize_league_key(m.get('league'))
        league_base = league_names.get(league_key, str(league_key).upper())
        year = str(kickoff_msk.year)
        league_label = f"{league_base} {year}".strip()
        month_key = f"{kickoff_msk.year:04d}-{kickoff_msk.month:02d}"
        day_key = kickoff_msk.date().isoformat()
        manual_grouped_map[league_label][month_key][day_key].append(m)

    manual_grouped = []
    for league_label in sorted(manual_grouped_map.keys()):
        months = []
        league_total = 0
        for month_key in sorted(manual_grouped_map[league_label].keys()):
            if "-" in month_key:
                year, month = month_key.split("-", 1)
                month_label = format_month_label(year, month)
            else:
                month_label = month_key
            days = []
            month_total = 0
            for day_key in sorted(manual_grouped_map[league_label][month_key].keys()):
                matches_for_day = manual_grouped_map[league_label][month_key][day_key]
                days.append({
                    'key': day_key,
                    'date': format_date_ru(day_key),
                    'matches': matches_for_day
                })
                month_total += len(matches_for_day)
            months.append({
                'key': month_key,
                'label': month_label,
                'days': days,
                'total_matches': month_total
            })
            league_total += month_total
        manual_grouped.append({
            'key': league_label.lower().replace(" ", "_"),
            'label': league_label,
            'months': months,
            'total_matches': league_total
        })

    data['manual_grouped'] = manual_grouped
    return data
