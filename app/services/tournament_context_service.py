from app.db import close_db, get_db
from app.services.tournament_service import (
    get_active_tournament_id,
    get_tournament_by_id,
)
from app.utils import utc_now


def get_session_start_tournament_id(cur):
    """Choose one active tournament for a new authenticated session."""
    cur.execute(
        """
        SELECT m.tournament_id
        FROM matches m
        JOIN tournaments t ON t.id = m.tournament_id
        WHERE t.is_active = 1
          AND m.tournament_id IS NOT NULL
          AND m.deadline IS NOT NULL
          AND m.deadline > NOW()
          AND COALESCE(UPPER(m.status), 'SCHEDULED')
              NOT IN ('FINISHED', 'COMPLETE', 'COMPLETED', 'CANCELLED', 'POSTPONED', 'SUSPENDED', 'LIVE', 'IN_PLAY', 'PAUSED', 'HALFTIME')
        ORDER BY
          m.deadline ASC,
          m.kickoff_time ASC,
          m.id ASC
        LIMIT 1
        """
    )
    row = cur.fetchone()
    if row and row[0]:
        return row[0]

    cur.execute(
        """
        SELECT m.tournament_id
        FROM matches m
        JOIN tournaments t ON t.id = m.tournament_id
        WHERE t.is_active = 1
          AND m.tournament_id IS NOT NULL
          AND m.kickoff_time >= NOW()
          AND COALESCE(UPPER(m.status), 'SCHEDULED')
              NOT IN ('FINISHED', 'COMPLETE', 'COMPLETED', 'CANCELLED', 'POSTPONED', 'SUSPENDED', 'LIVE', 'IN_PLAY', 'PAUSED', 'HALFTIME')
        ORDER BY
          m.kickoff_time ASC,
          m.id ASC
        LIMIT 1
        """
    )
    row = cur.fetchone()
    if row and row[0]:
        return row[0]

    cur.execute(
        """
        SELECT id
        FROM tournaments
        WHERE is_active = 1
        ORDER BY id
        LIMIT 1
        """
    )
    row = cur.fetchone()
    return row[0] if row else None


def select_default_tournament_by_unfinished_match(cur, now=None):
    """Choose the active tournament containing the current or next visible match."""
    now = now or utc_now()
    candidate_statuses = ['SCHEDULED', 'TIMED', 'IN_PLAY', 'LIVE', 'PAUSED', 'HALFTIME']

    cur.execute(
        """
        SELECT m.tournament_id
        FROM matches m
        JOIN tournaments t ON t.id = m.tournament_id
        WHERE t.is_active = 1
          AND m.tournament_id IS NOT NULL
          AND m.kickoff_time IS NOT NULL
          AND COALESCE(UPPER(m.status), 'SCHEDULED') = ANY(%s)
          AND m.kickoff_time <= %s
        ORDER BY m.kickoff_time DESC, m.id DESC
        LIMIT 1
        """,
        (candidate_statuses, now),
    )
    row = cur.fetchone()
    if row and row[0]:
        return row[0]

    cur.execute(
        """
        SELECT m.tournament_id
        FROM matches m
        JOIN tournaments t ON t.id = m.tournament_id
        WHERE t.is_active = 1
          AND m.tournament_id IS NOT NULL
          AND m.kickoff_time IS NOT NULL
          AND COALESCE(UPPER(m.status), 'SCHEDULED') = ANY(%s)
          AND m.kickoff_time > %s
        ORDER BY m.kickoff_time ASC, m.id ASC
        LIMIT 1
        """,
        (candidate_statuses, now),
    )
    row = cur.fetchone()
    return row[0] if row and row[0] else None


def get_nearest_upcoming_tournament_id(cur=None):
    """Choose the active tournament with the nearest upcoming match."""
    conn = None
    if cur is None:
        conn = get_db()
        cur = conn.cursor()
    try:
        return get_session_start_tournament_id(cur)
    finally:
        if conn is not None:
            close_db(conn, cur)


def get_first_active_tournament_id(cur=None):
    """Return the first explicitly active tournament by id."""
    conn = None
    if cur is None:
        conn = get_db()
        cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT id
            FROM tournaments
            WHERE is_active = 1
            ORDER BY id
            LIMIT 1
            """
        )
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        if conn is not None:
            close_db(conn, cur)


def get_latest_tournament_id(cur=None):
    """Return the latest tournament as the final user-facing fallback."""
    conn = None
    if cur is None:
        conn = get_db()
        cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT id
            FROM tournaments
            ORDER BY start_date DESC, id DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        if conn is not None:
            close_db(conn, cur)


def get_current_tournament_id():
    """Compatibility current-context lookup for non-route callers."""
    return (
        get_nearest_upcoming_tournament_id()
        or get_first_active_tournament_id()
        or get_active_tournament_id()
    )


def get_current_tournament():
    tournament_id = get_current_tournament_id()
    return get_tournament_by_id(tournament_id) if tournament_id else None


def get_selected_tournament_id(requested_tid, cur=None):
    """Canonical tournament selection for all user-facing routes.

    Order:
    1) explicit existing ``?tid=``;
    2) active tournament with the nearest upcoming match;
    3) first explicitly active tournament;
    4) latest tournament in history.
    """
    if requested_tid and get_tournament_by_id(requested_tid, cur=cur):
        return requested_tid

    return (
        get_nearest_upcoming_tournament_id(cur=cur)
        or get_first_active_tournament_id(cur=cur)
        or get_latest_tournament_id(cur=cur)
    )


def get_tournament_state_flags(tournaments):
    tournaments = tournaments or []
    has_any_tournament = bool(tournaments)
    has_active_tournament = any(t.get("is_active") for t in tournaments)

    return {
        "has_any_tournament": has_any_tournament,
        "has_active_tournament": has_active_tournament,
        "is_offseason": has_any_tournament and not has_active_tournament,
    }
