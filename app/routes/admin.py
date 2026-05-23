# app/routes/admin.py

from collections import defaultdict
from functools import wraps
from zoneinfo import ZoneInfo

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
)

from app.db import get_db, close_db
from app.utils import (
    format_date_ru,
    format_month_label,
    parse_datetime,
)
from app.config import START_DATE
from app.routes.admin_sync import (
    build_sync_panel_context,
)
from app.routes.admin_actions import ALLOWED_TITLES, dispatch_admin_action
from app.routes.admin_tournaments import (
    handle_activate_tournament,
    handle_archive_tournament,
)


# =========================================================
# BLUEPRINT
# =========================================================

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

MSK = ZoneInfo("Europe/Moscow")


# =========================================================
# HELPERS
# =========================================================

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('auth.login'))

        conn = get_db()
        cur = conn.cursor()

        try:
            cur.execute(
                "SELECT is_admin FROM users WHERE id = %s",
                (session['user_id'],)
            )
            user = cur.fetchone()

            if not user or user[0] != 1:
                flash("������ ��������", "error")
                return redirect(url_for('main.index'))

        finally:
            close_db(conn, cur)

        return f(*args, **kwargs)

    return decorated


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


def _prepare_admin_view_data(cur):
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
        SELECT id, home_team, away_team, kickoff_time, deadline, status, league
        FROM matches
        WHERE (api_match_id IS NULL OR api_match_id = '')
        AND kickoff_time >= %s
        ORDER BY kickoff_time
    """, (start_date_str,))
    manual_matches = []
    for m in cur.fetchall():
        kickoff_dt = parse_datetime(m[3])
        deadline_dt = parse_datetime(m[4])
        kickoff_msk = kickoff_dt.astimezone(MSK) if kickoff_dt else None
        deadline_msk = deadline_dt.astimezone(MSK) if deadline_dt else None
        manual_matches.append({
            'id': m[0],
            'home_team': m[1],
            'away_team': m[2],
            'kickoff_time': kickoff_dt,
            'deadline': deadline_dt,
            'status': m[5],
            'league': m[6] if len(m) > 6 else 'other',
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

    cur.execute("""
        SELECT id, username, is_admin, last_seen
        FROM users
        ORDER BY username
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
            )
        })
        if u[2] == 0:
            title_users.append({'id': u[0], 'username': u[1]})

    return {
        'free_months': free_months_list,
        'finished_months': finished_months_list,
        'manual_matches': manual_matches,
        'users': users,
        'title_users': title_users,
        'allowed_titles': ALLOWED_TITLES,
        'tournaments': tournaments,
        'active_tournaments': active_tournaments,
    }


def _prepare_admin_matches_data(cur):
    data = _prepare_admin_view_data(cur)

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


# =========================================================
# MAIN ADMIN PAGE
# =========================================================

@admin_bp.route('/', methods=['GET', 'POST'])
@admin_required
def admin():

    conn = get_db()
    cur = conn.cursor()

    try:

        if request.method == 'POST':

            action = request.form.get('action')
            return dispatch_admin_action(action, conn, cur)

        return render_template('admin.html')
    finally:
        close_db(conn, cur)


@admin_bp.route('/matches', methods=['GET'])
@admin_required
def admin_matches():
    conn = get_db()
    cur = conn.cursor()
    try:
        data = _prepare_admin_matches_data(cur)
        data.update(build_sync_panel_context())
    finally:
        close_db(conn, cur)
    return render_template('admin_matches.html', **data)


@admin_bp.route('/tournaments', methods=['GET'])
@admin_required
def admin_tournaments():
    conn = get_db()
    cur = conn.cursor()
    try:
        data = _prepare_admin_view_data(cur)
    finally:
        close_db(conn, cur)
    return render_template('admin_tournaments.html', **data)


@admin_bp.route('/users', methods=['GET'])
@admin_required
def admin_users():
    conn = get_db()
    cur = conn.cursor()
    try:
        data = _prepare_admin_view_data(cur)
    finally:
        close_db(conn, cur)
    return render_template('admin_users.html', **data)


@admin_bp.route('/archive_tournament/<int:tid>', methods=['POST'])
@admin_required
def archive_tournament(tid):
    return handle_archive_tournament(tid)


@admin_bp.route('/activate_tournament/<int:tid>', methods=['POST'])
@admin_required
def activate_tournament(tid):
    return handle_activate_tournament(tid)



