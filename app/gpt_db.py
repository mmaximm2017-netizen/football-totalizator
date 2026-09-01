"""Dedicated, fail-closed PostgreSQL access for the Custom GPT API."""

import logging
import os
import threading

from psycopg2 import InterfaceError, OperationalError
from psycopg2.pool import PoolError, ThreadedConnectionPool

logger = logging.getLogger(__name__)
GPT_DB_STATEMENT_TIMEOUT_MS = 10_000
_pool = None
_pool_lock = threading.Lock()


class GPTDatabaseUnavailable(RuntimeError):
    """The isolated GPT database connection cannot be used."""


def _database_url():
    # Never fall back to DATABASE_URL: isolation must fail closed.
    return (os.getenv("TOTISH_GPT_DATABASE_URL") or "").strip()


def _init_pool():
    global _pool
    if _pool is not None:
        return _pool

    database_url = _database_url()
    if not database_url:
        raise GPTDatabaseUnavailable("gpt_database_not_configured")

    with _pool_lock:
        if _pool is None:
            _pool = ThreadedConnectionPool(
                minconn=1,
                maxconn=3,
                dsn=database_url,
                connect_timeout=10,
                keepalives=1,
                keepalives_idle=30,
                keepalives_interval=10,
                keepalives_count=5,
            )
            logger.info("GPT read-only database pool initialized")
    return _pool


def get_gpt_db():
    """Return a connection configured read-only for this request only."""
    try:
        conn = _init_pool().getconn()
        if conn.closed:
            raise GPTDatabaseUnavailable("gpt_database_connection_closed")
        conn.rollback()
        conn.set_session(readonly=True, autocommit=False)
        with conn.cursor() as cur:
            # Keep both settings explicit even if the role was misconfigured.
            cur.execute("SET default_transaction_read_only = on")
            cur.execute("SET statement_timeout = %s", (GPT_DB_STATEMENT_TIMEOUT_MS,))
        return conn
    except (PoolError, OperationalError, InterfaceError, GPTDatabaseUnavailable) as exc:
        logger.warning("gpt_database_unavailable type=%s", type(exc).__name__)
        raise GPTDatabaseUnavailable("gpt_database_unavailable") from None


def close_gpt_db(conn, cur=None):
    """Rollback and return the connection; read paths deliberately never commit."""
    if cur is not None and not cur.closed:
        cur.close()
    if conn is None:
        return
    try:
        if not conn.closed:
            conn.rollback()
    except (OperationalError, InterfaceError):
        pass
    pool = _pool
    if pool is not None:
        try:
            pool.putconn(conn, close=bool(conn.closed))
            return
        except (PoolError, OperationalError, InterfaceError):
            pass
    try:
        conn.close()
    except (OperationalError, InterfaceError):
        pass


def reset_gpt_pool():
    """Test/support helper; never used by request handling."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.closeall()
        _pool = None
