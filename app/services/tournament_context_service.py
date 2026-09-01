from app.db import close_db, get_db
from app.services.tournament_service import (
    get_active_tournament,
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
    """
    Historical default used by the main page and table:
    choose the tournament with the nearest upcoming match.
    """
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
    """
    Historical fallback used by duplicated route helpers:
    first active tournament by id ASC.
    """
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
    """
    Last-resort table fallback when no current/active tournament exists.
    Mirrors the table page ordering.
    """
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
    """
    Central current-tournament choice for main match context.

    Order intentionally preserves existing product behavior:
    1) tournament with nearest upcoming match
    2) first explicit active tournament
    3) runtime active tournament from tournament_service
       (latest start_date <= today, then latest active)
    """
    return (
        get_nearest_upcoming_tournament_id()
        or get_first_active_tournament_id()
        or get_active_tournament_id()
    )


def get_current_tournament():
    tournament_id = get_current_tournament_id()
    return get_tournament_by_id(tournament_id) if tournament_id else None


def get_selected_tournament_id(requested_tid, cur=None):
    """
    Unified selected tournament state for user-facing pages.

    The explicit ?tid= query param is the primary state. If it is missing
    or points to a missing tournament, use the shared fallback order.
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


def get_requested_or_current_tournament_id(requested_id):
    return requested_id or get_current_tournament_id()


def get_table_tournament_id(requested_id):
    """
    Table keeps its old final fallback: if there are tournaments but no
    current/active tournament, show the latest listed tournament.
    """
    return (
        requested_id
        or get_nearest_upcoming_tournament_id()
        or get_first_active_tournament_id()
        or get_latest_tournament_id()
    )


def get_profile_tournament_id(requested_id, active_tournaments=None):
    """
    Profile used to prefer the first active tournament from get_all_tournaments().
    Keep that behavior before falling back to tournament_service current active.
    """
    if requested_id:
        return requested_id

    active_tournaments = active_tournaments or []
    if active_tournaments:
        return active_tournaments[0].get("id")

    return get_active_tournament_id()


def get_active_context_tournament_id():
    """
    Active/current context for pages that historically used get_active_tournament_id().
    """
    tournament = get_active_tournament()
    return tournament["id"] if tournament else None
