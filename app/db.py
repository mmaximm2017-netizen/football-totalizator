# app/db.py

import logging
import threading

from psycopg2 import InterfaceError, OperationalError
from psycopg2.pool import PoolError, ThreadedConnectionPool
from werkzeug.security import generate_password_hash

from app.config import ADMIN_PASSWORD, ADMIN_USERNAME, DATABASE_URL

logger = logging.getLogger(__name__)

_init_lock = threading.Lock()


class PoolExhausted(Exception):
    pass


# =========================================================
# CONNECTION POOL
# =========================================================

db_pool = None

RUSSIAN_CUP_TOURNAMENT_NAME = "Кубок России"


def build_bootstrap_admin_password_hash():
    return generate_password_hash(ADMIN_PASSWORD)


def init_pool():
    global db_pool

    if db_pool is not None:
        return

    with _init_lock:
        if db_pool is not None:
            return

        db_pool = ThreadedConnectionPool(
            minconn=1,
            maxconn=3,
            dsn=DATABASE_URL,
            connect_timeout=10,
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=5,
        )

    logger.info(
        "DB pool initialized: type=ThreadedConnectionPool min=%d max=%d",
        db_pool.minconn, db_pool.maxconn,
    )


def reset_pool():
    global db_pool

    with _init_lock:
        pool = db_pool
        if pool is not None and getattr(pool, "_used", None):
            raise RuntimeError("cannot_reset_pool_with_active_connections")
        if pool is not None:
            pool.closeall()
        db_pool = None
    init_pool()
    logger.info("DB pool reset complete")


def is_connection_alive(conn):
    try:
        if conn is None or conn.closed:
            return False

        if conn.info.transaction_status == 4:
            return False

        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        cur.close()

        return True

    except Exception:  # noqa: BLE001 - liveness probes must fail closed.
        return False


def get_db():
    if db_pool is None:
        init_pool()

    try:
        conn = db_pool.getconn()

        if not is_connection_alive(conn):
            db_pool.putconn(conn, close=True)
            logger.info("Dead pool connection discarded; obtaining pooled replacement")
            conn = db_pool.getconn()
            if not is_connection_alive(conn):
                db_pool.putconn(conn, close=True)
                raise OperationalError("pooled_connection_replacement_failed")

        return conn

    except PoolError:
        logger.error("DB pool exhausted (max=%d)", db_pool.maxconn)
        raise PoolExhausted("All database connections are in use")

    except (OperationalError, InterfaceError) as exc:
        logger.warning("DB pool connection failed type=%s", type(exc).__name__)
        raise


def close_db(conn, cur=None):
    if cur is not None:
        try:
            if not cur.closed:
                cur.close()
        except Exception:
            logger.warning("DB cursor cleanup failed", exc_info=True)

    if conn is None:
        return
    try:
        if not conn.closed:
            conn.rollback()
    except Exception:
        logger.warning("DB rollback failed", exc_info=True)

    if db_pool is not None:
        try:
            db_pool.putconn(conn, close=bool(conn.closed))
            return
        except Exception:
            logger.warning("DB pool return failed; closing connection", exc_info=True)
    try:
        conn.close()
    except Exception:
        logger.warning("DB connection close failed", exc_info=True)


# =========================================================
# INIT DB
# =========================================================

def seed_russian_cup_tournament(cur):
    """Create the initial Russian Cup record without changing existing settings."""
    cur.execute(
        """
        INSERT INTO tournaments (name, is_active, start_date, end_date)
        SELECT %s, 1, NULL, NULL
        WHERE NOT EXISTS (
            SELECT 1 FROM tournaments WHERE name = %s
        )
        """,
        (RUSSIAN_CUP_TOURNAMENT_NAME, RUSSIAN_CUP_TOURNAMENT_NAME),
    )


def ensure_prediction_integrity_constraints(cur):
    """Add prediction FKs only after a non-destructive integrity preflight."""
    cur.execute(
        "LOCK TABLE predictions, users, matches, tournaments IN SHARE ROW EXCLUSIVE MODE"
    )
    cur.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM predictions
             WHERE user_id IS NULL OR match_id IS NULL OR tournament_id IS NULL),
            (SELECT COUNT(*) FROM (
                SELECT user_id, match_id, tournament_id
                FROM predictions
                GROUP BY user_id, match_id, tournament_id
                HAVING COUNT(*) > 1
             ) duplicates),
            (SELECT COUNT(*) FROM predictions p
             LEFT JOIN users u ON u.id = p.user_id
             WHERE u.id IS NULL),
            (SELECT COUNT(*) FROM predictions p
             LEFT JOIN matches m ON m.id = p.match_id
             WHERE m.id IS NULL),
            (SELECT COUNT(*) FROM predictions p
             LEFT JOIN tournaments t ON t.id = p.tournament_id
             WHERE t.id IS NULL),
            (SELECT COUNT(*) FROM predictions p
             JOIN matches m ON m.id = p.match_id
             WHERE p.tournament_id IS DISTINCT FROM m.tournament_id)
        """
    )
    nulls, duplicates, orphan_users, orphan_matches, orphan_tournaments, mismatches = cur.fetchone()
    problems = {
        "null_keys": nulls,
        "duplicate_keys": duplicates,
        "orphan_users": orphan_users,
        "orphan_matches": orphan_matches,
        "orphan_tournaments": orphan_tournaments,
        "tournament_mismatches": mismatches,
    }
    invalid = {name: count for name, count in problems.items() if count}
    if invalid:
        cur.execute(
            """
            SELECT user_id, match_id, tournament_id, reason
            FROM (
                SELECT p.user_id, p.match_id, p.tournament_id, 'orphan_user' AS reason
                FROM predictions p LEFT JOIN users u ON u.id = p.user_id
                WHERE u.id IS NULL
                UNION ALL
                SELECT p.user_id, p.match_id, p.tournament_id, 'orphan_match'
                FROM predictions p LEFT JOIN matches m ON m.id = p.match_id
                WHERE m.id IS NULL
                UNION ALL
                SELECT p.user_id, p.match_id, p.tournament_id, 'orphan_tournament'
                FROM predictions p LEFT JOIN tournaments t ON t.id = p.tournament_id
                WHERE t.id IS NULL
                UNION ALL
                SELECT p.user_id, p.match_id, p.tournament_id, 'tournament_mismatch'
                FROM predictions p JOIN matches m ON m.id = p.match_id
                WHERE p.tournament_id IS DISTINCT FROM m.tournament_id
            ) problems
            LIMIT 20
            """
        )
        samples = cur.fetchall()
        raise RuntimeError(
            "Prediction integrity preflight failed; no constraints were added: "
            + ", ".join(f"{name}={count}" for name, count in invalid.items())
            + f"; samples={samples}"
        )

    constraints = (
        ("predictions_user_fk", "FOREIGN KEY (user_id) REFERENCES users(id) NOT VALID"),
        ("predictions_match_fk", "FOREIGN KEY (match_id) REFERENCES matches(id) NOT VALID"),
        ("predictions_tournament_fk", "FOREIGN KEY (tournament_id) REFERENCES tournaments(id) NOT VALID"),
    )
    for name, definition in constraints:
        cur.execute(
            """
            SELECT 1
            FROM pg_constraint
            WHERE conname = %s
              AND conrelid = 'predictions'::regclass
            """,
            (name,),
        )
        if not cur.fetchone():
            cur.execute(f"ALTER TABLE predictions ADD CONSTRAINT {name} {definition}")

        cur.execute(f"ALTER TABLE predictions VALIDATE CONSTRAINT {name}")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_predictions_match_tournament "
        "ON predictions(match_id, tournament_id)"
    )


def migrate_prediction_integrity():
    """Run only the additive prediction-integrity migration in one transaction."""
    conn = get_db()
    cur = conn.cursor()
    try:
        ensure_prediction_integrity_constraints(cur)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        close_db(conn, cur)


def init_db():
    """Seed bootstrap data after versioned migrations have created the schema."""
    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute("SET lock_timeout = '5s';")
        cur.execute("SET statement_timeout = '30s';")

        cur.execute("SELECT id FROM tournaments LIMIT 1")
        if not cur.fetchone():
            cur.execute(
                """
                INSERT INTO tournaments (name, is_active, start_date)
                VALUES ('Кубок Матч-премьер', 1, '2026-05-06')
                """
            )

        seed_russian_cup_tournament(cur)

        cur.execute("SELECT id FROM users WHERE username = %s", (ADMIN_USERNAME,))
        if not cur.fetchone():
            cur.execute(
                """
                INSERT INTO users (username, password, is_admin)
                VALUES (%s, %s, 1)
                """,
                (ADMIN_USERNAME, build_bootstrap_admin_password_hash()),
            )

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
