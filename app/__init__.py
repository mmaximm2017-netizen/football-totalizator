# app/__init__.py
import logging
import hmac
import secrets
from flask import Flask, g, session, request, abort, jsonify
from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import SECRET_KEY
from app.db import get_db, close_db, PoolExhausted
from app.services.ranking_service import get_tournament_ranking
from app.services.tournament_service import (
    ensure_single_active_tournament,
    get_active_tournament_id,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CSRF_SESSION_KEY = "csrf_token"


def ensure_csrf_token():
    token = session.get(CSRF_SESSION_KEY)

    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token

    return token


def create_app():
    logger.info("[STARTUP] create_app start")
    app = Flask(
        __name__,
        template_folder='../templates',
        static_folder='../static'
    )
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    app.secret_key = SECRET_KEY
    app.config.update(
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )

    # ❗ правильно: Flask ждёт timedelta, а не int
    from datetime import timedelta
    app.permanent_session_lifetime = timedelta(days=7)

    # IMPORTANT:
    # Do not run DB DDL/migrations, API sync, recalculation, or background loops
    # during web startup. Render must see an open port quickly.
    logger.info("[STARTUP] db init skipped in web startup")

    # =====================================================
    # BLUEPRINTS
    # =====================================================
    from app.routes.main import main_bp
    from app.routes.auth import auth_bp
    from app.routes.admin import admin_bp
    from app.routes.admin_sync import admin_sync_bp
    from app.routes.admin_matches import admin_matches_bp
    from app.routes.admin_tournaments import admin_tournaments_bp
    from app.routes.profile import profile_bp
    from app.routes.table import table_bp
    from app.routes.predictions import predictions_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(admin_sync_bp)
    app.register_blueprint(admin_matches_bp)
    app.register_blueprint(admin_tournaments_bp)
    app.register_blueprint(profile_bp)
    app.register_blueprint(table_bp)
    app.register_blueprint(predictions_bp)

    # =====================================================
    # CSRF BASELINE
    # =====================================================
    @app.context_processor
    def inject_csrf_token():
        return {"csrf_token": ensure_csrf_token()}

    @app.before_request
    def csrf_protect():
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return

        token = ensure_csrf_token()
        provided = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")

        if not provided or not hmac.compare_digest(str(provided), str(token)):
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"ok": False, "message": "CSRF token invalid"}), 400

            abort(400)

    # =====================================================
    # POOL EXHAUSTED HANDLER
    # =====================================================
    @app.errorhandler(PoolExhausted)
    def handle_pool_exhausted(e):
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": False, "message": str(e)}), 503

        return "Service temporarily unavailable. Please try again later.", 503

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
                "SELECT is_admin, last_seen, COALESCE(is_deleted, 0) FROM users WHERE id = %s",
                (session['user_id'],)
            )

            user = cur.fetchone()

            if user:
                if user[2] == 1:
                    session.pop('user_id', None)
                    return

                if user[0] == 1:
                    g.is_admin = True

                # Throttle writes: update last_seen at most once every 10 minutes.
                try:
                    cur.execute(
                        """
                        UPDATE users
                        SET last_seen = NOW()
                        WHERE id = %s
                          AND (last_seen IS NULL OR last_seen < NOW() - INTERVAL '10 minutes')
                        """,
                        (session['user_id'],)
                    )
                    conn.commit()
                except Exception:
                    conn.rollback()

        finally:
            close_db(conn, cur)

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})

    @app.get("/health/db")
    def health_db():
        result = {
            "db": "fail",
            "active_tournament": "fail",
            "ranking": "fail",
            "single_active": "fail",
        }
        http_status = 500

        try:
            conn = get_db()
            cur = conn.cursor()
            try:
                cur.execute("SELECT 1")
                cur.fetchone()
                result["db"] = "ok"

                active_tid = get_active_tournament_id()
                if active_tid:
                    result["active_tournament"] = "ok"

                    ranking = get_tournament_ranking(active_tid)
                    if isinstance(ranking, list):
                        result["ranking"] = "ok"

                single = ensure_single_active_tournament()
                if single.get("ok"):
                    result["single_active"] = "ok"
                else:
                    result["single_active"] = f"warn:{single.get('active_count')}"
            finally:
                close_db(conn, cur)
        except Exception:
            pass

        if (
            result["db"] == "ok"
            and result["active_tournament"] == "ok"
            and result["ranking"] == "ok"
            and str(result["single_active"]).startswith("ok")
        ):
            http_status = 200
        elif (
            result["db"] == "ok"
            and result["active_tournament"] == "ok"
            and result["ranking"] == "ok"
        ):
            http_status = 200

        return jsonify(result), http_status

    logger.info("[STARTUP] create_app done")
    return app
