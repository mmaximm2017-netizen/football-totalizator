# app/db.py

import logging

import psycopg2
from psycopg2.pool import SimpleConnectionPool
from psycopg2 import OperationalError, InterfaceError

from app.config import DATABASE_URL, ADMIN_USERNAME, ADMIN_PASSWORD


logger = logging.getLogger(__name__)


# =========================================================
# CONNECTION POOL
# =========================================================

db_pool = None


def init_pool():
    global db_pool

    if db_pool is None:
        db_pool = SimpleConnectionPool(
            minconn=1,
            maxconn=5,
            dsn=DATABASE_URL,
            connect_timeout=10,
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=5,
        )


def reset_pool():
    global db_pool

    if db_pool is not None:
        try:
            db_pool.closeall()
        except Exception:
            pass

    db_pool = None
    init_pool()


def is_connection_alive(conn):
    try:
        if conn is None or conn.closed:
            return False

        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        cur.close()

        return True

    except Exception:
        return False


def get_db():
    global db_pool

    if db_pool is None:
        init_pool()

    try:
        conn = db_pool.getconn()

        if not is_connection_alive(conn):
            try:
                db_pool.putconn(conn, close=True)
            except Exception:
                pass

            conn = psycopg2.connect(
                DATABASE_URL,
                connect_timeout=10,
                keepalives=1,
                keepalives_idle=30,
                keepalives_interval=10,
                keepalives_count=5,
            )

        return conn

    except (OperationalError, InterfaceError) as e:
        logger.warning(f"DB pool connection failed, resetting pool: {e}")
        reset_pool()
        return db_pool.getconn()


def close_db(conn, cur=None):
    global db_pool

    if cur and not cur.closed:
        cur.close()

    if conn and not conn.closed:
        if db_pool is None:
            init_pool()

        try:
            db_pool.putconn(conn)
        except Exception:
            try:
                conn.close()
            except Exception:
                pass


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
            is_admin INTEGER DEFAULT 0,
            last_seen TIMESTAMP WITH TIME ZONE DEFAULT NULL
        );
        """)

        cur.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name='users' AND column_name='last_seen'
            ) THEN
                ALTER TABLE users
                ADD COLUMN last_seen TIMESTAMP WITH TIME ZONE DEFAULT NULL;
            END IF;
        END $$;
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
            kickoff_time TIMESTAMP WITH TIME ZONE,
            deadline TIMESTAMP WITH TIME ZONE,
            status TEXT DEFAULT 'SCHEDULED',
            home_score INTEGER,
            away_score INTEGER,
            league TEXT DEFAULT 'other',
            tournament_id INTEGER REFERENCES tournaments(id)
        );
        """)

        cur.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name='matches' AND column_name='tournament_id'
            ) THEN
                ALTER TABLE matches
                ADD COLUMN tournament_id INTEGER REFERENCES tournaments(id);
            END IF;
        END $$;
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

        cur.execute("""
        CREATE TABLE IF NOT EXISTS user_titles (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            awarded_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
            awarded_by INTEGER NULL,
            UNIQUE (user_id, title)
        );
        """)

        # =====================================================
        # SAFE MIGRATIONS
        # =====================================================

        cur.execute("""
        ALTER TABLE matches
        ALTER COLUMN kickoff_time TYPE TIMESTAMP WITH TIME ZONE
        USING kickoff_time::timestamptz;
        """)

        cur.execute("""
        ALTER TABLE matches
        ALTER COLUMN deadline TYPE TIMESTAMP WITH TIME ZONE
        USING deadline::timestamptz;
        """)

        # =====================================================
        # INDEXES
        # =====================================================

        cur.execute("CREATE INDEX IF NOT EXISTS idx_matches_status ON matches(status);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_matches_kickoff ON matches(kickoff_time);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_matches_league ON matches(league);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_matches_tournament ON matches(tournament_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_predictions_user ON predictions(user_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_predictions_match ON predictions(match_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_predictions_tournament ON predictions(tournament_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_user_titles_user ON user_titles(user_id);")

        # Remove legacy "single active tournament" restriction if present.
        # Supports both cases:
        # 1) it was created as a table constraint
        # 2) it was created as a unique index
        cur.execute(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conname = 'idx_tournaments_single_active'
                ) THEN
                    ALTER TABLE tournaments
                    DROP CONSTRAINT idx_tournaments_single_active;
                END IF;
            END $$;
            """
        )
        cur.execute(
            """
            DROP INDEX IF EXISTS idx_tournaments_single_active;
            """
        )

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
        # BACKFILL MATCH TOURNAMENT LINKS
        # =====================================================

        cur.execute(
            """
            SELECT id
            FROM tournaments
            WHERE name = 'Кубок Матч-премьер'
               OR (name <> 'ЧМ-2026' AND name IS NOT NULL)
            ORDER BY
                CASE WHEN name = 'Кубок Матч-премьер' THEN 0 ELSE 1 END,
                is_active DESC,
                id DESC
            LIMIT 1
            """
        )
        cup_row = cur.fetchone()
        cur.execute("SELECT id FROM tournaments WHERE name = 'ЧМ-2026' ORDER BY id DESC LIMIT 1")
        wc_row = cur.fetchone()

        if cup_row:
            cur.execute(
                """
                UPDATE matches
                SET tournament_id = %s
                WHERE tournament_id IS NULL
                  AND (league != 'wc2026' OR league IS NULL)
                """,
                (cup_row[0],),
            )

        if wc_row:
            cur.execute(
                """
                UPDATE matches
                SET tournament_id = %s
                WHERE tournament_id IS NULL
                  AND league = 'wc2026'
                """,
                (wc_row[0],),
            )

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

    except Exception:
        conn.rollback()
        raise

    finally:
        close_db(conn, cur)


# =========================================================
# ACTIVE TOURNAMENT
# =========================================================

def get_active_tournament_id():
    """
    Возвращает текущий турнир по дате старта:
    последний турнир, у которого start_date <= сегодня.
    Если такого нет — возвращает турнир с is_active = 1.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    conn = get_db()
    cur = conn.cursor()

    try:
        today = datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat()

        cur.execute("""
            SELECT id
            FROM tournaments
            WHERE start_date IS NOT NULL
              AND start_date <= %s
            ORDER BY start_date DESC, id DESC
            LIMIT 1
        """, (today,))

        row = cur.fetchone()

        if row:
            return row[0]

        cur.execute("""
            SELECT id
            FROM tournaments
            WHERE is_active = 1
            ORDER BY id DESC
            LIMIT 1
        """)

        row = cur.fetchone()

        return row[0] if row else None

    finally:
        close_db(conn, cur)
