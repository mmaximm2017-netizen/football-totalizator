# app/__init__.py
import logging
from flask import Flask
from app.config import SECRET_KEY
from app.db import init_db

logging.basicConfig(level=logging.INFO)

def create_app():
    app = Flask(__name__, template_folder='../templates', static_folder='../static')
    app.secret_key = SECRET_KEY
    
    app.permanent_session_lifetime = 60 * 60 * 24 * 7  # 7 дней
    
    # Инициализация БД
    with app.app_context():
        init_db()
        # Первичная загрузка матчей при старте
        try:
            from app.services.match_service import update_matches
            from app.services.point_service import calculate_all_points
            update_matches()
            calculate_all_points()
        except Exception as e:
            logging.warning(f"Не удалось загрузить матчи при старте: {e}")
    
    # Регистрируем blueprints
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
    
    # Настройка загрузки пользователя перед каждым запросом
    from flask import g, session
    from app.db import get_db, close_db
    
    @app.before_request
    def load_user():
        g.is_admin = False
        if 'user_id' in session:
            conn = get_db()
            cur = conn.cursor()
            try:
                cur.execute("SELECT is_admin FROM users WHERE id = %s", (session['user_id'],))
                user = cur.fetchone()
                if user and user[0] == 1:
                    g.is_admin = True
            finally:
                close_db(conn, cur)
    
    return app