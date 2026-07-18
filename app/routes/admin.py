# app/routes/admin.py

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app.db import get_db, close_db
from app.routes.admin_common import admin_required
from app.routes.admin_actions import ALLOWED_TITLES, dispatch_admin_action
from app.routes.admin_tournaments import (
    handle_activate_tournament,
    handle_archive_tournament,
)
from app.services.admin_view_service import (
    prepare_admin_view_data,
    parse_admin_match_filters,
    parse_russian_cup_match_filters,
    parse_rpl_match_filters,
    prepare_rpl_match_list,
    prepare_russian_cup_match_list,
    prepare_wc_playoff_page_data,
)
from app.services.rpl_admin_service import prepare_rpl_admin_page_data
from app.services.russian_cup_admin_service import prepare_russian_cup_admin_page_data


# =========================================================
# BLUEPRINT
# =========================================================

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


# =========================================================
# MAIN ADMIN PAGE
# =========================================================

@admin_bp.route('/', methods=['GET', 'POST'])
@admin_required
def admin():

    conn = get_db()
    cur = conn.cursor()

    try:

        if request.method == 'POST':

            action = request.form.get('action')
            return dispatch_admin_action(action, conn, cur)

        return render_template('admin.html')
    finally:
        close_db(conn, cur)


@admin_bp.route('/wc-playoff', methods=['GET'])
@admin_required
def admin_wc_playoff():
    conn = get_db()
    cur = conn.cursor()
    try:
        filters = parse_admin_match_filters(request.args)
        data = prepare_wc_playoff_page_data(cur, filters)
        data['admin_match_filters'] = filters
        data['current_tournament_name'] = 'Плей-офф ЧМ-2026'
        cur.execute("SELECT DISTINCT team_name FROM (SELECT home_team AS team_name FROM matches WHERE tournament_id IN (SELECT id FROM tournaments WHERE name = 'ЧМ-2026') AND home_team IS NOT NULL UNION SELECT away_team AS team_name FROM matches WHERE tournament_id IN (SELECT id FROM tournaments WHERE name = 'ЧМ-2026') AND away_team IS NOT NULL) teams WHERE team_name IS NOT NULL AND team_name <> 'Unknown' ORDER BY team_name")
        data['wc_team_options'] = [row[0] for row in cur.fetchall()]
    finally:
        close_db(conn, cur)
    return render_template('admin_wc_playoff.html', **data)


@admin_bp.route('/russia-2027', methods=['GET'])
@admin_required
def admin_russia_2027():
    conn = get_db()
    cur = conn.cursor()
    try:
        data = prepare_rpl_admin_page_data(cur)
        data['current_tournament_name'] = 'Чемпионат России 🇷🇺'
        data['current_tournament_slug'] = 'rpl'
        if data.get('rpl_tournament'):
            data['current_tournament_id'] = data['rpl_tournament']['id']
        filters = parse_rpl_match_filters(request.args)
        data['admin_match_filters'] = filters
        if data.get('rpl_tournament'):
            data['admin_match_list'] = prepare_rpl_match_list(cur, data['rpl_tournament']['id'], filters)
        else:
            data['admin_match_list'] = {
                'matches': [], 'groups': [], 'total': 0, 'page': 1,
                'pages': 1, 'per_page': filters['per_page'], 'first': 0, 'last': 0,
                'fallback_notice': False, 'pending_count': 0,
            }
    finally:
        close_db(conn, cur)
    return render_template('admin_russia_2027.html', **data)


@admin_bp.route('/russian-cup', methods=['GET'])
@admin_required
def admin_russian_cup():
    conn = get_db()
    cur = conn.cursor()
    try:
        data = prepare_russian_cup_admin_page_data(cur)
        data['current_tournament_name'] = 'Кубок России'
        data['current_tournament_slug'] = 'rcup'
        if data.get('russian_cup_tournament'):
            data['current_tournament_id'] = data['russian_cup_tournament']['id']
            filters = parse_russian_cup_match_filters(request.args)
            data['admin_match_filters'] = filters
            data['admin_match_list'] = prepare_russian_cup_match_list(cur, data['russian_cup_tournament']['id'], filters)
        else:
            data['admin_match_filters'] = parse_russian_cup_match_filters(request.args)
            data['admin_match_list'] = {'matches': [], 'groups': [], 'total': 0, 'page': 1, 'pages': 1, 'per_page': 5, 'first': 0, 'last': 0}
    finally:
        close_db(conn, cur)
    return render_template('admin_russian_cup.html', **data)


@admin_bp.route('/tournaments', methods=['GET'])
@admin_required
def admin_tournaments():
    conn = get_db()
    cur = conn.cursor()
    try:
        data = prepare_admin_view_data(cur)
    finally:
        close_db(conn, cur)
    return render_template('admin_tournaments.html', **data)


@admin_bp.route('/users', methods=['GET'])
@admin_required
def admin_users():
    conn = get_db()
    cur = conn.cursor()
    try:
        data = prepare_admin_view_data(cur)
    finally:
        close_db(conn, cur)
    return render_template('admin_users.html', **data)


@admin_bp.route('/users/<int:user_id>/deactivate', methods=['POST'])
@admin_required
def deactivate_user(user_id):
    current_user_id = session.get('user_id')

    if user_id == current_user_id:
        flash("Нельзя деактивировать самого себя", "error")
        return redirect(url_for('admin.admin_users'))

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT username, is_admin, COALESCE(is_deleted, 0) FROM users WHERE id = %s", (user_id,))
        user = cur.fetchone()
        if not user:
            conn.rollback()
            flash("Пользователь не найден", "error")
            return redirect(url_for('admin.admin_users'))

        if user[1] == 1:
            cur.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1 AND COALESCE(is_deleted, 0) = 0")
            active_admins = cur.fetchone()[0] or 0
            if active_admins <= 1:
                conn.rollback()
                flash("Нельзя деактивировать последнего администратора", "error")
                return redirect(url_for('admin.admin_users'))

        cur.execute("UPDATE users SET is_deleted = 1 WHERE id = %s", (user_id,))
        conn.commit()

        flash("Пользователь деактивирован. Прогнозы сохранены.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Ошибка деактивации пользователя: {e}", "error")
    finally:
        close_db(conn, cur)

    return redirect(url_for('admin.admin_users'))


@admin_bp.route('/users/<int:user_id>/restore', methods=['POST'])
@admin_required
def restore_user(user_id):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE users SET is_deleted = 0 WHERE id = %s", (user_id,))
        if cur.rowcount == 0:
            conn.rollback()
            flash("Пользователь не найден", "error")
        else:
            conn.commit()
            flash("Пользователь восстановлен", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Ошибка восстановления пользователя: {e}", "error")
    finally:
        close_db(conn, cur)

    return redirect(url_for('admin.admin_users'))


@admin_bp.route('/archive_tournament/<int:tid>', methods=['POST'])
@admin_required
def archive_tournament(tid):
    return handle_archive_tournament(tid)


@admin_bp.route('/activate_tournament/<int:tid>', methods=['POST'])
@admin_required
def activate_tournament(tid):
    return handle_activate_tournament(tid)



