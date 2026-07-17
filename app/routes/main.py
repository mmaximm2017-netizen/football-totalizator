# app/routes/main.py

import logging
from datetime import datetime, timezone
from collections import defaultdict
from zoneinfo import ZoneInfo

from flask import (
    Blueprint,
    g,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    jsonify
)

from app.db import get_db, close_db
from app.utils import (
    get_flag,
    get_club_logo,
    cached_to_msk,
    is_before_deadline,
    format_date_ru,
    format_month_label,
)
from app.config import START_DATE
from app.services.tournament_context_service import (
    get_selected_tournament_id,
    get_session_start_tournament_id,
    get_tournament_state_flags,
)
from app.services.tournament_service import (
    get_all_tournaments,
    get_tournament_by_id,
)
from app.services.wc_playoff_service import (
    determine_effective_playoff_stage,
    get_playoff_stage_label,
    get_playoff_stage_sort_order,
    is_wc2026_playoff_match,
)

main_bp = Blueprint('main', __name__)
logger = logging.getLogger(__name__)

MSK = ZoneInfo("Europe/Moscow")

VISIBLE_MATCH_STATUSES = (
    'SCHEDULED',
    'TIMED',
    'IN_PLAY',
    'LIVE',
    'PAUSED',
    'HALFTIME',
    'FINISHED',
)

RUSSIA_TOURNAMENT_NAME = 'Чемпионат России 🇷🇺'
RUSSIAN_CUP_TOURNAMENT_NAME = 'Кубок России'
SUPERCUP_LOGO_STATIC_PATH = 'clubs/Russian_Super_Cup_Logo.jpeg'
RPL_LOGO_STATIC_PATH = 'clubs/russian-premier-league-footballlogos-org.png'
RFU_LOGO_STATIC_PATH = 'clubs/Russian_Football_Union_Logo.svg'
RCUP_LOGO_STATIC_PATH = 'clubs/Fonbet_Russian_Cup.png'
WC2026_LOGO_STATIC_PATH = 'clubs/wc2026-logo.png'

PLAYOFF_STAGE_CARD_CLASSES = {
    "playoff": "match-card--playoff",
    "round_32": "match-card--playoff match-card--r32",
    "round_16": "match-card--playoff match-card--r16",
    "quarter_final": "match-card--playoff match-card--qf",
    "semi_final": "match-card--playoff match-card--sf",
    "third_place": "match-card--playoff match-card--third",
    "final": "match-card--playoff match-card--final",
}


def normalize_match_category(value):
    category = str(value or '').strip().lower().replace('-', '_').replace(' ', '_')
    if category in {'supercup', 'super_cup'}:
        return 'supercup'
    if category in {'national_team', 'national', 'russia'}:
        return 'national_team'
    if category == 'rpl':
        return 'rpl'
    return category


def normalize_tournament_slug(name=None, slug=None, tournament_type=None):
    raw = (slug or tournament_type or '').strip().lower()
    if raw == 'rcup' or name == RUSSIAN_CUP_TOURNAMENT_NAME:
        return 'rcup'
    return raw


def is_russian_cup_match(league=None, tournament_name=None, tournament_slug=None, tournament_type=None):
    return (
        (league or '').strip().lower() == 'rcup'
        or tournament_name == RUSSIAN_CUP_TOURNAMENT_NAME
        or normalize_tournament_slug(tournament_name, tournament_slug, tournament_type) == 'rcup'
    )


def is_russia_team_match(home_team, away_team):
    teams = {str(home_team or '').strip().lower(), str(away_team or '').strip().lower()}
    return 'россия' in teams or 'russia' in teams


def get_match_logo(home_team, away_team, league, is_supercup=False):
    if is_supercup:
        return SUPERCUP_LOGO_STATIC_PATH, 'Суперкубок России', RPL_LOGO_STATIC_PATH
    if is_russia_team_match(home_team, away_team):
        return RFU_LOGO_STATIC_PATH, 'Сборная России', None
    if league == 'rpl':
        return RPL_LOGO_STATIC_PATH, 'РПЛ', None
    if league == 'rcup':
        return RCUP_LOGO_STATIC_PATH, 'Кубок России', None
    if league == 'wc2026':
        return WC2026_LOGO_STATIC_PATH, 'Турнир', None
    return None, None, None


def get_russia_match_event(home_team, away_team, stage_text, tournament_name, match_category=None):
    if tournament_name != RUSSIA_TOURNAMENT_NAME:
        return None, None

    category = normalize_match_category(match_category)
    if category == 'supercup':
        return 'supercup', 'Суперкубок'
    if category == 'national_team':
        return 'national', 'Сборная России'
    if category == 'rpl':
        return 'league', None

    if is_russia_team_match(home_team, away_team):
        return 'national', 'Сборная России'

    stage = str(stage_text or '').strip().lower()
    if 'суперкуб' in stage or 'supercup' in stage or 'super cup' in stage:
        return 'supercup', 'Суперкубок'

    return 'league', None


# =========================================================
# HELPERS
# =========================================================

def parse_dt(value):
    if not value:
        return None

    if isinstance(value, datetime):
        return value

    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def is_ajax_request():
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def ajax_error(message, status=400):
    return jsonify({
        "ok": False,
        "message": message
    }), status


def ajax_success(message, data=None):
    payload = {
        "ok": True,
        "message": message
    }

    if data:
        payload.update(data)

    return jsonify(payload)


# =========================================================
# INDEX
# =========================================================

@main_bp.route('/', methods=['GET', 'POST'])
def index():

    if 'user_id' not in session:
        if is_ajax_request():
            return ajax_error("Нужно войти в аккаунт", 401)

        return redirect(url_for('auth.login'))

    conn = get_db()
    cur = conn.cursor()

    try:

        league = request.args.get('league', 'all')
        requested_tid = request.args.get('tid', type=int)
        if request.method == 'GET' and requested_tid is None:
            session_tid = session.get('selected_tournament_id')
            if not session_tid:
                session_tid = get_session_start_tournament_id(cur)
                if session_tid:
                    session['selected_tournament_id'] = session_tid
                    session['tournament_selection_initialized'] = True
            if session_tid:
                redirect_args = {'tid': session_tid}
                if league != 'all':
                    redirect_args['league'] = league
                return redirect(url_for('main.index', **redirect_args))
        all_tournaments = get_all_tournaments()
        active_tournaments = [t for t in all_tournaments if t.get("is_active")]
        tid = get_selected_tournament_id(requested_tid)
        if requested_tid and tid == requested_tid:
            session['selected_tournament_id'] = tid
            session['tournament_selection_initialized'] = True

        start = START_DATE.strftime("%Y-%m-%dT%H:%M:%S")

        # =================================================
        # SAVE PREDICTION
        # =================================================

        if request.method == 'POST':
            if getattr(g, "is_admin", False):
                if is_ajax_request():
                    return ajax_error("Админ не участвует в ставках", 403)

                flash("Админ не участвует в ставках", "error")
                return redirect(url_for('main.index'))

            match_id = request.form.get('match_id')
            home_raw = request.form.get('home_goals')
            away_raw = request.form.get('away_goals')

            if not match_id or home_raw is None or away_raw is None:
                if is_ajax_request():
                    return ajax_error("Не хватает данных прогноза", 400)

                flash("Не хватает данных прогноза", "error")
                return redirect(url_for('main.index'))

            try:
                h = int(str(home_raw).strip())
                a = int(str(away_raw).strip())

                if h < 0 or a < 0 or h > 99 or a > 99:
                    raise ValueError

            except Exception:
                if is_ajax_request():
                    return ajax_error("Некорректный счёт")

                flash("Некорректный счёт", "error")
                return redirect(url_for('main.index'))

            cur.execute("""
SELECT
    id,
    home_team,
    away_team,
    kickoff_time,
    deadline,
    status,
    tournament_id
FROM matches
JOIN tournaments ON tournaments.id = matches.tournament_id
JOIN users ON users.id = %s AND COALESCE(users.is_deleted, 0) = 0
WHERE matches.id = %s
            """, (session['user_id'], match_id))

            match = cur.fetchone()

            if not match:
                if is_ajax_request():
                    return ajax_error("Матч не найден", 404)

                flash("Матч не найден", "error")
                return redirect(url_for('main.index'))

            match_tid = match[6]
            if not match_tid:
                if is_ajax_request():
                    return ajax_error("Турнир для матча не определён", 400)
                flash("Турнир для матча не определён", "error")
                return redirect(url_for('main.index', league=league, tid=tid))

            if match_tid != tid:
                if is_ajax_request():
                    return ajax_error("Матч относится к другому турниру", 400)
                flash("Матч относится к другому турниру", "error")
                return redirect(url_for('main.index', league=league, tid=tid))

            if not is_before_deadline({
                "deadline": match[4]
            }):
                if is_ajax_request():
                    return ajax_error("Дедлайн прошёл")

                flash("Дедлайн прошёл", "error")
                return redirect(url_for('main.index'))

            cur.execute("""
                INSERT INTO predictions (
                    user_id, match_id, tournament_id,
                    home_goals, away_goals
                )
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (user_id, match_id, tournament_id)
                DO UPDATE SET
                    home_goals = EXCLUDED.home_goals,
                    away_goals = EXCLUDED.away_goals
            """, (
                session['user_id'],
                match_id,
                match_tid,
                h,
                a,
            ))

            conn.commit()

            if is_ajax_request():
                return ajax_success(
                    "������� �������",
                    {
                        "match_id": int(match_id),
                        "home_goals": h,
                        "away_goals": a
                    }
                )

            flash("Ставка сохранена", "success")

            return redirect(url_for('main.index', league=league, tid=tid))

        # =================================================
        # LOAD MATCHES
        # =================================================

        cur.execute("""
            SELECT m.id, m.home_team, m.away_team,
                   m.kickoff_time, m.deadline,
                   m.status, m.league, m.tournament_id,
                   m.home_score, m.away_score,
                   m.playoff_stage_manual,
                   m.playoff_stage_auto,
                   m.api_match_id,
                   t.name,
                   m.match_category
            FROM matches m
            LEFT JOIN tournaments t ON t.id = m.tournament_id
            WHERE (m.kickoff_time >= %s OR m.kickoff_time IS NULL)
            AND (%s IS NULL OR m.tournament_id = %s OR m.tournament_id IS NULL)
            ORDER BY m.kickoff_time NULLS LAST
        """, (start, tid, tid))

        rows = cur.fetchall()

        raw_matches = []

        for m in rows:

            is_playoff = is_wc2026_playoff_match(
                m[13],
                m[6],
                m[3],
            )
            effective_stage = determine_effective_playoff_stage(m[10], m[11]) if is_playoff else None
            css_class = PLAYOFF_STAGE_CARD_CLASSES.get(effective_stage, "")
            included_on_home = True
            exclude_reason = ""

            if not m[3]:
                included_on_home = False
                exclude_reason = "missing_kickoff"
            elif not is_playoff and m[5] not in VISIBLE_MATCH_STATUSES:
                included_on_home = False
                exclude_reason = f"status_not_visible:{m[5]}"

            logger.info(
                "[MAIN_MATCHES] match_id=%s api_match_id=%s kickoff=%s home_team=%s away_team=%s stage=%s effective_playoff_stage=%s is_playoff_match=%s included_on_home=%s exclude_reason=%s css_class=%s",
                m[0],
                m[12],
                m[3],
                m[1],
                m[2],
                m[10] or m[11],
                effective_stage,
                is_playoff,
                included_on_home,
                exclude_reason,
                css_class,
            )

            if not included_on_home:
                continue

            event_type, event_label = get_russia_match_event(
                m[1],
                m[2],
                m[10] or m[11],
                m[13],
                m[14],
            )
            match_category = normalize_match_category(m[14])
            is_russian_cup = is_russian_cup_match(m[6], m[13])
            is_rpl_category = match_category == 'rpl' and not is_russian_cup
            is_supercup = match_category == 'supercup'
            is_russia_category = match_category == 'national_team'
            logo_path, logo_alt, logo_fallback_path = get_match_logo(
                m[1],
                m[2],
                m[6],
                is_supercup,
            )

            raw_matches.append({
                "id": m[0],
                "home_team": m[1],
                "away_team": m[2],
                "kickoff_time": m[3],
                "deadline": m[4],
                "status": m[5],
                "league": m[6],
                "tournament_id": m[7],
                "home_score": m[8],
                "away_score": m[9],
                "playoff_stage": effective_stage,
                "effective_playoff_stage": effective_stage,
                "playoff_stage_css_class": css_class,
                "playoff_stage_label": get_playoff_stage_label(effective_stage),
                "match_category": m[14],
                "stage": m[10] or "",
                "tournament_name": m[13],
                "tournament_slug": normalize_tournament_slug(m[13]),
                "match_category_normalized": match_category,
                "is_rpl_category": is_rpl_category,
                "is_supercup": is_supercup,
                "is_russia_category": is_russia_category,
                "is_russian_cup": is_russian_cup,
                "tournament_logo_path": logo_path,
                "tournament_logo_alt": logo_alt,
                "tournament_logo_fallback_path": logo_fallback_path,
                "event_type": event_type,
                "event_label": event_label,
                "event_css_class": f"rpl-event-{event_type}" if event_type else "",
            })

        # =================================================
        # USER PREDICTIONS
        # =================================================


        match_ids = [m["id"] for m in raw_matches]

        user_preds = {}

        if match_ids:

            cur.execute("""
                SELECT
                    p.match_id,
                    p.home_goals,
                    p.away_goals,
                    p.points
                FROM predictions p
                WHERE p.user_id = %s
                  AND p.match_id = ANY(%s)
                  AND p.tournament_id = %s
            """, (
                session['user_id'],
                match_ids,
                tid
            ))

            for r in cur.fetchall():
                user_preds[r[0]] = {
                    "home": r[1],
                    "away": r[2],
                    "points": r[3]
                }

        # =================================================
        # GROUP BY DAY
        # =================================================

        grouped = defaultdict(list)

        today = datetime.now(timezone.utc).astimezone(MSK).strftime("%Y-%m-%d")

        for m in raw_matches:

            dt = parse_dt(m["kickoff_time"])
            if not dt:
                continue

            day = dt.astimezone(MSK).strftime("%Y-%m-%d")

            m["deadline_passed"] = not is_before_deadline({
                "deadline": m["deadline"]
            })

            m["finished"] = (m["status"] == "FINISHED")

            if m["id"] in user_preds:
                m["pred_home"] = user_preds[m["id"]]["home"]
                m["pred_away"] = user_preds[m["id"]]["away"]
                m["my_points"] = user_preds[m["id"]]["points"] if m["finished"] else 0
            else:
                m["pred_home"] = ""
                m["pred_away"] = ""
                m["my_points"] = 0

            grouped[day].append(m)

        for day_matches in grouped.values():
            day_matches.sort(
                key=lambda x: (
                    get_playoff_stage_sort_order(x.get("effective_playoff_stage")),
                    parse_dt(x.get("kickoff_time")) or datetime.max.replace(tzinfo=timezone.utc),
                )
            )

        # =================================================
        # BUILD DAYS
        # =================================================

        now_msk = datetime.now(timezone.utc).astimezone(MSK)
        nearest_future_match = None

        for m in raw_matches:
            if m.get("finished"):
                continue

            kickoff_dt = parse_dt(m.get("kickoff_time"))
            if not kickoff_dt:
                continue

            kickoff_msk = kickoff_dt.astimezone(MSK)
            if kickoff_msk < now_msk:
                continue

            if nearest_future_match is None or kickoff_msk < nearest_future_match["kickoff_msk"]:
                nearest_future_match = {
                    "id": m["id"],
                    "day": kickoff_msk.strftime("%Y-%m-%d"),
                    "kickoff_msk": kickoff_msk,
                }

        days = []

        for day in sorted(grouped.keys()):

            if day == today:
                t = "today"
            elif day < today:
                t = "past"
            else:
                t = "future"

            has_open = any(
                not x["deadline_passed"]
                for x in grouped[day]
            )

            days.append({
                "key": day,
                "label": format_date_ru(day),
                "type": t,
                "matches": grouped[day],
                "count": len(grouped[day]),
                "has_open": has_open
            })

        # Choose which day should be opened by default:
        # 1) day with nearest future unfinished match
        # 2) otherwise today (MSK), if present
        # 3) otherwise nearest future day
        # 4) otherwise last available day
        initial_match_id = nearest_future_match["id"] if nearest_future_match else None
        open_day = nearest_future_match["day"] if nearest_future_match else None
        if open_day is None:
            open_day = next((d["key"] for d in days if d["key"] == today), None)
        if open_day is None:
            next_future_day = next((d for d in days if d["key"] > today), None)
            if next_future_day:
                open_day = next_future_day["key"]
            elif days:
                open_day = days[-1]["key"]

        # =================================================
        # GROUP BY MONTH
        # =================================================

        months = defaultdict(list)

        for d in days:
            day_dt = parse_dt(d["key"])
            if day_dt:
                month_key = f"{day_dt.year:04d}-{day_dt.month:02d}"
            else:
                month_key = d["key"][:7]
            months[month_key].append(d)

        grouped_months = []

        for mk in sorted(months.keys()):
            year, month = mk.split('-')
            month_label = format_month_label(year, month)

            grouped_months.append({
                'key': mk,
                'label': month_label,
                'days': months[mk],
                'count': sum(d['count'] for d in months[mk])
            })

    finally:
        close_db(conn, cur)

    tournaments = all_tournaments
    active_tournaments = [t for t in tournaments if t.get("is_active")]
    tournament_state = get_tournament_state_flags(tournaments)
    selected_tournament = get_tournament_by_id(tid) if tid else None
    current_tournament_name = selected_tournament["name"] if selected_tournament else "Турнир"
    current_tournament_slug = normalize_tournament_slug(current_tournament_name)
    russian_cup_header_stage = next(
        (m.get("stage") for m in raw_matches if m.get("is_russian_cup") and m.get("stage")),
        None,
    )

    return render_template(
        "index.html",
        months=grouped_months,
        open_day=open_day,
        initial_match_id=initial_match_id,
        get_flag=get_flag,
        get_club_logo=get_club_logo,
        to_msk=cached_to_msk,
        current_filter=league,
        tournaments=tournaments,
        active_tournaments=active_tournaments,
        **tournament_state,
        current_tournament_id=tid,
        current_tournament_name=current_tournament_name,
        current_tournament_slug=current_tournament_slug,
        russian_cup_header_stage=russian_cup_header_stage,
        russian_cup_header_season="Сезон 2025/26" if current_tournament_slug == "rcup" else None,
    )


