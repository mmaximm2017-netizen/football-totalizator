# app/__init__.py
import logging
import hmac
import json
import os
import secrets
from urllib.parse import urlsplit
from flask import Flask, g, session, request, abort, jsonify, make_response
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
DIAGNOSTICS_MAX_BODY_BYTES = 4096
DIAGNOSTICS_STRING_FIELDS = {
    "event", "pathname", "readyState", "visibilityState", "splashClass",
    "splashDisplay", "splashVisibility", "splashOpacity", "splashPointerEvents",
    "transitionClass", "transitionDisplay", "transitionVisibility", "transitionOpacity",
    "serviceWorkerScriptPath", "navigationType", "errorMessage", "resourcePath",
    "pendingResources", "brokenResources", "resourceStateSummary", "slowResources", "fontStatus",
}
DIAGNOSTICS_BOOLEAN_FIELDS = {
    "splashExists", "transitionExists", "serviceWorkerControlled",
}
DIAGNOSTICS_NUMBER_FIELDS = {
    "timestamp", "domContentLoadedEventEnd", "loadEventEnd",
    "pendingResourceCount", "brokenResourceCount",
}
DIAGNOSTICS_PATH_FIELDS = {"pathname", "resourcePath", "serviceWorkerScriptPath"}
DIAGNOSTICS_STRING_LIMITS = {
    "pendingResources": 700,
    "brokenResources": 700,
    "resourceStateSummary": 400,
    "slowResources": 700,
    "fontStatus": 100,
}


def diagnostics_enabled():
    return os.getenv("IOS_DIAGNOSTICS") == "1"


def sanitize_diagnostics_payload(payload):
    if not isinstance(payload, dict):
        return {}

    sanitized = {}
    for field, value in payload.items():
        if field in DIAGNOSTICS_STRING_FIELDS and isinstance(value, str):
            value = value[:DIAGNOSTICS_STRING_LIMITS.get(field, 512)]
            if field in DIAGNOSTICS_PATH_FIELDS:
                parsed = urlsplit(value)
                if parsed.scheme or parsed.netloc:
                    continue
                value = parsed.path[:512]
            sanitized[field] = value
        elif field in DIAGNOSTICS_BOOLEAN_FIELDS and isinstance(value, bool):
            sanitized[field] = value
        elif field in DIAGNOSTICS_NUMBER_FIELDS and isinstance(value, (int, float)) and not isinstance(value, bool):
            sanitized[field] = value

    return sanitized


def diagnostics_request_is_same_origin():
    origin = request.headers.get("Origin")
    if origin:
        origin_parts = urlsplit(origin)
        host_parts = urlsplit(request.host_url)
        if (
            origin_parts.scheme.lower() != host_parts.scheme.lower()
            or origin_parts.netloc.lower() != host_parts.netloc.lower()
        ):
            return False

    fetch_site = request.headers.get("Sec-Fetch-Site")
    return not fetch_site or fetch_site.lower() == "same-origin"


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
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    app.secret_key = SECRET_KEY
    app.config.update(
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_DOMAIN=None,
        SESSION_COOKIE_PATH="/",
        IOS_DIAGNOSTICS=diagnostics_enabled(),
        MAX_CONTENT_LENGTH=9 * 1024 * 1024,
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
    from app.routes.push import push_bp
    from app.routes.agent_api import agent_api_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(admin_sync_bp)
    app.register_blueprint(admin_matches_bp)
    app.register_blueprint(admin_tournaments_bp)
    app.register_blueprint(profile_bp)
    app.register_blueprint(table_bp)
    app.register_blueprint(predictions_bp)
    app.register_blueprint(push_bp)
    app.register_blueprint(agent_api_bp)

    @app.get("/service-worker.js")
    def service_worker():
        response = make_response(app.send_static_file("service-worker.js"))
        response.headers["Content-Type"] = "application/javascript"
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Service-Worker-Allowed"] = "/"
        return response

    @app.post("/__diagnostics/client")
    def client_diagnostics():
        if not app.config["IOS_DIAGNOSTICS"]:
            abort(404)
        if not diagnostics_request_is_same_origin():
            abort(403)
        if "user_id" not in session:
            abort(401)
        if not request.is_json:
            abort(415)
        if request.content_length is not None and request.content_length > DIAGNOSTICS_MAX_BODY_BYTES:
            abort(413)

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            abort(400)

        logger.info("ios_client_diagnostics payload=%s", json.dumps(sanitize_diagnostics_payload(payload), sort_keys=True))
        return "", 204

    # =====================================================
    # CSRF BASELINE
    # =====================================================
    @app.context_processor
    def inject_csrf_token():
        return {"csrf_token": ensure_csrf_token()}

    @app.before_request
    def log_ios_diagnostics():
        if not app.config["IOS_DIAGNOSTICS"]:
            return

        logger.info(
            "ios_diagnostics request_id=%s method=%s path=%s scheme=%s forwarded_proto=%s "
            "session_cookie_present=%s session_user_id_present=%s user_agent=%s",
            request.headers.get("X-Request-ID", "-"),
            request.method,
            request.path,
            request.scheme,
            request.headers.get("X-Forwarded-Proto", "-"),
            app.config["SESSION_COOKIE_NAME"] in request.cookies,
            "user_id" in session,
            request.user_agent.string,
        )

    @app.after_request
    def prevent_auth_caching(response):
        if request.path in {"/login", "/logout"}:
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.before_request
    def csrf_protect():
        # This project uses this custom CSRF hook instead of Flask-WTF CSRFProtect.
        # Exempt only the actual registered diagnostics view, never a matching path.
        if request.method == "POST" and app.view_functions.get(request.endpoint) is client_diagnostics:
            return
        # Agent API authenticates every request with a dedicated bearer token.
        # Exempt by registered endpoint, not by a user-controlled path prefix.
        if request.endpoint and request.endpoint.startswith("agent_api."):
            return
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

        if app.config["IOS_DIAGNOSTICS"] and request.path == "/__diagnostics/client":
            return

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
