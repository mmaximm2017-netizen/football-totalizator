"""Read-only aggregate statistics for the signed-in participant profile."""

from app.db import close_db, get_db

POINT_BUCKETS = (11, 10, 8, 7, 5, 3, 2, 0)


class ProfileStatsIntegrityError(RuntimeError):
    """Finished prediction points contain values outside the scoring contract."""


def get_profile_stats(user_id, tournament_id):
    """Return bounded finished-prediction stats for one user and tournament."""
    conn = cur = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                COUNT(*),
                COALESCE(SUM(p.points), 0),
                COALESCE(AVG(p.points), 0),
                COALESCE(SUM(CASE WHEN ABS(m.home_score - m.away_score) >= 3 THEN 11 ELSE 10 END), 0),
                COALESCE(SUM(CASE WHEN SIGN(p.home_goals - p.away_goals) = SIGN(m.home_score - m.away_score) THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN p.points IN (7, 8, 10, 11) THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN p.home_goals = m.home_score AND p.away_goals = m.away_score THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN p.points = 0 THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN p.points = 11 THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN p.points = 10 THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN p.points = 8 THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN p.points = 7 THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN p.points = 5 THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN p.points = 3 THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN p.points = 2 THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN p.points = 0 THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN p.points IS NULL OR p.points NOT IN (0, 2, 3, 5, 7, 8, 10, 11) THEN 1 ELSE 0 END), 0)
            FROM predictions p
            JOIN matches m ON m.id = p.match_id
                         AND m.tournament_id = p.tournament_id
            WHERE p.user_id = %s
              AND p.tournament_id = %s
              AND m.status = 'FINISHED'
            """,
            (user_id, tournament_id),
        )
        aggregate = cur.fetchone() or (0,) * 17
        submitted_count = int(aggregate[0] or 0)
        bucket_counts = dict(zip(POINT_BUCKETS, (int(value or 0) for value in aggregate[8:16])))
        unexpected_count = int(aggregate[16] or 0)
        if unexpected_count or sum(bucket_counts.values()) != submitted_count:
            raise ProfileStatsIntegrityError("unexpected_finished_prediction_points")

        cur.execute(
            """
            SELECT p.points
            FROM predictions p
            JOIN matches m ON m.id = p.match_id
                         AND m.tournament_id = p.tournament_id
            WHERE p.user_id = %s
              AND p.tournament_id = %s
              AND m.status = 'FINISHED'
            ORDER BY m.kickoff_time DESC NULLS LAST, m.id DESC
            LIMIT 10
            """,
            (user_id, tournament_id),
        )
        newest_first = [int(row[0]) for row in cur.fetchall()]
    finally:
        if conn is not None:
            close_db(conn, cur)

    if any(points not in POINT_BUCKETS for points in newest_first):
        raise ProfileStatsIntegrityError("unexpected_recent_prediction_points")

    total_points = int(aggregate[1] or 0)
    maximum_points = int(aggregate[3] or 0)
    percentage = round((total_points / maximum_points * 100) if maximum_points else 0, 1)
    quality_counts = {
        "correct_outcome": int(aggregate[4] or 0),
        "seven_plus": int(aggregate[5] or 0),
        "exact_score": int(aggregate[6] or 0),
        "zero_points": int(aggregate[7] or 0),
    }
    recent_oldest_first = list(reversed(newest_first))
    comparison = None
    if len(newest_first) == 10:
        latest_five = sum(newest_first[:5])
        previous_five = sum(newest_first[5:10])
        comparison = {
            "latest_five": latest_five,
            "previous_five": previous_five,
            "difference": latest_five - previous_five,
        }

    return {
        "submitted_count": submitted_count,
        "total_points": total_points,
        "average_points": round(float(aggregate[2] or 0), 1),
        "maximum_points": maximum_points,
        "percentage": percentage,
        "quality": {
            name: {
                "count": count,
                "percent": round(count / submitted_count * 100, 1) if submitted_count else 0,
            }
            for name, count in quality_counts.items()
        },
        "buckets": [
            {"points": points, "count": bucket_counts[points], "percent": round(bucket_counts[points] / submitted_count * 100, 1) if submitted_count else 0}
            for points in POINT_BUCKETS
        ],
        "recent_points": recent_oldest_first,
        "comparison": comparison,
    }
