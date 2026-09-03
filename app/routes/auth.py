# app/routes/auth.py

import psycopg2
from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from app.config import INVITE_CODE
from app.db import close_db, get_db
from app.services.login_rate_limit_service import (
    clear_login_failures,
    is_login_blocked,
    record_login_failure,
)
from app.services.tournament_context_service import (
    get_session_start_tournament_id,
    select_default_tournament_by_unfinished_match,
)


auth_bp = Blueprint('auth', __name__)


def is_password_hash(value):
    if not value:
        return False
    return value.startswith("pbkdf2:") or value.startswith("scrypt:")


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        remote_addr = request.remote_addr

        if is_login_blocked(remote_addr, username):
            flash("Слишком много неудачных попыток. Попробуйте позже.", "error")
            return render_template('login.html'), 429

        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT id, password, COALESCE(is_deleted, 0)
                FROM users
                WHERE username = %s
                """,
                (username,),
            )
            user = cur.fetchone()

            if not user:
                record_login_failure(remote_addr, username)
                flash("Неверное имя или пароль", "error")
                return render_template('login.html')

            user_id = user[0]
            stored_password = user[1]
            is_deleted = user[2]

            if is_deleted == 1:
                record_login_failure(remote_addr, username)
                flash("Неверное имя или пароль", "error")
                return render_template('login.html')

            if is_password_hash(stored_password):
                password_ok = check_password_hash(stored_password, password)
            else:
                password_ok = stored_password == password
                if password_ok:
                    new_hash = generate_password_hash(password)
                    cur.execute(
                        """
                        UPDATE users
                        SET password = %s
                        WHERE id = %s
                        """,
                        (new_hash, user_id),
                    )
                    conn.commit()

            if not password_ok:
                record_login_failure(remote_addr, username)
                flash("Неверное имя или пароль", "error")
                return render_template('login.html')

            clear_login_failures(remote_addr, username)

            selected_tournament_id = select_default_tournament_by_unfinished_match(cur)
            if selected_tournament_id is None:
                selected_tournament_id = get_session_start_tournament_id(cur)
            session.clear()
            session['user_id'] = user_id
            if selected_tournament_id:
                session['selected_tournament_id'] = selected_tournament_id
                session['tournament_selection_initialized'] = True
            session.permanent = True
            return redirect(
                url_for('main.index', tid=selected_tournament_id)
                if selected_tournament_id
                else url_for('main.index'),
                code=303,
            )
        finally:
            close_db(conn, cur)

    return render_template('login.html')


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        invite_code = request.form.get('invite_code', '').strip()
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        if invite_code != INVITE_CODE:
            flash("Неверный инвайт-код", "error")
            return redirect(url_for('auth.register'))

        if not username or not password:
            flash("Введите логин и пароль", "error")
            return redirect(url_for('auth.register'))

        password_hash = generate_password_hash(password)

        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO users (username, password)
                VALUES (%s, %s)
                """,
                (username, password_hash),
            )
            conn.commit()

            flash("Регистрация успешна, теперь войдите", "success")
            return redirect(url_for('auth.login'))
        except psycopg2.IntegrityError:
            conn.rollback()
            flash("Такой пользователь уже существует", "error")
        finally:
            close_db(conn, cur)

    return render_template('register.html')


@auth_bp.route('/logout', methods=['POST'])
def logout():
    session.pop('user_id', None)
    session.pop('selected_tournament_id', None)
    session.pop('tournament_selection_initialized', None)
    flash("Вы вышли из аккаунта", "success")
    return redirect(url_for('auth.login'))
