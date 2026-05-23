from app.db import close_db, get_db
from app.services.tournament_service import (
    get_active_tournament,
    get_active_tournament_id,
    get_tournament_by_id,
)


def get_nearest_upcoming_tournament_id():
    """
    Historical default used by the main page and table:
    choose the tournament with the nearest upcoming match.
    """
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT tournament_id
            FROM matches
            WHERE kickoff_time >= NOW()
              AND tournament_id IS NOT NULL
            GROUP BY tournament_id
            ORDER BY MIN(kickoff_time) ASC
            LIMIT 1
            """
        )
        row = cur.fetchone()
        return row[0] if row and row[0] else None
    finally:
        close_db(conn, cur)


def get_first_active_tournament_id():
    """
    Historical fallback used by duplicated route helpers:
    first active tournament by id ASC.
    """
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
        close_db(conn, cur)


def get_latest_tournament_id():
    """
    Last-resort table fallback when no current/active tournament exists.
    Mirrors the table page ordering.
    """
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
