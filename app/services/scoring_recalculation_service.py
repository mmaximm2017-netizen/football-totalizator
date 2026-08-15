from app.db import close_db, get_db
import logging

from app.models.scoring import calculate_points, has_valid_finished_score


logger = logging.getLogger(__name__)

RESULT_EVENT_TYPE = "match_result"


def _enqueue_result_event(cur, user_id, match_id):
    """Create the result-push outbox row only after points were calculated."""
    cur.execute(
        """
        INSERT INTO push_delivery_log
            (user_id, match_id, event_type, event_key, status, sent_at, updated_at)
        VALUES (%s, %s, %s, %s, 'ready', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT (user_id, event_type, event_key) DO NOTHING
        """,
        (user_id, match_id, RESULT_EVENT_TYPE, f"match:{match_id}"),
    )


def _get_cursor(conn=None, cur=None):
    if conn is not None and cur is not None:
        return conn, cur, False

    owned_conn = get_db()
    owned_cur = owned_conn.cursor()
    return owned_conn, owned_cur, True


def recalc_match_points(match_id, tournament_id=None, conn=None, cur=None):
    """
    Recalculate prediction points for one match.

    Optional conn/cur keeps existing admin transactions intact.
    """
    conn, cur, owns_connection = _get_cursor(conn, cur)

    try:
        cur.execute(
            """
            SELECT id, status, home_score, away_score, tournament_id
            FROM matches
            WHERE id = %s
            """,
            (match_id,),
        )
        match = cur.fetchone()

        if not match:
            return {
                "match_id": match_id,
                "updated": 0,
                "found": False,
            }

        if not has_valid_finished_score(match[1], match[2], match[3]) or match[4] is None:
            logger.warning(
                "Skipping points recalculation for match_id=%s status=%s home_score=%r away_score=%r: incomplete or invalid finished score",
                match_id,
                match[1],
                match[2],
                match[3],
            )
            return {
                "match_id": match_id,
                "tournament_id": tournament_id,
                "updated": 0,
                "found": True,
                "skipped": True,
                "reason": "incomplete_or_invalid_finished_score",
            }

        if tournament_id is not None and tournament_id != match[4]:
            return {
                "match_id": match_id,
                "tournament_id": tournament_id,
                "updated": 0,
                "found": True,
                "skipped": True,
                "reason": "match_tournament_mismatch",
            }

        cur.execute(
            f"""
            SELECT user_id, home_goals, away_goals, tournament_id
            FROM predictions
            WHERE match_id = %s
              AND tournament_id = %s
            """,
            (match_id, match[4]),
        )

        updated = 0
        for p in cur.fetchall():
            pts = calculate_points(
                match[2],
                match[3],
                p[1],
                p[2],
            )

            cur.execute(
                """
                UPDATE predictions
                SET points = %s
                WHERE user_id = %s
                  AND match_id = %s
                  AND tournament_id = %s
                """,
                (
                    pts,
                    p[0],
                    match_id,
                    p[3],
                ),
            )
            _enqueue_result_event(cur, p[0], match_id)
            updated += 1

        if owns_connection:
            conn.commit()

        return {
            "match_id": match_id,
            "tournament_id": tournament_id,
            "updated": updated,
            "found": True,
        }
    except Exception:
        if owns_connection:
            conn.rollback()
        raise
    finally:
        if owns_connection:
            close_db(conn, cur)


def recalc_tournament_points(tournament_id, conn=None, cur=None):
    """
    Recalculate points for finished matches that have predictions in a tournament.
    """
    conn, cur, owns_connection = _get_cursor(conn, cur)

    try:
        cur.execute(
            """
            SELECT DISTINCT m.id
            FROM matches m
            JOIN predictions p
              ON p.match_id = m.id
             AND p.tournament_id = m.tournament_id
            WHERE m.status = 'FINISHED'
              AND p.tournament_id = %s
            ORDER BY m.id
            """,
            (tournament_id,),
        )

        match_ids = [r[0] for r in cur.fetchall()]
        total_updated = 0
        skipped = 0

        for match_id in match_ids:
            result = recalc_match_points(
                match_id,
                tournament_id=tournament_id,
                conn=conn,
                cur=cur,
            )
            total_updated += result.get("updated", 0)
            skipped += int(result.get("skipped", False))

        if owns_connection:
            conn.commit()

        return {
            "tournament_id": tournament_id,
            "matches": len(match_ids),
            "updated": total_updated,
            "skipped": skipped,
        }
    except Exception:
        if owns_connection:
            conn.rollback()
        raise
    finally:
        if owns_connection:
            close_db(conn, cur)


def recalc_all_points(conn=None, cur=None):
    """
    Recalculate points for all predictions attached to finished matches.
    """
    conn, cur, owns_connection = _get_cursor(conn, cur)

    try:
        cur.execute(
            """
            SELECT id
            FROM matches
            WHERE status = 'FINISHED'
            ORDER BY id
            """
        )

        match_ids = [r[0] for r in cur.fetchall()]
        total_updated = 0
        skipped = 0

        for match_id in match_ids:
            result = recalc_match_points(match_id, conn=conn, cur=cur)
            total_updated += result.get("updated", 0)
            skipped += int(result.get("skipped", False))

        if owns_connection:
            conn.commit()

        return {
            "matches": len(match_ids),
            "updated": total_updated,
            "skipped": skipped,
        }
    except Exception:
        if owns_connection:
            conn.rollback()
        raise
    finally:
        if owns_connection:
            close_db(conn, cur)
