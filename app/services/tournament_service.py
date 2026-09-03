from datetime import datetime
from zoneinfo import ZoneInfo

from app.db import close_db, get_db

MSK = ZoneInfo("Europe/Moscow")


def _row_to_tournament(row):
    if not row:
        return None
    return {
        "id": row[0],
        "name": row[1],
        "is_active": bool(row[2]),
        "start_date": row[3],
        "end_date": row[4],
    }


def get_all_tournaments(cur=None):
    conn = None
    if cur is None:
        conn = get_db()
        cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT id, name, is_active, start_date, end_date
            FROM tournaments
            ORDER BY start_date DESC NULLS LAST, id DESC
            """
        )
        return [_row_to_tournament(r) for r in cur.fetchall()]
    finally:
        if conn is not None:
            close_db(conn, cur)


def get_tournament_by_id(tournament_id, cur=None):
    conn = None
    if cur is None:
        conn = get_db()
        cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT id, name, is_active, start_date, end_date
            FROM tournaments
            WHERE id = %s
            """,
            (tournament_id,),
        )
        return _row_to_tournament(cur.fetchone())
    finally:
        if conn is not None:
            close_db(conn, cur)


def get_active_tournament(cur=None):
    """
    Active tournament resolution for runtime use:
    1) latest by start_date <= today (MSK)
    2) fallback to latest is_active=1
    """
    conn = None
    if cur is None:
        conn = get_db()
        cur = conn.cursor()
    try:
        today = datetime.now(MSK).date().isoformat()

        cur.execute(
            """
            SELECT id, name, is_active, start_date, end_date
            FROM tournaments
            WHERE start_date IS NOT NULL
              AND start_date <= %s
            ORDER BY start_date DESC, id DESC
            LIMIT 1
            """,
            (today,),
        )
        row = cur.fetchone()
        if row:
            return _row_to_tournament(row)

        cur.execute(
            """
            SELECT id, name, is_active, start_date, end_date
            FROM tournaments
            WHERE is_active = 1
            ORDER BY id DESC
            LIMIT 1
            """
        )
        return _row_to_tournament(cur.fetchone())
    finally:
        if conn is not None:
            close_db(conn, cur)


def get_active_tournament_id(cur=None):
    t = get_active_tournament(cur=cur)
    return t["id"] if t else None


def count_active_tournaments(cur=None):
    conn = None
    if cur is None:
        conn = get_db()
        cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM tournaments WHERE is_active = 1")
        return cur.fetchone()[0] or 0
    finally:
        if conn is not None:
            close_db(conn, cur)


def ensure_single_active_tournament(cur=None):
    """
    Read-only safety check. Does not mutate data.
    """
    active_count = count_active_tournaments(cur=cur)
    return {
        "ok": active_count <= 1,
        "active_count": active_count,
    }


def get_tournament_status(tournament):
    """
    Status helper for future lifecycle usage.
    - active: explicit active flag
    - upcoming: start_date in future
    - finished: end_date in past (or non-active, already started)
    """
    if not tournament:
        return "finished"

    if tournament.get("is_active"):
        return "active"

    today = datetime.now(MSK).date()
    start_raw = tournament.get("start_date")
    end_raw = tournament.get("end_date")

    def parse_date(value):
        if not value:
            return None
        try:
            return datetime.strptime(str(value), "%Y-%m-%d").date()
        except Exception:
            return None

    start_date = parse_date(start_raw)
    end_date = parse_date(end_raw)

    if start_date and start_date > today:
        return "upcoming"
    if end_date and end_date < today:
        return "finished"
    if start_date and start_date <= today:
        return "finished"
    return "upcoming"
