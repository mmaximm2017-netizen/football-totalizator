# app/routes/predictions.py

from flask import Blueprint, render_template, redirect, request, url_for, flash, session

from app.db import get_db, close_db
from app.services.tournament_context_service import get_selected_tournament_id
from app.services.tournament_service import get_tournament_by_id
from app.utils import cached_to_msk, is_before_deadline, utc_now

predictions_bp = Blueprint('predictions', __name__)


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
            tournament_id = get_selected_tournament_id(request.args.get('tid', type=int))

        # ������ �����: ������� ������� � deadline
        deadline_passed = not is_before_deadline({
            "deadline": m[4]
        })

        if not deadline_passed:
            flash("Ставки будут доступны после дедлайна", "error")
            return redirect(url_for('main.index'))

        cur.execute("""
            SELECT u.username,
                   p.home_goals,
                   p.away_goals,
                   COALESCE(p.points, 0)
            FROM predictions p
            JOIN users u ON p.user_id = u.id
            WHERE p.match_id = %s
              AND p.tournament_id = %s
            ORDER BY u.username
        """, (match_id, tournament_id))

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

    return render_template(
        'match_predictions.html',
        match=match,
        predictions=predictions,
        to_msk=cached_to_msk
    )


# =========================================================
# MY PREDICTIONS
# =========================================================

@predictions_bp.route('/my-predictions')
def my_predictions():

    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    conn = get_db()
    cur = conn.cursor()

    try:

        uid = session['user_id']
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
                   m.home_team,
                   m.away_team,
                   m.home_score,
                   m.away_score,
                   p.home_goals,
                   p.away_goals,
                   COALESCE(p.points, 0)
            FROM predictions p
            JOIN matches m ON p.match_id = m.id
            WHERE p.user_id = %s
              AND p.tournament_id = %s
              AND m.status = 'FINISHED'
        """, (uid, tournament_id))

        finished = [
            {
                'id': r[0],
                'home_team': r[1],
                'away_team': r[2],
                'home_score': r[3],
                'away_score': r[4],
                'home_goals': r[5],
                'away_goals': r[6],
                'points': r[7],
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
        to_msk=cached_to_msk,
        current_tournament_id=tournament_id,
        current_tournament_name=current_tournament_name,
    )


