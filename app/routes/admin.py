# app/routes/admin.py

from datetime import datetime, timezone
import logging
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
    jsonify,
)

from markupsafe import escape

from app.db import get_db, close_db
from app.services.tournament_service import (
    get_active_tournament_id,
)
from app.services.scoring_recalculation_service import (
    recalc_match_points,
    recalc_tournament_points,
)
from app.utils import (
    translate_name,
    format_date_ru,
    format_month_label,
    parse_datetime,
)
from app.config import START_DATE
from app.services.sync_history_service import get_last_sync, get_sync_health


# =========================================================
# BLUEPRINT
# =========================================================

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')
logger = logging.getLogger(__name__)

MSK = ZoneInfo("Europe/Moscow")
ALLOWED_TITLES = (
    "Обладатель Кубка Матч-Премьер",
    "Чемпион Мира 2026",
)


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


def validate_score(home_score, away_score):
    try:
        home_score = int(home_score)
        away_score = int(away_score)

        if home_score < 0 or away_score < 0:
            return None, None

        return home_score, away_score

    except ValueError:
        return None, None


def build_manual_deadline_utc(match_date, match_time, deadline_date, deadline_time):
    dt_msk = datetime.strptime(
        f"{match_date} {match_time}",
        "%Y-%m-%d %H:%M"
    ).replace(tzinfo=MSK)

    kickoff_utc = dt_msk.astimezone(timezone.utc)

    if deadline_date or deadline_time:
        if not deadline_date or not deadline_time:
            raise ValueError("������� � ����, � ����� ��������")

        deadline_msk = datetime.strptime(
            f"{deadline_date} {deadline_time}",
            "%Y-%m-%d %H:%M"
        ).replace(tzinfo=MSK)
    else:
        deadline_msk = dt_msk.replace(hour=11, minute=0, second=0, microsecond=0)

    deadline_utc = deadline_msk.astimezone(timezone.utc)
    return kickoff_utc, deadline_utc


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

            # =============================================
            # UPDATE MATCHES
            # =============================================

            if action == 'update_matches':

                from app.services.match_service import run_sync_with_lock

                try:
                    sync_result = run_sync_with_lock()
                    logger.info("admin sync summary: %s", sync_result)
                    if sync_result.get("status") == "completed":
                        flash("����� � ���� ���������", "success")
                    else:
                        flash("���������� ��� �����������", "error")
                except Exception as e:
                    flash(f"������ ����������: {e}", "error")

                return redirect(url_for('admin.admin'))

            # =============================================
            # ADD MATCH
            # =============================================

            elif action == 'add_match':

                try:

                    home = request.form['home_team'].strip()
                    away = request.form['away_team'].strip()
                    league = request.form.get('league', 'other').strip()
                    tournament_id = request.form.get('tournament_id', type=int)

                    match_date = request.form['match_date']
                    match_time = request.form['match_time']
                    deadline_date = request.form.get('deadline_date', '').strip()
                    deadline_time = request.form.get('deadline_time', '').strip()

                    kickoff_utc, deadline_utc = build_manual_deadline_utc(
                        match_date,
                        match_time,
                        deadline_date,
                        deadline_time
                    )

                    cur.execute("""
                        SELECT id
                        FROM matches
                        WHERE home_team = %s
                        AND away_team = %s
                        AND kickoff_time = %s
                    """, (
                        home,
                        away,
                        kickoff_utc
                    ))

                    existing = cur.fetchone()

                    if existing:
                        flash("����� ���� ��� ����������", "error")
                        return redirect(url_for('admin.admin'))

                    if not tournament_id:
                        flash("Выберите турнир для матча", "error")
                        return redirect(url_for('admin.admin_matches'))

                    cur.execute("""
                        INSERT INTO matches (
                            home_team,
                            away_team,
                            kickoff_time,
                            deadline,
                            status,
                            league,
                            tournament_id
                        )
                        VALUES (%s, %s, %s, %s, 'SCHEDULED', %s, %s)
                    """, (
                        home,
                        away,
                        kickoff_utc,
                        deadline_utc,
                        league,
                        tournament_id
                    ))

                    conn.commit()

                    flash(
                        f"���� {home} � {away} ��������",
                        "success"
                    )

                except Exception as e:

                    conn.rollback()

                    flash(f"������: {e}", "error")

                return redirect(url_for('admin.admin'))

            # =============================================
            # SET RESULT
            # =============================================

            elif action == 'set_result':

                match_id = request.form.get('match_id')

                home_score, away_score = validate_score(
                    request.form.get('home_score'),
                    request.form.get('away_score')
                )

                if home_score is None:
                    flash("������������ ����", "error")
                    return redirect(url_for('admin.admin'))

                try:

                    cur.execute("""
                        UPDATE matches
                        SET status = 'FINISHED',
                            home_score = %s,
                            away_score = %s
                        WHERE id = %s
                    """, (
                        home_score,
                        away_score,
                        match_id
                    ))

                    if cur.rowcount == 0:
                        flash("���� �� ������", "error")
                        return redirect(url_for('admin.admin'))

                    recalc_match_points(match_id, conn=conn, cur=cur)

                    conn.commit()

                    flash(
                        "��������� �����, ���� �����������",
                        "success"
                    )

                except Exception as e:

                    conn.rollback()

                    flash(f"������: {e}", "error")

                return redirect(url_for('admin.admin'))

            # =============================================
            # AWARD TITLE
            # =============================================

            elif action == 'award_title':

                user_id = request.form.get('user_id', type=int)
                title = (request.form.get('title') or '').strip()

                if not user_id or not title:
                    flash("������� ������������ � �����", "error")
                    return redirect(url_for('admin.admin'))

                if title not in ALLOWED_TITLES:
                    flash("������������ �����", "error")
                    return redirect(url_for('admin.admin'))

                try:
                    cur.execute("""
                        SELECT is_admin
                        FROM users
                        WHERE id = %s
                    """, (user_id,))
                    row = cur.fetchone()

                    if not row:
                        flash("������������ �� ������", "error")
                        return redirect(url_for('admin.admin'))

                    if row[0] == 1:
                        flash("������ �������� ����� ��������������", "error")
                        return redirect(url_for('admin.admin'))

                    cur.execute("""
                        INSERT INTO user_titles (user_id, title, awarded_by)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (user_id, title) DO NOTHING
                    """, (user_id, title, session.get('user_id')))

                    if cur.rowcount == 0:
                        conn.rollback()
                        flash("� ������������ ��� ���� ���� �����", "error")
                        return redirect(url_for('admin.admin'))

                    conn.commit()
                    flash("����� �����", "success")

                except Exception as e:
                    conn.rollback()
                    flash(f"������ ������ ������: {e}", "error")

                return redirect(url_for('admin.admin'))

            else:
                flash("����������� ��������", "error")
                return redirect(url_for('admin.admin'))

        return render_template('admin.html')
    finally:
        close_db(conn, cur)


@admin_bp.route('/sync-health', methods=['GET'])
@admin_required
def sync_health():
    return jsonify(get_sync_health())


@admin_bp.route('/matches', methods=['GET'])
@admin_required
def admin_matches():
    conn = get_db()
    cur = conn.cursor()
    try:
        data = _prepare_admin_matches_data(cur)
        sync_health_data = get_sync_health()
        last_sync = get_last_sync()
        last_status = sync_health_data.get("last_status")

        if not sync_health_data.get("is_healthy"):
            sync_status_class = "sync-status-bad"
        elif last_status == "skipped_already_running":
            sync_status_class = "sync-status-warning"
        else:
            sync_status_class = "sync-status-good"

        data["sync_health"] = sync_health_data
        data["last_sync"] = last_sync
        data["sync_status_class"] = sync_status_class
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


# =========================================================
# DEBUG MATCH
# =========================================================

@admin_bp.route('/debug_match', methods=['POST'])
@admin_required
def debug_match():

    match_id = request.form.get('match_id', type=int)

    if not match_id:
        flash("���� �� ������", "error")
        return redirect(url_for('admin.admin'))

    conn = get_db()
    cur = conn.cursor()

    try:

        cur.execute("""
            SELECT id,
                   home_score,
                   away_score
            FROM matches
            WHERE id = %s
        """, (match_id,))

        match = cur.fetchone()

        if not match:
            return "���� �� ������", 404

        summary = recalc_match_points(match_id, conn=conn, cur=cur)
        updated = summary.get("updated", 0)

        conn.commit()

        cur.execute("""
            SELECT u.username,
                   p.home_goals,
                   p.away_goals,
                   p.points
            FROM predictions p
            JOIN users u
            ON p.user_id = u.id
            WHERE p.match_id = %s
        """, (match_id,))

        preds = cur.fetchall()

        result = f"""
        <h3>
            ���� #{match[0]}:
            ���� {match[1]}:{match[2]}
            (��������� {updated} �������)
        </h3>
        """

        result += """
        <table border='1'>
            <tr>
                <th>�����</th>
                <th>�������</th>
                <th>����</th>
            </tr>
        """

        for p in preds:

            username = escape(p[0])

            result += f"""
            <tr>
                <td>{username}</td>
                <td>{p[1]}:{p[2]}</td>
                <td>{p[3]}</td>
            </tr>
            """

        result += "}</table>"

        return result

    finally:
        close_db(conn, cur)


# =========================================================
# RECALCULATE ALL
# =========================================================

@admin_bp.route('/recalc_all', methods=['POST'])
@admin_required
def recalc_all():

    conn = get_db()
    cur = conn.cursor()

    try:

        tournament_id = get_active_tournament_id()

        if not tournament_id:
            flash("�������� ������ �� ������", "error")
            return redirect(url_for('admin.admin'))

        summary = recalc_tournament_points(tournament_id, conn=conn, cur=cur)
        total_updated = summary.get("updated", 0)

        conn.commit()

        flash(
            f"����������� {total_updated} ���������",
            "success"
        )

    except Exception as e:

        conn.rollback()

        flash(f"������ ���������: {e}", "error")

    finally:
        close_db(conn, cur)

    return redirect(url_for('admin.admin'))


# =========================================================
# FORCE FINISH MATCH
# =========================================================

@admin_bp.route('/force_finish', methods=['POST'])
@admin_required
def force_finish():

    match_id = request.form.get('match_id', type=int)
    h = request.form.get('home_score', type=int)
    a = request.form.get('away_score', type=int)

    if match_id is None or h is None or a is None:
        flash("������������ ������ �����", "error")
        return redirect(url_for('admin.admin'))

    conn = get_db()
    cur = conn.cursor()

    try:

        if h < 0 or a < 0:
            flash("���� �� ����� ���� �������������", "error")
            return redirect(url_for('admin.admin'))

        tournament_id = get_active_tournament_id()

        if not tournament_id:
            flash("�������� ������ �� ������", "error")
            return redirect(url_for('admin.admin'))

        cur.execute("""
            UPDATE matches
            SET status = 'FINISHED',
                home_score = %s,
                away_score = %s
            WHERE id = %s
        """, (
            h,
            a,
            match_id
        ))

        if cur.rowcount == 0:
            flash("���� �� ������", "error")
            return redirect(url_for('admin.admin'))

        recalc_match_points(
            match_id,
            tournament_id=tournament_id,
            conn=conn,
            cur=cur,
        )

        conn.commit()

        flash(
            f"���� #{match_id} ��������: {h}:{a}",
            "success"
        )

    except Exception as e:

        conn.rollback()

        flash(f"������: {e}", "error")

    finally:
        close_db(conn, cur)

    return redirect(url_for('admin.admin'))


# =========================================================
# TRANSLATE TEAMS
# =========================================================

@admin_bp.route('/translate', methods=['POST'])
@admin_required
def admin_translate():

    conn = get_db()
    cur = conn.cursor()

    try:

        cur.execute("""
            SELECT id,
                   home_team,
                   away_team
            FROM matches
        """)

        matches = cur.fetchall()

        updated = 0

        for m in matches:

            new_home = translate_name(m[1])
            new_away = translate_name(m[2])

            if (
                new_home != m[1]
                or new_away != m[2]
            ):

                cur.execute("""
                    UPDATE matches
                    SET home_team = %s,
                        away_team = %s
                    WHERE id = %s
                """, (
                    new_home,
                    new_away,
                    m[0]
                ))

                updated += 1

        conn.commit()

        flash(
            f"���������� {updated} ������",
            "success"
        )

    except Exception as e:

        conn.rollback()

        flash(f"������ ��������: {e}", "error")

    finally:
        close_db(conn, cur)

    return redirect(url_for('admin.admin'))


# =========================================================
# FIX RESULT
# =========================================================

@admin_bp.route('/fix_result', methods=['POST'])
@admin_required
def admin_fix_result():

    match_id = request.form.get('match_id')

    home_score, away_score = validate_score(
        request.form.get('home_score'),
        request.form.get('away_score')
    )

    if home_score is None:
        flash("������������ ����", "error")
        return redirect(url_for('admin.admin'))

    conn = get_db()
    cur = conn.cursor()

    try:

        tournament_id = get_active_tournament_id()

        if not tournament_id:
            flash("�������� ������ �� ������", "error")
            return redirect(url_for('admin.admin'))

        cur.execute("""
            UPDATE matches
            SET home_score = %s,
                away_score = %s
            WHERE id = %s
        """, (
            home_score,
            away_score,
            match_id
        ))

        if cur.rowcount == 0:
            flash("���� �� ������", "error")
            return redirect(url_for('admin.admin'))

        recalc_match_points(
            match_id,
            tournament_id=tournament_id,
            conn=conn,
            cur=cur,
        )

        conn.commit()

        flash(
            f"��������� �������: {home_score}:{away_score}",
            "success"
        )

    except Exception as e:

        conn.rollback()

        flash(f"������: {e}", "error")

    finally:
        close_db(conn, cur)

    return redirect(url_for('admin.admin'))


# =========================================================
# EDIT MATCH
# =========================================================

@admin_bp.route('/edit_match', methods=['POST'])
@admin_required
def admin_edit_match():

    match_id = request.form.get('match_id')
    home_team = request.form.get('home_team', '').strip()
    away_team = request.form.get('away_team', '').strip()
    match_date = request.form.get('match_date', '').strip()
    match_time = request.form.get('match_time', '').strip()
    deadline_date = request.form.get('deadline_date', '').strip()
    deadline_time = request.form.get('deadline_time', '').strip()

    if not match_id or not home_team or not away_team or not match_date or not match_time:

        flash("��������� ��� ����", "error")

        return redirect(url_for('admin.admin'))

    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT status
            FROM matches
            WHERE id = %s
        """, (match_id,))

        row = cur.fetchone()

        if not row:
            flash("���� �� ������", "error")
            return redirect(url_for('admin.admin'))

        status = row[0]

        try:
            kickoff_utc, deadline_utc = build_manual_deadline_utc(
                match_date,
                match_time,
                deadline_date,
                deadline_time
            )
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for('admin.admin'))
        except Exception:
            flash("������������ ���� ��� �����", "error")
            return redirect(url_for('admin.admin'))

        if status == 'FINISHED':
            cur.execute("""
                UPDATE matches
                SET home_team = %s,
                    away_team = %s
                WHERE id = %s
            """, (
                home_team,
                away_team,
                match_id
            ))

            flash("��� FINISHED ����� ��������� kickoff/deadline ��������� ��� ������������", "error")
        else:
            cur.execute("""
                UPDATE matches
                SET home_team = %s,
                    away_team = %s,
                    kickoff_time = %s,
                    deadline = %s
                WHERE id = %s
            """, (
                home_team,
                away_team,
                kickoff_utc,
                deadline_utc,
                match_id
            ))

        if cur.rowcount == 0:
            flash("���� �� ������", "error")
            return redirect(url_for('admin.admin'))

        conn.commit()

        flash(
            f"���� #{match_id} �������",
            "success"
        )

    except Exception as e:

        conn.rollback()

        flash(f"������: {e}", "error")

    finally:
        close_db(conn, cur)

    return redirect(url_for('admin.admin'))


# =========================================================
# DELETE MATCH
# =========================================================

@admin_bp.route('/delete_match', methods=['POST'])
@admin_required
def admin_delete_match():

    match_id = request.form.get('match_id')

    if not match_id:

        flash("�� ������ match_id", "error")

        return redirect(url_for('admin.admin'))

    conn = get_db()
    cur = conn.cursor()

    try:

        cur.execute("""
            DELETE FROM predictions
            WHERE match_id = %s
        """, (match_id,))

        cur.execute("""
            DELETE FROM matches
            WHERE id = %s
        """, (match_id,))

        if cur.rowcount == 0:
            flash("���� �� ������", "error")
            return redirect(url_for('admin.admin'))

        conn.commit()

        flash(
            f"���� #{match_id} �����",
            "success"
        )

    except Exception as e:

        conn.rollback()

        flash(f"������ ��������: {e}", "error")

    finally:
        close_db(conn, cur)

    return redirect(url_for('admin.admin'))


# =========================================================
# NEW TOURNAMENT
# =========================================================

@admin_bp.route('/new_tournament', methods=['POST'])
@admin_required
def admin_new_tournament():

    name = request.form.get('name', '').strip()
    start_date = request.form.get('start_date')

    if not name:

        flash("������� �������� �������", "error")

        return redirect(url_for('admin.admin_tournaments'))

    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT id
            FROM tournaments
            WHERE LOWER(name) = LOWER(%s)
        """, (name,))

        existing = cur.fetchone()

        if existing:

            flash("������ � ����� ��������� ��� ����������", "error")

            return redirect(url_for('admin.admin_tournaments'))

        cur.execute("""
            INSERT INTO tournaments (
                name,
                is_active,
                start_date
            )
            VALUES (%s, 1, %s)
        """, (
            name,
            start_date
        ))

        conn.commit()

        flash(
            f"������ �{name}� ������",
            "success"
        )

    except Exception as e:

        conn.rollback()

        flash(f"������ �������� �������: {e}", "error")

    finally:
        close_db(conn, cur)

    return redirect(url_for('admin.admin_tournaments'))


@admin_bp.route('/archive_tournament/<int:tid>', methods=['POST'])
@admin_required
def archive_tournament(tid):
    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute(
            """
            UPDATE tournaments
            SET is_active = 0
            WHERE id = %s
            """,
            (tid,),
        )

        if cur.rowcount == 0:
            flash("Турнир не найден", "error")
            return redirect(url_for('admin.admin_tournaments'))

        conn.commit()
        flash(f"Турнир #{tid} отправлен в архив", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Ошибка: {e}", "error")
    finally:
        close_db(conn, cur)

    return redirect(url_for('admin.admin_tournaments'))


@admin_bp.route('/activate_tournament/<int:tid>', methods=['POST'])
@admin_required
def activate_tournament(tid):
    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute(
            """
            UPDATE tournaments
            SET is_active = 1
            WHERE id = %s
            """,
            (tid,),
        )

        if cur.rowcount == 0:
            flash("Турнир не найден", "error")
            return redirect(url_for('admin.admin_tournaments'))

        conn.commit()
        flash(f"Турнир #{tid} активирован", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Ошибка: {e}", "error")
    finally:
        close_db(conn, cur)

    return redirect(url_for('admin.admin_tournaments'))


# =========================================================
# DELETE TOURNAMENT
# =========================================================

@admin_bp.route('/delete_tournament', methods=['POST'])
@admin_required
def delete_tournament():

    tid = request.form.get('tid', type=int)

    if not tid:
        flash("������ �� ������", "error")
        return redirect(url_for('admin.admin_tournaments'))

    conn = get_db()
    cur = conn.cursor()

    try:

        cur.execute("""
            SELECT is_active
            FROM tournaments
            WHERE id = %s
        """, (tid,))

        row = cur.fetchone()

        if not row:
            flash("������ �� ������", "error")
            return redirect(url_for('admin.admin_tournaments'))

        if row[0] == 1:
            flash("������ ������� �������� ������", "error")
            return redirect(url_for('admin.admin_tournaments'))

        cur.execute("""
            DELETE FROM tournaments
            WHERE id = %s
        """, (tid,))

        conn.commit()

        flash(f"������ #{tid} �����", "success")

    except Exception as e:

        conn.rollback()

        flash(f"������: {e}", "error")

    finally:

        close_db(conn, cur)

    return redirect(url_for('admin.admin_tournaments'))



