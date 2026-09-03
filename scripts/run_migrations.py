#!/usr/bin/env python3
"""Apply pending versioned database migrations."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.db_migration_service import MigrationError, run_migrations


def main() -> int:
    logging.basicConfig(level=logging.INFO)

    try:
        result = run_migrations()
    except MigrationError:
        logging.exception("Database migration failed")
        return 1

    logging.info(
        "Database migrations complete: applied=%s known=%s",
        result["applied_count"],
        result["known_count"],
    )
    for filename in result["applied"]:
        logging.info("Applied migration: %s", filename)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
