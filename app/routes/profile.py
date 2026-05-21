# app/routes/profile.py

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app.db import close_db, get_db
from app.services.ranking_service import get_tournament_ranking
from app.services.tournament_service import (
    get_active_tournament_id,
    get_all_tournaments,
    get_tournament_by_id,
)
from app.utils import get_club_logo, get_flag


profile_bp = Blueprint('profile', __name__)


@profile_bp.route('/profile')
def profile():
    conn = get_db()
    cur = conn.cursor()

    try:
        username = request.args.get('username')

        if username:
            cur.execute(
                """
                SELECT id
                FROM users
                WHERE username = %s
                """,
                (username,),
            )
            row = cur.fetchone()
            if not row:
                flash("Игрок не найден", "error")
                return redirect(url_for('table.table'))
            uid = row[0]
        else:
            uid = session.get('user_id')
            if not uid:
                flash("Сессия не найдена", "error")
                return redirect(url_for('auth.login'))

            cur.execute(
                """
                SELECT username
                FROM users
                WHERE id = %s
                """,
                (uid,),
            )
            row = cur.fetchone()
            if not row:
                flash("Пользователь не найден", "error")
                return redirect(url_for('auth.login'))
            username = row[0]

        all_tournaments = get_all_tournaments()
        active_tournaments = [t for t in all_tournaments if t.get("is_active")]

        tournament_id = request.args.get('tid', type=int)
        if not tournament_id:
            tournament_id = active_tournaments[0]["id"] if active_tournaments else get_active_tournament_id()
        if not tournament_id:
            flash("Активный турнир не найден", "error")
            return redirect(url_for('table.table'))

        ranking = get_tournament_ranking(tournament_id)
        user_row = next((r for r in ranking if r['user_id'] == uid), None)
        current_place = user_row['place'] if user_row else None

        cur.execute(
            """
            SELECT COUNT(*)
            FROM users
            WHERE is_admin = 0
            """
        )
        total_players = cur.fetchone()[0] or 0

        cur.execute(
            """
            SELECT
                COUNT(*) AS total_bets,
                COALESCE(SUM(CASE WHEN p.points >= 10 THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN p.points BETWEEN 7 AND 9 THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN p.points BETWEEN 3 AND 6 THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN p.points = 2 THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN p.points = 0 THEN 1 ELSE 0 END), 0),
                ROUND(COALESCE(AVG(p.points), 0), 1),
                COALESCE(SUM(p.points), 0)
            FROM predictions p
            JOIN matches m ON p.match_id = m.id
            WHERE p.user_id = %s
              AND m.status = 'FINISHED'
            """,
            (uid,),
        )

        row = cur.fetchone() or (0, 0, 0, 0, 0, 0, 0, 0)
        stats = {
            'total_bets': row[0],
            'exact_scores': row[1],
            'exact_diffs': row[2],
            'outcomes': row[3],
            'close_misses': row[4],
            'misses': row[5],
            'avg_points': float(row[6]),
            'total_points': row[7],
        }

        cur.execute(
            """
            SELECT
                m.home_team,
                m.away_team,
                p.home_goals,
                p.away_goals,
                m.home_score,
                m.away_score,
                p.points
            FROM predictions p
            JOIN matches m ON p.match_id = m.id
            WHERE p.user_id = %s
              AND m.status = 'FINISHED'
            ORDER BY m.kickoff_time DESC
            LIMIT 10
            """,
            (uid,),
        )

        recent = [
            {
                'home_team': r[0],
                'away_team': r[1],
                'home_goals': r[2],
                'away_goals': r[3],
                'home_score': r[4],
                'away_score': r[5],
                'points': r[6],
            }
            for r in cur.fetchall()
        ]

        cur.execute(
            """
            SELECT title, awarded_at
            FROM user_titles
            WHERE user_id = %s
            ORDER BY awarded_at DESC
            """,
            (uid,),
        )
        titles = [{'title': r[0], 'awarded_at': r[1]} for r in cur.fetchall()]
    finally:
        close_db(conn, cur)

    tournaments = all_tournaments
    selected_tournament = get_tournament_by_id(tournament_id) if tournament_id else None
    current_tournament_name = selected_tournament["name"] if selected_tournament else "Турнир"

    return render_template(
        'profile.html',
        username=username,
        stats=stats,
        recent=recent,
        titles=titles,
        current_place=current_place,
        total_players=total_players,
        get_flag=get_flag,
        get_club_logo=get_club_logo,
        tournaments=tournaments,
        active_tournaments=active_tournaments,
        current_tournament_id=tournament_id,
        current_tournament_name=current_tournament_name,
    )
