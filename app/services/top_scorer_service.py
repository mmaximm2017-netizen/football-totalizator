from app.db import close_db, get_db

FINISHED_STATUSES = ("FINISHED", "COMPLETE", "COMPLETED")


def build_top_scorers(rows):
    scorers = {}

    for row in rows:
        status = row.get("status")
        if status is not None and str(status).upper() not in FINISHED_STATUSES:
            continue

        user_id = row["user_id"]
        scorer = scorers.setdefault(
            user_id,
            {
                "user_id": user_id,
                "username": row["username"],
                "scorer_goals": 0,
                "points": 0,
            },
        )
        scorer["points"] += row.get("points") or 0

        if (
            row.get("pred_home") is not None
            and row.get("pred_away") is not None
            and row.get("home_score") is not None
            and row.get("away_score") is not None
            and row["pred_home"] == row["home_score"]
            and row["pred_away"] == row["away_score"]
        ):
            scorer["scorer_goals"] += 1

    top_scorers = [row for row in scorers.values() if row["scorer_goals"] > 0]
    top_scorers.sort(key=lambda row: (-row["scorer_goals"], -row["points"], row["username"]))

    for index, row in enumerate(top_scorers, start=1):
        row["place"] = index

    return top_scorers


def get_tournament_top_scorers(tournament_id, cur=None):
    conn = None
    if cur is None:
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

        cur.execute(
            """
            SELECT
                u.id,
                u.username,
                p.home_goals,
                p.away_goals,
                m.home_score,
                m.away_score,
                COALESCE(p.points, 0)
            FROM predictions p
            JOIN users u ON u.id = p.user_id
            JOIN matches m ON m.id = p.match_id
            WHERE p.tournament_id = %s
              AND m.tournament_id = p.tournament_id
              AND u.is_admin = 0
              AND (%s = FALSE OR COALESCE(u.is_deleted, 0) = 0)
              AND UPPER(m.status) = ANY(%s)
              AND m.kickoff_time <= NOW()
              AND m.home_score IS NOT NULL
              AND m.away_score IS NOT NULL
              AND p.home_goals IS NOT NULL
              AND p.away_goals IS NOT NULL
            """,
            (tournament_id, tournament_is_active, list(FINISHED_STATUSES)),
        )

        rows = [
            {
                "user_id": row[0],
                "username": row[1],
                "pred_home": row[2],
                "pred_away": row[3],
                "home_score": row[4],
                "away_score": row[5],
                "points": row[6],
            }
            for row in cur.fetchall()
        ]

        return build_top_scorers(rows)
    finally:
        if conn is not None:
            close_db(conn, cur)
