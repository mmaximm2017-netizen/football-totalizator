import json
import logging
import sys

from app import create_app
from app.services.match_service import run_sync_with_lock


def main():
    logging.basicConfig(level=logging.INFO)

    print("start sync")

    app = create_app()

    with app.app_context():
        result = run_sync_with_lock()
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

        if result.get("status") == "completed":
            print("matches updated")
            print("points calculated")
        else:
            print(f"sync not completed: {result.get('status')}")
            return

    print("sync done")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.exception("sync failed")
        sys.exit(1)
