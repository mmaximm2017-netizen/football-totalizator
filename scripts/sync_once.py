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
        result = run_sync_with_lock(strict_lock=True)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

        if result.get("status") == "completed":
            print("matches updated")
            print("points calculated")
            print("sync done")
            return 0

        if result.get("status") == "skipped_already_running":
            print("sync already running")
            return 0

        if result.get("status") == "lock_error":
            print("sync lock error")
            return 1

        print(f"sync not completed: {result.get('status')}")
        return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        logging.exception("sync failed")
        sys.exit(1)
