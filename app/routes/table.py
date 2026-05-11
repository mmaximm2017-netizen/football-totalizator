# app/routes/table.py

from flask import Blueprint, render_template, request
from app.db import get_db, close_db

table_bp = Blueprint('table', __name__)


@table_bp.route('/table')
def table():

    conn = get_db()
    cur = conn.cursor()

    try:

        # =================================================
        # TOURNAMENTS
        # =================================================

        cur.execute("""
            SELECT id, name, is_active
            FROM tournaments
            ORDER BY is_active DESC, id DESC
        """)

        tournaments = [
            {'id': r[0], 'name': r[1], 'is_active': r[2]}
            for r in cur.fetchall()
        ]

        # безопасный выбор турнира
        tid = request.args.get('tid', type=int)

        if not tid:

            active = next((t for t in tournaments if t['is_active']), None)

            if active:
                tid = active['id']
            elif tournaments:
                tid = tournaments[0]['id']
            else:
                return render_template(
                    'table.html',
                    table=[],
                    tournaments=[],
                    selected_tid=None,
                    selected_name="Нет турниров"
                )

        cur.execute("""
            SELECT name
            FROM tournaments
            WHERE id = %s
        """, (tid,))

        row = cur.fetchone()
        selected_name = row[0] if row else "Турнир"

        # =================================================
        # RANKING
        # =================================================

        cur.execute("""
            SELECT
                u.username,
                COALESCE(SUM(p.points), 0) AS total_points,
                COALESCE(SUM(CASE WHEN p.points >= 10 THEN 1 ELSE 0 END), 0) AS exact_scores,
                COALESCE(SUM(CASE WHEN p.points BETWEEN 7 AND 8 THEN 1 ELSE 0 END), 0) AS exact_diffs,
                COALESCE(SUM(CASE WHEN p.points BETWEEN 3 AND 6 THEN 1 ELSE 0 END), 0) AS outcomes
            FROM users u
            LEFT JOIN predictions p
                ON u.id = p.user_id
                AND p.tournament_id = %s
            WHERE u.is_admin = 0
            GROUP BY u.id, u.username
            ORDER BY
                total_points DESC,
                exact_scores DESC,
                exact_diffs DESC,
                outcomes DESC,
                u.username ASC
        """, (tid,))

        rows = cur.fetchall()

    finally:
        close_db(conn, cur)

    # =================================================
    # PLACE CALCULATION (FIXED TIES)
    # =================================================

    table_data = []

    prev_key = None
    place = 0
    skip = 0

    for i, row in enumerate(rows):

        username, pts, exact, diff, outcome = row

        pts = pts or 0
        exact = exact or 0
        diff = diff or 0
        outcome = outcome or 0

        key = (pts, exact, diff, outcome)

        if key != prev_key:
            place = i + 1 - skip
            shared = False
        else:
            shared = True
            skip += 1

        table_data.append({
            'place': place,
            'username': username,
            'points': pts,
            'shared': shared
        })

        prev_key = key

    return render_template(
        'table.html',
        table=table_data,
        tournaments=tournaments,
        selected_tid=tid,
        selected_name=selected_name
    )