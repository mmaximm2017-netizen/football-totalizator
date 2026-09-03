def get_auth_user(cur, username):
    cur.execute(
        """
        SELECT id, password, COALESCE(is_deleted, 0)
        FROM users
        WHERE username = %s
        """,
        (username,),
    )
    return cur.fetchone()


def upgrade_user_password_hash(cur, user_id, password_hash):
    cur.execute(
        """
        UPDATE users
        SET password = %s
        WHERE id = %s
        """,
        (password_hash, user_id),
    )


def create_user(cur, username, password_hash):
    cur.execute(
        """
        INSERT INTO users (username, password)
        VALUES (%s, %s)
        """,
        (username, password_hash),
    )
