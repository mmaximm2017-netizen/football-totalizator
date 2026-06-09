from app.db import get_db, close_db


def get_tournament_ranking(tournament_id):
    """
    Canonical ranking logic for leaderboard/profile.
    Tie-break order:
    1) total_points
    2) exact_scores (points >= 10)
    3) exact_diffs (points 7 or 8)
    4) outcomes (points 3)
    5) username ASC

    Place numbering uses SQL RANK(): 1,1,3...
    """
    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute(
            """
            WITH ranked AS (
                SELECT
                    u.id AS user_id,
                    u.username,
                    COALESCE(SUM(p.points), 0) AS total_points,
                    COALESCE(SUM(CASE WHEN p.points >= 10 THEN 1 ELSE 0 END), 0) AS exact_scores,
                    COALESCE(SUM(CASE WHEN p.points BETWEEN 7 AND 8 THEN 1 ELSE 0 END), 0) AS exact_diffs,
                    COALESCE(SUM(CASE WHEN p.points = 3 THEN 1 ELSE 0 END), 0) AS outcomes,
                    RANK() OVER (
                        ORDER BY
                            COALESCE(SUM(p.points), 0) DESC,
                            COALESCE(SUM(CASE WHEN p.points >= 10 THEN 1 ELSE 0 END), 0) DESC,
                            COALESCE(SUM(CASE WHEN p.points BETWEEN 7 AND 8 THEN 1 ELSE 0 END), 0) DESC,
                            COALESCE(SUM(CASE WHEN p.points = 3 THEN 1 ELSE 0 END), 0) DESC,
                            u.username ASC
                    ) AS place_rank
                FROM users u
                LEFT JOIN predictions p
                    ON p.user_id = u.id
                    AND p.tournament_id = %s
                WHERE u.is_admin = 0
                GROUP BY u.id, u.username
            )
            SELECT
                user_id,
                username,
                total_points,
                exact_scores,
                exact_diffs,
                outcomes,
                place_rank,
                COUNT(*) OVER (PARTITION BY place_rank) AS shared_count
            FROM ranked
            ORDER BY
                total_points DESC,
                exact_scores DESC,
                exact_diffs DESC,
                outcomes DESC,
                username ASC
            """,
            (tournament_id,),
        )

        return [
            {
                "user_id": r[0],
                "username": r[1],
                "points": r[2] or 0,
                "exact_scores": r[3] or 0,
                "exact_diffs": r[4] or 0,
                "outcomes": r[5] or 0,
                "place": r[6],
                "shared": (r[7] or 0) > 1,
            }
            for r in cur.fetchall()
        ]
    finally:
        close_db(conn, cur)
