# app/routes/main.py

from datetime import datetime, timedelta, timezone, date
from collections import defaultdict
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

from app.db import get_db, close_db, get_active_tournament_id
from app.utils import cached_to_msk, is_before_deadline, get_flag, get_club_logo
from app.config import START_DATE


main_bp = Blueprint('main', __name__)

MSK = ZoneInfo("Europe/Moscow")


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

        now_utc = datetime.now(timezone.utc)
        now_msk = now_utc.astimezone(MSK)
        today_msk = now_msk.date()

        league_filter = request.args.get('league', 'all')

        # безопасная конвертация START_DATE
        if START_DATE.tzinfo is None:
            start_date_utc = START_DATE.replace(tzinfo=MSK).astimezone(timezone.utc)
        else:
            start_date_utc = START_DATE.astimezone(timezone.utc)

        # =================================================
        # POST: PREDICTION
        # =================================================

        if request.method == 'POST':

            match_id = request.form.get('match_id')

            tournament_id = get_active_tournament_id(cur)

            if not tournament_id:
                flash("Активный турнир не найден", "error")
                return redirect(url_for('main.index'))

            try:
                home_goals = int(request.form.get('home_goals', 0))
                away_goals = int(request.form.get('away_goals', 0))

                if home_goals < 0 or away_goals < 0:
                    raise ValueError

            except ValueError:
                flash("Некорректный счёт", "error")
                return redirect(url_for('main.index', league=league_filter))

            cur.execute("""
                SELECT id, home_team, away_team, kickoff_time, deadline, status
                FROM matches
                WHERE id = %s
            """, (match_id,))

            match = cur.fetchone()

            if not match:
                flash("Матч не найден", "error")
                return redirect(url_for('main.index'))

            if match[5] not in ('SCHEDULED', 'TIMED'):
                flash("Ставки закрыты", "error")
                return redirect(url_for('main.index'))

            if not is_before_deadline(match[4]):
                flash("Дедлайн прошёл", "error")
                return redirect(url_for('main.index'))

            try:

                cur.execute("""
                    SELECT 1
                    FROM predictions
                    WHERE user_id = %s
                    AND match_id = %s
                    AND tournament_id = %s
                """, (session['user_id'], match_id, tournament_id))

                exists = cur.fetchone()

                if exists:
                    cur.execute("""
                        UPDATE predictions
                        SET home_goals = %s,
                            away_goals = %s
                        WHERE user_id = %s
                        AND match_id = %s
                        AND tournament_id = %s
                    """, (home_goals, away_goals, session['user_id'], match_id, tournament_id))

                else:
                    cur.execute("""
                        INSERT INTO predictions (
                            user_id, match_id, tournament_id,
                            home_goals, away_goals
                        )
                        VALUES (%s, %s, %s, %s, %s)
                    """, (session['user_id'], match_id, tournament_id, home_goals, away_goals))

                conn.commit()
                flash("Ставка принята", "success")

            except Exception as e:
                conn.rollback()
                flash(f"Ошибка: {e}", "error")

            return redirect(url_for('main.index', league=league_filter))

        # =================================================
        # GET MATCHES
        # =================================================

        params = [start_date_utc]

        base_query = """
            SELECT id, home_team, away_team,
                   kickoff_time, deadline,
                   status, league,
                   home_score, away_score
            FROM matches
            WHERE status IN ('SCHEDULED','TIMED','FINISHED')
            AND kickoff_time >= %s
        """

        if league_filter != 'all':
            base_query += " AND league = %s"
            params.append(league_filter)

        base_query += " ORDER BY kickoff_time"

        cur.execute(base_query, tuple(params))

        raw_matches = cur.fetchall()

        tournament_id = get_active_tournament_id(cur)

        match_ids = [m[0] for m in raw_matches]

        user_data = {}

        if match_ids and tournament_id:

            cur.execute("""
                SELECT match_id, home_goals, away_goals, points
                FROM predictions
                WHERE user_id = %s
                AND tournament_id = %s
                AND match_id = ANY(%s::int[])
            """, (session['user_id'], tournament_id, match_ids))

            for r in cur.fetchall():
                user_data[r[0]] = (r[1], r[2], r[3])

        matches_by_day = defaultdict(list)

        for m in raw_matches:

            kickoff_msk = m[3].astimezone(MSK)
            day = kickoff_msk.date()

            match = {
                'id': m[0],
                'home_team': m[1],
                'away_team': m[2],
                'kickoff_time': m[3],
                'deadline': m[4],
                'status': m[5],
                'league': m[6],
                'home_score': m[7],
                'away_score': m[8],
            }

            match['deadline_passed'] = not is_before_deadline(m[4])
            match['finished'] = (m[5] == 'FINISHED')

            if m[0] in user_data:
                match['pred_home'], match['pred_away'], match['my_points'] = user_data[m[0]]
            else:
                match['pred_home'] = ''
                match['pred_away'] = ''
                match['my_points'] = 0

            matches_by_day[(day, kickoff_msk.strftime("%d.%m.%Y"))].append(match)

        days = []

        for (day, label), matches in sorted(matches_by_day.items(), key=lambda x: x[0][0]):

            if day == today_msk:
                day_type = 'today'
            elif day < today_msk:
                day_type = 'past'
            else:
                day_type = 'future'

            days.append({
                'key': str(day),
                'label': label,
                'type': day_type,
                'matches': matches,
                'count': len(matches),
                'has_open': any(not m['deadline_passed'] for m in matches)
            })

        open_day = None

        for d in days:
            if d['type'] == 'today':
                open_day = d['key']
                break

        if not open_day:
            for d in days:
                if d['type'] == 'future':
                    open_day = d['key']
                    break

    finally:
        close_db(conn, cur)

    return render_template(
        'index.html',
        days=days,
        open_day=open_day,
        to_msk=cached_to_msk,
        current_filter=league_filter,
        get_flag=get_flag,
        get_club_logo=get_club_logo
    )