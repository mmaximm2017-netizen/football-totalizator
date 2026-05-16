import logging
import sys

from app import create_app
from app.services.match_service import run_sync_with_lock


def main():
    logging.basicConfig(level=logging.INFO)

    print("start sync")

    app = create_app()

    with app.app_context():
        completed = run_sync_with_lock()
        if completed:
            print("matches updated")
            print("points calculated")
        else:
            print("sync already running")
            return

    print("sync done")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.exception("sync failed")
        sys.exit(1)
