import logging
import unicodedata

from flask import flash, redirect, request, session, url_for

from app.routes.admin_matches import (
    handle_add_match,
    handle_set_result,
)
from app.routes.admin_sync import handle_manual_sync_update


logger = logging.getLogger(__name__)

ALLOWED_TITLES = (
    "Обладатель Кубка Матч-Премьер",
    "Чемпион Мира 2026",
)

def _admin_redirect():
    return redirect(url_for("admin.admin_users"))


def _contains_disallowed_title_control_chars(value):
    for char in value:
        if char in {"\n", "\r", "\t", "\u2028", "\u2029"}:
            return True
        if unicodedata.category(char) in {"Cc", "Cs"}:
            return True
    return False


def _resolve_title():
    custom_title = unicodedata.normalize(
        "NFC",
        request.form.get("custom_title") or "",
    )
    selected_title = (request.form.get("title") or "").strip()

    if custom_title:
        if _contains_disallowed_title_control_chars(custom_title):
            flash("Титул не должен содержать переносы строк или управляющие символы", "error")
            return None
        custom_title = custom_title.strip()
    if custom_title:
        if len(custom_title) > 40:
            flash("Свой титул не должен быть длиннее 40 символов", "error")
            return None
        return custom_title

    if selected_title not in ALLOWED_TITLES:
        flash("Выберите готовый титул или введите свой", "error")
        return None
    return selected_title


def _validate_title_user(cur, user_id):
    if not user_id:
        flash("Выберите пользователя", "error")
        return False

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
        flash("Пользователь не найден", "error")
        return False
    if row[0] == 1:
        flash("Нельзя назначать титулы администраторам", "error")
        return False
    return True


def handle_award_title(conn, cur):
    user_id = request.form.get("user_id", type=int)
    title = _resolve_title()
    if title is None or not _validate_title_user(cur, user_id):
        return _admin_redirect()

    try:
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
            flash("У пользователя уже есть этот титул", "error")
            return _admin_redirect()

        conn.commit()
        flash("Титул успешно выдан", "success")

    except Exception:
        conn.rollback()
        logger.exception("admin_title_award_failed user_id=%s", user_id)
        flash("Не удалось выдать титул. Ошибка записана в журнал.", "error")

    return _admin_redirect()


def handle_replace_title(conn, cur):
    user_id = request.form.get("user_id", type=int)
    old_title = (request.form.get("old_title") or "").strip()
    title = _resolve_title()
    if title is None or not old_title or not _validate_title_user(cur, user_id):
        return _admin_redirect()

    try:
        cur.execute(
            "DELETE FROM user_titles WHERE user_id = %s AND title = %s",
            (user_id, old_title),
        )
        if cur.rowcount == 0:
            conn.rollback()
            flash("Титул для замены не найден", "error")
            return _admin_redirect()

        cur.execute(
            "INSERT INTO user_titles (user_id, title, awarded_by) VALUES (%s, %s, %s)",
            (user_id, title, session.get("user_id")),
        )
        conn.commit()
        flash("Титул успешно заменён", "success")
    except Exception:
        conn.rollback()
        logger.exception("admin_title_replace_failed user_id=%s", user_id)
        flash("Не удалось заменить титул. Ошибка записана в журнал.", "error")
    return _admin_redirect()


def handle_remove_title(conn, cur):
    user_id = request.form.get("user_id", type=int)
    title = (request.form.get("title") or "").strip()
    if not title or not _validate_title_user(cur, user_id):
        return _admin_redirect()

    try:
        cur.execute(
            "DELETE FROM user_titles WHERE user_id = %s AND title = %s",
            (user_id, title),
        )
        if cur.rowcount == 0:
            conn.rollback()
            flash("Титул не найден", "error")
            return _admin_redirect()
        conn.commit()
        flash("Титул удалён", "success")
    except Exception:
        conn.rollback()
        logger.exception("admin_title_remove_failed user_id=%s", user_id)
        flash("Не удалось удалить титул. Ошибка записана в журнал.", "error")
    return _admin_redirect()


ACTION_HANDLERS = {
    "update_matches": lambda conn, cur: handle_manual_sync_update(),
    "add_match": handle_add_match,
    "set_result": handle_set_result,
    "award_title": handle_award_title,
    "replace_title": handle_replace_title,
    "remove_title": handle_remove_title,
}


def dispatch_admin_action(action, conn, cur):
    handler = ACTION_HANDLERS.get(action)

    if not handler:
        flash("����������� ��������", "error")
        return redirect(url_for("admin.admin"))

    return handler(conn, cur)
