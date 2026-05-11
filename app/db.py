def get_active_tournament_id():
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT id
            FROM tournaments
            WHERE is_active = 1
            LIMIT 1
        """)
        row = cur.fetchone()
        return row[0] if row else 1
    finally:
        close_db(conn, cur)