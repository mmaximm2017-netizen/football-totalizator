def save_prediction_before_deadline(cur, user_id, match_id, tournament_id, home_goals, away_goals):
    """Atomically insert/update one prediction only while the DB deadline is still open."""
    cur.execute(
        """
        INSERT INTO predictions
            (user_id, match_id, tournament_id, home_goals, away_goals)
        SELECT %s, %s, %s, %s, %s
        WHERE (
            SELECT CURRENT_TIMESTAMP < m.deadline
            FROM matches m
            WHERE m.id = %s
        )
        ON CONFLICT (user_id, match_id, tournament_id)
        DO UPDATE SET
            home_goals = EXCLUDED.home_goals,
            away_goals = EXCLUDED.away_goals
        WHERE (
            SELECT CURRENT_TIMESTAMP < m.deadline
            FROM matches m
            WHERE m.id = %s
        )
        RETURNING 1
        """,
        (
            user_id,
            match_id,
            tournament_id,
            home_goals,
            away_goals,
            match_id,
            match_id,
        ),
    )
    return cur.fetchone() is not None
