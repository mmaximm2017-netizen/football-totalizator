# app/routes/auth.py

from datetime import timedelta

import psycopg2
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, current_app
from werkzeug.security import generate_password_hash, check_password_hash

from app.db import get_db, close_db
from app.config import INVITE_CODE


auth_bp = Blueprint('auth', __name__)


def is_password_hash(value):
    if not value:
        return False

    return (
        value.startswith("pbkdf2:")
        or value.startswith("scrypt:")
    )


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':

        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        conn = get_db()
        cur = conn.cursor()

        try:
            cur.execute("""
                SELECT id, password
                FROM users
                WHERE username = %s
            """, (username,))

            user = cur.fetchone()

            if not user:
                flash("Неверное имя или пароль", "error")
                return render_template('login.html')

            user_id = user[0]
            stored_password = user[1]

            password_ok = False

            if is_password_hash(stored_password):
                password_ok = check_password_hash(stored_password, password)
            else:
                password_ok = stored_password == password

                if password_ok:
                    new_hash = generate_password_hash(password)

                    cur.execute("""
                        UPDATE users
                        SET password = %s
                        WHERE id = %s
                    """, (new_hash, user_id))

                    conn.commit()

            if not password_ok:
                flash("Неверное имя или пароль", "error")
                return render_template('login.html')

            session['user_id'] = user_id
            session.permanent = True
            current_app.permanent_session_lifetime = timedelta(days=7)

            return redirect(url_for('main.index'))

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
            cur.execute("""
                INSERT INTO users (username, password)
                VALUES (%s, %s)
            """, (username, password_hash))

            conn.commit()

            flash("Регистрация успешна, теперь войдите", "success")
            return redirect(url_for('auth.login'))

        except psycopg2.IntegrityError:
            conn.rollback()
            flash("Такой пользователь уже существует", "error")

        finally:
            close_db(conn, cur)

    return render_template('register.html')


@auth_bp.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('auth.login'))