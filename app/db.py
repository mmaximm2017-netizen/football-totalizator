import psycopg2
from psycopg2.pool import SimpleConnectionPool

from app.config import DATABASE_URL, ADMIN_USERNAME, ADMIN_PASSWORD

# =========================================================
# CONNECTION POOL (ленивая инициализация)
# =========================================================

db_pool = None


def init_pool():
    global db_pool
    if db_pool is None:
        db_pool = SimpleConnectionPool(1, 5, DATABASE_URL)


def get_db():
    if db_pool is None:
        init_pool()
    return db_pool.getconn()


def close_db(conn, cur=None):
    if cur and not cur.closed:
        cur.close()
    if conn and not conn.closed and db_pool:
        db_pool.putconn(conn)


# =========================================================
# INIT DB
# =========================================================

def init_db():
    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS tournaments (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            is_active INTEGER DEFAULT 0,
            start_date TEXT,
            end_date TEXT
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS matches (
            id SERIAL PRIMARY KEY,
            api_match_id TEXT UNIQUE,
            home_team TEXT,
            away_team TEXT,
            kickoff_time TIMESTAMP,
            deadline TIMESTAMP,
            status TEXT DEFAULT 'SCHEDULED',
            home_score INTEGER,
            away_score INTEGER,
            league TEXT DEFAULT 'other'
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            user_id INTEGER,
            match_id INTEGER,
            tournament_id INTEGER DEFAULT 1,
            home_goals INTEGER,
            away_goals INTEGER,
            points INTEGER DEFAULT 0
        );
        """)

        # =====================================================
        # INDEXES
        # =====================================================

        cur.execute("CREATE INDEX IF NOT EXISTS idx_matches_status ON matches(status);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_matches_kickoff ON matches(kickoff_time);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_matches_league ON matches(league);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_predictions_user ON predictions(user_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_predictions_match ON predictions(match_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_predictions_tournament ON predictions(tournament_id);")

        # =====================================================
        # UNIQUE CONSTRAINT
        # =====================================================

        cur.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'pred_unique'
            ) THEN
                ALTER TABLE predictions
                ADD CONSTRAINT pred_unique UNIQUE (user_id, match_id, tournament_id);
            END IF;
        END $$;
        """)

        # =====================================================
        # DEFAULT TOURNAMENT
        # =====================================================

        cur.execute("""
        SELECT id FROM tournaments WHERE is_active = 1 LIMIT 1
        """)

        if not cur.fetchone():
            cur.execute("""
            INSERT INTO tournaments (name, is_active, start_date)
            VALUES ('Кубок Матч-премьер', 1, '2026-05-06')
            """)

        # =====================================================
        # ADMIN USER
        # =====================================================

        cur.execute("""
        SELECT id FROM users WHERE username = %s
        """, (ADMIN_USERNAME,))

        if not cur.fetchone():
            cur.execute("""
            INSERT INTO users (username, password, is_admin)
            VALUES (%s, %s, 1)
            """, (ADMIN_USERNAME, ADMIN_PASSWORD))

        conn.commit()

    finally:
        close_db(conn, cur)


# =========================================================
# ACTIVE TOURNAMENT
# =========================================================

def get_active_tournament_id():
    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute("""
        SELECT id
        FROM tournaments
        WHERE is_active = 1
        LIMIT 1
        """)

        row = cur.fetchone()

        return row[0] if row else 1

    finally:
        close_db(conn, cur)