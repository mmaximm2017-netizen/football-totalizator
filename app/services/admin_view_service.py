from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.config import START_DATE
from app.routes.admin_actions import ALLOWED_TITLES
from app.services.wc_playoff_service import (
    PLAYOFF_STAGES,
    determine_effective_playoff_stage,
    get_playoff_stage_label,
    get_playoff_stage_sort_order,
    is_wc2026_playoff_match,
)
from app.utils import (
    format_admin_match_date,
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


def parse_russian_cup_match_filters(args):
    return parse_tournament_match_filters(args)


def parse_rpl_match_filters(args):
    return parse_tournament_match_filters(args)


def parse_tournament_match_filters(args):
    view = args.get("view", "upcoming")
    if view not in {"upcoming", "pending_result", "finished", "all"}:
        view = "upcoming"
    page = max(args.get("page", 1, type=int) or 1, 1)
    return {
        "view": view,
        "page": page,
        "per_page": 5,
    }


def prepare_russian_cup_match_list(cur, tournament_id, filters):
    return prepare_tournament_match_list(cur, tournament_id, "rcup", filters, include_pending_preview=True)


def prepare_rpl_match_list(cur, tournament_id, filters):
    return prepare_tournament_match_list(cur, tournament_id, "rpl", filters, include_pending_preview=True)


def prepare_tournament_match_list(cur, tournament_id, league, filters, include_pending_preview=False):
    query_filters = dict(filters, period="30" if filters["view"] == "upcoming" else "all")
    query_filters["view"] = "attention" if filters["view"] == "pending_result" else filters["view"]
    now = datetime.now(timezone.utc)
    where, params = _admin_match_where(query_filters, now)
    where += f" AND m.tournament_id = %s AND m.league = '{league}'"
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
    pending_preview = []
    if filters["view"] == "upcoming":
        pending_filters = dict(filters, view="attention", period="all")
        pending_where, pending_params = _admin_match_where(pending_filters, now)
        pending_where += f" AND m.tournament_id = %s AND m.league = '{league}'"
        pending_params.append(tournament_id)
        pending_count = count_rows(pending_where, pending_params)
        if include_pending_preview and filters["page"] == 1:
            cur.execute(
                f"""
                SELECT m.id, m.home_team, m.away_team, m.kickoff_time, m.deadline,
                       m.status, m.home_score, m.away_score, m.playoff_stage_manual,
                       m.result_origin
                FROM matches m
                LEFT JOIN tournaments t ON t.id = m.tournament_id
                WHERE {pending_where}
                ORDER BY m.kickoff_time DESC, m.id DESC
                LIMIT %s
                """,
                tuple(pending_params + [5]),
            )
            pending_preview = [
                {
                    "id": row[0], "home_team": row[1], "away_team": row[2],
                    "kickoff_time": parse_datetime(row[3]), "deadline": parse_datetime(row[4]),
                    "status": row[5], "home_score": row[6], "away_score": row[7],
                    "stage": row[8] or "", "tournament_id": tournament_id,
                    "result_origin": row[9],
                    "is_auto_result": row[9] == "auto_result_worker",
                    "has_result": row[6] is not None and row[7] is not None,
                    "match_date_msk": (parse_datetime(row[3]).astimezone(MSK).strftime("%Y-%m-%d") if parse_datetime(row[3]) else ""),
                    "match_time_msk": (parse_datetime(row[3]).astimezone(MSK).strftime("%H:%M") if parse_datetime(row[3]) else ""),
                    "deadline_date_msk": (parse_datetime(row[4]).astimezone(MSK).strftime("%Y-%m-%d") if parse_datetime(row[4]) else ""),
                    "deadline_time_msk": (parse_datetime(row[4]).astimezone(MSK).strftime("%H:%M") if parse_datetime(row[4]) else ""),
                    "date_label": format_admin_match_date(parse_datetime(row[3]).astimezone(MSK)) if parse_datetime(row[3]) else "Дата не указана",
                    "pending_result": True,
                }
                for row in cur.fetchall()
            ]
    fallback_notice = False
    if filters["view"] == "upcoming" and total == 0 and query_filters["period"] == "30":
        query_filters["period"] = "all"
        where, params = _admin_match_where(query_filters, now)
        where += f" AND m.tournament_id = %s AND m.league = '{league}'"
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
               m.status, m.home_score, m.away_score, m.playoff_stage_manual,
               m.result_origin
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
            "tournament_id": tournament_id,
            "result_origin": row[9],
            "is_auto_result": row[9] == "auto_result_worker",
            "has_result": row[6] is not None and row[7] is not None,
            "pending_result": False,
            "match_date_msk": kickoff_msk.strftime("%Y-%m-%d") if kickoff_msk else "",
            "date_label": format_admin_match_date(kickoff_msk) if kickoff_msk else "Дата не указана",
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
            label = match["date_label"]
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
        "pending_preview": pending_preview,
        "pending_preview_total": pending_count if include_pending_preview and filters["view"] == "upcoming" else 0,
    }


def normalize_league_key(raw_value):
    """Normalize supported league aliases into stable internal keys."""
    if raw_value is None:
        return "other"

    lowered = str(raw_value).strip().lower()
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
    return alias_map.get(lowered, "other")


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
