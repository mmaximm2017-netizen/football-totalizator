# app/routes/table.py

from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify, render_template, request

from app.db import close_db, get_db
from app.services.ranking_service import get_tournament_ranking
from app.services.tournament_service import get_tournament_status

table_bp = Blueprint('table', __name__)


def get_default_tournament_id():
    """
    Returns tournament id with the nearest upcoming match.
    Fallback: first active tournament by id.
    """
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT tournament_id
            FROM matches
            WHERE kickoff_time >= NOW()
              AND tournament_id IS NOT NULL
            GROUP BY tournament_id
            ORDER BY MIN(kickoff_time) ASC
            LIMIT 1
            """
        )
        row = cur.fetchone()
        if row and row[0]:
            return row[0]

        cur.execute(
            """
            SELECT id
            FROM tournaments
            WHERE is_active = 1
            ORDER BY id
            LIMIT 1
            """
        )
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        close_db(conn, cur)


@table_bp.route('/table')
def table():
    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute(
            """
            SELECT id, name, is_active, start_date
            FROM tournaments
            ORDER BY start_date DESC, id DESC
            """
        )

        tournaments = [
            {
                'id': r[0],
                'name': r[1],
                'is_active': r[2],
                'start_date': r[3] if r[3] else '—',
            }
            for r in cur.fetchall()
        ]
        active_tournaments = [t for t in tournaments if t['is_active']]

        today = datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat()

        current_tid = None
        for t in sorted(
            [x for x in tournaments if x['start_date'] != '—' and x['start_date'] <= today],
            key=lambda x: (x['start_date'], x['id']),
            reverse=True,
        ):
            current_tid = t['id']
            break

        for t in tournaments:
            lifecycle = get_tournament_status(
                {
                    "id": t["id"],
                    "name": t["name"],
                    "is_active": t["is_active"],
                    "start_date": None if t["start_date"] == "—" else t["start_date"],
                    "end_date": None,
                }
            )
            if t['id'] == current_tid:
                t['status'] = 'current'
            elif lifecycle == "upcoming":
                t['status'] = 'future'
            else:
                t['status'] = 'archive'

        tid = request.args.get('tid', type=int)
        if not tid:
            default_tid = get_default_tournament_id()
            if default_tid:
                tid = default_tid
            elif active_tournaments:
                tid = active_tournaments[0]['id']
            elif tournaments:
                tid = tournaments[0]['id']
            else:
                return render_template(
                    'table.html',
                    table=[],
                    tournaments=[],
                    active_tournaments=[],
                    selected_tid=None,
                    selected_name="Нет турниров",
                    selected_is_active=False,
                    current_tournament_id=None,
                    current_tournament_name="Нет турниров",
                )

        cur.execute(
            """
            SELECT name, is_active, start_date
            FROM tournaments
            WHERE id = %s
            """,
            (tid,),
        )
        row = cur.fetchone()

        selected_name = row[0] if row else "Турнир"
        selected_is_active = row[1] if row else False
        selected_start_date = row[2] if row else "—"
        selected_status = next((t['status'] for t in tournaments if t['id'] == tid), 'archive')
    finally:
        close_db(conn, cur)

    table_data = get_tournament_ranking(tid)

    is_ajax = request.args.get('ajax') == '1'

    if is_ajax:
        html = render_template(
            'table_content.html',
            table=table_data,
            tournaments=tournaments,
            active_tournaments=active_tournaments,
            selected_tid=tid,
            selected_name=selected_name,
            selected_is_active=selected_is_active,
            selected_start_date=selected_start_date,
            selected_status=selected_status,
            current_tournament_id=tid,
            current_tournament_name=selected_name,
        )
        tournament_key = 'wc2026' if selected_name == 'ЧМ-2026' else 'cup'
        return jsonify(
            {
                'html': html,
                'tournament_key': tournament_key,
                'tournament_name': selected_name,
                'tid': tid,
            }
        )

    return render_template(
        'table.html',
        table=table_data,
        tournaments=tournaments,
        active_tournaments=active_tournaments,
        selected_tid=tid,
        selected_name=selected_name,
        selected_is_active=selected_is_active,
        selected_start_date=selected_start_date,
        selected_status=selected_status,
        current_tournament_id=tid,
        current_tournament_name=selected_name,
    )
