# app/routes/main.py
from datetime import datetime, timedelta
from collections import defaultdict
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, g
from app.db import get_db, close_db, get_active_tournament_id
from app.utils import utc_now, cached_to_msk, is_before_deadline, get_flag, get_club_logo
from app.config import START_DATE, MSK_OFFSET

main_bp = Blueprint('main', __name__)

@main_bp.route('/', methods=['GET', 'POST'])
def index():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    conn = get_db(); cur = conn.cursor(); now = utc_now()
    league_filter = request.args.get('league', 'all')
    start_date_str = START_DATE.strftime("%Y-%m-%dT%H:%M:%S")
    today_str = (now + timedelta(hours=MSK_OFFSET)).strftime("%Y-%m-%d")
    
    if request.method == 'POST':
        match_id = request.form.get('match_id')
        t_id = get_active_tournament_id()
        if match_id:
            h = str(request.form.get('home_goals', '0')).strip()
            a = str(request.form.get('away_goals', '0')).strip()
            if h == '' or h == 'None': h = '0'
            if a == '' or a == 'None': a = '0'
            try: home_goals = int(h); away_goals = int(a)
            except: home_goals = 0; away_goals = 0
            
            cur.execute("SELECT id, home_team, away_team, deadline, status FROM matches WHERE id = %s", (match_id,))
            m = cur.fetchone()
            if m and m[4] in ('SCHEDULED', 'TIMED') and is_before_deadline(m):
                cur.execute("SELECT 1 FROM predictions WHERE user_id = %s AND match_id = %s AND tournament_id = %s",
                            (session['user_id'], match_id, t_id))
                if cur.fetchone():
                    cur.execute("UPDATE predictions SET home_goals = %s, away_goals = %s WHERE user_id = %s AND match_id = %s AND tournament_id = %s",
                                (home_goals, away_goals, session['user_id'], match_id, t_id))
                else:
                    cur.execute("INSERT INTO predictions (user_id, match_id, tournament_id, home_goals, away_goals) VALUES (%s,%s,%s,%s,%s)",
                                (session['user_id'], match_id, t_id, home_goals, away_goals))
                flash("✅ Ставка принята", "success")
            else:
                flash("Ставки закрыты", "error")
        close_db(conn, cur)
        return redirect(url_for('main.index', league=league_filter))
    
    try:
        if league_filter == 'all':
            cur.execute("""SELECT id, home_team, away_team, kickoff_time, deadline, status, league, home_score, away_score
                FROM matches WHERE status IN ('SCHEDULED','TIMED','FINISHED') AND kickoff_time >= %s ORDER BY kickoff_time""", (start_date_str,))
        else:
            cur.execute("""SELECT id, home_team, away_team, kickoff_time, deadline, status, league, home_score, away_score
                FROM matches WHERE status IN ('SCHEDULED','TIMED','FINISHED') AND league=%s AND kickoff_time >= %s ORDER BY kickoff_time""", (league_filter, start_date_str))
        raw_matches = [{'id': m[0], 'home_team': m[1], 'away_team': m[2], 'kickoff_time': m[3], 'deadline': m[4], 'status': m[5], 'league': m[6], 'home_score': m[7], 'away_score': m[8]} for m in cur.fetchall()]
        t_id = get_active_tournament_id()
        
        # Пакетная загрузка прогнозов
        match_ids = [match['id'] for match in raw_matches]
        user_data = {}
        if match_ids:
            cur.execute("""SELECT match_id, home_goals, away_goals, points 
                           FROM predictions 
                           WHERE user_id = %s AND tournament_id = %s AND match_id = ANY(%s)""",
                        (session['user_id'], t_id, match_ids))
            user_data = {row[0]: (row[1], row[2], row[3]) for row in cur.fetchall()}
        
        matches_by_day = defaultdict(list)
        for match in raw_matches:
            clean_str = match['kickoff_time'].replace('Z', '').replace('+00:00', '').replace('-00:00', '')
            try: dt_utc = datetime.fromisoformat(clean_str)
            except: dt_utc = datetime.strptime(match['kickoff_time'], "%Y-%m-%d %H:%M:%S")
            dt_msk = dt_utc + timedelta(hours=MSK_OFFSET)
            day_key = dt_msk.strftime("%Y-%m-%d"); match['day_key'] = day_key
            match['deadline_passed'] = not is_before_deadline((match['id'], None, None, match['deadline'], None))
            match['finished'] = match['status'] == 'FINISHED'
            
            if match['id'] in user_data:
                match['pred_home'] = user_data[match['id']][0]
                match['pred_away'] = user_data[match['id']][1]
                match['my_points'] = user_data[match['id']][2] if match['finished'] else 0
            else:
                match['pred_home'] = ''; match['pred_away'] = ''; match['my_points'] = 0
            
            day_label = dt_msk.strftime("%d.%m.%Y"); matches_by_day[(day_key, day_label)].append(match)
            
        days = []
        for (day_key, day_label), day_matches in sorted(matches_by_day.items(), key=lambda x: x[0][0]):
            if day_key == today_str: day_type = 'today'
            elif day_key < today_str: day_type = 'past'
            else: day_type = 'future'
            has_open = any(not m['deadline_passed'] for m in day_matches)
            days.append({'key': day_key, 'label': day_label, 'type': day_type, 'matches': day_matches, 'count': len(day_matches), 'has_open': has_open})
        open_day = None
        for d in days:
            if d['type'] == 'today': open_day = d['key']; break
        if not open_day:
            for d in days:
                if d['type'] == 'future': open_day = d['key']; break
    finally: close_db(conn, cur)
    return render_template('index.html', days=days, open_day=open_day, to_msk=cached_to_msk, current_filter=league_filter, get_flag=get_flag, get_club_logo=get_club_logo)