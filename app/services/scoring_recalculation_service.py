from app.db import close_db, get_db
from app.models.scoring import calculate_points


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
            SELECT id, home_score, away_score
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

        params = [match_id]
        tournament_filter = ""
        if tournament_id is not None:
            tournament_filter = "AND tournament_id = %s"
            params.append(tournament_id)

        cur.execute(
            f"""
            SELECT user_id, home_goals, away_goals, tournament_id
            FROM predictions
            WHERE match_id = %s
            {tournament_filter}
            """,
            tuple(params),
        )

        updated = 0
        for p in cur.fetchall():
            pts = calculate_points(
                match[1],
                match[2],
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
            WHERE m.status = 'FINISHED'
              AND p.tournament_id = %s
            ORDER BY m.id
            """,
            (tournament_id,),
        )

        match_ids = [r[0] for r in cur.fetchall()]
        total_updated = 0

        for match_id in match_ids:
            result = recalc_match_points(
                match_id,
                tournament_id=tournament_id,
                conn=conn,
                cur=cur,
            )
            total_updated += result.get("updated", 0)

        if owns_connection:
            conn.commit()

        return {
            "tournament_id": tournament_id,
            "matches": len(match_ids),
            "updated": total_updated,
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

        for match_id in match_ids:
            result = recalc_match_points(match_id, conn=conn, cur=cur)
            total_updated += result.get("updated", 0)

        if owns_connection:
            conn.commit()

        return {
            "matches": len(match_ids),
            "updated": total_updated,
        }
    except Exception:
        if owns_connection:
            conn.rollback()
        raise
    finally:
        if owns_connection:
            close_db(conn, cur)
