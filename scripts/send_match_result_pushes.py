#!/usr/bin/env python3
"""Run one scored match-result push iteration; never starts a background loop."""

import argparse
from datetime import datetime
import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.match_result_push_service import run_once


def parse_datetime(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--now", type=parse_datetime, help="ISO-8601 time with timezone offset")
    parser.add_argument("--since", type=parse_datetime, help="Only events at/after this UTC cutoff")
    args = parser.parse_args()
    result = run_once(now=args.now, since=args.since, dry_run=args.dry_run)
    if args.dry_run:
        print("DRY RUN")
        for candidate in result["candidates"]:
            print(
                f"match_id={candidate['match_id']} user_id={candidate['user_id']} "
                f"score={candidate['home_score']}:{candidate['away_score']} "
                f"prediction={candidate['predicted_home']}:{candidate['predicted_away']} "
                f"points={candidate['points']}"
            )
        print(f"matches={result['matches']}")
        print(f"users={result['users']}")
        print(f"would_send={result['would_send']}")
    else:
        print(
            f"matches={result['matches']} claimed={result['claimed']} "
            f"sent={result['sent']} expired={result['expired']} failed={result['failed']}"
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
