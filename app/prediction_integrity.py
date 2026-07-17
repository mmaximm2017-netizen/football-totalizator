"""Controlled non-destructive prediction integrity migration entrypoint."""

import logging
import sys

from app.db import migrate_prediction_integrity


def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    try:
        migrate_prediction_integrity()
    except Exception:
        logging.exception("Prediction integrity migration failed")
        return 1

    logging.info("Prediction integrity migration completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
