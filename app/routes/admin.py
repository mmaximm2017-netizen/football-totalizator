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
from app.utils import translate_name
from app.config import START_DATE


# =========================================================
# BLUEPRINT (должен быть первым!)
# =========================================================

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

MSK = ZoneInfo("Europe/Moscow")


# =========================================================
# HELPERS
# =========================================================

def get_active_tournament_id():
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

            if action == 'update_matches':

                from app.services.match_service import update_matches
                from app.services.point_service import calculate_all_points

                try:
                    update_matches()
                    calculate_all_points()

                    flash("Матчи и очки обновлены", "success")

                except Exception as e:
                    flash(f"Ошибка обновления: {e}", "error")

                return redirect(url_for('admin.admin'))

            elif action == 'add_match':

                try:

                    home = request.form['home_team'].strip()
                    away = request.form['away_team'].strip()
                    league = request.form.get('league', 'other').strip()

                    match_date = request.form['match_date']
                    match_time = request.form['match_time']

                    dt_msk = datetime.strptime(
                        f"{match_date} {match_time}",
                        "%Y-%m-%d %H:%M"
                    )

                    dt_msk = dt_msk.replace(tzinfo=MSK)

                    kickoff_utc = dt_msk.astimezone(timezone.utc)

                    deadline_utc = kickoff_utc - timedelta(hours=1)

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

                    tournament_id = get_active_tournament_id()

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
                        home_score,
                        away_score,
                        match_id
                    ))

                    if cur.rowcount == 0:
                        flash("Матч не найден", "error")
                        return redirect(url_for('admin.admin'))

                    from app.models.scoring import calculate_points

                    cur.execute("""
                        SELECT user_id, home_goals, away_goals
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
                        "Результат внесён, очки пересчитаны",
                        "success"
                    )

                except Exception as e:

                    conn.rollback()

                    flash(f"Ошибка: {e}", "error")

                return redirect(url_for('admin.admin'))

        # =================================================
        # PAGE DATA
        # =================================================

        start_date_str = START_DATE.strftime("%Y-%m-%dT%H:%M:%S")

        cur.execute("""
            SELECT id,
                   home_team,
                   away_team,
                   kickoff_time,
                   status
            FROM matches
            WHERE status IN ('SCHEDULED', 'TIMED')
            AND kickoff_time >= %s
            ORDER BY kickoff_time
        """, (start_date_str,))

        raw_free = cur.fetchall()

        free_by_day = defaultdict(list)

        for m in raw_free:

            day = m[3].strftime("%Y-%m-%d")

            free_by_day[day].append({
                'id': m[0],
                'home_team': m[1],
                'away_team': m[2],
                'kickoff_time': m[3],
                'status': m[4]
            })

        free_days = [
            {
                'date': d,
                'matches': free_by_day[d]
            }
            for d in sorted(free_by_day.keys())
        ]

        cur.execute("""
            SELECT id,
                   home_team,
                   away_team,
                   kickoff_time,
                   status
            FROM matches
            WHERE status = 'FINISHED'
            AND kickoff_time >= %s
            ORDER BY kickoff_time
        """, (start_date_str,))

        raw_finished = cur.fetchall()

        fin_by_day = defaultdict(list)

        for m in raw_finished:

            day = m[3].strftime("%Y-%m-%d")

            fin_by_day[day].append({
                'id': m[0],
                'home_team': m[1],
                'away_team': m[2],
                'kickoff_time': m[3],
                'status': m[4]
            })

        finished_days = [
            {
                'date': d,
                'matches': fin_by_day[d]
            }
            for d in sorted(fin_by_day.keys())
        ]

        cur.execute("""
            SELECT id,
                   home_team,
                   away_team,
                   kickoff_time,
                   status
            FROM matches
            WHERE kickoff_time >= %s
            ORDER BY kickoff_time
        """, (start_date_str,))

        all_matches = []

        for m in cur.fetchall():

            all_matches.append({
                'id': m[0],
                'home_team': m[1],
                'away_team': m[2],
                'kickoff_time': m[3],
                'status': m[4]
            })

        cur.execute("""
            SELECT id,
                   home_team,
                   away_team,
                   kickoff_time,
                   status
            FROM matches
            WHERE (
                api_match_id IS NULL
                OR api_match_id = ''
            )
            AND kickoff_time >= %s
            ORDER BY kickoff_time
        """, (start_date_str,))

        manual_matches = []

        for m in cur.fetchall():

            manual_matches.append({
                'id': m[0],
                'home_team': m[1],
                'away_team': m[2],
                'kickoff_time': m[3],
                'status': m[4]
            })

        cur.execute("""
            SELECT id, username
            FROM users
            ORDER BY username
        """)

        users = []

        for u in cur.fetchall():

            users.append({
                'id': u[0],
                'username': u[1]
            })

    finally:
        close_db(conn, cur)

    return render_template(
        'admin.html',
        free_days=free_days,
        finished_days=finished_days,
        all_matches=all_matches,
        manual_matches=manual_matches,
        users=users
    )


# =========================================================
# DEBUG MATCH
# =========================================================

@admin_bp.route('/debug_match/<int:match_id>')
@admin_required
def debug_match(match_id):

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

        result += "</table>"

        return result

    finally:
        close_db(conn, cur)


# =========================================================
# RECALCULATE ALL
# =========================================================

@admin_bp.route('/recalc_all')
@admin_required
def recalc_all():

    conn = get_db()
    cur = conn.cursor()

    try:

        from app.models.scoring import calculate_points

        tournament_id = get_active_tournament_id()

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

@admin_bp.route('/force_finish/<int:match_id>/<int:h>/<int:a>')
@admin_required
def force_finish(match_id, h, a):

    conn = get_db()
    cur = conn.cursor()

    try:

        if h < 0 or a < 0:
            flash("Счёт не может быть отрицательным", "error")
            return redirect(url_for('admin.admin'))

        from app.models.scoring import calculate_points

        tournament_id = get_active_tournament_id()

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

        tournament_id = get_active_tournament_id()

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

    if not match_id or not home_team or not away_team:

        flash("Заполните все поля", "error")

        return redirect(url_for('admin.admin'))

    conn = get_db()
    cur = conn.cursor()

    try:

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
            UPDATE tournaments
            SET is_active = 0
            WHERE is_active = 1
        """)

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
            f"Турнир «{name}» создан",
            "success"
        )

    except Exception as e:

        conn.rollback()

        flash(f"Ошибка создания турнира: {e}", "error")

    finally:
        close_db(conn, cur)

    return redirect(url_for('admin.admin'))
