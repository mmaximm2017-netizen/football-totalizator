#!/usr/bin/env python3
"""Run one deadline-push iteration; never starts a background loop."""

import argparse
from datetime import datetime
import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.deadline_push_service import run_once


def parse_now(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--now", type=parse_now, help="ISO-8601 time with timezone offset")
    args = parser.parse_args()
    result = run_once(now=args.now, dry_run=args.dry_run)
    if args.dry_run:
        print("DRY RUN")
        for candidate in result["candidates"]:
            print(
                f"match_id={candidate['match_id']} deadline={candidate['deadline']} "
                f"user_id={candidate['user_id']} reason=missing_prediction"
            )
        print(
            f"matches={result['matches']} users={len({c['user_id'] for c in result['candidates']})} "
            f"would_send={result['would_send']}"
        )
    else:
        print(
            f"matches={len({c['match_id'] for c in result['candidates']})} "
            f"claimed={result['claimed']} sent={result['sent']} "
            f"expired={result['expired']} failed={result['failed']}"
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
