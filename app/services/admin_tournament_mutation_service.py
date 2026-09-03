def translate_match_names(cur, translator):
    cur.execute("SELECT id, home_team, away_team FROM matches")
    rows = cur.fetchall()
    updated = 0
    for match_id, home_team, away_team in rows:
        new_home = translator(home_team)
        new_away = translator(away_team)
        if new_home == home_team and new_away == away_team:
            continue
        cur.execute(
            """
            UPDATE matches
            SET home_team = %s, away_team = %s
            WHERE id = %s
            """,
            (new_home, new_away, match_id),
        )
        updated += 1
    return updated


def create_tournament(cur, name, start_date):
    cur.execute(
        "SELECT id FROM tournaments WHERE LOWER(name) = LOWER(%s)",
        (name,),
    )
    if cur.fetchone():
        return False
    cur.execute(
        """
        INSERT INTO tournaments (name, is_active, start_date)
        VALUES (%s, 1, %s)
        """,
        (name, start_date),
    )
    return True


def set_tournament_active(cur, tournament_id, is_active):
    cur.execute(
        "UPDATE tournaments SET is_active = %s WHERE id = %s",
        (1 if is_active else 0, tournament_id),
    )
    return cur.rowcount > 0


def delete_archived_tournament(cur, tournament_id):
    cur.execute(
        "SELECT is_active FROM tournaments WHERE id = %s",
        (tournament_id,),
    )
    row = cur.fetchone()
    if not row:
        return "missing"
    if row[0] == 1:
        return "active"

    cur.execute(
        "SELECT 1 FROM predictions WHERE tournament_id = %s LIMIT 1",
        (tournament_id,),
    )
    if cur.fetchone():
        return "has_predictions"

    cur.execute("DELETE FROM tournaments WHERE id = %s", (tournament_id,))
    return "deleted"
