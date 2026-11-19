#!/usr/bin/env python3
"""Build one Moscow-time admin digest without starting a background loop."""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.morning_digest_service import collect_digest, render_digest
from app.services.telegram_error_notifier import enqueue_telegram_message


def _health_from_env(name):
    raw_value = os.getenv(name, "")
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--container-state")
    args = parser.parse_args()

    message = render_digest(
        collect_digest(
            container_state=args.container_state,
            local_health=_health_from_env("MORNING_DIGEST_LOCAL_HEALTH_JSON"),
            db_health=_health_from_env("MORNING_DIGEST_DB_HEALTH_JSON"),
        )
    )
    if args.dry_run:
        print(message)
        return 0
    if not enqueue_telegram_message(message):
        print("Morning digest was not enqueued.", file=sys.stderr)
        return 1
    print("MORNING_DIGEST_ENQUEUED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
