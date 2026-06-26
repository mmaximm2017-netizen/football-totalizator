import logging
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logger.info("[STARTUP] import wsgi")

from app import create_app

app = create_app()

logger.info("[STARTUP] gunicorn app ready")

if __name__ == '__main__':
    logger.info("[STARTUP] run development server")
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
