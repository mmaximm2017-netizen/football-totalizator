# app/__init__.py
import logging
from flask import Flask, g, session

from app.config import SECRET_KEY
from app.db import init_db, get_db, close_db

logging.basicConfig(level=logging.INFO)


def create_app():
    app = Flask(
        __name__,
        template_folder='../templates',
        static_folder='../static'
    )

    app.secret_key = SECRET_KEY

    # ❗ правильно: Flask ждёт timedelta, а не int
    from datetime import timedelta
    app.permanent_session_lifetime = timedelta(days=7)

    # =====================================================
    # INIT DB
    # =====================================================
    with app.app_context():
        init_db()

        # IMPORTANT:
        # Do not run heavy data sync in web startup.
        # Under Gunicorn/Render create_app() can run in multiple workers,
        # which may trigger duplicate API/DB sync work and slow boot.
        # Keep match/points sync manual (admin action) for now.

    # =====================================================
    # BLUEPRINTS
    # =====================================================
    from app.routes.main import main_bp
    from app.routes.auth import auth_bp
    from app.routes.admin import admin_bp
    from app.routes.profile import profile_bp
    from app.routes.table import table_bp
    from app.routes.predictions import predictions_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(profile_bp)
    app.register_blueprint(table_bp)
    app.register_blueprint(predictions_bp)

    # =====================================================
    # BEFORE REQUEST (user context)
    # =====================================================
    @app.before_request
    def load_user():
        g.is_admin = False

        if 'user_id' not in session:
            return

        conn = get_db()
        cur = conn.cursor()

        try:
            cur.execute(
                "SELECT is_admin FROM users WHERE id = %s",
                (session['user_id'],)
            )

            user = cur.fetchone()

            if user and user[0] == 1:
                g.is_admin = True

        finally:
            close_db(conn, cur)

    return app
