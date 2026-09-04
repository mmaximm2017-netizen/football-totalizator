"""Guarded finalization path for automatic match results."""

from app.db import close_db, get_db
from app.services.scoring_recalculation_service import recalc_match_points


ALLOWED_AUTO_STATUSES = {"SCHEDULED", "TIMED", "LIVE"}
AUTO_RESULT_ORIGIN = "auto_result_worker"


class AutoResultFinalizeError(RuntimeError):
    """Raised when an automatic result cannot be safely finalized."""


def finalize_auto_result(
    match_id: int,
    home_score: int,
    away_score: int,
    *,
    tournament_id: int,
    league: str,
    expected_home_team: str,
    expected_away_team: str,
    expected_kickoff_time,
    expected_match_category: str,
) -> str:
    """Finalize one result only if the match is still untouched by a human.

    Returns ``saved`` when this call wrote the result and ``already_done`` when
    the match stopped being eligible before the write (for example because an
    admin entered the score while the worker was checking sources).
    """
    if not isinstance(home_score, int) or not isinstance(away_score, int):
        raise AutoResultFinalizeError("invalid_score_type")
    if home_score < 0 or away_score < 0:
        raise AutoResultFinalizeError("invalid_negative_score")

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT status, home_score, away_score, tournament_id, league,
                   home_team, away_team, kickoff_time, COALESCE(match_category, '')
            FROM matches
            WHERE id = %s
            FOR UPDATE
            """,
            (match_id,),
        )
        row = cur.fetchone()
        if not row:
            conn.rollback()
            raise AutoResultFinalizeError("match_not_found")

        (
            status, existing_home, existing_away, actual_tournament_id, actual_league,
            actual_home_team, actual_away_team, actual_kickoff_time, actual_match_category,
        ) = row
        if actual_tournament_id != tournament_id or actual_league != league:
            conn.rollback()
            raise AutoResultFinalizeError("match_scope_changed")
        if (
            actual_home_team != expected_home_team
            or actual_away_team != expected_away_team
            or actual_kickoff_time != expected_kickoff_time
            or actual_match_category != expected_match_category
        ):
            conn.rollback()
            raise AutoResultFinalizeError("match_identity_changed")
        if status not in ALLOWED_AUTO_STATUSES or existing_home is not None or existing_away is not None:
            conn.rollback()
            return "already_done"

        cur.execute(
            """
            UPDATE matches
            SET home_score = %s,
                away_score = %s,
                status = 'FINISHED',
                result_origin = %s
            WHERE id = %s
              AND tournament_id = %s
              AND league = %s
              AND status IN ('SCHEDULED', 'TIMED', 'LIVE')
              AND home_score IS NULL
              AND away_score IS NULL
            """,
            (
                home_score,
                away_score,
                AUTO_RESULT_ORIGIN,
                match_id,
                tournament_id,
                league,
            ),
        )
        if cur.rowcount != 1:
            conn.rollback()
            return "already_done"

        recalc_match_points(
            match_id,
            tournament_id=tournament_id,
            conn=conn,
            cur=cur,
        )
        conn.commit()
        return "saved"
    except Exception:
        conn.rollback()
        raise
    finally:
        close_db(conn, cur)
