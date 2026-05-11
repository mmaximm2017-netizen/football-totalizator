# app/services/point_service.py
from app.db import get_db, close_db
from app.models.scoring import calculate_points

def calculate_points_for_match(match_id):
    conn = get_db(); cur = conn.cursor()
    from app.db import get_active_tournament_id
    t_id = get_active_tournament_id()
    try:
        cur.execute("SELECT id, home_score, away_score FROM matches WHERE id = %s", (match_id,))
        match = cur.fetchone()
        if not match:
            return
        cur.execute("SELECT user_id, home_goals, away_goals FROM predictions WHERE match_id = %s AND tournament_id = %s", (match_id, t_id))
        for p in cur.fetchall():
            pts = calculate_points(match[1], match[2], p[1], p[2])
            cur.execute("UPDATE predictions SET points = %s WHERE user_id = %s AND match_id = %s AND tournament_id = %s",
                        (pts, p[0], match_id, t_id))
    finally:
        close_db(conn, cur)

def calculate_all_points():
    conn = get_db(); cur = conn.cursor()
    from app.db import get_active_tournament_id
    t_id = get_active_tournament_id()
    try:
        cur.execute("""
            SELECT m.id, m.home_score, m.away_score, 
                   p.user_id, p.home_goals, p.away_goals
            FROM matches m
            JOIN predictions p ON p.match_id = m.id
            WHERE m.status = 'FINISHED' AND p.tournament_id = %s
        """, (t_id,))
        
        for row in cur.fetchall():
            match_id, real_h, real_a, user_id, pred_h, pred_a = row
            pts = calculate_points(real_h, real_a, pred_h, pred_a)
            cur.execute("UPDATE predictions SET points = %s WHERE user_id = %s AND match_id = %s AND tournament_id = %s",
                        (pts, user_id, match_id, t_id))
    finally:
        close_db(conn, cur)