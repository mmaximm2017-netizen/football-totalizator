import psycopg2
from psycopg2.pool import SimpleConnectionPool

from app.config import DATABASE_URL, ADMIN_USERNAME, ADMIN_PASSWORD

# =========================================================
# CONNECTION POOL
# =========================================================

db_pool = SimpleConnectionPool(
    minconn=1,
    maxconn=5,
    dsn=DATABASE_URL
)


def get_db():
    return db_pool.getconn()


def close_db(conn, cur=None):

    try:
        if cur:
            cur.close()
    except:
        pass

    try:
        if conn:
            db_pool.putconn(conn)
    except:
        pass


# =========================================================
# INIT DB
# =========================================================

def init_db():

    conn = get_db()
    cur = conn.cursor()

    try:

        # USERS
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                is_admin INTEGER DEFAULT 0
            );
        """)

        # MATCHES
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

        # TOURNAMENTS
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tournaments (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                is_active INTEGER DEFAULT 0,
                start_date TIMESTAMP,
                end_date TIMESTAMP
            );
        """)

        # PREDICTIONS
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

        # UNIQUE constraint
        cur.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'predictions_unique'
                ) THEN
                    ALTER TABLE predictions
                    ADD CONSTRAINT predictions_unique
                    UNIQUE (user_id, match_id, tournament_id);
                END IF;
            END $$;
        """)

        # INDEXES
        cur.execute("CREATE INDEX IF NOT EXISTS idx_matches_status ON matches(status);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_matches_kickoff ON matches(kickoff_time);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_predictions_user ON predictions(user_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_predictions_match ON predictions(match_id);")

        # DEFAULT TOURNAMENT
        cur.execute("""
            SELECT id FROM tournaments
            WHERE name = 'Default Tournament'
        """)

        if not cur.fetchone():
            cur.execute("""
                INSERT INTO tournaments (name, is_active, start_date)
                VALUES ('Default Tournament', 1, NOW())
            """)

        # ADMIN
        cur.execute("""
            SELECT id FROM users WHERE username = %s
        """, (ADMIN_USERNAME,))

        if not cur.fetchone():
            cur.execute("""
                INSERT INTO users (username, password, is_admin)
                VALUES (%s, %s, 1)
            """, (ADMIN_USERNAME, ADMIN_PASSWORD))

        conn.commit()

    except Exception as e:
        conn.rollback()
        raise e

    finally:
        close_db(conn, cur)


# =========================================================
# ACTIVE TOURNAMENT (FIXED VERSION)
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

        # fallback = 1 (чтобы система не падала вообще)
        return row[0] if row else 1

    except Exception:
        return 1

    finally:
        close_db(conn, cur)