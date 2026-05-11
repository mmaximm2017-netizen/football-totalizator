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

from app.db import get_db, close_db, get_active_tournament_id
from app.utils import translate_name
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
    except Exception:
        return None, None


def parse_dt(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


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
# ADMIN PANEL
# =========================================================

@admin_bp.route('/', methods=['GET', 'POST'])
@admin_required
def admin():

    conn = get_db()
    cur = conn.cursor()

    try:

        # =================================================
        # POST ACTIONS
        # =================================================
        if request.method == 'POST':

            action = request.form.get('action')

            # ---------------- UPDATE MATCHES ----------------
            if action == 'update_matches':

                from app.services.match_service import update_matches
                from app.services.point_service import calculate_all_points

                try:
                    update_matches()
                    calculate_all_points()
                    flash("Матчи обновлены", "success")
                except Exception as e:
                    flash(f"Ошибка обновления: {e}", "error")

                return redirect(url_for('admin.admin'))

            # ---------------- ADD MATCH ----------------
            if action == 'add_match':

                try:
                    home = request.form['home_team'].strip()
                    away = request.form['away_team'].strip()
                    league = request.form.get('league', 'other')

                    dt_msk = datetime.strptime(
                        f"{request.form['match_date']} {request.form['match_time']}",
                        "%Y-%m-%d %H:%M"
                    ).replace(tzinfo=MSK)

                    kickoff = dt_msk.astimezone(timezone.utc)
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

                except Exception as e:
                    conn.rollback()
                    flash(f"Ошибка: {e}", "error")

                return redirect(url_for('admin.admin'))

            # ---------------- SET RESULT ----------------
            if action == 'set_result':

                match_id = request.form.get('match_id')

                h, a = validate_score(
                    request.form.get('home_score'),
                    request.form.get('away_score')
                )

                if h is None:
                    flash("Некорректный счёт", "error")
                    return redirect(url_for('admin.admin'))

                tid = get_active_tournament_id()

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

                preds = cur.fetchall()

                for p in preds:
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

        # =================================================
        # LOAD MATCHES
        # =================================================

        start = START_DATE.strftime("%Y-%m-%dT%H:%M:%S")

        cur.execute("""
            SELECT id, home_team, away_team, kickoff_time, status
            FROM matches
            WHERE kickoff_time >= %s
            ORDER BY kickoff_time
        """, (start,))

        rows = cur.fetchall()

        free_days = defaultdict(list)
        finished_days = defaultdict(list)

        for m in rows:

            kt = parse_dt(m[3])
            if not kt:
                continue

            day = kt.strftime("%Y-%m-%d")

            item = {
                'id': m[0],
                'home_team': m[1],
                'away_team': m[2],
                'kickoff_time': m[3],
                'status': m[4]
            }

            if m[4] == 'FINISHED':
                finished_days[day].append(item)
            else:
                free_days[day].append(item)

        # USERS
        cur.execute("SELECT id, username FROM users ORDER BY username")

        users = [
            {'id': u[0], 'username': u[1]}
            for u in cur.fetchall()
        ]

    finally:
        close_db(conn, cur)

    return render_template(
        'admin.html',
        free_days=[{'date': d, 'matches': free_days[d]} for d in sorted(free_days)],
        finished_days=[{'date': d, 'matches': finished_days[d]} for d in sorted(finished_days)],
        users=users
    )