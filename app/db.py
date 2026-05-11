# app/db.py
import psycopg2
from psycopg2.pool import SimpleConnectionPool
from app.config import DATABASE_URL, ADMIN_USERNAME, ADMIN_PASSWORD

# Пул соединений
db_pool = SimpleConnectionPool(1, 5, DATABASE_URL)

def get_db():
    return db_pool.getconn()

def close_db(conn, cur=None):
    if cur and not cur.closed:
        cur.close()
    if conn and not conn.closed:
        db_pool.putconn(conn)

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
        # Индексы
        cur.execute('CREATE INDEX IF NOT EXISTS idx_matches_status ON matches(status);')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_matches_kickoff ON matches(kickoff_time);')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_matches_league ON matches(league);')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_predictions_user ON predictions(user_id);')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_predictions_match ON predictions(match_id);')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_predictions_tournament ON predictions(tournament_id);')
        
        # Уникальное ограничение
        cur.execute('''
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'predictions_unique') THEN
                    ALTER TABLE predictions ADD CONSTRAINT predictions_unique UNIQUE (user_id, match_id, tournament_id);
                END IF;
            END $$;
        ''')
        
        # Первый турнир
        cur.execute("SELECT id FROM tournaments WHERE name = 'Кубок Матч-премьер'")
        if not cur.fetchone():
            cur.execute("INSERT INTO tournaments (name, is_active, start_date) VALUES ('Кубок Матч-премьер', 1, '2026-05-06')")
        
        # Старые ставки
        cur.execute("UPDATE predictions SET tournament_id = 1 WHERE tournament_id IS NULL")
        
        # Админ
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