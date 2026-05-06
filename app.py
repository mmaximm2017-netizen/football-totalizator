# football_site/app.py
import os
import logging
from datetime import datetime, timedelta
from collections import defaultdict
import requests
import psycopg2
import psycopg2.extras
from flask import Flask, render_template, request, redirect, url_for, session, flash, g

# Пробуем импортировать Understat
try:
    from understatapi import UnderstatClient
    UNDERSTAT_AVAILABLE = True
except ImportError:
    UNDERSTAT_AVAILABLE = False
    print("Understat не установлен. РПЛ будет недоступна.")

# ---------- НАСТРОЙКИ ----------
API_KEY = "3c1f32333b1c4b5eacb45b01dd83170c"
LEAGUE_IDS = [2000]                        # ЧМ-2026
INVITE_CODE = "FIFA2026"
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"
MSK_OFFSET = 3
# Дата, с которой начинаем показывать матчи
START_DATE = datetime(2026, 5, 6)

# Подключение к Render PostgreSQL
DATABASE_URL = "postgresql://admin:o9TURy3G7gDFVJO6s04E6jtISWbpcDMM@dpg-d7sf75egkk3c73e2a6qg-a/football_ou1f"

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = "fifa2026-totalizator-secret-key-dont-change"

# ---------- РАБОТА С БД ----------
def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    return conn

def close_db(conn, cur=None):
    """Закрывает курсор и соединение"""
    try:
        if cur and not cur.closed:
            cur.close()
    except:
        pass
    try:
        if conn and not conn.closed:
            conn.close()
    except:
        pass

def init_db():
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                is_admin INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS matches (
                id SERIAL PRIMARY KEY,
                api_match_id TEXT UNIQUE,
                home_team TEXT,
                away_team TEXT,
                kickoff_time TEXT,
                deadline TEXT,
                status TEXT DEFAULT 'SCHEDULED',
                home_score INTEGER,
                away_score INTEGER,
                league TEXT DEFAULT 'other'
            );
            CREATE TABLE IF NOT EXISTS predictions (
                user_id INTEGER REFERENCES users(id),
                match_id INTEGER REFERENCES matches(id),
                home_goals INTEGER,
                away_goals INTEGER,
                points INTEGER DEFAULT 0,
                UNIQUE(user_id, match_id)
            );
        ''')
        cur.execute("SELECT id FROM users WHERE username = %s", (ADMIN_USERNAME,))
        if not cur.fetchone():
            cur.execute("INSERT INTO users (username, password, is_admin) VALUES (%s, %s, 1)",
                        (ADMIN_USERNAME, ADMIN_PASSWORD))
    finally:
        close_db(conn, cur)

# ---------- ЗАГРУЗКА ПОЛЬЗОВАТЕЛЯ ----------
@app.before_request
def load_user():
    g.is_admin = False
    if 'user_id' in session:
        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute("SELECT is_admin FROM users WHERE id = %s", (session['user_id'],))
            user = cur.fetchone()
            if user and user[0] == 1:
                g.is_admin = True
        finally:
            close_db(conn, cur)

# ---------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ----------
def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute("SELECT is_admin FROM users WHERE id = %s", (session['user_id'],))
            user = cur.fetchone()
            if not user or user[0] != 1:
                flash("Доступ запрещён", "error")
                return redirect(url_for('index'))
        finally:
            close_db(conn, cur)
        return f(*args, **kwargs)
    return decorated

def utc_now():
    from datetime import timezone
    return datetime.now(timezone.utc).replace(tzinfo=None)

def to_msk(utc_time_str):
    if not utc_time_str:
        return "—"
    clean_str = utc_time_str.replace('Z', '').replace('+00:00', '').replace('-00:00', '')
    try:
        dt_utc = datetime.fromisoformat(clean_str)
    except:
        try:
            dt_utc = datetime.strptime(utc_time_str, "%Y-%m-%d %H:%M:%S")
        except:
            dt_utc = datetime.fromisoformat(utc_time_str)
    dt_msk = dt_utc + timedelta(hours=MSK_OFFSET)
    return dt_msk.strftime("%d.%m %H:%M МСК")

def parse_utc_time(utc_str):
    if not utc_str:
        return None
    clean_str = utc_str.replace('Z', '').replace('+00:00', '').replace('-00:00', '')
    try:
        return datetime.fromisoformat(clean_str)
    except:
        try:
            return datetime.strptime(utc_str, "%Y-%m-%d %H:%M:%S")
        except:
            return datetime.fromisoformat(utc_str)

def is_before_deadline(match_tuple):
    """Проверяет, прошёл ли дедлайн. 
    match_tuple — кортеж из БД: (id, home_team, away_team, deadline, status)"""
    deadline_str = match_tuple[3]  # deadline — 4-й элемент (индекс 3)
    if not deadline_str:
        return False
    deadline = parse_utc_time(deadline_str)
    if deadline is None:
        return False
    return utc_now() < deadline

def calculate_points(real_home, real_away, pred_home, pred_away):
    if real_home is None or real_away is None:
        return 0
    real_diff = real_home - real_away
    pred_diff = pred_home - pred_away

    def outcome(diff):
        if diff > 0: return 1
        elif diff == 0: return 0
        else: return -1

    real_out = outcome(real_diff)
    pred_out = outcome(pred_diff)

    big_margin = abs(real_diff) >= 3

    if real_home == pred_home and real_away == pred_away:
        if abs(real_diff) >= 3:
            return 11
        else:
            return 10

    if real_out == pred_out and real_diff == pred_diff:
        return 8 if big_margin else 7

    if real_out == pred_out and abs(real_diff - pred_diff) == 1:
        return 6 if big_margin else 5

    if real_out == pred_out and abs(real_diff) >= 3 and abs(pred_diff) >= 3:
        return 4

    if abs(real_diff - pred_diff) == 1:
        return 2

    if real_out == pred_out:
        return 3

    return 0

def calculate_points_for_match(match_id):
    """Пересчитывает очки только для одного матча"""
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, home_score, away_score FROM matches WHERE id = %s", (match_id,))
        match = cur.fetchone()
        if not match:
            return
        cur.execute(
            "SELECT user_id, home_goals, away_goals FROM predictions WHERE match_id = %s",
            (match_id,)
        )
        preds = cur.fetchall()
        for p in preds:
            pts = calculate_points(match[1], match[2], p[1], p[2])
            cur.execute(
                "UPDATE predictions SET points = %s WHERE user_id = %s AND match_id = %s",
                (pts, p[0], match_id)
            )
    finally:
        close_db(conn, cur)

def calculate_all_points():
    """Пересчёт очков для всех завершённых матчей (только при ручном запуске)"""
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM matches WHERE status = 'FINISHED'")
        finished = cur.fetchall()
        for match in finished:
            calculate_points_for_match(match[0])
    finally:
        close_db(conn, cur)

# ---------- РАБОТА С API ----------
def fetch_matches():
    """ЧМ-2026 из football-data.org"""
    headers = {'X-Auth-Token': API_KEY}
    all_matches = []
    for league_id in LEAGUE_IDS:
        url = f"https://api.football-data.org/v4/competitions/{league_id}/matches"
        params = {'status': 'SCHEDULED,TIMED,FINISHED,IN_PLAY,PAUSED,POSTPONED,CANCELLED'}
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                for m in data.get('matches', []):
                    m['league'] = 'wc2026'
                    all_matches.append(m)
            else:
                logger.error(f"football-data.org API error: {resp.status_code}")
        except Exception as e:
            logger.error(f"football-data.org API request failed: {e}")
    return all_matches

def create_match_from_understat(match, prefix, league_tag):
    """Преобразует матч из Understat в стандартный формат"""
    return {
        'id': f"{prefix}_{match['id']}",
        'home_team': match['h']['title'],
        'away_team': match['a']['title'],
        'utcDate': match['datetime'],
        'status': 'SCHEDULED' if match.get('status', '').upper() != 'FINISHED' else 'FINISHED',
        'score': {
            'fullTime': {
                'home': int(match['goals']['h']) if match.get('goals') and match['goals']['h'] is not None else None,
                'away': int(match['goals']['a']) if match.get('goals') and match['goals']['a'] is not None else None
            }
        },
        'league': league_tag
    }

def fetch_rpl_matches():
    """РПЛ через Understat"""
    if not UNDERSTAT_AVAILABLE:
        logger.info(">>> Understat не установлен — РПЛ пропущена")
        return []
    
    logger.info(">>> Пытаемся загрузить РПЛ через Understat...")
    all_matches = []
    try:
        understat = UnderstatClient()
        league_data = understat.league(league="RFPL").get_match_data(season="2025")
        logger.info(f">>> РПЛ: Understat вернул {len(league_data)} матчей")
        for match in league_data:
            all_matches.append(create_match_from_understat(match, "rpl", "rpl"))
        logger.info(f">>> РПЛ загружено: {len(all_matches)} матчей")
    except Exception as e:
        logger.error(f">>> РПЛ Understat API request failed: {e}")
    return all_matches

def should_update():
    """Проверяет, нужно ли обновлять матчи (прошло больше 55 минут)"""
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT MAX(kickoff_time) FROM matches")
        last = cur.fetchone()[0]
        if last:
            last_update = parse_utc_time(last)
            if last_update:
                return utc_now() - last_update > timedelta(minutes=55)
    except:
        pass
    finally:
        close_db(conn, cur)
    return True

def update_matches():
    matches_data = fetch_matches()
    
    try:
        rpl_matches = fetch_rpl_matches()
        if rpl_matches:
            matches_data.extend(rpl_matches)
    except Exception as e:
        logger.error(f"Не удалось загрузить РПЛ: {e}")

    if not matches_data:
        return

    conn = get_db()
    cur = conn.cursor()
    try:
        for match in matches_data:
            api_id = match['id']
            home_team = match.get('home_team', match.get('homeTeam', {}).get('name', 'Unknown'))
            away_team = match.get('away_team', match.get('awayTeam', {}).get('name', 'Unknown'))
            utc_time = match.get('utcDate', match.get('datetime', ''))
            if isinstance(utc_time, str):
                utc_time = utc_time.replace('Z', '')
            status = match.get('status', 'SCHEDULED')
            league = match.get('league', 'other')

            score = None
            if status == 'FINISHED':
                score_data = match.get('score', {})
                extra = score_data.get('extraTime')
                full = score_data.get('fullTime')
                score = extra if extra and extra.get('home') is not None else full

            home_score = None
            away_score = None
            if score and score.get('home') is not None and score.get('away') is not None:
                home_score = int(score['home'])
                away_score = int(score['away'])

            if ' ' in utc_time:
                kickoff_utc = datetime.strptime(utc_time, "%Y-%m-%d %H:%M:%S")
            else:
                kickoff_utc = datetime.fromisoformat(utc_time)

            kickoff_msk = kickoff_utc + timedelta(hours=MSK_OFFSET)
            deadline_msk = kickoff_msk.replace(hour=11, minute=0, second=0, microsecond=0)
            if deadline_msk >= kickoff_msk:
                deadline_msk = kickoff_msk - timedelta(hours=1)
            deadline_utc = deadline_msk - timedelta(hours=MSK_OFFSET)

            cur.execute("SELECT id FROM matches WHERE api_match_id = %s", (str(api_id),))
            existing = cur.fetchone()

            if not existing:
                cur.execute(
                    """INSERT INTO matches (api_match_id, home_team, away_team, kickoff_time, deadline, status,
                       home_score, away_score, league)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (str(api_id), home_team, away_team,
                     kickoff_utc.strftime("%Y-%m-%dT%H:%M:%S"), deadline_utc.strftime("%Y-%m-%dT%H:%M:%S"),
                     status, home_score, away_score, league)
                )
            else:
                cur.execute(
                    """UPDATE matches SET status = %s, home_score = %s, away_score = %s,
                       kickoff_time = %s, deadline = %s, league = %s
                       WHERE api_match_id = %s""",
                    (status, home_score, away_score,
                     kickoff_utc.strftime("%Y-%m-%dT%H:%M:%S"), deadline_utc.strftime("%Y-%m-%dT%H:%M:%S"),
                     league, str(api_id))
                )
                if status in ('POSTPONED', 'CANCELLED'):
                    cur.execute(
                        "UPDATE predictions SET points = 0 WHERE match_id = (SELECT id FROM matches WHERE api_match_id = %s)",
                        (str(api_id),)
                    )
    finally:
        close_db(conn, cur)

def update_matches_safe():
    """Безопасное обновление с проверкой времени"""
    try:
        if should_update():
            logger.info("Обновление матчей...")
            update_matches()
            calculate_all_points()
        else:
            logger.info("Обновление не требуется (прошло <55 минут)")
    except Exception as e:
        logger.error(f"Ошибка при обновлении матчей: {e}")

# ---------- МАРШРУТЫ ----------
@app.route('/')
@login_required
def index():
    conn = get_db()
    cur = conn.cursor()
    now = utc_now()
    
    league_filter = request.args.get('league', 'all')
    start_date_str = START_DATE.strftime("%Y-%m-%dT%H:%M:%S")
    
    try:
        if league_filter == 'all':
            cur.execute(
                """SELECT id, home_team, away_team, kickoff_time, deadline, status, league
                   FROM matches
                   WHERE status IN ('SCHEDULED', 'TIMED')
                     AND kickoff_time >= %s
                   ORDER BY kickoff_time""",
                (start_date_str,)
            )
        else:
            cur.execute(
                """SELECT id, home_team, away_team, kickoff_time, deadline, status, league
                   FROM matches
                   WHERE status IN ('SCHEDULED', 'TIMED')
                     AND league = %s
                     AND kickoff_time >= %s
                   ORDER BY kickoff_time""",
                (league_filter, start_date_str)
            )
        matches = []
        for m in cur.fetchall():
            match = {
                'id': m[0], 'home_team': m[1], 'away_team': m[2],
                'kickoff_time': m[3], 'deadline': m[4], 'status': m[5], 'league': m[6]
            }
            match['deadline_passed'] = not is_before_deadline((m[0], m[1], m[2], m[4], m[5]))
            matches.append(match)
    finally:
        close_db(conn, cur)
    return render_template('index.html', matches=matches, to_msk=to_msk, current_filter=league_filter)

@app.route('/predict')
@login_required
def predict():
    conn = get_db()
    cur = conn.cursor()
    now = utc_now()
    start_date_str = START_DATE.strftime("%Y-%m-%dT%H:%M:%S")
    
    try:
        cur.execute(
            """SELECT id, home_team, away_team, kickoff_time, deadline, league
               FROM matches
               WHERE status IN ('SCHEDULED', 'TIMED')
                 AND deadline > %s
                 AND kickoff_time >= %s
               ORDER BY kickoff_time""",
            (now.strftime("%Y-%m-%dT%H:%M:%S"), start_date_str)
        )
        raw_matches = [{'id': m[0], 'home_team': m[1], 'away_team': m[2], 'kickoff_time': m[3], 'deadline': m[4], 'league': m[5]} for m in cur.fetchall()]
        
        matches_by_day = defaultdict(list)
        for match in raw_matches:
            clean_str = match['kickoff_time'].replace('Z', '').replace('+00:00', '').replace('-00:00', '')
            try:
                dt_utc = datetime.fromisoformat(clean_str)
            except:
                dt_utc = datetime.strptime(match['kickoff_time'], "%Y-%m-%d %H:%M:%S")
            dt_msk = dt_utc + timedelta(hours=MSK_OFFSET)
            day_key = dt_msk.strftime("%Y-%m-%d")
            day_label = dt_msk.strftime("%d.%m.%Y")
            match['day_key'] = day_key
            matches_by_day[day_label].append(match)
        
        days = []
        for day_label, day_matches in sorted(matches_by_day.items(), key=lambda x: datetime.strptime(x[0], "%d.%m.%Y")):
            days.append({
                'label': day_label,
                'key': day_matches[0]['day_key'],
                'count': len(day_matches)
            })
    finally:
        close_db(conn, cur)
    return render_template('predict.html', days=days, to_msk=to_msk)

@app.route('/predict/<string:day_key>', methods=['GET', 'POST'])
@login_required
def predict_day(day_key):
    conn = get_db()
    cur = conn.cursor()
    now = utc_now()
    start_date_str = START_DATE.strftime("%Y-%m-%dT%H:%M:%S")
    
    if request.method == 'POST':
        match_id = request.form.get('match_id')
        home_goals = request.form.get('home_goals')
        away_goals = request.form.get('away_goals')

        try:
            home_goals = int(home_goals)
            away_goals = int(away_goals)
            if home_goals < 0 or away_goals < 0:
                raise ValueError
        except (ValueError, TypeError):
            flash("Некорректный ввод: голы должны быть целыми неотрицательными числами", "error")
            return redirect(url_for('predict_day', day_key=day_key))

        try:
            cur.execute(
                "SELECT id, home_team, away_team, deadline, status FROM matches WHERE id = %s",
                (match_id,)
            )
            m = cur.fetchone()
            
            if not m or m[4] not in ('SCHEDULED', 'TIMED') or not is_before_deadline(m):
                flash("Ставки на этот матч закрыты", "error")
                return redirect(url_for('predict_day', day_key=day_key))

            cur.execute(
                """INSERT INTO predictions (user_id, match_id, home_goals, away_goals)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (user_id, match_id) DO UPDATE SET home_goals = %s, away_goals = %s""",
                (session['user_id'], match_id, home_goals, away_goals, home_goals, away_goals)
            )
            
            flash(f"✅ Ставка на матч {m[1]} – {m[2]}: {home_goals}:{away_goals} принята", "success")
        finally:
            close_db(conn, cur)
        return redirect(url_for('predict_day', day_key=day_key))

    # GET
    try:
        cur.execute(
            """SELECT id, home_team, away_team, kickoff_time, deadline, league
               FROM matches
               WHERE status IN ('SCHEDULED', 'TIMED')
                 AND deadline > %s
                 AND kickoff_time >= %s
               ORDER BY kickoff_time""",
            (now.strftime("%Y-%m-%dT%H:%M:%S"), start_date_str)
        )
        raw_matches = [{'id': m[0], 'home_team': m[1], 'away_team': m[2], 'kickoff_time': m[3], 'deadline': m[4], 'league': m[5]} for m in cur.fetchall()]
        
        cur.execute(
            """SELECT match_id, home_goals, away_goals FROM predictions WHERE user_id = %s""",
            (session['user_id'],)
        )
        user_preds = {p[0]: (p[1], p[2]) for p in cur.fetchall()}
        
        day_matches = []
        day_label = ""
        for match in raw_matches:
            clean_str = match['kickoff_time'].replace('Z', '').replace('+00:00', '').replace('-00:00', '')
            try:
                dt_utc = datetime.fromisoformat(clean_str)
            except:
                dt_utc = datetime.strptime(match['kickoff_time'], "%Y-%m-%d %H:%M:%S")
            dt_msk = dt_utc + timedelta(hours=MSK_OFFSET)
            if dt_msk.strftime("%Y-%m-%d") == day_key:
                match['deadline_passed'] = not is_before_deadline((match['id'], None, None, match['deadline'], None))
                match['has_prediction'] = match['id'] in user_preds
                if match['has_prediction']:
                    match['pred_home'] = user_preds[match['id']][0]
                    match['pred_away'] = user_preds[match['id']][1]
                day_matches.append(match)
                day_label = dt_msk.strftime("%d.%m.%Y")
        
        if not day_matches:
            flash("На этот день нет доступных матчей", "error")
            return redirect(url_for('predict'))
    finally:
        close_db(conn, cur)
    
    return render_template('predict_day.html', day_label=day_label, day_key=day_key, matches=day_matches, to_msk=to_msk)

@app.route('/my-predictions')
@login_required
def my_predictions():
    conn = get_db()
    cur = conn.cursor()
    now = utc_now()
    uid = session['user_id']

    try:
        cur.execute(
            """SELECT m.id, m.home_team, m.away_team, p.home_goals, p.away_goals,
                      m.kickoff_time, m.deadline
               FROM predictions p JOIN matches m ON p.match_id = m.id
               WHERE p.user_id = %s AND m.deadline > %s""",
            (uid, now.strftime("%Y-%m-%dT%H:%M:%S"))
        )
        pending = [{'id': p[0], 'home_team': p[1], 'away_team': p[2], 'home_goals': p[3], 'away_goals': p[4], 'kickoff_time': p[5], 'deadline': p[6]} for p in cur.fetchall()]

        cur.execute(
            """SELECT m.id, m.home_team, m.away_team, p.home_goals, p.away_goals
               FROM predictions p JOIN matches m ON p.match_id = m.id
               WHERE p.user_id = %s AND m.deadline <= %s AND m.status NOT IN ('FINISHED', 'POSTPONED', 'CANCELLED')""",
            (uid, now.strftime("%Y-%m-%dT%H:%M:%S"))
        )
        awaiting = [{'id': a[0], 'home_team': a[1], 'away_team': a[2], 'home_goals': a[3], 'away_goals': a[4]} for a in cur.fetchall()]

        cur.execute(
            """SELECT m.id, m.home_team, m.away_team, m.home_score, m.away_score,
                      p.home_goals, p.away_goals, p.points
               FROM predictions p JOIN matches m ON p.match_id = m.id
               WHERE p.user_id = %s AND m.status = 'FINISHED'""",
            (uid,)
        )
        finished = [{'id': f[0], 'home_team': f[1], 'away_team': f[2], 'home_score': f[3], 'away_score': f[4], 'home_goals': f[5], 'away_goals': f[6], 'points': f[7]} for f in cur.fetchall()]

        cur.execute(
            """SELECT m.id, m.home_team, m.away_team, m.status, p.points
               FROM predictions p JOIN matches m ON p.match_id = m.id
               WHERE p.user_id = %s AND m.status IN ('POSTPONED', 'CANCELLED')""",
            (uid,)
        )
        cancelled = [{'id': c[0], 'home_team': c[1], 'away_team': c[2], 'status': c[3], 'points': c[4]} for c in cur.fetchall()]
    finally:
        close_db(conn, cur)
    return render_template('my_predictions.html',
                           pending=pending, awaiting=awaiting,
                           finished=finished, cancelled=cancelled, to_msk=to_msk)

@app.route('/match/<int:match_id>/predictions')
@login_required
def match_predictions(match_id):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id, home_team, away_team, kickoff_time, deadline, status FROM matches WHERE id = %s",
            (match_id,)
        )
        m = cur.fetchone()
        match = {'id': m[0], 'home_team': m[1], 'away_team': m[2], 'kickoff_time': m[3], 'deadline': m[4], 'status': m[5]} if m else None

        if not match:
            flash("Матч не найден", "error")
            return redirect(url_for('index'))

        # Дедлайн прошёл? Показываем ставки
        if not is_before_deadline(m):
            cur.execute(
                """SELECT u.username, p.home_goals, p.away_goals, p.points
                   FROM predictions p JOIN users u ON p.user_id = u.id
                   WHERE p.match_id = %s
                   ORDER BY u.username""",
                (match_id,)
            )
            predictions = [{'username': p[0], 'home_goals': p[1], 'away_goals': p[2], 'points': p[3]} for p in cur.fetchall()]
            return render_template('match_predictions.html',
                                   match=match,
                                   predictions=predictions,
                                   to_msk=to_msk)
        else:
            flash("Ставки других игроков будут доступны после закрытия приёма прогнозов", "error")
            return redirect(url_for('index'))
    finally:
        close_db(conn, cur)

@app.route('/table')
@login_required
def table():
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT u.username, COALESCE(SUM(p.points), 0) as total
               FROM users u LEFT JOIN predictions p ON u.id = p.user_id
               GROUP BY u.id ORDER BY total DESC"""
        )
        rows = cur.fetchall()
    finally:
        close_db(conn, cur)
    table_data = []
    for idx, row in enumerate(rows, 1):
        table_data.append({'place': idx, 'username': row[0], 'points': int(row[1])})
    return render_template('table.html', table=table_data)

@app.route('/admin', methods=['GET', 'POST'])
@admin_required
def admin():
    conn = get_db()
    cur = conn.cursor()
    try:
        if request.method == 'POST':
            action = request.form.get('action')
            if action == 'update_matches':
                update_matches_safe()
                flash("Данные матчей обновлены из API", "success")
            elif action == 'add_match':
                home = request.form['home_team']
                away = request.form['away_team']
                league = request.form.get('league', 'other')
                try:
                    kickoff_msk_str = request.form['kickoff_msk']
                    dt_msk = datetime.strptime(kickoff_msk_str, "%Y-%m-%d %H:%M")
                    utc = dt_msk - timedelta(hours=MSK_OFFSET)
                    deadline_msk = dt_msk.replace(hour=11, minute=0)
                    if deadline_msk >= dt_msk:
                        deadline_msk = dt_msk - timedelta(hours=1)
                    deadline_utc = deadline_msk - timedelta(hours=MSK_OFFSET)
                    cur.execute(
                        """INSERT INTO matches (home_team, away_team, kickoff_time, deadline, status, league)
                           VALUES (%s, %s, %s, %s, 'SCHEDULED', %s)""",
                        (home, away, utc.strftime("%Y-%m-%dT%H:%M:%S"),
                         deadline_utc.strftime("%Y-%m-%dT%H:%M:%S"), league)
                    )
                    flash(f"Матч {home} – {away} добавлен", "success")
                except Exception as e:
                    flash(f"Неверный формат даты: {e}", "error")
            elif action == 'set_result':
                match_id = request.form['match_id']
                home_score = request.form['home_score']
                away_score = request.form['away_score']
                try:
                    home_score = int(home_score)
                    away_score = int(away_score)
                except:
                    flash("Результат должен быть числами", "error")
                    return redirect(url_for('admin'))
                cur.execute(
                    "UPDATE matches SET status='FINISHED', home_score=%s, away_score=%s WHERE id=%s",
                    (home_score, away_score, match_id)
                )
                calculate_points_for_match(match_id)
                flash("Результат внесён, очки пересчитаны", "success")
            elif action == 'cancel_match':
                match_id = request.form['match_id']
                cur.execute("UPDATE matches SET status='CANCELLED' WHERE id=%s", (match_id,))
                cur.execute("UPDATE predictions SET points=0 WHERE match_id=%s", (match_id,))
                flash("Матч отменён, очки сброшены", "success")
            elif action == 'reset_all_points':
                cur.execute("UPDATE predictions SET points = 0")
                cur.execute("UPDATE matches SET status = 'CANCELLED' WHERE status = 'FINISHED'")
                cur.execute("UPDATE matches SET home_score = NULL, away_score = NULL WHERE status = 'CANCELLED'")
                flash("Все очки обнулены! Турнирная таблица сброшена.", "success")

        cur.execute("SELECT id, home_team, away_team, kickoff_time, status FROM matches WHERE status IN ('SCHEDULED', 'TIMED')")
        free_matches = [{'id': m[0], 'home_team': m[1], 'away_team': m[2], 'kickoff_time': m[3], 'status': m[4]} for m in cur.fetchall()]
        cur.execute("SELECT id, home_team, away_team, kickoff_time, status FROM matches")
        all_matches = [{'id': m[0], 'home_team': m[1], 'away_team': m[2], 'kickoff_time': m[3], 'status': m[4]} for m in cur.fetchall()]
        cur.execute("SELECT id, username FROM users")
        users = [{'id': u[0], 'username': u[1]} for u in cur.fetchall()]
    finally:
        close_db(conn, cur)
    return render_template('admin.html',
                           free_matches=free_matches,
                           all_matches=all_matches,
                           users=users)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute("SELECT id FROM users WHERE username = %s AND password = %s", (username, password))
            user = cur.fetchone()
        finally:
            close_db(conn, cur)
        if user:
            session['user_id'] = user[0]
            session.permanent = True
            app.permanent_session_lifetime = timedelta(days=7)
            return redirect(url_for('index'))
        flash("Неверное имя или пароль", "error")
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        invite = request.form['invite_code']
        if invite != INVITE_CODE:
            flash("Неверный инвайт-код", "error")
            return redirect(url_for('register'))
        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO users (username, password) VALUES (%s, %s)",
                (username, password)
            )
            flash("Регистрация успешна, теперь войдите", "success")
            return redirect(url_for('login'))
        except psycopg2.IntegrityError:
            flash("Такой пользователь уже существует", "error")
        finally:
            close_db(conn, cur)
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('login'))

# ---------- ЗАПУСК ----------
if __name__ == '__main__':
    init_db()
    update_matches_safe()
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)