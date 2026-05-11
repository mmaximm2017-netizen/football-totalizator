# app/routes/profile.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from app.db import get_db, close_db, get_active_tournament_id
from app.utils import get_flag, get_club_logo

profile_bp = Blueprint('profile', __name__)

@profile_bp.route('/profile')
def profile():
    username = request.args.get('username')
    if username:
        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute("SELECT id FROM users WHERE username = %s", (username,))
            user_row = cur.fetchone()
            if not user_row:
                flash("Игрок не найден", "error")
                return redirect(url_for('table.table'))
            uid = user_row[0]
        finally:
            close_db(conn, cur)
    else:
        uid = session['user_id']
        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute("SELECT username FROM users WHERE id = %s", (uid,))
            username = cur.fetchone()[0]
        finally:
            close_db(conn, cur)
    
    t_id = get_active_tournament_id()
    conn = get_db()
    cur = conn.cursor()
    try:
        # Текущее место игрока
        cur.execute("""
            WITH ranked AS (
                SELECT u.id, u.username,
                       COALESCE(SUM(p.points), 0) as total,
                       RANK() OVER (ORDER BY COALESCE(SUM(p.points), 0) DESC,
                                             COUNT(CASE WHEN p.points >= 10 THEN 1 END) DESC,
                                             COUNT(CASE WHEN p.points IN (7,8) THEN 1 END) DESC,
                                             COUNT(CASE WHEN p.points BETWEEN 3 AND 6 THEN 1 END) DESC) as place
                FROM users u
                LEFT JOIN predictions p ON u.id = p.user_id AND p.tournament_id = %s
                WHERE u.is_admin = 0
                GROUP BY u.id
            )
            SELECT place, (SELECT COUNT(*) FROM users WHERE is_admin = 0) as total_players
            FROM ranked
            WHERE id = %s
        """, (t_id, uid))
        rank_row = cur.fetchone()
        current_place = rank_row[0] if rank_row else None
        total_players = rank_row[1] if rank_row else 0
        
        # Основная статистика
        cur.execute("""
            SELECT 
                COUNT(*) as total_bets,
                SUM(CASE WHEN p.points >= 10 THEN 1 ELSE 0 END) as exact_scores,
                SUM(CASE WHEN p.points >= 7 AND p.points < 10 THEN 1 ELSE 0 END) as exact_diffs,
                SUM(CASE WHEN p.points >= 3 AND p.points < 7 THEN 1 ELSE 0 END) as outcomes,
                SUM(CASE WHEN p.points = 2 THEN 1 ELSE 0 END) as close_misses,
                SUM(CASE WHEN p.points = 0 THEN 1 ELSE 0 END) as misses,
                ROUND(AVG(p.points), 1) as avg_points,
                SUM(p.points) as total_points
            FROM predictions p
            JOIN matches m ON p.match_id = m.id
            WHERE p.user_id = %s AND p.tournament_id = %s AND m.status = 'FINISHED'
        """, (uid, t_id))
        row = cur.fetchone()
        stats = {
            'total_bets': row[0] or 0,
            'exact_scores': row[1] or 0,
            'exact_diffs': row[2] or 0,
            'outcomes': row[3] or 0,
            'close_misses': row[4] or 0,
            'misses': row[5] or 0,
            'avg_points': float(row[6]) if row[6] else 0,
            'total_points': row[7] or 0,
        }
        # Любимый счёт
        cur.execute("""
            SELECT home_goals, away_goals, COUNT(*) as cnt
            FROM predictions
            WHERE user_id = %s AND tournament_id = %s
            GROUP BY home_goals, away_goals
            ORDER BY cnt DESC
            LIMIT 3
        """, (uid, t_id))
        favorites = [{'home': r[0], 'away': r[1], 'count': r[2]} for r in cur.fetchall()]
        # Последние 10 матчей
        cur.execute("""
            SELECT m.home_team, m.away_team, p.home_goals, p.away_goals, m.home_score, m.away_score, p.points
            FROM predictions p
            JOIN matches m ON p.match_id = m.id
            WHERE p.user_id = %s AND p.tournament_id = %s AND m.status = 'FINISHED'
            ORDER BY m.kickoff_time DESC
            LIMIT 10
        """, (uid, t_id))
        recent = [{'home_team': r[0], 'away_team': r[1], 'home_goals': r[2], 'away_goals': r[3],
                    'home_score': r[4], 'away_score': r[5], 'points': r[6]} for r in cur.fetchall()]
    finally:
        close_db(conn, cur)
    return render_template('profile.html', username=username, stats=stats, favorites=favorites, recent=recent,
                           current_place=current_place, total_players=total_players,
                           get_flag=get_flag, get_club_logo=get_club_logo)