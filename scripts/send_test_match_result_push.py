#!/usr/bin/env python3
"""Send one synthetic match-result push without touching football data."""

import argparse
import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import close_db, get_db
from app.services.match_result_push_service import build_match_result_payload
from app.services.web_push_service import (
    delivery_error_status,
    disable_expired_subscription,
    get_enabled_subscriptions_for_users,
    send_push,
)


logger = logging.getLogger(__name__)

TEST_CANDIDATE = {
    "match_id": 999999,
    "tournament_id": 5,
    "home_team": "Зенит",
    "away_team": "Динамо",
    "home_score": 2,
    "away_score": 1,
    "predicted_home": 1,
    "predicted_away": 0,
    "points": 5,
}


def parse_user_id(value):
    try:
        user_id = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("--user-id must be an integer") from exc
    if user_id <= 0:
        raise argparse.ArgumentTypeError("--user-id must be positive")
    return user_id


def synthetic_candidate():
    """Return a fresh in-memory candidate; never read or write match data."""
    return dict(TEST_CANDIDATE)


def disable_expired_subscriptions(endpoints, db_getter=get_db, db_closer=close_db):
    if not endpoints:
        return
    conn = db_getter()
    cur = conn.cursor()
    try:
        for endpoint in endpoints:
            disable_expired_subscription(cur, endpoint)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        db_closer(conn, cur)


def run_test(user_id, *, sender=None, db_getter=get_db, db_closer=close_db):
    if sender is None:
        sender = send_push

    candidate = synthetic_candidate()
    payload = build_match_result_payload(candidate)

    conn = db_getter()
    cur = conn.cursor()
    try:
        subscriptions_by_user = get_enabled_subscriptions_for_users(cur, {user_id})
    finally:
        db_closer(conn, cur)

    subscriptions = subscriptions_by_user.get(user_id, [])
    sent = 0
    expired = 0
    failed = 0
    expired_endpoints = []

    for subscription in subscriptions:
        try:
            sender(subscription, payload)
            sent += 1
        except Exception as exc:
            status = delivery_error_status(exc)
            if status in (404, 410):
                expired += 1
                expired_endpoints.append(subscription["endpoint"])
            else:
                failed += 1
            logger.warning(
                "test match-result push delivery failed status=%s error_type=%s",
                status,
                type(exc).__name__,
            )

    disable_expired_subscriptions(expired_endpoints, db_getter, db_closer)

    return {
        "user_id": user_id,
        "subscriptions": len(subscriptions),
        "sent": sent,
        "expired": expired,
        "failed": failed,
        "ok": bool(sent),
        "no_subscriptions": not subscriptions,
        "payload": payload,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Send one synthetic match-result push")
    parser.add_argument("--user-id", required=True, type=parse_user_id)
    args = parser.parse_args(argv)

    result = run_test(args.user_id)
    print("TEST MATCH RESULT PUSH")
    print(f"user_id={result['user_id']}")
    print(f"subscriptions={result['subscriptions']}")
    print(f"sent={result['sent']}")
    print(f"expired={result['expired']}")
    print(f"failed={result['failed']}")
    if result["no_subscriptions"]:
        print("ERROR=no active subscriptions")
    print(f"RESULT={'OK' if result['ok'] else 'FAILED'}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
