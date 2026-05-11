# app/routes/auth.py
from datetime import timedelta
import psycopg2
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from app.db import get_db, close_db
from app.config import INVITE_CODE, ADMIN_USERNAME, ADMIN_PASSWORD

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        conn = get_db(); cur = conn.cursor()
        try:
            cur.execute("SELECT id FROM users WHERE username=%s AND password=%s", (request.form['username'], request.form['password']))
            user = cur.fetchone()
        finally: close_db(conn, cur)
        if user:
            session['user_id'] = user[0]
            session.permanent = True
            from flask import current_app
            current_app.permanent_session_lifetime = timedelta(days=7)
            return redirect(url_for('main.index'))
        flash("Неверное имя или пароль", "error")
    return render_template('login.html')

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        if request.form['invite_code'] != INVITE_CODE:
            flash("Неверный инвайт-код", "error")
            return redirect(url_for('auth.register'))
        conn = get_db(); cur = conn.cursor()
        try:
            cur.execute("INSERT INTO users (username, password) VALUES (%s,%s)", (request.form['username'], request.form['password']))
            flash("Регистрация успешна, теперь войдите", "success")
            return redirect(url_for('auth.login'))
        except psycopg2.IntegrityError:
            flash("Такой пользователь уже существует", "error")
        finally: close_db(conn, cur)
    return render_template('register.html')

@auth_bp.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('auth.login'))