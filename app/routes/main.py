# app/routes/main.py
from datetime import datetime, timedelta, timezone
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

from app.db import get_db, close_db
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

        now = datetime.now(timezone.utc)

        league_filter = request.args.get('league', 'all')

        start_date_str = START_DATE.strftime("%Y-%m-%dT%H:%M:%S")

        today_msk = now.astimezone(MSK).strftime("%Y-%m-%d")

        # =================================================
        # POST: MAKE PREDICTION
        # =================================================

        if request.method == 'POST':

            match_id = request.form.get('match_id')

            cur.execute("SELECT id FROM tournaments WHERE is_active = 1 LIMIT 1")
            row = cur.fetchone()
            tournament_id = row[0] if row else None

            if not tournament_id:
                flash("Активный турнир не найден", "error")
                return redirect(url_for('main.index'))

            try:

                home_goals = int(request.form.get('home_goals', '0').strip() or '0')
                away_goals = int(request.form.get('away_goals', '0').strip() or '0')

                if home_goals < 0 or away_goals < 0:
                    raise ValueError

            except ValueError:
                flash("Некорректный счёт", "error")
                return redirect(url_for('main.index', league=league_filter))

            cur.execute("""
                SELECT id,
                       home_team,
                       away_team,
                       deadline,
                       status
                FROM matches
                WHERE id = %s
            """, (match_id,))

            match = cur.fetchone()

            if not match:
                flash("Матч не найден", "error")
                return redirect(url_for('main.index'))

            if match[4] not in ('SCHEDULED', 'TIMED'):
                flash("Ставки на этот матч закрыты", "error")
                return redirect(url_for('main.index'))

            if not is_before_deadline(match):
                flash("Дедлайн прошёл", "error")
                return redirect(url_for('main.index'))

            try:

                cur.execute("""
                    SELECT 1
                    FROM predictions
                    WHERE user_id = %s
                    AND match_id = %s
                    AND tournament_id = %s
                """, (
                    session['user_id'],
                    match_id,
                    tournament_id
                ))

                exists = cur.fetchone()

                if exists:

                    cur.execute("""
                        UPDATE predictions
                        SET home_goals = %s,
                            away_goals = %s
                        WHERE user_id = %s
                        AND match_id = %s
                        AND tournament_id = %s
                    """, (
                        home_goals,
                        away_goals,
                        session['user_id'],
                        match_id,
                        tournament_id
                    ))

                else:

                    cur.execute("""
                        INSERT INTO predictions (
                            user_id,
                            match_id,
                            tournament_id,
                            home_goals,
                            away_goals
                        )
                        VALUES (%s, %s, %s, %s, %s)
                    """, (
                        session['user_id'],
                        match_id,
                        tournament_id,
                        home_goals,
                        away_goals
                    ))

                conn.commit()

                flash("✅ Ставка принята", "success")

            except Exception as e:

                conn.rollback()

                flash(f"Ошибка сохранения ставки: {e}", "error")

            return redirect(url_for('main.index', league=league_filter))

        # =================================================
        # GET: SHOW MATCHES
        # =================================================

        if league_filter == 'all':

            cur.execute("""
                SELECT id,
                       home_team,
                       away_team,
                       kickoff_time,
                       deadline,
                       status,
                       league,
                       home_score,
                       away_score
                FROM matches
                WHERE status IN ('SCHEDULED', 'TIMED', 'FINISHED')
                AND kickoff_time >= %s
                ORDER BY kickoff_time
            """, (start_date_str,))

        else:

            cur.execute("""
                SELECT id,
                       home_team,
                       away_team,
                       kickoff_time,
                       deadline,
                       status,
                       league,
                       home_score,
                       away_score
                FROM matches
                WHERE status IN ('SCHEDULED', 'TIMED', 'FINISHED')
                AND league = %s
                AND kickoff_time >= %s
                ORDER BY kickoff_time
            """, (league_filter, start_date_str))

        raw_matches = []

        for m in cur.fetchall():

            kickoff = m[3]
            if isinstance(kickoff, str):
                kickoff = datetime.fromisoformat(kickoff.replace('Z', '+00:00'))

            raw_matches.append({
                'id': m[0],
                'home_team': m[1],
                'away_team': m[2],
                'kickoff_time': kickoff,
                'deadline': m[4],
                'status': m[5],
                'league': m[6],
                'home_score': m[7],
                'away_score': m[8]
            })

        cur.execute("SELECT id FROM tournaments WHERE is_active = 1 LIMIT 1")
        row = cur.fetchone()
        tournament_id = row[0] if row else None

        match_ids = [match['id'] for match in raw_matches]

        user_data = {}

        if match_ids and tournament_id:

            cur.execute("""
                SELECT match_id,
                       home_goals,
                       away_goals,
                       points
                FROM predictions
                WHERE user_id = %s
                AND tournament_id = %s
                AND match_id = ANY(%s)
            """, (
                session['user_id'],
                tournament_id,
                match_ids
            ))

            for row in cur.fetchall():

                user_data[row[0]] = (
                    row[1],
                    row[2],
                    row[3]
                )

        matches_by_day = defaultdict(list)

        for match in raw_matches:

            kickoff = match['kickoff_time']
            if isinstance(kickoff, datetime):
                kickoff_msk = kickoff.astimezone(MSK)
            else:
                kickoff_msk = datetime.fromisoformat(str(kickoff).replace('Z', '+00:00')).astimezone(MSK)

            day_key = kickoff_msk.strftime("%Y-%m-%d")

            match['day_key'] = day_key

            match['deadline_passed'] = not is_before_deadline((
                match['id'],
                None,
                None,
                match['deadline'],
                None
            ))

            match['finished'] = (
                match['status'] == 'FINISHED'
            )

            if match['id'] in user_data:

                match['pred_home'] = user_data[match['id']][0]
                match['pred_away'] = user_data[match['id']][1]
                match['my_points'] = user_data[match['id']][2] if match['finished'] else 0

            else:

                match['pred_home'] = ''
                match['pred_away'] = ''
                match['my_points'] = 0

            day_label = kickoff_msk.strftime("%d.%m.%Y")

            matches_by_day[(day_key, day_label)].append(match)

        days = []

        for (day_key, day_label), day_matches in sorted(
            matches_by_day.items(),
            key=lambda x: x[0][0]
        ):

            if day_key == today_msk:
                day_type = 'today'

            elif day_key < today_msk:
                day_type = 'past'

            else:
                day_type = 'future'

            has_open = any(
                not m['deadline_passed']
                for m in day_matches
            )

            days.append({
                'key': day_key,
                'label': day_label,
                'type': day_type,
                'matches': day_matches,
                'count': len(day_matches),
                'has_open': has_open
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