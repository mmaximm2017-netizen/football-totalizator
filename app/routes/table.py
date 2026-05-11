# app/routes/table.py
from flask import Blueprint, render_template, request
from app.db import get_db, close_db

table_bp = Blueprint('table', __name__)

@table_bp.route('/table')
def table():
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, name, is_active FROM tournaments ORDER BY is_active DESC, id DESC")
        tournaments = [{'id': r[0], 'name': r[1], 'is_active': r[2]} for r in cur.fetchall()]
        tid = request.args.get('tid', type=int)
        if not tid:
            active = next((t for t in tournaments if t['is_active']), None)
            tid = active['id'] if active else (tournaments[0]['id'] if tournaments else 1)
        cur.execute("SELECT name FROM tournaments WHERE id = %s", (tid,))
        selected = cur.fetchone()
        selected_name = selected[0] if selected else 'Турнир'

        cur.execute("""
            SELECT u.username,
                   COALESCE(SUM(p.points), 0) as total_points,
                   COUNT(CASE WHEN p.points >= 10 THEN 1 END) as exact_scores,
                   COUNT(CASE WHEN p.points IN (7,8) THEN 1 END) as exact_diffs,
                   COUNT(CASE WHEN p.points BETWEEN 3 AND 6 THEN 1 END) as outcomes
            FROM users u
            LEFT JOIN predictions p ON u.id = p.user_id AND p.tournament_id = %s
            WHERE u.is_admin = 0
            GROUP BY u.id
            ORDER BY total_points DESC, exact_scores DESC, exact_diffs DESC, outcomes DESC
        """, (tid,))
        rows = cur.fetchall()
    finally:
        close_db(conn, cur)

    table_data = []
    prev_pts = prev_exact = prev_diff = prev_outcome = None
    for i, row in enumerate(rows):
        username, pts, exact, diff, outcome = row
        pts = int(pts) if pts else 0
        exact = int(exact) if exact else 0
        diff = int(diff) if diff else 0
        outcome = int(outcome) if outcome else 0

        if i == 0:
            current_place = 1
            shared = False
        else:
            if pts == prev_pts and exact == prev_exact and diff == prev_diff and outcome == prev_outcome:
                shared = True
            else:
                current_place = i + 1
                shared = False

        table_data.append({
            'place': current_place,
            'username': username,
            'points': pts,
            'shared': shared
        })
        prev_pts, prev_exact, prev_diff, prev_outcome = pts, exact, diff, outcome

    return render_template('table.html', table=table_data, tournaments=tournaments, selected_tid=tid, selected_name=selected_name)