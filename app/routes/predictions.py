# app/routes/predictions.py

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.db import close_db, get_db
from app.services.tournament_context_service import (
    get_selected_tournament_id,
    get_tournament_state_flags,
)
from app.services.tournament_service import get_all_tournaments, get_tournament_by_id
from app.utils import (
    MSK,
    RU_MONTHS_GENITIVE,
    cached_to_msk,
    get_club_logo,
    get_flag,
    is_before_deadline,
    parse_datetime,
    utc_now,
)

predictions_bp = Blueprint('predictions', __name__)


def format_prediction_date(value):
    value = parse_datetime(value)
    if value is None:
        return "Дата не указана"
    value = value.astimezone(MSK)
    return f"{value.day} {RU_MONTHS_GENITIVE[value.month]}"


# =========================================================
# MATCH PREDICTIONS (PUBLIC VIEW AFTER DEADLINE)
# =========================================================

@predictions_bp.route('/match/<int:match_id>/predictions')
def match_predictions(match_id):

    conn = get_db()
    cur = conn.cursor()

    try:

        cur.execute("""
            SELECT id, home_team, away_team,
                   kickoff_time, deadline,
                   status, home_score, away_score,
                   tournament_id
            FROM matches
            WHERE id = %s
        """, (match_id,))

        m = cur.fetchone()

        if not m:
            flash("Матч не найден", "error")
            return redirect(url_for('main.index'))

        match = {
            'id': m[0],
            'home_team': m[1],
            'away_team': m[2],
            'kickoff_time': m[3],
            'deadline': m[4],
            'status': m[5],
            'home_score': m[6],
            'away_score': m[7],
            'tournament_id': m[8],
        }

        tournament_id = match['tournament_id']
        if tournament_id is None:
            flash("Турнир для матча не определён", "error")
            return redirect(url_for('main.index'))

        # ������ �����: ������� ������� � deadline
        deadline_passed = not is_before_deadline({
            "deadline": m[4]
        })

        if not deadline_passed:
            flash("Ставки будут доступны после дедлайна", "error")
            return redirect(url_for('main.index'))

        order_by = "COALESCE(p.points, 0) DESC, u.username ASC" if match['status'] == 'FINISHED' else "u.username ASC"

        cur.execute(f"""
            SELECT u.username,
                   p.home_goals,
                   p.away_goals,
                   COALESCE(p.points, 0)
            FROM predictions p
            JOIN users u ON p.user_id = u.id
            WHERE p.match_id = %s
              AND p.tournament_id = %s
              AND (
                  NOT EXISTS (SELECT 1 FROM tournaments t WHERE t.id = %s AND t.is_active = 1)
                  OR COALESCE(u.is_deleted, 0) = 0
              )
            ORDER BY {order_by}
        """, (match_id, tournament_id, tournament_id))

        predictions = [
            {
                'username': r[0],
                'home_goals': r[1],
                'away_goals': r[2],
                'points': r[3],
            }
            for r in cur.fetchall()
        ]

    finally:
        close_db(conn, cur)

    tournaments = get_all_tournaments()
    active_tournaments = [t for t in tournaments if t.get("is_active")]
    tournament_state = get_tournament_state_flags(tournaments)
    selected_tournament = get_tournament_by_id(tournament_id) if tournament_id else None
    current_tournament_name = selected_tournament["name"] if selected_tournament else "Турнир"

    return render_template(
        'match_predictions.html',
        match=match,
        predictions=predictions,
        to_msk=cached_to_msk,
        get_flag=get_flag,
        get_club_logo=get_club_logo,
        tournaments=tournaments,
        active_tournaments=active_tournaments,
        **tournament_state,
        current_tournament_id=tournament_id,
        current_tournament_name=current_tournament_name,
    )


# =========================================================
# MY PREDICTIONS
# =========================================================

@predictions_bp.route('/my-predictions')
def my_predictions():

    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    current_filter = request.args.get('filter', 'active')
    if current_filter not in {'active', 'finished'}:
        current_filter = 'active'

    conn = get_db()
    cur = conn.cursor()

    try:

        uid = session['user_id']
        tournaments = get_all_tournaments()
        active_tournaments = [t for t in tournaments if t.get("is_active")]
        tournament_state = get_tournament_state_flags(tournaments)
        tournament_id = get_selected_tournament_id(request.args.get('tid', type=int))

        if not tournament_id:
            flash("Активный турнир не найден", "error")
            return redirect(url_for('main.index'))

        now = utc_now()

        # =================================================
        # PENDING (before deadline)
        # =================================================

        cur.execute("""
            SELECT m.id, m.home_team, m.away_team,
                   p.home_goals, p.away_goals,
                   m.kickoff_time, m.deadline
            FROM predictions p
            JOIN matches m ON p.match_id = m.id
                         AND p.tournament_id = m.tournament_id
            WHERE p.user_id = %s
              AND p.tournament_id = %s
              AND m.deadline::timestamptz > %s
        """, (uid, tournament_id, now))

        pending = [
            {
                'id': r[0],
                'home_team': r[1],
                'away_team': r[2],
                'home_goals': r[3],
                'away_goals': r[4],
                'kickoff_time': r[5],
                'deadline': r[6],
            }
            for r in cur.fetchall()
        ]

        # =================================================
        # AWAITING (deadline passed, not finished)
        # =================================================

        cur.execute("""
            SELECT m.id, m.home_team, m.away_team,
                   p.home_goals, p.away_goals
            FROM predictions p
            JOIN matches m ON p.match_id = m.id
                         AND p.tournament_id = m.tournament_id
            WHERE p.user_id = %s
              AND p.tournament_id = %s
              AND m.deadline <= %s
              AND m.status NOT IN ('FINISHED','POSTPONED','CANCELLED')
        """, (uid, tournament_id, now))

        awaiting = [
            {
                'id': r[0],
                'home_team': r[1],
                'away_team': r[2],
                'home_goals': r[3],
                'away_goals': r[4],
            }
            for r in cur.fetchall()
        ]

        # =================================================
        # FINISHED
        # =================================================

        cur.execute("""
            SELECT m.id,
                   m.kickoff_time,
                   m.home_team,
                   m.away_team,
                   m.home_score,
                   m.away_score,
                   p.home_goals,
                   p.away_goals,
                   COALESCE(p.points, 0)
            FROM predictions p
            JOIN matches m ON p.match_id = m.id
                         AND p.tournament_id = m.tournament_id
            WHERE p.user_id = %s
              AND p.tournament_id = %s
              AND m.status = 'FINISHED'
            ORDER BY m.kickoff_time DESC, m.id DESC
        """, (uid, tournament_id))

        finished = [
            {
                'id': r[0],
                'date': format_prediction_date(r[1]),
                'home_team': r[2],
                'away_team': r[3],
                'home_score': r[4],
                'away_score': r[5],
                'home_goals': r[6],
                'away_goals': r[7],
                'points': r[8],
            }
            for r in cur.fetchall()
        ]

        # =================================================
        # CANCELLED / POSTPONED
        # =================================================

        cur.execute("""
            SELECT m.id,
                   m.home_team,
                   m.away_team,
                   m.status,
                   COALESCE(p.points, 0)
            FROM predictions p
            JOIN matches m ON p.match_id = m.id
                         AND p.tournament_id = m.tournament_id
            WHERE p.user_id = %s
              AND p.tournament_id = %s
              AND m.status IN ('POSTPONED','CANCELLED')
        """, (uid, tournament_id))

        cancelled = [
            {
                'id': r[0],
                'home_team': r[1],
                'away_team': r[2],
                'status': r[3],
                'points': r[4],
            }
            for r in cur.fetchall()
        ]

    finally:
        close_db(conn, cur)

    selected_tournament = get_tournament_by_id(tournament_id) if tournament_id else None
    current_tournament_name = selected_tournament["name"] if selected_tournament else "Турнир"

    return render_template(
        'my_predictions.html',
        pending=pending,
        awaiting=awaiting,
        finished=finished,
        cancelled=cancelled,
        current_filter=current_filter,
        to_msk=cached_to_msk,
        tournaments=tournaments,
        active_tournaments=active_tournaments,
        **tournament_state,
        current_tournament_id=tournament_id,
        current_tournament_name=current_tournament_name,
    )


