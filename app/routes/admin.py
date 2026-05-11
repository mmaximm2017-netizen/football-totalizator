from datetime import datetime, timedelta, timezone
from collections import defaultdict
from functools import wraps
from zoneinfo import ZoneInfo

from flask import (
    Blueprint, render_template, request,
    redirect, url_for, flash, session
)

from markupsafe import escape

from app.db import get_db, close_db, get_active_tournament_id
from app.models.scoring import calculate_points
from app.config import START_DATE

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

MSK = ZoneInfo("Europe/Moscow")


# =========================================================
# HELPERS
# =========================================================

def validate_score(h, a):
    try:
        h = int(h)
        a = int(a)
        if h < 0 or a < 0:
            return None, None
        return h, a
    except:
        return None, None


# =========================================================
# AUTH
# =========================================================

def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):

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

    return wrapper


# =========================================================
# ADMIN PAGE
# =========================================================

@admin_bp.route('/', methods=['GET', 'POST'])
@admin_required
def admin():

    conn = get_db()
    cur = conn.cursor()

    try:

        # ================= POST =================
        if request.method == 'POST':
            action = request.form.get('action')

            # -------- update matches --------
            if action == 'update_matches':
                from app.services.match_service import update_matches
                from app.services.point_service import calculate_all_points

                update_matches()
                calculate_all_points()

                flash("Матчи обновлены", "success")
                return redirect(url_for('admin.admin'))

            # -------- add match --------
            if action == 'add_match':

                home = request.form['home_team'].strip()
                away = request.form['away_team'].strip()
                league = request.form.get('league', 'other')

                dt = datetime.strptime(
                    f"{request.form['match_date']} {request.form['match_time']}",
                    "%Y-%m-%d %H:%M"
                ).replace(tzinfo=MSK)

                kickoff = dt.astimezone(timezone.utc)
                deadline = kickoff - timedelta(hours=1)

                cur.execute("""
                    INSERT INTO matches (
                        home_team, away_team,
                        kickoff_time, deadline,
                        status, league
                    )
                    VALUES (%s,%s,%s,%s,'SCHEDULED',%s)
                """, (home, away, kickoff, deadline, league))

                conn.commit()
                flash("Матч добавлен", "success")
                return redirect(url_for('admin.admin'))

            # -------- set result --------
            if action == 'set_result':

                match_id = request.form.get('match_id')

                h, a = validate_score(
                    request.form.get('home_score'),
                    request.form.get('away_score')
                )

                if h is None:
                    flash("Ошибка счёта", "error")
                    return redirect(url_for('admin.admin'))

                tid = get_active_tournament_id()

                if not tid:
                    flash("Нет активного турнира", "error")
                    return redirect(url_for('admin.admin'))

                cur.execute("""
                    UPDATE matches
                    SET status='FINISHED',
                        home_score=%s,
                        away_score=%s
                    WHERE id=%s
                """, (h, a, match_id))

                cur.execute("""
                    SELECT user_id, home_goals, away_goals
                    FROM predictions
                    WHERE match_id=%s AND tournament_id=%s
                """, (match_id, tid))

                for p in cur.fetchall():
                    pts = calculate_points(h, a, p[1], p[2])

                    cur.execute("""
                        UPDATE predictions
                        SET points=%s
                        WHERE user_id=%s
                        AND match_id=%s
                        AND tournament_id=%s
                    """, (pts, p[0], match_id, tid))

                conn.commit()
                flash("Результат обновлён", "success")
                return redirect(url_for('admin.admin'))

        # ================= LOAD =================

        start = START_DATE.strftime("%Y-%m-%dT%H:%M:%S")

        cur.execute("""
            SELECT id, home_team, away_team, kickoff_time, status
            FROM matches
            WHERE kickoff_time >= %s
            ORDER BY kickoff_time
        """, (start,))

        matches = [
            {
                'id': m[0],
                'home_team': m[1],
                'away_team': m[2],
                'kickoff_time': m[3],
                'status': m[4]
            }
            for m in cur.fetchall()
        ]

        free_days = defaultdict(list)
        finished_days = defaultdict(list)

        for m in matches:

            kt = m['kickoff_time']

            if isinstance(kt, str):
                kt = datetime.fromisoformat(kt.replace("Z", "+00:00"))

            day = kt.strftime("%Y-%m-%d")

            if m['status'] == 'FINISHED':
                finished_days[day].append(m)
            else:
                free_days[day].append(m)

        cur.execute("SELECT id, username FROM users ORDER BY username")
        users = [{'id': u[0], 'username': u[1]} for u in cur.fetchall()]

    finally:
        close_db(conn, cur)

    return render_template(
        'admin.html',
        free_days=[{'date': d, 'matches': free_days[d]} for d in sorted(free_days)],
        finished_days=[{'date': d, 'matches': finished_days[d]} for d in sorted(finished_days)],
        users=users
    )