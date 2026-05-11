from datetime import datetime, timezone
from collections import defaultdict
from zoneinfo import ZoneInfo

from flask import (
    Blueprint, render_template, request,
    redirect, url_for, flash, session
)

from app.db import get_db, close_db, get_active_tournament_id
from app.utils import get_flag, get_club_logo, is_before_deadline
from app.config import START_DATE

main_bp = Blueprint('main', __name__)

MSK = ZoneInfo("Europe/Moscow")


# =========================================================
# HELPERS
# =========================================================

def to_msk(value):
    if not value:
        return ""

    try:
        if isinstance(value, datetime):
            dt = value
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(MSK).strftime("%d.%m.%Y %H:%M")

    except Exception:
        return str(value)


# =========================================================
# INDEX
# =========================================================

@main_bp.route('/', methods=['GET', 'POST'])
def index():

    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    conn = get_db()
    cur = conn.cursor()

    try:

        league_filter = request.args.get('league', 'all')
        start_date = START_DATE.strftime("%Y-%m-%dT%H:%M:%S")

        # =================================================
        # SAVE PREDICTION
        # =================================================

        if request.method == 'POST':

            match_id = request.form.get('match_id')
            tournament_id = get_active_tournament_id()

            if not tournament_id:
                flash("Активный турнир не найден", "error")
                return redirect(url_for('main.index'))

            try:
                home_goals = int(request.form.get('home_goals', 0))
                away_goals = int(request.form.get('away_goals', 0))

                if home_goals < 0 or away_goals < 0:
                    raise ValueError

            except Exception:
                flash("Некорректный счёт", "error")
                return redirect(url_for('main.index', league=league_filter))

            cur.execute("""
                SELECT id, home_team, away_team, deadline, status
                FROM matches
                WHERE id = %s
            """, (match_id,))

            match = cur.fetchone()

            if not match:
                flash("Матч не найден", "error")
                return redirect(url_for('main.index'))

            if match[4] not in ('SCHEDULED', 'TIMED'):
                flash("Ставки закрыты", "error")
                return redirect(url_for('main.index'))

            if not is_before_deadline(match):
                flash("Дедлайн прошёл", "error")
                return redirect(url_for('main.index'))

            # UPSERT
            cur.execute("""
                SELECT 1
                FROM predictions
                WHERE user_id=%s AND match_id=%s AND tournament_id=%s
            """, (session['user_id'], match_id, tournament_id))

            exists = cur.fetchone()

            if exists:
                cur.execute("""
                    UPDATE predictions
                    SET home_goals=%s, away_goals=%s
                    WHERE user_id=%s AND match_id=%s AND tournament_id=%s
                """, (home_goals, away_goals, session['user_id'], match_id, tournament_id))
            else:
                cur.execute("""
                    INSERT INTO predictions (
                        user_id, match_id, tournament_id,
                        home_goals, away_goals
                    )
                    VALUES (%s,%s,%s,%s,%s)
                """, (session['user_id'], match_id, tournament_id, home_goals, away_goals))

            conn.commit()
            flash("Ставка сохранена", "success")

            return redirect(url_for('main.index', league=league_filter))

        # =================================================
        # LOAD MATCHES
        # =================================================

        if league_filter == 'all':
            cur.execute("""
                SELECT id, home_team, away_team,
                       kickoff_time, deadline,
                       status, league,
                       home_score, away_score
                FROM matches
                WHERE status IN ('SCHEDULED','TIMED','FINISHED')
                AND kickoff_time >= %s
                ORDER BY kickoff_time
            """, (start_date,))
        else:
            cur.execute("""
                SELECT id, home_team, away_team,
                       kickoff_time, deadline,
                       status, league,
                       home_score, away_score
                FROM matches
                WHERE status IN ('SCHEDULED','TIMED','FINISHED')
                AND league=%s
                AND kickoff_time >= %s
                ORDER BY kickoff_time
            """, (league_filter, start_date))

        rows = cur.fetchall()

        matches = []
        for m in rows:
            matches.append({
                'id': m[0],
                'home_team': m[1],
                'away_team': m[2],
                'kickoff_time': m[3],
                'deadline': m[4],
                'status': m[5],
                'league': m[6],
                'home_score': m[7],
                'away_score': m[8]
            })

        # =================================================
        # GROUP BY DAY
        # =================================================

        grouped = defaultdict(list)

        for m in matches:

            kt = m['kickoff_time']

            if isinstance(kt, str):
                kt = datetime.fromisoformat(kt.replace("Z", "+00:00"))

            if kt.tzinfo is None:
                kt = kt.replace(tzinfo=timezone.utc)

            kt_msk = kt.astimezone(MSK)

            day_key = kt_msk.strftime("%Y-%m-%d")
            day_label = kt_msk.strftime("%d.%m.%Y")

            m['deadline_passed'] = not is_before_deadline((
                m['id'], None, None, m['deadline'], m['status']
            ))

            m['finished'] = (m['status'] == 'FINISHED')

            grouped[(day_key, day_label)].append(m)

        days = []

        today = datetime.now(timezone.utc).astimezone(MSK).strftime("%Y-%m-%d")

        for (day_key, day_label), day_matches in sorted(grouped.items()):

            if day_key == today:
                dtype = 'today'
            elif day_key < today:
                dtype = 'past'
            else:
                dtype = 'future'

            days.append({
                'key': day_key,
                'label': day_label,
                'type': dtype,
                'matches': day_matches,
                'count': len(day_matches)
            })

        open_day = next(
            (d['key'] for d in days if d['type'] == 'today'),
            next((d['key'] for d in days if d['type'] == 'future'), None)
        )

    finally:
        close_db(conn, cur)

    return render_template(
        'index.html',
        days=days,
        open_day=open_day,
        to_msk=to_msk,
        current_filter=league_filter,
        get_flag=get_flag,
        get_club_logo=get_club_logo
    )