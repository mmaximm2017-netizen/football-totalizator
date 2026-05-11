# app/routes/predictions.py
from flask import Blueprint, render_template, redirect, url_for, flash, session, request
from app.db import get_db, close_db, get_active_tournament_id
from app.utils import cached_to_msk, is_before_deadline

predictions_bp = Blueprint('predictions', __name__)

@predictions_bp.route('/match/<int:match_id>/predictions')
def match_predictions(match_id):
    conn = get_db(); cur = conn.cursor()
    t_id = get_active_tournament_id()
    try:
        cur.execute("SELECT id, home_team, away_team, kickoff_time, deadline, status, home_score, away_score FROM matches WHERE id = %s", (match_id,))
        m = cur.fetchone()
        if not m: flash("Матч не найден", "error"); return redirect(url_for('main.index'))
        
        match = {'id': m[0], 'home_team': m[1], 'away_team': m[2], 'kickoff_time': m[3], 'deadline': m[4], 'status': m[5], 'home_score': m[6], 'away_score': m[7]}
        deadline_passed = not is_before_deadline((m[0], m[1], m[2], m[4], m[5]))
        
        if deadline_passed:
            cur.execute("""
                SELECT u.username, p.home_goals, p.away_goals, COALESCE(p.points, 0) as pts
                FROM predictions p 
                JOIN users u ON p.user_id = u.id 
                WHERE p.match_id = %s
                ORDER BY u.username
            """, (match_id,))
            predictions = [{'username': p[0], 'home_goals': p[1], 'away_goals': p[2], 'points': p[3]} for p in cur.fetchall()]
            return render_template('match_predictions.html', match=match, predictions=predictions, to_msk=cached_to_msk)
        else:
            flash("Ставки будут доступны после дедлайна", "error")
            return redirect(url_for('main.index'))
    finally:
        close_db(conn, cur)

@predictions_bp.route('/my-predictions')
def my_predictions():
    conn = get_db(); cur = conn.cursor()
    from app.utils import utc_now
    now = utc_now()
    uid = session['user_id']
    current_filter = request.args.get('filter', 'active')
    t_id = get_active_tournament_id()
    try:
        cur.execute("""SELECT m.id, m.home_team, m.away_team, p.home_goals, p.away_goals, m.kickoff_time, m.deadline
            FROM predictions p JOIN matches m ON p.match_id=m.id WHERE p.user_id=%s AND p.tournament_id=%s AND m.deadline>%s""", (uid, t_id, now.strftime("%Y-%m-%dT%H:%M:%S")))
        pending = [{'id': p[0], 'home_team': p[1], 'away_team': p[2], 'home_goals': p[3], 'away_goals': p[4], 'kickoff_time': p[5], 'deadline': p[6]} for p in cur.fetchall()]
        cur.execute("""SELECT m.id, m.home_team, m.away_team, p.home_goals, p.away_goals
            FROM predictions p JOIN matches m ON p.match_id=m.id WHERE p.user_id=%s AND p.tournament_id=%s AND m.deadline<=%s AND m.status NOT IN ('FINISHED','POSTPONED','CANCELLED')""", (uid, t_id, now.strftime("%Y-%m-%dT%H:%M:%S")))
        awaiting = [{'id': a[0], 'home_team': a[1], 'away_team': a[2], 'home_goals': a[3], 'away_goals': a[4]} for a in cur.fetchall()]
        cur.execute("""SELECT m.id, m.home_team, m.away_team, m.home_score, m.away_score, p.home_goals, p.away_goals, COALESCE(p.points, 0) as pts
            FROM predictions p JOIN matches m ON p.match_id=m.id WHERE p.user_id=%s AND p.tournament_id=%s AND m.status='FINISHED'""", (uid, t_id))
        finished = [{'id': f[0], 'home_team': f[1], 'away_team': f[2], 'home_score': f[3], 'away_score': f[4], 'home_goals': f[5], 'away_goals': f[6], 'points': f[7]} for f in cur.fetchall()]
        cur.execute("""SELECT m.id, m.home_team, m.away_team, m.status, COALESCE(p.points, 0) as pts
            FROM predictions p JOIN matches m ON p.match_id=m.id WHERE p.user_id=%s AND p.tournament_id=%s AND m.status IN ('POSTPONED','CANCELLED')""", (uid, t_id))
        cancelled = [{'id': c[0], 'home_team': c[1], 'away_team': c[2], 'status': c[3], 'points': c[4]} for c in cur.fetchall()]
    finally: close_db(conn, cur)
    return render_template('my_predictions.html', pending=pending, awaiting=awaiting, finished=finished, cancelled=cancelled, to_msk=cached_to_msk, current_filter=current_filter)