from flask import flash, redirect, request, session, url_for

from app.routes.admin_matches import (
    handle_add_match,
    handle_set_result,
)
from app.routes.admin_sync import handle_manual_sync_update


ALLOWED_TITLES = (
    "Обладатель Кубка Матч-Премьер",
    "Чемпион Мира 2026",
)


def handle_award_title(conn, cur):
    user_id = request.form.get("user_id", type=int)
    title = (request.form.get("title") or "").strip()

    if not user_id or not title:
        flash("������� ������������ � �����", "error")
        return redirect(url_for("admin.admin"))

    if title not in ALLOWED_TITLES:
        flash("������������ �����", "error")
        return redirect(url_for("admin.admin"))

    try:
        cur.execute(
            """
            SELECT is_admin
            FROM users
            WHERE id = %s
            """,
            (user_id,),
        )
        row = cur.fetchone()

        if not row:
            flash("������������ �� ������", "error")
            return redirect(url_for("admin.admin"))

        if row[0] == 1:
            flash("������ �������� ����� ��������������", "error")
            return redirect(url_for("admin.admin"))

        cur.execute(
            """
            INSERT INTO user_titles (user_id, title, awarded_by)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id, title) DO NOTHING
            """,
            (user_id, title, session.get("user_id")),
        )

        if cur.rowcount == 0:
            conn.rollback()
            flash("� ������������ ��� ���� ���� �����", "error")
            return redirect(url_for("admin.admin"))

        conn.commit()
        flash("����� �����", "success")

    except Exception as e:
        conn.rollback()
        flash(f"������ ������ ������: {e}", "error")

    return redirect(url_for("admin.admin"))


ACTION_HANDLERS = {
    "update_matches": lambda conn, cur: handle_manual_sync_update(),
    "add_match": handle_add_match,
    "set_result": handle_set_result,
    "award_title": handle_award_title,
}


def dispatch_admin_action(action, conn, cur):
    handler = ACTION_HANDLERS.get(action)

    if not handler:
        flash("����������� ��������", "error")
        return redirect(url_for("admin.admin"))

    return handler(conn, cur)
