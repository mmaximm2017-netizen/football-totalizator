# app/routes/admin.py
from datetime import datetime, timedelta
from collections import defaultdict
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from app.db import get_db, close_db
from app.utils import translate_name
from app.config import START_DATE, MSK_OFFSET

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('auth.login'))
        conn = get_db(); cur = conn.cursor()
        try:
            cur.execute("SELECT is_admin FROM users WHERE id = %s", (session['user_id'],))
            user = cur.fetchone()
            if not user or user[0] != 1:
                flash("Доступ запрещён", "error")
                return redirect(url_for('main.index'))
        finally: close_db(conn, cur)
        return f(*args, **kwargs)
    return decorated

@admin_bp.route('/', methods=['GET', 'POST'])
@admin_required
def admin():
    conn = get_db(); cur = conn.cursor()
    try:
        if request.method == 'POST':
            action = request.form.get('action')
            if action == 'update_matches':
                from app.services.match_service import update_matches
                from app.services.point_service import calculate_all_points
                update_matches()
                calculate_all_points()
                flash("Обновлено", "success")
                close_db(conn, cur)
                return redirect(url_for('admin.admin'))
            elif action == 'add_match':
                home = request.form['home_team']; away = request.form['away_team']; league = request.form.get('league', 'other')
                try:
                    match_date = request.form['match_date']; match_time = request.form['match_time']
                    dt_msk = datetime.strptime(f"{match_date} {match_time}", "%Y-%m-%d %H:%M")
                    utc = dt_msk - timedelta(hours=MSK_OFFSET)
                    deadline_msk = dt_msk.replace(hour=11, minute=0)
                    if deadline_msk >= dt_msk: deadline_msk = dt_msk - timedelta(hours=1)
                    deadline_utc = deadline_msk - timedelta(hours=MSK_OFFSET)
                    cur.execute("""INSERT INTO matches (home_team, away_team, kickoff_time, deadline, status, league)
                        VALUES (%s,%s,%s,%s,'SCHEDULED',%s)""", (home, away, utc.strftime("%Y-%m-%dT%H:%M:%S"), deadline_utc.strftime("%Y-%m-%dT%H:%M:%S"), league))
                    flash(f"Матч {home} – {away} добавлен", "success")
                except Exception as e: flash(f"Ошибка: {e}", "error")
            elif action == 'set_result':
                match_id = request.form['match_id']
                home_score = request.form['home_score']
                away_score = request.form['away_score']
                try:
                    home_score = int(home_score)
                    away_score = int(away_score)
                except:
                    flash("Результат должен быть числами", "error")
                    close_db(conn, cur)
                    return redirect(url_for('admin.admin'))
                
                cur.execute(
                    "UPDATE matches SET status='FINISHED', home_score=%s, away_score=%s WHERE id=%s",
                    (home_score, away_score, match_id)
                )
                
                from app.models.scoring import calculate_points
                cur.execute("SELECT user_id, home_goals, away_goals FROM predictions WHERE match_id = %s AND tournament_id = 1", (match_id,))
                for p in cur.fetchall():
                    pts = calculate_points(home_score, away_score, p[1], p[2])
                    cur.execute("UPDATE predictions SET points = %s WHERE user_id = %s AND match_id = %s AND tournament_id = 1",
                                (pts, p[0], match_id))
                
                flash("Результат внесён, очки пересчитаны", "success")
                close_db(conn, cur)
                return redirect(url_for('admin.admin'))
        
        start_date_str = START_DATE.strftime("%Y-%m-%dT%H:%M:%S")
        
        cur.execute("""SELECT id, home_team, away_team, kickoff_time, status FROM matches
            WHERE status IN ('SCHEDULED','TIMED') AND kickoff_time >= %s ORDER BY kickoff_time""", (start_date_str,))
        raw_free = cur.fetchall()
        free_by_day = defaultdict(list)
        for m in raw_free:
            day = m[3][:10] if m[3] else '???'
            free_by_day[day].append({'id': m[0], 'home_team': m[1], 'away_team': m[2], 'kickoff_time': m[3], 'status': m[4]})
        free_days = [{'date': d, 'matches': free_by_day[d]} for d in sorted(free_by_day.keys())]
        
        cur.execute("""SELECT id, home_team, away_team, kickoff_time, status FROM matches
            WHERE status = 'FINISHED' AND kickoff_time >= %s ORDER BY kickoff_time""", (start_date_str,))
        raw_finished = cur.fetchall()
        fin_by_day = defaultdict(list)
        for m in raw_finished:
            day = m[3][:10] if m[3] else '???'
            fin_by_day[day].append({'id': m[0], 'home_team': m[1], 'away_team': m[2], 'kickoff_time': m[3], 'status': m[4]})
        finished_days = [{'date': d, 'matches': fin_by_day[d]} for d in sorted(fin_by_day.keys())]
        
        cur.execute("""SELECT id, home_team, away_team, kickoff_time, status FROM matches
            WHERE kickoff_time >= %s ORDER BY kickoff_time""", (start_date_str,))
        all_matches = [{'id': m[0], 'home_team': m[1], 'away_team': m[2], 'kickoff_time': m[3], 'status': m[4]} for m in cur.fetchall()]
        
        cur.execute("""SELECT id, home_team, away_team, kickoff_time, status FROM matches
            WHERE (api_match_id IS NULL OR api_match_id = '') AND kickoff_time >= %s ORDER BY kickoff_time""", (start_date_str,))
        manual_matches = [{'id': m[0], 'home_team': m[1], 'away_team': m[2], 'kickoff_time': m[3], 'status': m[4]} for m in cur.fetchall()]
        
        cur.execute("SELECT id, username FROM users")
        users = [{'id': u[0], 'username': u[1]} for u in cur.fetchall()]
    finally:
        close_db(conn, cur)
    return render_template('admin.html', free_days=free_days, finished_days=finished_days, all_matches=all_matches, manual_matches=manual_matches, users=users)

@admin_bp.route('/debug_match/<int:match_id>')
@admin_required
def debug_match(match_id):
    conn = get_db()
    cur = conn.cursor()
    try:
        from app.models.scoring import calculate_points
        
        cur.execute("SELECT id, home_score, away_score FROM matches WHERE id = %s", (match_id,))
        match = cur.fetchone()
        
        cur.execute("UPDATE predictions SET points = 0 WHERE match_id = %s", (match_id,))
        
        cur.execute("""SELECT user_id, home_goals, away_goals FROM predictions WHERE match_id = %s""", (match_id,))
        preds = cur.fetchall()
        
        updated = 0
        for p in preds:
            pts = calculate_points(match[1], match[2], p[1], p[2])
            cur.execute("UPDATE predictions SET points = %s WHERE user_id = %s AND match_id = %s",
                        (pts, p[0], match_id))
            updated += 1
        
        cur.execute("""SELECT u.username, p.home_goals, p.away_goals, p.points
            FROM predictions p JOIN users u ON p.user_id = u.id
            WHERE p.match_id = %s""", (match_id,))
        preds = cur.fetchall()
        
        result = f"<h3>Матч #{match[0]}: Счёт {match[1]}:{match[2]} (обновлено {updated} записей)</h3>"
        result += "<table border='1'><tr><th>Игрок</th><th>Прогноз</th><th>Очки</th></tr>"
        for p in preds:
            result += f"<tr><td>{p[0]}</td><td>{p[1]}:{p[2]}</td><td>{p[3]}</td></tr>"
        result += "</table>"
        return result
    finally:
        close_db(conn, cur)

@admin_bp.route('/recalc_all')
@admin_required
def recalc_all():
    conn = get_db()
    cur = conn.cursor()
    try:
        from app.models.scoring import calculate_points
        
        cur.execute("SELECT id, home_score, away_score FROM matches WHERE status = 'FINISHED'")
        matches = cur.fetchall()
        
        total_updated = 0
        for match in matches:
            cur.execute("UPDATE predictions SET points = 0 WHERE match_id = %s", (match[0],))
            cur.execute("SELECT user_id, home_goals, away_goals FROM predictions WHERE match_id = %s", (match[0],))
            for p in cur.fetchall():
                pts = calculate_points(match[1], match[2], p[1], p[2])
                cur.execute("UPDATE predictions SET points = %s WHERE user_id = %s AND match_id = %s",
                            (pts, p[0], match[0]))
                total_updated += 1
        
        flash(f"Все очки пересчитаны! Обновлено {total_updated} записей в {len(matches)} матчах.", "success")
    finally:
        close_db(conn, cur)
    return redirect(url_for('admin.admin'))

@admin_bp.route('/translate', methods=['POST'])
@admin_required
def admin_translate():
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("SELECT id, home_team, away_team FROM matches"); matches = cur.fetchall(); updated = 0
        for m in matches:
            new_home = translate_name(m[1]); new_away = translate_name(m[2])
            if new_home != m[1] or new_away != m[2]:
                cur.execute("UPDATE matches SET home_team=%s, away_team=%s WHERE id=%s", (new_home, new_away, m[0])); updated += 1
        flash(f"Переведено {updated} матчей из {len(matches)}", "success")
    finally: close_db(conn, cur)
    return redirect(url_for('admin.admin'))

@admin_bp.route('/fix_result', methods=['POST'])
@admin_required
def admin_fix_result():
    match_id = request.form.get('match_id')
    home_score = request.form.get('home_score')
    away_score = request.form.get('away_score')
    try:
        home_score = int(home_score)
        away_score = int(away_score)
    except:
        flash("Результат должен быть числами", "error")
        return redirect(url_for('admin.admin'))
    
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE matches SET home_score=%s, away_score=%s WHERE id=%s",
                    (home_score, away_score, match_id))
        
        from app.models.scoring import calculate_points
        cur.execute("SELECT user_id, home_goals, away_goals FROM predictions WHERE match_id = %s", (match_id,))
        for p in cur.fetchall():
            pts = calculate_points(home_score, away_score, p[1], p[2])
            cur.execute("UPDATE predictions SET points = %s WHERE user_id = %s AND match_id = %s",
                        (pts, p[0], match_id))
        
        flash(f"Результат матча #{match_id} обновлён: {home_score}:{away_score}", "success")
    finally:
        close_db(conn, cur)
    return redirect(url_for('admin.admin'))

@admin_bp.route('/edit_match', methods=['POST'])
@admin_required
def admin_edit_match():
    match_id = request.form.get('match_id'); home_team = request.form.get('home_team'); away_team = request.form.get('away_team')
    if match_id and home_team and away_team:
        conn = get_db(); cur = conn.cursor()
        try: cur.execute("UPDATE matches SET home_team=%s, away_team=%s WHERE id=%s", (home_team, away_team, match_id)); flash(f"Матч #{match_id} обновлён", "success")
        finally: close_db(conn, cur)
    return redirect(url_for('admin.admin'))

@admin_bp.route('/delete_match', methods=['POST'])
@admin_required
def admin_delete_match():
    match_id = request.form.get('match_id')
    if match_id:
        conn = get_db(); cur = conn.cursor()
        try: cur.execute("DELETE FROM predictions WHERE match_id=%s", (match_id,)); cur.execute("DELETE FROM matches WHERE id=%s", (match_id,)); flash(f"Матч #{match_id} удалён", "success")
        finally: close_db(conn, cur)
    return redirect(url_for('admin.admin'))

@admin_bp.route('/new_tournament', methods=['POST'])
@admin_required
def admin_new_tournament():
    name = request.form.get('name', 'Новый турнир')
    start_date = request.form.get('start_date', datetime.now().strftime('%Y-%m-%d'))
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("UPDATE tournaments SET is_active = 0 WHERE is_active = 1")
        cur.execute("INSERT INTO tournaments (name, is_active, start_date) VALUES (%s, 1, %s)", (name, start_date))
        flash(f"Новый турнир «{name}» создан! Таблица обнулена.", "success")
    finally: close_db(conn, cur)
    return redirect(url_for('admin.admin'))