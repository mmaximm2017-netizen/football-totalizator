from functools import wraps

from flask import flash, redirect, session, url_for

from app.db import close_db, get_db


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("auth.login"))

        conn = get_db()
        cur = conn.cursor()

        try:
            cur.execute(
                "SELECT is_admin FROM users WHERE id = %s",
                (session["user_id"],),
            )
            user = cur.fetchone()

            if not user or user[0] != 1:
                flash("������ ��������", "error")
                return redirect(url_for("main.index"))

        finally:
            close_db(conn, cur)

        return f(*args, **kwargs)

    return decorated
