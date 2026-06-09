# app/routes/admin.py

from flask import (
    Blueprint,
    render_template,
    request,
)

from app.db import get_db, close_db
from app.routes.admin_common import admin_required
from app.routes.admin_sync import (
    build_sync_panel_context,
)
from app.routes.admin_actions import ALLOWED_TITLES, dispatch_admin_action
from app.routes.admin_tournaments import (
    handle_activate_tournament,
    handle_archive_tournament,
)
from app.services.admin_view_service import (
    prepare_admin_matches_data,
    prepare_admin_view_data,
)


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


@admin_bp.route('/matches', methods=['GET'])
@admin_required
def admin_matches():
    requested_tid = request.args.get('tid', type=int)
    conn = get_db()
    cur = conn.cursor()
    try:
        data = prepare_admin_matches_data(cur)
        tournament_ids = {t.get('id') for t in data.get('tournaments', [])}
        selected_tid = requested_tid if requested_tid in tournament_ids else None
        selected_tournament = next(
            (t for t in data.get('tournaments', []) if t.get('id') == selected_tid),
            None,
        )
        data['selected_recalc_tournament_id'] = selected_tid
        data['current_tournament_id'] = selected_tid
        data['current_tournament_name'] = selected_tournament.get('name') if selected_tournament else 'Админка'
        data.update(build_sync_panel_context())
    finally:
        close_db(conn, cur)
    return render_template('admin_matches.html', **data)


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


@admin_bp.route('/archive_tournament/<int:tid>', methods=['POST'])
@admin_required
def archive_tournament(tid):
    return handle_archive_tournament(tid)


@admin_bp.route('/activate_tournament/<int:tid>', methods=['POST'])
@admin_required
def activate_tournament(tid):
    return handle_activate_tournament(tid)



