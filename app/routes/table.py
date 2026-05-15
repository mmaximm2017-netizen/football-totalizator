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
        # TOURNAMENTS (все, для выбора в выпадающем списке)
        # =================================================

        cur.execute("""
            SELECT id, name, is_active, start_date
            FROM tournaments
            ORDER BY start_date DESC, id DESC
        """)

        tournaments = [
            {
                'id': r[0],
                'name': r[1],
                'is_active': r[2],
                'start_date': r[3] if r[3] else '—'
            }
            for r in cur.fetchall()
        ]

        from datetime import datetime
        from zoneinfo import ZoneInfo

        today = datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat()

        current_tid = None

        for t in sorted(
            [x for x in tournaments if x['start_date'] != '—' and x['start_date'] <= today],
            key=lambda x: (x['start_date'], x['id']),
            reverse=True
        ):
            current_tid = t['id']
            break

        for t in tournaments:
            if t['start_date'] != '—' and t['start_date'] > today:
                t['status'] = 'future'
            elif t['id'] == current_tid:
                t['status'] = 'current'
            else:
                t['status'] = 'archive'
                
        # безопасный выбор турнира
        tid = request.args.get('tid', type=int)

        if not tid:
            current = next(
                (t for t in tournaments if t['status'] == 'current'),
                None
            )

            if current:
                tid = current['id']
            elif tournaments:
                tid = tournaments[0]['id']
            else:
                return render_template(
                    'table.html',
                    table=[],
                    tournaments=[],
                    selected_tid=None,
                    selected_name="Нет турниров",
                    selected_is_active=False
                )

        cur.execute("""
            SELECT name, is_active, start_date
            FROM tournaments
            WHERE id = %s
        """, (tid,))

        row = cur.fetchone()
        selected_name = row[0] if row else "Турнир"
        selected_is_active = row[1] if row else False
        selected_start_date = row[2] if row else "—"
                selected_status = next(
            (t['status'] for t in tournaments if t['id'] == tid),
            'archive'
        )

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
        selected_name=selected_name,
        selected_is_active=selected_is_active,
        selected_start_date=selected_start_date,
        selected_status=selected_status
    )