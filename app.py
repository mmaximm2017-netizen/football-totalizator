# football_site/app.py
import os
import logging
from datetime import datetime, timedelta
from collections import defaultdict
import requests
import psycopg2
import psycopg2.extras
from flask import Flask, render_template, request, redirect, url_for, session, flash, g

try:
    from understatapi import UnderstatClient
    UNDERSTAT_AVAILABLE = True
except ImportError:
    UNDERSTAT_AVAILABLE = False
    print("Understat не установлен. РПЛ будет недоступна.")

API_KEY = "3c1f32333b1c4b5eacb45b01dd83170c"
LEAGUE_IDS = [2000]
INVITE_CODE = "FIFA2026"
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"
MSK_OFFSET = 3
START_DATE = datetime(2026, 5, 6)

DATABASE_URL = "postgresql://admin:o9TURy3G7gDFVJO6s04E6jtISWbpcDMM@dpg-d7sf75egkk3c73e2a6qg-a/football_ou1f"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = "fifa2026-totalizator-secret-key-dont-change"

WEEKDAYS = {
    "Monday": "Понедельник", "Tuesday": "Вторник", "Wednesday": "Среда",
    "Thursday": "Четверг", "Friday": "Пятница", "Saturday": "Суббота", "Sunday": "Воскресенье"
}

TEAM_NAMES = {
    "Spartak Moscow": "Спартак", "Dinamo Moscow": "Динамо", "CSKA Moscow": "ЦСКА",
    "Zenit St. Petersburg": "Зенит", "Lokomotiv Moscow": "Локомотив", "FC Krasnodar": "Краснодар",
    "FK Akhmat": "Ахмат", "FC Rostov": "Ростов", "Rubin Kazan": "Рубин",
    "Krylya Sovetov Samara": "Крылья Советов", "Nizhny Novgorod": "Пари НН",
    "FC Orenburg": "Оренбург", "Baltika": "Балтика", "PFC Sochi": "Сочи",
    "Dynamo Makhachkala": "Динамо Мх", "Akron": "Акрон",
    "Mexico": "Мексика", "South Africa": "ЮАР", "South Korea": "Южная Корея",
    "Czechia": "Чехия", "Canada": "Канада", "Bosnia-Herzegovina": "Босния и Герцеговина",
    "United States": "США", "Paraguay": "Парагвай", "Qatar": "Катар", "Switzerland": "Швейцария",
    "Brazil": "Бразилия", "Morocco": "Марокко", "Haiti": "Гаити", "Scotland": "Шотландия",
    "Australia": "Австралия", "Turkey": "Турция", "Germany": "Германия", "Curaçao": "Кюрасао",
    "Netherlands": "Нидерланды", "Japan": "Япония", "Ivory Coast": "Кот-д'Ивуар",
    "Ecuador": "Эквадор", "Sweden": "Швеция", "Tunisia": "Тунис", "Spain": "Испания",
    "Cape Verde Islands": "Кабо-Верде", "Belgium": "Бельгия", "Egypt": "Египет",
    "Saudi Arabia": "Саудовская Аравия", "Uruguay": "Уругвай", "Iran": "Иран",
    "New Zealand": "Новая Зеландия", "France": "Франция", "Senegal": "Сенегал",
    "Iraq": "Ирак", "Norway": "Норвегия", "Argentina": "Аргентина", "Algeria": "Алжир",
    "Austria": "Австрия", "Jordan": "Иордания", "Portugal": "Португалия", "Congo DR": "ДР Конго",
    "England": "Англия", "Croatia": "Хорватия", "Ghana": "Гана", "Panama": "Панама",
    "Uzbekistan": "Узбекистан", "Colombia": "Колумбия",
}

TEAM_FLAGS = {
    "Россия": "ru", "Бразилия": "br", "Аргентина": "ar", "Германия": "de",
    "Франция": "fr", "Испания": "es", "Англия": "gb", "Италия": "it",
    "Нидерланды": "nl", "Португалия": "pt", "Бельгия": "be", "Хорватия": "hr",
    "Уругвай": "uy", "Колумбия": "co", "Мексика": "mx", "США": "us",
    "Япония": "jp", "Южная Корея": "kr", "Сенегал": "sn", "Марокко": "ma",
    "Египет": "eg", "Саудовская Аравия": "sa", "Катар": "qa", "Австралия": "au",
    "Канада": "ca", "Швейцария": "ch", "Швеция": "se", "Норвегия": "no",
    "Турция": "tr", "Австрия": "at", "Чехия": "cz", "Иран": "ir",
    "Ирак": "iq", "Алжир": "dz", "Гана": "gh", "Панама": "pa",
    "Парагвай": "py", "Эквадор": "ec", "Тунис": "tn", "ЮАР": "za",
    "Шотландия": "gb-sct", "Гаити": "ht", "Босния и Герцеговина": "ba",
    "Кюрасао": "cw", "Кот-д'Ивуар": "ci", "Новая Зеландия": "nz",
    "Кабо-Верде": "cv", "Узбекистан": "uz", "ДР Конго": "cd", "Иордания": "jo",
}

CLUB_LOGOS = {
    "Спартак": "/static/clubs/spartak-moscow-footballlogos-org.png",
    "Динамо": "/static/clubs/dynamo-moscow-footballlogos-org.png",
    "ЦСКА": "/static/clubs/cska-moscow-footballlogos-org.png",
    "Зенит": "/static/clubs/zenit-saint-petersburg-footballlogos-org.png",
    "Локомотив": "/static/clubs/lokomotiv-moscow-footballlogos-org.png",
    "Краснодар": "/static/clubs/krasnodar-footballlogos-org.png",
    "Ахмат": "/static/clubs/akhmat-grozny-footballlogos-org.png",
    "Ростов": "/static/clubs/rostov-footballlogos-org.png",
    "Рубин": "/static/clubs/rubin-kazan-footballlogos-org.png",
    "Крылья Советов": "/static/clubs/krylia-sovetov-footballlogos-org.png",
    "Пари НН": "/static/clubs/pari-nn-footballlogos-org.png",
    "Оренбург": "/static/clubs/orenburg-footballlogos-org.png",
    "Балтика": "/static/clubs/baltika-kaliningrad-footballlogos-org.png",
    "Сочи": "/static/clubs/sochi-footballlogos-org.png",
    "Динамо Мх": "/static/clubs/dynamo-makhachkala-footballlogos-org.png",
    "Акрон": "/static/clubs/akron-togliatti-footballlogos-org.png",
}

def translate_name(name):
    return TEAM_NAMES.get(name, name)

def get_flag(name):
    translated = TEAM_NAMES.get(name, name)
    code = TEAM_FLAGS.get(translated)
    if code:
        return f'<img src="https://flagcdn.com/w40/{code}.png" width="24" height="16" style="vertical-align: middle; margin-right: 4px; border-radius: 2px;" alt="">'
    return ""

def get_club_logo(name):
    logo_url = CLUB_LOGOS.get(name)
    if logo_url:
        return f'<img src="{logo_url}" width="24" height="24" style="vertical-align: middle; border-radius: 4px;" alt="">'
    return ""

def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    return conn

def close_db(conn, cur=None):
    try:
        if cur and not cur.closed: cur.close()
    except: pass
    try:
        if conn and not conn.closed: conn.close()
    except: pass

def init_db():
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL, is_admin INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS matches (
                id SERIAL PRIMARY KEY, api_match_id TEXT UNIQUE,
                home_team TEXT, away_team TEXT, kickoff_time TEXT,
                deadline TEXT, status TEXT DEFAULT 'SCHEDULED',
                home_score INTEGER, away_score INTEGER, league TEXT DEFAULT 'other'
            );
            CREATE TABLE IF NOT EXISTS tournaments (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                is_active INTEGER DEFAULT 0,
                start_date TEXT,
                end_date TEXT
            );
            CREATE TABLE IF NOT EXISTS predictions (
                user_id INTEGER REFERENCES users(id),
                match_id INTEGER REFERENCES matches(id),
                tournament_id INTEGER REFERENCES tournaments(id) DEFAULT 1,
                home_goals INTEGER, away_goals INTEGER, points INTEGER DEFAULT 0
            );
        ''')
        cur.execute('''
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'predictions_unique') THEN
                    ALTER TABLE predictions ADD CONSTRAINT predictions_unique UNIQUE (user_id, match_id, tournament_id);
                END IF;
            END $$;
        ''')
        cur.execute("SELECT id FROM tournaments WHERE name = 'Кубок Матч-премьер'")
        if not cur.fetchone():
            cur.execute("INSERT INTO tournaments (name, is_active, start_date) VALUES ('Кубок Матч-премьер', 1, '2026-05-06')")
        cur.execute("UPDATE predictions SET tournament_id = 1 WHERE tournament_id IS NULL")
        cur.execute("SELECT id FROM users WHERE username = %s", (ADMIN_USERNAME,))
        if not cur.fetchone():
            cur.execute("INSERT INTO users (username, password, is_admin) VALUES (%s, %s, 1)",
                        (ADMIN_USERNAME, ADMIN_PASSWORD))
    finally:
        close_db(conn, cur)

def get_active_tournament_id():
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM tournaments WHERE is_active = 1")
        row = cur.fetchone()
        return row[0] if row else 1
    finally:
        close_db(conn, cur)

@app.before_request
def load_user():
    g.is_admin = False
    if 'user_id' in session:
        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute("SELECT is_admin FROM users WHERE id = %s", (session['user_id'],))
            user = cur.fetchone()
            if user and user[0] == 1: g.is_admin = True
        finally:
            close_db(conn, cur)

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session: return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session: return redirect(url_for('login'))
        conn = get_db(); cur = conn.cursor()
        try:
            cur.execute("SELECT is_admin FROM users WHERE id = %s", (session['user_id'],))
            user = cur.fetchone()
            if not user or user[0] != 1:
                flash("Доступ запрещён", "error"); return redirect(url_for('index'))
        finally: close_db(conn, cur)
        return f(*args, **kwargs)
    return decorated

def utc_now():
    from datetime import timezone
    return datetime.now(timezone.utc).replace(tzinfo=None)

def to_msk(utc_time_str):
    if not utc_time_str: return "—"
    clean_str = utc_time_str.replace('Z', '').replace('+00:00', '').replace('-00:00', '')
    try: dt_utc = datetime.fromisoformat(clean_str)
    except:
        try: dt_utc = datetime.strptime(utc_time_str, "%Y-%m-%d %H:%M:%S")
        except: dt_utc = datetime.fromisoformat(utc_time_str)
    dt_msk = dt_utc + timedelta(hours=MSK_OFFSET)
    weekday = WEEKDAYS.get(dt_msk.strftime("%A"), dt_msk.strftime("%A"))
    return dt_msk.strftime("%d.%m %H:%M МСК") + f" ({weekday})"

def parse_utc_time(utc_str):
    if not utc_str: return None
    clean_str = utc_str.replace('Z', '').replace('+00:00', '').replace('-00:00', '')
    try: return datetime.fromisoformat(clean_str)
    except:
        try: return datetime.strptime(utc_str, "%Y-%m-%d %H:%M:%S")
        except: return datetime.fromisoformat(utc_str)

def is_before_deadline(match_tuple):
    deadline_str = match_tuple[3]
    if not deadline_str: return False
    deadline = parse_utc_time(deadline_str)
    if deadline is None: return False
    return utc_now() < deadline

def calculate_points(real_home, real_away, pred_home, pred_away):
    if real_home is None or real_away is None: return 0
    real_diff = real_home - real_away; pred_diff = pred_home - pred_away
    def outcome(diff):
        if diff > 0: return 1
        elif diff == 0: return 0
        else: return -1
    real_out = outcome(real_diff); pred_out = outcome(pred_diff)
    big_margin = abs(real_diff) >= 3
    if real_home == pred_home and real_away == pred_away:
        return 11 if abs(real_diff) >= 3 else 10
    if real_out == pred_out and real_diff == pred_diff:
        return 8 if big_margin else 7
    if real_out == pred_out and abs(real_diff - pred_diff) == 1:
        return 6 if big_margin else 5
    if real_out == pred_out and abs(real_diff) >= 3 and abs(pred_diff) >= 3:
        return 4
    if abs(real_diff - pred_diff) == 1: return 2
    if real_out == pred_out: return 3
    return 0

def calculate_points_for_match(match_id):
    conn = get_db(); cur = conn.cursor()
    t_id = get_active_tournament_id()
    try:
        cur.execute("SELECT id, home_score, away_score FROM matches WHERE id = %s", (match_id,))
        match = cur.fetchone()
        if not match: return
        cur.execute("SELECT user_id, home_goals, away_goals FROM predictions WHERE match_id = %s AND tournament_id = %s", (match_id, t_id))
        for p in cur.fetchall():
            pts = calculate_points(match[1], match[2], p[1], p[2])
            cur.execute("UPDATE predictions SET points = %s WHERE user_id = %s AND match_id = %s AND tournament_id = %s", (pts, p[0], match_id, t_id))
    finally: close_db(conn, cur)

def calculate_all_points():
    conn = get_db(); cur = conn.cursor()
    t_id = get_active_tournament_id()
    try:
        cur.execute("SELECT id FROM matches WHERE status = 'FINISHED'")
        for match in cur.fetchall():
            calculate_points_for_match(match[0])
    finally: close_db(conn, cur)

def fetch_matches():
    headers = {'X-Auth-Token': API_KEY}
    all_matches = []
    for league_id in LEAGUE_IDS:
        url = f"https://api.football-data.org/v4/competitions/{league_id}/matches"
        params = {'status': 'SCHEDULED,TIMED,FINISHED,IN_PLAY,PAUSED,POSTPONED,CANCELLED'}
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            if resp.status_code == 200:
                for m in resp.json().get('matches', []):
                    m['league'] = 'wc2026'; all_matches.append(m)
        except Exception as e: logger.error(f"API error: {e}")
    return all_matches

def create_match_from_understat(match, prefix, league_tag):
    return {'id': f"{prefix}_{match['id']}", 'home_team': match['h']['title'], 'away_team': match['a']['title'],
            'utcDate': match['datetime'], 'status': 'SCHEDULED', 'score': {'fullTime': {'home': None, 'away': None}}, 'league': league_tag}

def fetch_rpl_matches():
    if not UNDERSTAT_AVAILABLE: return []
    all_matches = []
    try:
        understat = UnderstatClient()
        league_data = understat.league(league="RFPL").get_match_data(season="2025")
        for match in league_data: all_matches.append(create_match_from_understat(match, "rpl", "rpl"))
    except Exception as e: logger.error(f"Understat error: {e}")
    return all_matches

def should_update():
    """Временно всегда обновляет"""
    return True

def update_matches():
    matches_data = fetch_matches()
    try:
        rpl_matches = fetch_rpl_matches()
        if rpl_matches: matches_data.extend(rpl_matches)
    except: pass
    if not matches_data: return
    conn = get_db(); cur = conn.cursor()
    try:
        for match in matches_data:
            api_id = match['id']
            raw_home = match.get('homeTeam', {}).get('name') or match.get('home_team', 'Unknown')
            raw_away = match.get('awayTeam', {}).get('name') or match.get('away_team', 'Unknown')
            home_team = translate_name(raw_home); away_team = translate_name(raw_away)
            utc_time = match.get('utcDate', match.get('datetime', '')).replace('Z', '')
            status = match.get('status', 'SCHEDULED'); league = match.get('league', 'other')
            home_score = away_score = None
            if status == 'FINISHED':
                score = match.get('score', {}); ft = score.get('fullTime') or score.get('extraTime') or {}
                home_score = ft.get('home'); away_score = ft.get('away')
            if ' ' in utc_time: kickoff_utc = datetime.strptime(utc_time, "%Y-%m-%d %H:%M:%S")
            else: kickoff_utc = datetime.fromisoformat(utc_time)
            kickoff_msk = kickoff_utc + timedelta(hours=MSK_OFFSET)
            if league == 'wc2026':
                deadline_msk = kickoff_msk - timedelta(hours=2)
            else:
                deadline_msk = kickoff_msk.replace(hour=11, minute=0, second=0, microsecond=0)
                if deadline_msk >= kickoff_msk: deadline_msk = kickoff_msk - timedelta(hours=1)
            deadline_utc = deadline_msk - timedelta(hours=MSK_OFFSET)
            cur.execute("SELECT id FROM matches WHERE api_match_id = %s", (str(api_id),))
            if not cur.fetchone():
                cur.execute("""INSERT INTO matches (api_match_id, home_team, away_team, kickoff_time, deadline, status, home_score, away_score, league)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (str(api_id), home_team, away_team,
                    kickoff_utc.strftime("%Y-%m-%dT%H:%M:%S"), deadline_utc.strftime("%Y-%m-%dT%H:%M:%S"), status, home_score, away_score, league))
            else:
                cur.execute("""UPDATE matches SET status=%s, home_score=%s, away_score=%s, kickoff_time=%s, deadline=%s, league=%s WHERE api_match_id=%s""",
                    (status, home_score, away_score, kickoff_utc.strftime("%Y-%m-%dT%H:%M:%S"), deadline_utc.strftime("%Y-%m-%dT%H:%M:%S"), league, str(api_id)))
    finally: close_db(conn, cur)

def update_matches_safe():
    if should_update(): update_matches(); calculate_all_points()

# ---------- МАРШРУТЫ ----------
@app.route('/', methods=['GET', 'POST'])
@login_required
def index():
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
        return redirect(url_for('index', league=league_filter))
    try:
        if league_filter == 'all':
            cur.execute("""SELECT id, home_team, away_team, kickoff_time, deadline, status, league, home_score, away_score
                FROM matches WHERE status IN ('SCHEDULED','TIMED','FINISHED') AND kickoff_time >= %s ORDER BY kickoff_time""", (start_date_str,))
        else:
            cur.execute("""SELECT id, home_team, away_team, kickoff_time, deadline, status, league, home_score, away_score
                FROM matches WHERE status IN ('SCHEDULED','TIMED','FINISHED') AND league=%s AND kickoff_time >= %s ORDER BY kickoff_time""", (league_filter, start_date_str))
        raw_matches = [{'id': m[0], 'home_team': m[1], 'away_team': m[2], 'kickoff_time': m[3], 'deadline': m[4], 'status': m[5], 'league': m[6], 'home_score': m[7], 'away_score': m[8]} for m in cur.fetchall()]
        t_id = get_active_tournament_id()
        cur.execute("SELECT match_id, home_goals, away_goals FROM predictions WHERE user_id = %s AND tournament_id = %s", (session['user_id'], t_id))
        user_preds = {p[0]: (p[1], p[2]) for p in cur.fetchall()}
        matches_by_day = defaultdict(list)
        for match in raw_matches:
            clean_str = match['kickoff_time'].replace('Z', '').replace('+00:00', '').replace('-00:00', '')
            try: dt_utc = datetime.fromisoformat(clean_str)
            except: dt_utc = datetime.strptime(match['kickoff_time'], "%Y-%m-%d %H:%M:%S")
            dt_msk = dt_utc + timedelta(hours=MSK_OFFSET)
            day_key = dt_msk.strftime("%Y-%m-%d"); match['day_key'] = day_key
            match['deadline_passed'] = not is_before_deadline((match['id'], None, None, match['deadline'], None))
            match['finished'] = match['status'] == 'FINISHED'
            if match['id'] in user_preds:
                match['pred_home'] = user_preds[match['id']][0]; match['pred_away'] = user_preds[match['id']][1]
            else: match['pred_home'] = ''; match['pred_away'] = ''
            if match['finished']:
                cur.execute("SELECT points FROM predictions WHERE user_id=%s AND match_id=%s AND tournament_id=%s", (session['user_id'], match['id'], t_id))
                pts = cur.fetchone(); match['my_points'] = pts[0] if pts else 0
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
    return render_template('index.html', days=days, open_day=open_day, to_msk=to_msk, current_filter=league_filter, get_flag=get_flag, get_club_logo=get_club_logo)

@app.route('/my-predictions')
@login_required
def my_predictions():
    conn = get_db(); cur = conn.cursor(); now = utc_now(); uid = session['user_id']
    current_filter = request.args.get('filter', 'active')
    t_id = get_active_tournament_id()
    try:
        cur.execute("""SELECT m.id, m.home_team, m.away_team, p.home_goals, p.away_goals, m.kickoff_time, m.deadline
            FROM predictions p JOIN matches m ON p.match_id=m.id WHERE p.user_id=%s AND p.tournament_id=%s AND m.deadline>%s""", (uid, t_id, now.strftime("%Y-%m-%dT%H:%M:%S")))
        pending = [{'id': p[0], 'home_team': p[1], 'away_team': p[2], 'home_goals': p[3], 'away_goals': p[4], 'kickoff_time': p[5], 'deadline': p[6]} for p in cur.fetchall()]
        cur.execute("""SELECT m.id, m.home_team, m.away_team, p.home_goals, p.away_goals
            FROM predictions p JOIN matches m ON p.match_id=m.id WHERE p.user_id=%s AND p.tournament_id=%s AND m.deadline<=%s AND m.status NOT IN ('FINISHED','POSTPONED','CANCELLED')""", (uid, t_id, now.strftime("%Y-%m-%dT%H:%M:%S")))
        awaiting = [{'id': a[0], 'home_team': a[1], 'away_team': a[2], 'home_goals': a[3], 'away_goals': a[4]} for a in cur.fetchall()]
        cur.execute("""SELECT m.id, m.home_team, m.away_team, m.home_score, m.away_score, p.home_goals, p.away_goals, p.points
            FROM predictions p JOIN matches m ON p.match_id=m.id WHERE p.user_id=%s AND p.tournament_id=%s AND m.status='FINISHED'""", (uid, t_id))
        finished = [{'id': f[0], 'home_team': f[1], 'away_team': f[2], 'home_score': f[3], 'away_score': f[4], 'home_goals': f[5], 'away_goals': f[6], 'points': f[7]} for f in cur.fetchall()]
        cur.execute("""SELECT m.id, m.home_team, m.away_team, m.status, p.points
            FROM predictions p JOIN matches m ON p.match_id=m.id WHERE p.user_id=%s AND p.tournament_id=%s AND m.status IN ('POSTPONED','CANCELLED')""", (uid, t_id))
        cancelled = [{'id': c[0], 'home_team': c[1], 'away_team': c[2], 'status': c[3], 'points': c[4]} for c in cur.fetchall()]
    finally: close_db(conn, cur)
    return render_template('my_predictions.html', pending=pending, awaiting=awaiting, finished=finished, cancelled=cancelled, to_msk=to_msk, current_filter=current_filter)

@app.route('/profile')
@login_required
def profile():
    """Страница профиля игрока (по умолчанию — свой)"""
    username = request.args.get('username')
    if username:
        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute("SELECT id FROM users WHERE username = %s", (username,))
            user_row = cur.fetchone()
            if not user_row:
                flash("Игрок не найден", "error")
                return redirect(url_for('table'))
            uid = user_row[0]
        finally:
            close_db(conn, cur)
    else:
        uid = session['user_id']
        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute("SELECT username FROM users WHERE id = %s", (uid,))
            username = cur.fetchone()[0]
        finally:
            close_db(conn, cur)
    
    t_id = get_active_tournament_id()
    conn = get_db()
    cur = conn.cursor()
    try:
        # Текущее место игрока
        cur.execute("""
            WITH ranked AS (
                SELECT u.id, u.username,
                       COALESCE(SUM(p.points), 0) as total,
                       RANK() OVER (ORDER BY COALESCE(SUM(p.points), 0) DESC,
                                             COUNT(CASE WHEN p.points >= 10 THEN 1 END) DESC,
                                             COUNT(CASE WHEN p.points IN (7,8) THEN 1 END) DESC,
                                             COUNT(CASE WHEN p.points BETWEEN 3 AND 6 THEN 1 END) DESC) as place
                FROM users u
                LEFT JOIN predictions p ON u.id = p.user_id AND p.tournament_id = %s
                WHERE u.is_admin = 0
                GROUP BY u.id
            )
            SELECT place, (SELECT COUNT(*) FROM users WHERE is_admin = 0) as total_players
            FROM ranked
            WHERE id = %s
        """, (t_id, uid))
        rank_row = cur.fetchone()
        current_place = rank_row[0] if rank_row else None
        total_players = rank_row[1] if rank_row else 0
        
        # Основная статистика
        cur.execute("""
            SELECT 
                COUNT(*) as total_bets,
                SUM(CASE WHEN p.points >= 10 THEN 1 ELSE 0 END) as exact_scores,
                SUM(CASE WHEN p.points >= 7 AND p.points < 10 THEN 1 ELSE 0 END) as exact_diffs,
                SUM(CASE WHEN p.points >= 3 AND p.points < 7 THEN 1 ELSE 0 END) as outcomes,
                SUM(CASE WHEN p.points = 2 THEN 1 ELSE 0 END) as close_misses,
                SUM(CASE WHEN p.points = 0 THEN 1 ELSE 0 END) as misses,
                ROUND(AVG(p.points), 1) as avg_points,
                SUM(p.points) as total_points
            FROM predictions p
            JOIN matches m ON p.match_id = m.id
            WHERE p.user_id = %s AND p.tournament_id = %s AND m.status = 'FINISHED'
        """, (uid, t_id))
        row = cur.fetchone()
        stats = {
            'total_bets': row[0] or 0,
            'exact_scores': row[1] or 0,
            'exact_diffs': row[2] or 0,
            'outcomes': row[3] or 0,
            'close_misses': row[4] or 0,
            'misses': row[5] or 0,
            'avg_points': float(row[6]) if row[6] else 0,
            'total_points': row[7] or 0,
        }
        # Любимый счёт
        cur.execute("""
            SELECT home_goals, away_goals, COUNT(*) as cnt
            FROM predictions
            WHERE user_id = %s AND tournament_id = %s
            GROUP BY home_goals, away_goals
            ORDER BY cnt DESC
            LIMIT 3
        """, (uid, t_id))
        favorites = [{'home': r[0], 'away': r[1], 'count': r[2]} for r in cur.fetchall()]
        # Последние 10 матчей
        cur.execute("""
            SELECT m.home_team, m.away_team, p.home_goals, p.away_goals, m.home_score, m.away_score, p.points
            FROM predictions p
            JOIN matches m ON p.match_id = m.id
            WHERE p.user_id = %s AND p.tournament_id = %s AND m.status = 'FINISHED'
            ORDER BY m.kickoff_time DESC
            LIMIT 10
        """, (uid, t_id))
        recent = [{'home_team': r[0], 'away_team': r[1], 'home_goals': r[2], 'away_goals': r[3],
                    'home_score': r[4], 'away_score': r[5], 'points': r[6]} for r in cur.fetchall()]
    finally:
        close_db(conn, cur)
    return render_template('profile.html', username=username, stats=stats, favorites=favorites, recent=recent,
                           current_place=current_place, total_players=total_players,
                           get_flag=get_flag, get_club_logo=get_club_logo)

@app.route('/match/<int:match_id>/predictions')
@login_required
def match_predictions(match_id):
    conn = get_db(); cur = conn.cursor()
    t_id = get_active_tournament_id()
    try:
        cur.execute("SELECT id, home_team, away_team, kickoff_time, deadline, status, home_score, away_score FROM matches WHERE id = %s", (match_id,))
        m = cur.fetchone()
        if not m: flash("Матч не найден", "error"); return redirect(url_for('index'))
        match = {'id': m[0], 'home_team': m[1], 'away_team': m[2], 'kickoff_time': m[3], 'deadline': m[4], 'status': m[5], 'home_score': m[6], 'away_score': m[7]}
        deadline_passed = not is_before_deadline((m[0], m[1], m[2], m[4], m[5]))
        if deadline_passed:
            cur.execute("""SELECT u.username, p.home_goals, p.away_goals, p.points
                FROM predictions p JOIN users u ON p.user_id=u.id WHERE p.match_id=%s AND p.tournament_id=%s ORDER BY u.username""", (match_id, t_id))
            predictions = [{'username': p[0], 'home_goals': p[1], 'away_goals': p[2], 'points': p[3]} for p in cur.fetchall()]
            return render_template('match_predictions.html', match=match, predictions=predictions, to_msk=to_msk)
        else: flash("Ставки будут доступны после дедлайна", "error"); return redirect(url_for('index'))
    finally: close_db(conn, cur)

@app.route('/table')
@login_required
def table():
    conn = get_db()
    cur = conn.cursor()
    try:
        # Получаем список турниров для вкладок
        cur.execute("SELECT id, name, is_active FROM tournaments ORDER BY is_active DESC, id DESC")
        tournaments = [{'id': r[0], 'name': r[1], 'is_active': r[2]} for r in cur.fetchall()]

        # Определяем выбранный турнир
        tid = request.args.get('tid', type=int)
        if not tid:
            active = next((t for t in tournaments if t['is_active']), None)
            tid = active['id'] if active else (tournaments[0]['id'] if tournaments else 1)

        cur.execute("SELECT name FROM tournaments WHERE id = %s", (tid,))
        selected = cur.fetchone()
        selected_name = selected[0] if selected else 'Турнир'

        # Запрос с дополнительными критериями
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

    # Формируем список с учётом одинаковых мест и флагом shared
    table_data = []
    prev_pts = None
    prev_exact = None
    prev_diff = None
    prev_outcome = None
    counter = 0  # для нумерации строк с одинаковыми показателями

    for i, row in enumerate(rows):
        username, pts, exact, diff, outcome = row
        pts = int(pts) if pts else 0
        exact = int(exact) if exact else 0
        diff = int(diff) if diff else 0
        outcome = int(outcome) if outcome else 0

        # Определяем место и флаг shared
        if i == 0:
            current_place = 1
            shared = False
        else:
            # Если все критерии совпадают с предыдущей строкой
            if pts == prev_pts and exact == prev_exact and diff == prev_diff and outcome == prev_outcome:
                shared = True
            else:
                current_place = i + 1
                shared = False

        table_data.append({
            'place': current_place if not shared else current_place,  # место остаётся предыдущим при дележе
            'username': username,
            'points': pts,
            'shared': shared
        })

        # Обновляем предыдущие значения для следующей итерации
        prev_pts, prev_exact, prev_diff, prev_outcome = pts, exact, diff, outcome

    # Исправляем места при дележе: у первой строки с дележом место берётся от предыдущей итерации
    # (уже сделано)

    return render_template('table.html',
                           table=table_data,
                           tournaments=tournaments,
                           selected_tid=tid,
                           selected_name=selected_name)

@app.route('/admin', methods=['GET', 'POST'])
@admin_required
def admin():
    conn = get_db(); cur = conn.cursor()
    try:
        if request.method == 'POST':
            action = request.form.get('action')
            if action == 'update_matches': update_matches_safe(); flash("Обновлено", "success")
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
                match_id = request.form['match_id']; home_score = request.form['home_score']; away_score = request.form['away_score']
                try: home_score = int(home_score); away_score = int(away_score)
                except: flash("Результат должен быть числами", "error"); return redirect(url_for('admin'))
                cur.execute("UPDATE matches SET status='FINISHED', home_score=%s, away_score=%s WHERE id=%s", (home_score, away_score, match_id))
                calculate_points_for_match(match_id); flash("Результат внесён", "success")
        start_date_str = START_DATE.strftime("%Y-%m-%dT%H:%M:%S")
        # Сгруппированные матчи для внесения результата
        cur.execute("""SELECT id, home_team, away_team, kickoff_time, status FROM matches
            WHERE status IN ('SCHEDULED','TIMED') AND kickoff_time >= %s ORDER BY kickoff_time""", (start_date_str,))
        raw_free = cur.fetchall()
        free_by_day = defaultdict(list)
        for m in raw_free:
            day = m[3][:10] if m[3] else '???'
            free_by_day[day].append({'id': m[0], 'home_team': m[1], 'away_team': m[2], 'kickoff_time': m[3], 'status': m[4]})
        free_days = [{'date': d, 'matches': free_by_day[d]} for d in sorted(free_by_day.keys())]
        # Сгруппированные завершённые матчи для исправления результата
        cur.execute("""SELECT id, home_team, away_team, kickoff_time, status FROM matches
            WHERE status = 'FINISHED' AND kickoff_time >= %s ORDER BY kickoff_time""", (start_date_str,))
        raw_finished = cur.fetchall()
        fin_by_day = defaultdict(list)
        for m in raw_finished:
            day = m[3][:10] if m[3] else '???'
            fin_by_day[day].append({'id': m[0], 'home_team': m[1], 'away_team': m[2], 'kickoff_time': m[3], 'status': m[4]})
        finished_days = [{'date': d, 'matches': fin_by_day[d]} for d in sorted(fin_by_day.keys())]
        # Все матчи для удаления/редактирования
        cur.execute("""SELECT id, home_team, away_team, kickoff_time, status FROM matches
            WHERE kickoff_time >= %s ORDER BY kickoff_time""", (start_date_str,))
        all_matches = [{'id': m[0], 'home_team': m[1], 'away_team': m[2], 'kickoff_time': m[3], 'status': m[4]} for m in cur.fetchall()]
        cur.execute("""SELECT id, home_team, away_team, kickoff_time, status FROM matches
            WHERE (api_match_id IS NULL OR api_match_id = '') AND kickoff_time >= %s ORDER BY kickoff_time""", (start_date_str,))
        manual_matches = [{'id': m[0], 'home_team': m[1], 'away_team': m[2], 'kickoff_time': m[3], 'status': m[4]} for m in cur.fetchall()]
        cur.execute("SELECT id, username FROM users")
        users = [{'id': u[0], 'username': u[1]} for u in cur.fetchall()]
    finally: close_db(conn, cur)
    return render_template('admin.html', free_days=free_days, finished_days=finished_days, all_matches=all_matches, manual_matches=manual_matches, users=users)

@app.route('/admin/translate', methods=['POST'])
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
    return redirect(url_for('admin'))

@app.route('/admin/fix_result', methods=['POST'])
@admin_required
def admin_fix_result():
    match_id = request.form.get('match_id'); home_score = request.form.get('home_score'); away_score = request.form.get('away_score')
    try: home_score = int(home_score); away_score = int(away_score)
    except: flash("Результат должен быть числами", "error"); return redirect(url_for('admin'))
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("UPDATE matches SET home_score=%s, away_score=%s WHERE id=%s", (home_score, away_score, match_id))
        calculate_points_for_match(match_id)
        flash(f"Результат матча #{match_id} обновлён: {home_score}:{away_score}", "success")
    finally: close_db(conn, cur)
    return redirect(url_for('admin'))

@app.route('/admin/edit_match', methods=['POST'])
@admin_required
def admin_edit_match():
    match_id = request.form.get('match_id'); home_team = request.form.get('home_team'); away_team = request.form.get('away_team')
    if match_id and home_team and away_team:
        conn = get_db(); cur = conn.cursor()
        try: cur.execute("UPDATE matches SET home_team=%s, away_team=%s WHERE id=%s", (home_team, away_team, match_id)); flash(f"Матч #{match_id} обновлён", "success")
        finally: close_db(conn, cur)
    return redirect(url_for('admin'))

@app.route('/admin/delete_match', methods=['POST'])
@admin_required
def admin_delete_match():
    match_id = request.form.get('match_id')
    if match_id:
        conn = get_db(); cur = conn.cursor()
        try: cur.execute("DELETE FROM predictions WHERE match_id=%s", (match_id,)); cur.execute("DELETE FROM matches WHERE id=%s", (match_id,)); flash(f"Матч #{match_id} удалён", "success")
        finally: close_db(conn, cur)
    return redirect(url_for('admin'))

@app.route('/admin/new_tournament', methods=['POST'])
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
    return redirect(url_for('admin'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        conn = get_db(); cur = conn.cursor()
        try: cur.execute("SELECT id FROM users WHERE username=%s AND password=%s", (request.form['username'], request.form['password'])); user = cur.fetchone()
        finally: close_db(conn, cur)
        if user: session['user_id'] = user[0]; session.permanent = True; app.permanent_session_lifetime = timedelta(days=7); return redirect(url_for('index'))
        flash("Неверное имя или пароль", "error")
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        if request.form['invite_code'] != INVITE_CODE: flash("Неверный инвайт-код", "error"); return redirect(url_for('register'))
        conn = get_db(); cur = conn.cursor()
        try: cur.execute("INSERT INTO users (username, password) VALUES (%s,%s)", (request.form['username'], request.form['password'])); flash("Регистрация успешна", "success"); return redirect(url_for('login'))
        except psycopg2.IntegrityError: flash("Такой пользователь уже существует", "error")
        finally: close_db(conn, cur)
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('login'))

if __name__ == '__main__':
    init_db()
    update_matches_safe()
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)