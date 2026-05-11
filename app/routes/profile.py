# app/routes/profile.py

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
from app.utils import get_flag, get_club_logo


profile_bp = Blueprint('profile', __name__)


# =========================================================
# PROFILE
# =========================================================

@profile_bp.route('/profile')
def profile():

    conn = get_db()
    cur = conn.cursor()

    try:

        # =================================================
        # SAFE USER RESOLVE
        # =================================================

        username = request.args.get('username')

        if username:

            cur.execute("""
                SELECT id
                FROM users
                WHERE username = %s
            """, (username,))

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

            cur.execute("""
                SELECT username
                FROM users
                WHERE id = %s
            """, (uid,))

            row = cur.fetchone()

            if not row:
                flash("Пользователь не найден", "error")
                return redirect(url_for('auth.login'))

            username = row[0]

        # =================================================
        # TOURNAMENT
        # =================================================

        tournament_id = get_active_tournament_id()

        if not tournament_id:
            flash("Активный турнир не найден", "error")
            return redirect(url_for('table.table'))

        # =================================================
        # PLACE (RANKING)
        # =================================================

        cur.execute("""
            WITH ranked AS (
                SELECT
                    u.id,
                    COALESCE(SUM(p.points), 0) AS total_points,
                    RANK() OVER (
                        ORDER BY
                            COALESCE(SUM(p.points), 0) DESC,
                            COUNT(CASE WHEN p.points >= 10 THEN 1 END) DESC,
                            COUNT(CASE WHEN p.points BETWEEN 7 AND 9 THEN 1 END) DESC,
                            COUNT(CASE WHEN p.points BETWEEN 3 AND 6 THEN 1 END) DESC,
                            u.id ASC
                    ) AS place
                FROM users u
                LEFT JOIN predictions p
                    ON u.id = p.user_id
                    AND p.tournament_id = %s
                WHERE u.is_admin = 0
                GROUP BY u.id
            )
            SELECT place
            FROM ranked
            WHERE id = %s
        """, (tournament_id, uid))

        row = cur.fetchone()
        current_place = row[0] if row else None

        # total players
        cur.execute("""
            SELECT COUNT(*)
            FROM users
            WHERE is_admin = 0
        """)

        total_players = cur.fetchone()[0] or 0

        # =================================================
        # STATS
        # =================================================

        cur.execute("""
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
              AND p.tournament_id = %s
              AND m.status = 'FINISHED'
        """, (uid, tournament_id))

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

        # =================================================
        # RECENT MATCHES
        # =================================================

        cur.execute("""
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
              AND p.tournament_id = %s
              AND m.status = 'FINISHED'
            ORDER BY m.kickoff_time DESC
            LIMIT 10
        """, (uid, tournament_id))

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

    finally:
        close_db(conn, cur)

    return render_template(
        'profile.html',
        username=username,
        stats=stats,
        recent=recent,
        current_place=current_place,
        total_players=total_players,
        get_flag=get_flag,
        get_club_logo=get_club_logo
    )
