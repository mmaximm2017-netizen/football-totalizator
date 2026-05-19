# app/routes/admin.py

from datetime import datetime, timedelta, timezone
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
    session
)

from markupsafe import escape

from app.db import get_db, close_db
from app.utils import translate_name, format_date_ru
from app.config import START_DATE


# =========================================================
# BLUEPRINT
# =========================================================

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

MSK = ZoneInfo("Europe/Moscow")
ALLOWED_TITLES = (
    "Обладатель Кубка Матч-Премьер",
    "Чемпион Мира 2026",
)


# =========================================================
# HELPERS
# =========================================================

def get_active_tournament_id(cur):
    cur.execute(
        "SELECT id FROM tournaments WHERE is_active = 1 LIMIT 1"
    )
    row = cur.fetchone()
    return row[0] if row else None


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
                flash("Доступ запрещён", "error")
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
            raise ValueError("Укажите и дату, и время дедлайна")

        deadline_msk = datetime.strptime(
            f"{deadline_date} {deadline_time}",
            "%Y-%m-%d %H:%M"
        ).replace(tzinfo=MSK)
    else:
        deadline_msk = dt_msk.replace(hour=11, minute=0, second=0, microsecond=0)

    deadline_utc = deadline_msk.astimezone(timezone.utc)
    return kickoff_utc, deadline_utc


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
        kickoff = m[3]
        if isinstance(kickoff, str):
            kickoff = kickoff.replace('Z', '+00:00')
            try:
                kickoff = datetime.fromisoformat(kickoff)
            except Exception:
                kickoff = kickoff[:10]

        day = str(kickoff)[:10]
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

    month_names = {
        '01': 'Январь', '02': 'Февраль', '03': 'Март',
        '04': 'Апрель', '05': 'Май', '06': 'Июнь',
        '07': 'Июль', '08': 'Август', '09': 'Сентябрь',
        '10': 'Октябрь', '11': 'Ноябрь', '12': 'Декабрь'
    }

    free_months_list = []
    for mk in sorted(free_months.keys()):
        year, month = mk.split('-')
        month_label = f"{month_names[month]} {year}"
        free_months_list.append({
            'key': mk,
            'label': month_label,
            'days': free_months[mk],
            'total_matches': sum(d['count'] for d in free_months[mk])
        })

    finished_months_list = []
    for mk in sorted(finished_months.keys()):
        year, month = mk.split('-')
        month_label = f"{month_names[month]} {year}"
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
        kickoff = m[3]
        deadline = m[4]
        kickoff_msk = kickoff.astimezone(MSK) if kickoff else None
        deadline_msk = deadline.astimezone(MSK) if deadline else None
        manual_matches.append({
            'id': m[0],
            'home_team': m[1],
            'away_team': m[2],
            'kickoff_time': m[3],
            'deadline': m[4],
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

    cur.execute("""
        SELECT id, username, is_admin
        FROM users
        ORDER BY username
    """)
    users = []
    title_users = []
    for u in cur.fetchall():
        users.append({
            'id': u[0],
            'username': u[1],
            'is_admin': u[2]
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
        'tournaments': tournaments
    }


def _prepare_admin_matches_data(cur):
    data = _prepare_admin_view_data(cur)

    manual_matches = data.get('manual_matches', [])
    month_names = {
        '01': 'Январь', '02': 'Февраль', '03': 'Март',
        '04': 'Апрель', '05': 'Май', '06': 'Июнь',
        '07': 'Июль', '08': 'Август', '09': 'Сентябрь',
        '10': 'Октябрь', '11': 'Ноябрь', '12': 'Декабрь'
    }

    for month_block in data.get('free_months', []):
        key = month_block.get('key', '')
        if '-' in key:
            year, month = key.split('-', 1)
            month_block['label'] = f"{month_names.get(month, month)} {year}"

    for month_block in data.get('finished_months', []):
        key = month_block.get('key', '')
        if '-' in key:
            year, month = key.split('-', 1)
            month_block['label'] = f"{month_names.get(month, month)} {year}"
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
        kickoff = m.get('kickoff_time')
        kickoff_msk = kickoff.astimezone(MSK) if kickoff else None
        league_key = m.get('league') or 'other'
        league_base = league_names.get(league_key, str(league_key).upper())
        year = str(kickoff_msk.year) if kickoff_msk else ""
        league_label = f"{league_base} {year}".strip()
        month_key = kickoff_msk.strftime("%Y-%m") if kickoff_msk else "unknown"
        day_key = kickoff_msk.strftime("%Y-%m-%d") if kickoff_msk else "unknown"
        manual_grouped_map[league_label][month_key][day_key].append(m)

    manual_grouped = []
    for league_label in sorted(manual_grouped_map.keys()):
        months = []
        league_total = 0
        for month_key in sorted(manual_grouped_map[league_label].keys()):
            if "-" in month_key:
                year, month = month_key.split("-", 1)
                month_label = f"{month_names.get(month, month)} {year}"
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
                    completed = run_sync_with_lock()
                    if completed:
                        flash("Матчи и очки обновлены", "success")
                    else:
                        flash("Обновление уже выполняется", "error")
                except Exception as e:
                    flash(f"Ошибка обновления: {e}", "error")

                return redirect(url_for('admin.admin'))

            # =============================================
            # ADD MATCH
            # =============================================

            elif action == 'add_match':

                try:

                    home = request.form['home_team'].strip()
                    away = request.form['away_team'].strip()
                    league = request.form.get('league', 'other').strip()

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
                        flash("Такой матч уже существует", "error")
                        return redirect(url_for('admin.admin'))

                    cur.execute("""
                        INSERT INTO matches (
                            home_team,
                            away_team,
                            kickoff_time,
                            deadline,
                            status,
                            league
                        )
                        VALUES (%s, %s, %s, %s, 'SCHEDULED', %s)
                    """, (
                        home,
                        away,
                        kickoff_utc,
                        deadline_utc,
                        league
                    ))

                    conn.commit()

                    flash(
                        f"Матч {home} — {away} добавлен",
                        "success"
                    )

                except Exception as e:

                    conn.rollback()

                    flash(f"Ошибка: {e}", "error")

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
                    flash("Некорректный счёт", "error")
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
                        flash("Матч не найден", "error")
                        return redirect(url_for('admin.admin'))

                    from app.models.scoring import calculate_points

                    cur.execute("""
                        SELECT user_id, home_goals, away_goals, tournament_id
                        FROM predictions
                        WHERE match_id = %s
                    """, (
                        match_id,
                    ))

                    predictions = cur.fetchall()

                    for p in predictions:

                        pts = calculate_points(
                            home_score,
                            away_score,
                            p[1],
                            p[2]
                        )

                        cur.execute("""
                            UPDATE predictions
                            SET points = %s
                            WHERE user_id = %s
                            AND match_id = %s
                            AND tournament_id = %s
                        """, (
                            pts,
                            p[0],
                            match_id,
                            p[3]
                        ))

                    conn.commit()

                    flash(
                        "Результат внесён, очки пересчитаны",
                        "success"
                    )

                except Exception as e:

                    conn.rollback()

                    flash(f"Ошибка: {e}", "error")

                return redirect(url_for('admin.admin'))

            # =============================================
            # AWARD TITLE
            # =============================================

            elif action == 'award_title':

                user_id = request.form.get('user_id', type=int)
                title = (request.form.get('title') or '').strip()

                if not user_id or not title:
                    flash("Укажите пользователя и титул", "error")
                    return redirect(url_for('admin.admin'))

                if title not in ALLOWED_TITLES:
                    flash("Некорректный титул", "error")
                    return redirect(url_for('admin.admin'))

                try:
                    cur.execute("""
                        SELECT is_admin
                        FROM users
                        WHERE id = %s
                    """, (user_id,))
                    row = cur.fetchone()

                    if not row:
                        flash("Пользователь не найден", "error")
                        return redirect(url_for('admin.admin'))

                    if row[0] == 1:
                        flash("Нельзя выдавать титул администратору", "error")
                        return redirect(url_for('admin.admin'))

                    cur.execute("""
                        INSERT INTO user_titles (user_id, title, awarded_by)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (user_id, title) DO NOTHING
                    """, (user_id, title, session.get('user_id')))

                    if cur.rowcount == 0:
                        conn.rollback()
                        flash("У пользователя уже есть этот титул", "error")
                        return redirect(url_for('admin.admin'))

                    conn.commit()
                    flash("Титул выдан", "success")

                except Exception as e:
                    conn.rollback()
                    flash(f"Ошибка выдачи титула: {e}", "error")

                return redirect(url_for('admin.admin'))

            else:
                flash("Неизвестное действие", "error")
                return redirect(url_for('admin.admin'))

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
        flash("Матч не найден", "error")
        return redirect(url_for('admin.admin'))

    conn = get_db()
    cur = conn.cursor()

    try:

        from app.models.scoring import calculate_points

        cur.execute("""
            SELECT id,
                   home_score,
                   away_score
            FROM matches
            WHERE id = %s
        """, (match_id,))

        match = cur.fetchone()

        if not match:
            return "Матч не найден", 404

        cur.execute("""
            UPDATE predictions
            SET points = 0
            WHERE match_id = %s
        """, (match_id,))

        cur.execute("""
            SELECT user_id,
                   home_goals,
                   away_goals
            FROM predictions
            WHERE match_id = %s
        """, (match_id,))

        preds = cur.fetchall()

        updated = 0

        for p in preds:

            pts = calculate_points(
                match[1],
                match[2],
                p[1],
                p[2]
            )

            cur.execute("""
                UPDATE predictions
                SET points = %s
                WHERE user_id = %s
                AND match_id = %s
            """, (
                pts,
                p[0],
                match_id
            ))

            updated += 1

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
            Матч #{match[0]}:
            Счёт {match[1]}:{match[2]}
            (обновлено {updated} записей)
        </h3>
        """

        result += """
        <table border='1'>
            <tr>
                <th>Игрок</th>
                <th>Прогноз</th>
                <th>Очки</th>
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

        from app.models.scoring import calculate_points

        tournament_id = get_active_tournament_id(cur)

        if not tournament_id:
            flash("Активный турнир не найден", "error")
            return redirect(url_for('admin.admin'))

        cur.execute("""
            SELECT id,
                   home_score,
                   away_score
            FROM matches
            WHERE status = 'FINISHED'
        """)

        matches = cur.fetchall()

        total_updated = 0

        for match in matches:

            match_id = match[0]
            home_score = match[1]
            away_score = match[2]

            cur.execute("""
                UPDATE predictions
                SET points = 0
                WHERE match_id = %s
                AND tournament_id = %s
            """, (
                match_id,
                tournament_id
            ))

            cur.execute("""
                SELECT user_id,
                       home_goals,
                       away_goals
                FROM predictions
                WHERE match_id = %s
                AND tournament_id = %s
            """, (
                match_id,
                tournament_id
            ))

            predictions = cur.fetchall()

            for p in predictions:

                pts = calculate_points(
                    home_score,
                    away_score,
                    p[1],
                    p[2]
                )

                cur.execute("""
                    UPDATE predictions
                    SET points = %s
                    WHERE user_id = %s
                    AND match_id = %s
                    AND tournament_id = %s
                """, (
                    pts,
                    p[0],
                    match_id,
                    tournament_id
                ))

                total_updated += 1

        conn.commit()

        flash(
            f"Пересчитано {total_updated} прогнозов",
            "success"
        )

    except Exception as e:

        conn.rollback()

        flash(f"Ошибка пересчёта: {e}", "error")

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
        flash("Некорректные данные матча", "error")
        return redirect(url_for('admin.admin'))

    conn = get_db()
    cur = conn.cursor()

    try:

        if h < 0 or a < 0:
            flash("Счёт не может быть отрицательным", "error")
            return redirect(url_for('admin.admin'))

        from app.models.scoring import calculate_points

        tournament_id = get_active_tournament_id(cur)

        if not tournament_id:
            flash("Активный турнир не найден", "error")
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
            flash("Матч не найден", "error")
            return redirect(url_for('admin.admin'))

        cur.execute("""
            UPDATE predictions
            SET points = 0
            WHERE match_id = %s
            AND tournament_id = %s
        """, (
            match_id,
            tournament_id
        ))

        cur.execute("""
            SELECT user_id,
                   home_goals,
                   away_goals
            FROM predictions
            WHERE match_id = %s
            AND tournament_id = %s
        """, (
            match_id,
            tournament_id
        ))

        predictions = cur.fetchall()

        for p in predictions:

            pts = calculate_points(
                h,
                a,
                p[1],
                p[2]
            )

            cur.execute("""
                UPDATE predictions
                SET points = %s
                WHERE user_id = %s
                AND match_id = %s
                AND tournament_id = %s
            """, (
                pts,
                p[0],
                match_id,
                tournament_id
            ))

        conn.commit()

        flash(
            f"Матч #{match_id} завершён: {h}:{a}",
            "success"
        )

    except Exception as e:

        conn.rollback()

        flash(f"Ошибка: {e}", "error")

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
            f"Переведено {updated} матчей",
            "success"
        )

    except Exception as e:

        conn.rollback()

        flash(f"Ошибка перевода: {e}", "error")

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
        flash("Некорректный счёт", "error")
        return redirect(url_for('admin.admin'))

    conn = get_db()
    cur = conn.cursor()

    try:

        tournament_id = get_active_tournament_id(cur)

        if not tournament_id:
            flash("Активный турнир не найден", "error")
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
            flash("Матч не найден", "error")
            return redirect(url_for('admin.admin'))

        from app.models.scoring import calculate_points

        cur.execute("""
            SELECT user_id,
                   home_goals,
                   away_goals
            FROM predictions
            WHERE match_id = %s
            AND tournament_id = %s
        """, (
            match_id,
            tournament_id
        ))

        predictions = cur.fetchall()

        for p in predictions:

            pts = calculate_points(
                home_score,
                away_score,
                p[1],
                p[2]
            )

            cur.execute("""
                UPDATE predictions
                SET points = %s
                WHERE user_id = %s
                AND match_id = %s
                AND tournament_id = %s
            """, (
                pts,
                p[0],
                match_id,
                tournament_id
            ))

        conn.commit()

        flash(
            f"Результат обновлён: {home_score}:{away_score}",
            "success"
        )

    except Exception as e:

        conn.rollback()

        flash(f"Ошибка: {e}", "error")

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

        flash("Заполните все поля", "error")

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
            flash("Матч не найден", "error")
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
            flash("Некорректная дата или время", "error")
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

            flash("Для FINISHED матча изменение kickoff/deadline отключено для безопасности", "error")
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
            flash("Матч не найден", "error")
            return redirect(url_for('admin.admin'))

        conn.commit()

        flash(
            f"Матч #{match_id} обновлён",
            "success"
        )

    except Exception as e:

        conn.rollback()

        flash(f"Ошибка: {e}", "error")

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

        flash("Не указан match_id", "error")

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
            flash("Матч не найден", "error")
            return redirect(url_for('admin.admin'))

        conn.commit()

        flash(
            f"Матч #{match_id} удалён",
            "success"
        )

    except Exception as e:

        conn.rollback()

        flash(f"Ошибка удаления: {e}", "error")

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

        flash("Введите название турнира", "error")

        return redirect(url_for('admin.admin'))

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

            flash("Турнир с таким названием уже существует", "error")

            return redirect(url_for('admin.admin'))

        cur.execute("""
            INSERT INTO tournaments (
                name,
                is_active,
                start_date
            )
            VALUES (%s, 0, %s)
        """, (
            name,
            start_date
        ))

        conn.commit()

        flash(
            f"Турнир «{name}» создан",
            "success"
        )

    except Exception as e:

        conn.rollback()

        flash(f"Ошибка создания турнира: {e}", "error")

    finally:
        close_db(conn, cur)

    return redirect(url_for('admin.admin'))


# =========================================================
# DELETE TOURNAMENT
# =========================================================

@admin_bp.route('/delete_tournament', methods=['POST'])
@admin_required
def delete_tournament():

    tid = request.form.get('tid', type=int)

    if not tid:
        flash("Турнир не найден", "error")
        return redirect(url_for('admin.admin'))

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
            flash("Турнир не найден", "error")
            return redirect(url_for('admin.admin'))

        if row[0] == 1:
            flash("Нельзя удалить активный турнир", "error")
            return redirect(url_for('admin.admin'))

        cur.execute("""
            DELETE FROM tournaments
            WHERE id = %s
        """, (tid,))

        conn.commit()

        flash(f"Турнир #{tid} удалён", "success")

    except Exception as e:

        conn.rollback()

        flash(f"Ошибка: {e}", "error")

    finally:

        close_db(conn, cur)

    return redirect(url_for('admin.admin'))

