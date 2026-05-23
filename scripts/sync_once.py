import json
import logging
import sys

from app import create_app
from app.services.match_service import run_sync_with_lock


SUCCESS_EXIT_STATUSES = {"success", "partial_success", "skipped_already_running"}
FAILURE_EXIT_STATUSES = {"failed", "lock_error"}


def _errors_count(result):
    if not isinstance(result, dict):
        return 1
    return len(result.get("errors") or []) + len((result.get("sync") or {}).get("errors") or [])


def _worker_status(result):
    if not isinstance(result, dict):
        return "failed"
    status = result.get("status")
    if status == "completed":
        return "partial_success" if _errors_count(result) else "success"
    if status == "error":
        return "failed"
    return status or "failed"


def _machine_summary(result, status):
    if not isinstance(result, dict):
        result = {}

    sync_summary = result.get("sync") or {}
    scoring_summary = result.get("scoring") or {}
    matches_finished = sync_summary.get("changed_finished_matches_count")
    if matches_finished is None:
        matches_finished = len(sync_summary.get("matches_became_finished") or [])

    return {
        "status": status,
        "sync_run_id": result.get("sync_run_id"),
        "matches_updated": sync_summary.get("matches_updated", 0),
        "matches_finished": matches_finished,
        "predictions_recalculated": scoring_summary.get("predictions_recalculated", 0),
        "errors_count": _errors_count(result),
    }


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    logger = logging.getLogger(__name__)

    logger.info("sync worker start")
    app = create_app()

    with app.app_context():
        result = run_sync_with_lock(strict_lock=True)
        status = _worker_status(result)
        output = _machine_summary(result, status)

        if status in SUCCESS_EXIT_STATUSES:
            logger.info("sync worker finished with status=%s", status)
            print(json.dumps(output, ensure_ascii=False, sort_keys=True))
            return 0

        if status in FAILURE_EXIT_STATUSES:
            logger.error("sync worker failed with status=%s", status)
            print(json.dumps(output, ensure_ascii=False, sort_keys=True))
            return 1

        logger.error("sync worker returned unexpected status=%s", status)
        print(json.dumps(output, ensure_ascii=False, sort_keys=True))
        return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            stream=sys.stderr,
        )
        logging.exception("sync worker crashed")
        print(
            json.dumps(
                {
                    "status": "failed",
                    "sync_run_id": None,
                    "matches_updated": 0,
                    "matches_finished": 0,
                    "predictions_recalculated": 0,
                    "errors_count": 1,
                    "error": str(e),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        sys.exit(1)
