import logging
import sys

from app import create_app
from app.services.match_service import update_matches
from app.services.point_service import calculate_all_points


def main():
    logging.basicConfig(level=logging.INFO)

    print("start sync")

    app = create_app()

    with app.app_context():
        update_matches()
        print("matches updated")

        calculate_all_points()
        print("points calculated")

    print("sync done")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.exception("sync failed")
        sys.exit(1)
