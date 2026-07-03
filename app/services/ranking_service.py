from app.db import get_db, close_db


def apply_rank_movements(current_ranking, previous_ranking, has_finished_match=True):
    """Annotate current ranking with movement since the latest finished match."""
    previous_places = {
        row["user_id"]: row["place"]
        for row in previous_ranking
        if row.get("place") is not None
    }

    annotated = []
    for row in current_ranking:
        item = dict(row)
        movement = None

        if has_finished_match:
            previous_place = previous_places.get(item["user_id"])
            current_place = item.get("place")

            if previous_place is not None and current_place is not None:
                if current_place < previous_place:
                    movement = "up"
                elif current_place > previous_place:
                    movement = "down"

        item["movement"] = movement
        annotated.append(item)

    return annotated


def apply_leader_status(ranking):
    """Annotate edge players with leader/outsider statuses based on point gaps."""
    annotated = []
    for row in ranking:
        item = dict(row)
        item["leader_status"] = None
        item["outsider_status"] = None
        annotated.append(item)

    if not annotated or annotated[0].get("place") != 1:
        return annotated

    if len(annotated) < 2:
        annotated[0]["leader_status"] = "leader"
        return annotated

    try:
        gap = int(annotated[0].get("points") or 0) - int(annotated[1].get("points") or 0)
    except (TypeError, ValueError):
        return annotated

    if gap >= 40:
        annotated[0]["leader_status"] = "absolute"
    elif gap >= 30:
        annotated[0]["leader_status"] = "dominant"
    elif gap >= 20:
        annotated[0]["leader_status"] = "confident"
    elif gap >= 0:
        annotated[0]["leader_status"] = "leader"

    try:
        outsider_gap = int(annotated[-2].get("points") or 0) - int(annotated[-1].get("points") or 0)
    except (TypeError, ValueError):
        return annotated

    if outsider_gap >= 40:
        annotated[-1]["outsider_status"] = "absolute"
    elif outsider_gap >= 30:
        annotated[-1]["outsider_status"] = "dominant"
    elif outsider_gap >= 20:
        annotated[-1]["outsider_status"] = "confident"
    elif outsider_gap >= 0:
        annotated[-1]["outsider_status"] = "outsider"

    return annotated


def _fetch_ranking(cur, tournament_id, tournament_is_active, exclude_match_id=None):
    cur.execute(
        """
        WITH player_scores AS (
            SELECT
                u.id AS user_id,
                u.username,
                COALESCE(SUM(CASE WHEN m.id IS NOT NULL THEN p.points ELSE 0 END), 0) AS total_points,
                COALESCE(SUM(CASE WHEN m.id IS NOT NULL AND p.points >= 10 THEN 1 ELSE 0 END), 0) AS exact_scores,
                COALESCE(SUM(CASE WHEN m.id IS NOT NULL AND p.points BETWEEN 7 AND 8 THEN 1 ELSE 0 END), 0) AS exact_diffs,
                COALESCE(SUM(CASE WHEN m.id IS NOT NULL AND p.points = 3 THEN 1 ELSE 0 END), 0) AS outcomes
            FROM users u
            LEFT JOIN predictions p
                ON p.user_id = u.id
                AND p.tournament_id = %s
            LEFT JOIN matches m
                ON m.id = p.match_id
                AND m.tournament_id = p.tournament_id
                AND UPPER(m.status) IN ('FINISHED', 'COMPLETE', 'COMPLETED')
                AND m.kickoff_time <= NOW()
                AND (%s IS NULL OR m.id <> %s)
            WHERE u.is_admin = 0
              AND (%s = FALSE OR COALESCE(u.is_deleted, 0) = 0)
            GROUP BY u.id, u.username
        ),
        ranked AS (
            SELECT
                user_id,
                username,
                total_points,
                exact_scores,
                exact_diffs,
                outcomes,
                RANK() OVER (
                    ORDER BY
                        total_points DESC,
                        exact_scores DESC,
                        exact_diffs DESC,
                        outcomes DESC,
                        username ASC
                ) AS place_rank
            FROM player_scores
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
        (tournament_id, exclude_match_id, exclude_match_id, tournament_is_active),
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


def _fetch_latest_played_finished_match_id(cur, tournament_id):
    cur.execute(
        """
        SELECT id
        FROM matches
        WHERE tournament_id = %s
          AND UPPER(status) IN ('FINISHED', 'COMPLETE', 'COMPLETED')
          AND kickoff_time <= NOW()
        ORDER BY kickoff_time DESC, id DESC
        LIMIT 1
        """,
        (tournament_id,),
    )
    row = cur.fetchone()
    return row[0] if row else None


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
            SELECT COALESCE(is_active, 0)
            FROM tournaments
            WHERE id = %s
            """,
            (tournament_id,),
        )
        tournament = cur.fetchone()
        tournament_is_active = bool(tournament and tournament[0])

        current_ranking = _fetch_ranking(cur, tournament_id, tournament_is_active)

        last_finished_match_id = _fetch_latest_played_finished_match_id(cur, tournament_id)

        if not last_finished_match_id:
            return apply_leader_status(
                apply_rank_movements(current_ranking, [], has_finished_match=False)
            )

        previous_ranking = _fetch_ranking(
            cur,
            tournament_id,
            tournament_is_active,
            exclude_match_id=last_finished_match_id,
        )

        return apply_leader_status(apply_rank_movements(current_ranking, previous_ranking))
    finally:
        close_db(conn, cur)
