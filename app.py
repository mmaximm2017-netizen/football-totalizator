# football_site/app.py
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta
import requests
import schedule
from flask import Flask, render_template, request, redirect, url_for, session, flash, g

# Пробуем импортировать Understat — если не получится, просто отключим РПЛ
try:
    from understatapi import UnderstatClient
    UNDERSTAT_AVAILABLE = True
except ImportError:
    UNDERSTAT_AVAILABLE = False
    print("Understat не установлен. РПЛ будет недоступна.")

# ---------- НАСТРОЙКИ ----------
API_KEY = "3c1f32333b1c4b5eacb45b01dd83170c"
LEAGUE_IDS = [2000]                        # ЧМ-2026
RPL_LEAGUE_NAME = "RFPL"                  # РПЛ через Understat (если доступен)
INVITE_CODE = "FIFA2026"
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"
MSK_OFFSET = 3
DATABASE = "/opt/render/project/src/data/site.db" if os.path.exists("/opt/render") else "site.db"

app = Flask(__name__)
app.secret_key = os.urandom(24)

# ---------- СОЗДАЁМ ПАПКУ ДЛЯ БД НА RENDER ----------
if "/opt/render" in os.getcwd():
    os.makedirs("/opt/render/project/src/data", exist_ok=True)

# ---------- РАБОТА С БД ----------
def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_match_id INTEGER UNIQUE,
            home_team TEXT,
            away_team TEXT,
            kickoff_time TEXT,
            deadline TEXT,
            status TEXT DEFAULT 'SCHEDULED',
            home_score INTEGER,
            away_score INTEGER
        );
        CREATE TABLE IF NOT EXISTS predictions (
            user_id INTEGER,
            match_id INTEGER,
            home_goals INTEGER,
            away_goals INTEGER,
            points INTEGER DEFAULT 0,
            UNIQUE(user_id, match_id),
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(match_id) REFERENCES matches(id)
        );
    ''')
    cur.execute("SELECT id FROM users WHERE username = ?", (ADMIN_USERNAME,))
    if not cur.fetchone():
        cur.execute("INSERT INTO users (username, password, is_admin) VALUES (?, ?, 1)",
                    (ADMIN_USERNAME, ADMIN_PASSWORD))
    conn.commit()
    conn.close()

# ---------- ЗАГРУЗКА ПОЛЬЗОВАТЕЛЯ ----------
@app.before_request
def load_user():
    g.is_admin = False
    if 'user_id' in session:
        conn = get_db()
        user = conn.execute("SELECT is_admin FROM users WHERE id = ?",
                            (session['user_id'],)).fetchone()
        conn.close()
        if user and user['is_admin'] == 1:
            g.is_admin = True

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
        user = conn.execute("SELECT is_admin FROM users WHERE id = ?",
                            (session['user_id'],)).fetchone()
        conn.close()
        if not user or user['is_admin'] != 1:
            flash("Доступ запрещён", "error")
            return redirect(url_for('index'))
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

def is_before_deadline(match):
    deadline = parse_utc_time(match['deadline'])
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

def calculate_all_points():
    conn = get_db()
    finished = conn.execute(
        "SELECT id, home_score, away_score FROM matches WHERE status = 'FINISHED'"
    ).fetchall()
    for match in finished:
        preds = conn.execute(
            "SELECT user_id, home_goals, away_goals FROM predictions WHERE match_id = ?",
            (match['id'],)
        ).fetchall()
        for p in preds:
            pts = calculate_points(match['home_score'], match['away_score'],
                                   p['home_goals'], p['away_goals'])
            conn.execute(
                "UPDATE predictions SET points = ? WHERE user_id = ? AND match_id = ?",
                (pts, p['user_id'], match['id'])
            )
    conn.commit()
    conn.close()

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
                all_matches.extend(data.get('matches', []))
            else:
                print(f"football-data.org API error: {resp.status_code}")
        except Exception as e:
            print(f"football-data.org API request failed: {e}")
    return all_matches

def fetch_rpl_matches():
    """РПЛ через Understat (если доступен)"""
    if not UNDERSTAT_AVAILABLE:
        print(">>> Understat не установлен — РПЛ пропущена")
        return []
    
    print(">>> Пытаемся загрузить РПЛ через Understat...")
    all_matches = []
    try:
        understat = UnderstatClient()
        league_data = understat.league(league="RFPL").get_match_data(season="2025")
        print(f">>> РПЛ: Understat вернул {len(league_data)} матчей")
        for match in league_data:
            all_matches.append(create_match_from_understat(match, "rpl"))
        print(f">>> РПЛ загружено: {len(all_matches)} матчей")
    except Exception as e:
        print(f">>> РПЛ Understat API request failed: {e}")
    return all_matches

def fetch_rcup_matches():
    """Кубок России через Understat (если доступен)"""
    if not UNDERSTAT_AVAILABLE:
        print(">>> Understat не установлен — Кубок России пропущен")
        return []
    
    all_matches = []
    # Пробуем два возможных названия лиги для Кубка России
    for league_code in ["RCUP", "Russian Cup"]:
        try:
            understat = UnderstatClient()
            print(f">>> Пытаемся загрузить Кубок России через Understat с кодом '{league_code}'...")
            league_data = understat.league(league=league_code).get_match_data(season="2025")
            if league_data:
                print(f">>> Кубок России: Understat с кодом '{league_code}' вернул {len(league_data)} матчей")
                for match in league_data:
                    all_matches.append(create_match_from_understat(match, "rcup"))
                break  # Нашли рабочий код, выходим из цикла
        except Exception as e:
            print(f">>> Кубок России с кодом '{league_code}': {e}")
    
    print(f">>> Кубок России загружено: {len(all_matches)} матчей")
    return all_matches

def create_match_from_understat(match, prefix):
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
        }
    }
    matches_data = fetch_matches()
    
        # Пробуем добавить РПЛ
    try:
        rpl_matches = fetch_rpl_matches()
        if rpl_matches:
            matches_data.extend(rpl_matches)
    except Exception as e:
        print(f"Не удалось загрузить РПЛ: {e}")

    # Пробуем добавить Кубок России
    try:
        rcup_matches = fetch_rcup_matches()
        if rcup_matches:
            matches_data.extend(rcup_matches)
    except Exception as e:
        print(f"Не удалось загрузить Кубок России: {e}")

    if not matches_data:
        return

    conn = get_db()
    for match in matches_data:
        api_id = match['id']
        home_team = match.get('home_team', match.get('homeTeam', {}).get('name', 'Unknown'))
        away_team = match.get('away_team', match.get('awayTeam', {}).get('name', 'Unknown'))
        utc_time = match.get('utcDate', match.get('datetime', ''))
        if isinstance(utc_time, str):
            utc_time = utc_time.replace('Z', '')
        status = match.get('status', 'SCHEDULED')

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

        existing = conn.execute(
            "SELECT id, status FROM matches WHERE api_match_id = ?", (str(api_id),)
        ).fetchone()

        if not existing:
            conn.execute(
                """INSERT INTO matches (api_match_id, home_team, away_team, kickoff_time, deadline, status,
                   home_score, away_score)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(api_id), home_team, away_team,
                 kickoff_utc.strftime("%Y-%m-%dT%H:%M:%S"), deadline_utc.strftime("%Y-%m-%dT%H:%M:%S"),
                 status, home_score, away_score)
            )
        else:
            conn.execute(
                """UPDATE matches SET status = ?, home_score = ?, away_score = ?,
                   kickoff_time = ?, deadline = ?
                   WHERE api_match_id = ?""",
                (status, home_score, away_score,
                 kickoff_utc.strftime("%Y-%m-%dT%H:%M:%S"), deadline_utc.strftime("%Y-%m-%dT%H:%M:%S"), str(api_id))
            )
            if status in ('POSTPONED', 'CANCELLED'):
                conn.execute(
                    "UPDATE predictions SET points = 0 WHERE match_id = ?",
                    (existing['id'],)
                )
    conn.commit()
    conn.close()
    calculate_all_points()

def run_scheduler():
    schedule.every().hour.at(":00").do(update_matches)
    while True:
        schedule.run_pending()
        time.sleep(30)

# ---------- МАРШРУТЫ ----------
@app.route('/')
@login_required
def index():
    conn = get_db()
    now = utc_now()
    matches = conn.execute(
        """SELECT id, home_team, away_team, kickoff_time, deadline, status
           FROM matches
           WHERE status IN ('SCHEDULED', 'TIMED') AND deadline > ?""",
        (now.strftime("%Y-%m-%dT%H:%M:%S"),)
    ).fetchall()
    conn.close()
    return render_template('index.html', matches=matches, to_msk=to_msk)

@app.route('/predict', methods=['GET', 'POST'])
@login_required
def predict():
    conn = get_db()
    now = utc_now()
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
            return redirect(url_for('predict'))

        match = conn.execute(
            "SELECT id, home_team, away_team, deadline, status FROM matches WHERE id = ?",
            (match_id,)
        ).fetchone()
        if not match or match['status'] not in ('SCHEDULED', 'TIMED') or \
           not is_before_deadline(match):
            flash("Ставки на этот матч закрыты", "error")
            return redirect(url_for('predict'))

        conn.execute(
            """INSERT OR REPLACE INTO predictions (user_id, match_id, home_goals, away_goals)
               VALUES (?, ?, ?, ?)""",
            (session['user_id'], match_id, home_goals, away_goals)
        )
        conn.commit()
        flash(f"✅ Ставка на матч {match['home_team']} – {match['away_team']}: {home_goals}:{away_goals} принята", "success")
        return redirect(url_for('my_predictions'))

    matches = conn.execute(
        """SELECT id, home_team, away_team, kickoff_time, deadline
           FROM matches
           WHERE status IN ('SCHEDULED', 'TIMED') AND deadline > ?""",
        (now.strftime("%Y-%m-%dT%H:%M:%S"),)
    ).fetchall()
    conn.close()
    return render_template('predict.html', matches=matches, to_msk=to_msk)

@app.route('/my-predictions')
@login_required
def my_predictions():
    conn = get_db()
    now = utc_now()
    uid = session['user_id']

    pending = conn.execute(
        """SELECT m.id, m.home_team, m.away_team, p.home_goals, p.away_goals,
                  m.kickoff_time, m.deadline
           FROM predictions p JOIN matches m ON p.match_id = m.id
           WHERE p.user_id = ? AND m.deadline > ?""",
        (uid, now.strftime("%Y-%m-%dT%H:%M:%S"))
    ).fetchall()

    awaiting = conn.execute(
        """SELECT m.id, m.home_team, m.away_team, p.home_goals, p.away_goals
           FROM predictions p JOIN matches m ON p.match_id = m.id
           WHERE p.user_id = ? AND m.deadline <= ? AND m.status NOT IN ('FINISHED', 'POSTPONED', 'CANCELLED')""",
        (uid, now.strftime("%Y-%m-%dT%H:%M:%S"))
    ).fetchall()

    finished = conn.execute(
        """SELECT m.id, m.home_team, m.away_team, m.home_score, m.away_score,
                  p.home_goals, p.away_goals, p.points
           FROM predictions p JOIN matches m ON p.match_id = m.id
           WHERE p.user_id = ? AND m.status = 'FINISHED'""",
        (uid,)
    ).fetchall()

    cancelled = conn.execute(
        """SELECT m.id, m.home_team, m.away_team, m.status, p.points
           FROM predictions p JOIN matches m ON p.match_id = m.id
           WHERE p.user_id = ? AND m.status IN ('POSTPONED', 'CANCELLED')""",
        (uid,)
    ).fetchall()
    conn.close()
    return render_template('my_predictions.html',
                           pending=pending, awaiting=awaiting,
                           finished=finished, cancelled=cancelled, to_msk=to_msk)

@app.route('/match/<int:match_id>/predictions')
@login_required
def match_predictions(match_id):
    conn = get_db()
    match = conn.execute(
        "SELECT id, home_team, away_team, kickoff_time, deadline, status FROM matches WHERE id = ?",
        (match_id,)
    ).fetchone()

    if not match:
        conn.close()
        flash("Матч не найден", "error")
        return redirect(url_for('index'))

    if not is_before_deadline(match):
        predictions = conn.execute(
            """SELECT u.username, p.home_goals, p.away_goals, p.points
               FROM predictions p JOIN users u ON p.user_id = u.id
               WHERE p.match_id = ?
               ORDER BY u.username""",
            (match_id,)
        ).fetchall()
        conn.close()
        return render_template('match_predictions.html',
                               match=match,
                               predictions=predictions,
                               to_msk=to_msk)
    else:
        conn.close()
        flash("Ставки других игроков будут доступны после закрытия приёма прогнозов", "error")
        return redirect(url_for('index'))

@app.route('/table')
@login_required
def table():
    conn = get_db()
    rows = conn.execute(
        """SELECT u.username, COALESCE(SUM(p.points), 0) as total
           FROM users u LEFT JOIN predictions p ON u.id = p.user_id
           GROUP BY u.id ORDER BY total DESC"""
    ).fetchall()
    conn.close()
    table_data = []
    for idx, row in enumerate(rows, 1):
        table_data.append({'place': idx, 'username': row['username'], 'points': row['total']})
    return render_template('table.html', table=table_data)

@app.route('/admin', methods=['GET', 'POST'])
@admin_required
def admin():
    conn = get_db()
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'update_matches':
            update_matches()
            flash("Данные матчей обновлены из API", "success")
        elif action == 'add_match':
            home = request.form['home_team']
            away = request.form['away_team']
            try:
                kickoff_msk_str = request.form['kickoff_msk']
                dt_msk = datetime.strptime(kickoff_msk_str, "%Y-%m-%d %H:%M")
                utc = dt_msk - timedelta(hours=MSK_OFFSET)
                deadline_msk = dt_msk.replace(hour=11, minute=0)
                if deadline_msk >= dt_msk:
                    deadline_msk = dt_msk - timedelta(hours=1)
                deadline_utc = deadline_msk - timedelta(hours=MSK_OFFSET)
                conn.execute(
                    """INSERT INTO matches (home_team, away_team, kickoff_time, deadline, status)
                       VALUES (?, ?, ?, ?, 'SCHEDULED')""",
                    (home, away, utc.strftime("%Y-%m-%dT%H:%M:%S"),
                     deadline_utc.strftime("%Y-%m-%dT%H:%M:%S"))
                )
                conn.commit()
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
                conn.close()
                return redirect(url_for('admin'))
            conn.execute(
                "UPDATE matches SET status='FINISHED', home_score=?, away_score=? WHERE id=?",
                (home_score, away_score, match_id)
            )
            conn.commit()
            calculate_all_points()
            flash("Результат внесён, очки пересчитаны", "success")
        elif action == 'cancel_match':
            match_id = request.form['match_id']
            conn.execute(
                "UPDATE matches SET status='CANCELLED' WHERE id=?",
                (match_id,)
            )
            conn.execute(
                "UPDATE predictions SET points=0 WHERE match_id=?",
                (match_id,)
            )
            conn.commit()
            flash("Матч отменён, очки сброшены", "success")
        elif action == 'reset_all_points':
            conn.execute("UPDATE predictions SET points = 0")
            conn.execute("UPDATE matches SET status = 'CANCELLED' WHERE status = 'FINISHED'")
            conn.execute("UPDATE matches SET home_score = NULL, away_score = NULL WHERE status = 'CANCELLED'")
            conn.commit()
            flash("Все очки обнулены! Турнирная таблица сброшена.", "success")

    free_matches = conn.execute(
        "SELECT id, home_team, away_team, kickoff_time, status FROM matches "
        "WHERE status IN ('SCHEDULED', 'TIMED')"
    ).fetchall()
    all_matches = conn.execute(
        "SELECT id, home_team, away_team, kickoff_time, status FROM matches"
    ).fetchall()
    users = conn.execute("SELECT id, username FROM users").fetchall()
    conn.close()
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
        user = conn.execute(
            "SELECT id FROM users WHERE username = ? AND password = ?",
            (username, password)
        ).fetchone()
        conn.close()
        if user:
            session['user_id'] = user['id']
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
        try:
            conn.execute(
                "INSERT INTO users (username, password) VALUES (?, ?)",
                (username, password)
            )
            conn.commit()
            flash("Регистрация успешна, теперь войдите", "success")
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash("Такой пользователь уже существует", "error")
        conn.close()
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('login'))

# ---------- ЗАПУСК ----------
if __name__ == '__main__':
    init_db()
    update_matches()
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)