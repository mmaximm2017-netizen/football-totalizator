"""Controlled database initialization entrypoint for deployment steps."""

import logging
import sys

from app.db import init_db


def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)

    try:
        init_db()
    except Exception:
        logging.exception("Database initialization failed")
        return 1

    logging.info("Database initialization completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
