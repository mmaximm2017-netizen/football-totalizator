def get_title_target_admin_flag(cur, user_id):
    cur.execute(
        "SELECT is_admin FROM users WHERE id = %s",
        (user_id,),
    )
    row = cur.fetchone()
    return None if row is None else row[0]


def award_title(cur, user_id, title, awarded_by):
    cur.execute(
        """
        INSERT INTO user_titles (user_id, title, awarded_by)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id, title) DO NOTHING
        """,
        (user_id, title, awarded_by),
    )
    return cur.rowcount > 0


def replace_title(cur, user_id, old_title, new_title, awarded_by):
    cur.execute(
        "DELETE FROM user_titles WHERE user_id = %s AND title = %s",
        (user_id, old_title),
    )
    if cur.rowcount == 0:
        return False
    cur.execute(
        "INSERT INTO user_titles (user_id, title, awarded_by) VALUES (%s, %s, %s)",
        (user_id, new_title, awarded_by),
    )
    return True


def remove_title(cur, user_id, title):
    cur.execute(
        "DELETE FROM user_titles WHERE user_id = %s AND title = %s",
        (user_id, title),
    )
    return cur.rowcount > 0
