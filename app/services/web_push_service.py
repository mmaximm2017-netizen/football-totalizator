"""Server-side Web Push subscription storage and delivery primitives."""

import hashlib
import json
import logging
from datetime import datetime, timezone
from urllib.parse import urlsplit

from app.config import (
    WEB_PUSH_VAPID_PRIVATE_KEY,
    WEB_PUSH_VAPID_SUBJECT,
)

logger = logging.getLogger(__name__)

MAX_ENDPOINT_LENGTH = 2048
MAX_KEY_LENGTH = 512
TEST_PUSH_COOLDOWN_SECONDS = 60


class SubscriptionValidationError(ValueError):
    pass


class SubscriptionOwnershipError(ValueError):
    pass


def validate_subscription_payload(payload):
    if not isinstance(payload, dict):
        raise SubscriptionValidationError("subscription must be an object")

    endpoint = normalize_endpoint(payload.get("endpoint"))
    keys = payload.get("keys")
    if not isinstance(keys, dict):
        raise SubscriptionValidationError("keys are required")

    p256dh = keys.get("p256dh")
    auth = keys.get("auth")
    if not isinstance(p256dh, str) or not p256dh or len(p256dh) > MAX_KEY_LENGTH:
        raise SubscriptionValidationError("p256dh is invalid")
    if not isinstance(auth, str) or not auth or len(auth) > MAX_KEY_LENGTH:
        raise SubscriptionValidationError("auth is invalid")

    return {"endpoint": endpoint, "p256dh": p256dh, "auth": auth}


def normalize_endpoint(value):
    if not isinstance(value, str):
        raise SubscriptionValidationError("endpoint is required")
    endpoint = value.strip()
    if not endpoint or len(endpoint) > MAX_ENDPOINT_LENGTH:
        raise SubscriptionValidationError("endpoint is invalid")
    parsed = urlsplit(endpoint)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise SubscriptionValidationError("endpoint must use https")
    if parsed.username or parsed.password:
        raise SubscriptionValidationError("endpoint credentials are not allowed")
    return endpoint


def endpoint_fingerprint(endpoint):
    """Return a non-sensitive identifier suitable for logs."""
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:12]


def upsert_subscription(cur, user_id, payload, user_agent=None, device_label=None):
    if not user_id:
        raise SubscriptionValidationError("user is required")
    subscription = validate_subscription_payload(payload)
    endpoint = subscription["endpoint"]

    cur.execute(
        "SELECT user_id FROM push_subscriptions WHERE endpoint = %s",
        (endpoint,),
    )
    existing = cur.fetchone()
    if existing and int(existing[0]) != int(user_id):
        # Do not silently hijack a subscription belonging to another account.
        # A browser resubscription normally keeps the same endpoint for its user.
        raise SubscriptionOwnershipError("subscription belongs to another user")

    cur.execute(
        """
        INSERT INTO push_subscriptions
            (user_id, endpoint, p256dh, auth, user_agent, device_label, enabled,
             created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, TRUE, now(), now())
        ON CONFLICT (endpoint) DO UPDATE SET
            user_id = EXCLUDED.user_id,
            p256dh = EXCLUDED.p256dh,
            auth = EXCLUDED.auth,
            user_agent = EXCLUDED.user_agent,
            device_label = EXCLUDED.device_label,
            enabled = TRUE,
            updated_at = now()
        """,
        (
            user_id,
            subscription["endpoint"],
            subscription["p256dh"],
            subscription["auth"],
            user_agent[:512] if isinstance(user_agent, str) else None,
            device_label[:128] if isinstance(device_label, str) else None,
        ),
    )
    return subscription


def disable_subscription(cur, user_id, endpoint):
    normalized = normalize_endpoint(endpoint)
    cur.execute(
        """
        UPDATE push_subscriptions
        SET enabled = FALSE, updated_at = now()
        WHERE user_id = %s AND endpoint = %s
        """,
        (user_id, normalized),
    )
    return normalized


def get_enabled_subscriptions(cur, user_id):
    cur.execute(
        """
        SELECT endpoint, p256dh, auth
        FROM push_subscriptions
        WHERE user_id = %s AND enabled = TRUE
        ORDER BY id
        """,
        (user_id,),
    )
    return [
        {"endpoint": row[0], "keys": {"p256dh": row[1], "auth": row[2]}}
        for row in cur.fetchall()
    ]


def reserve_test_push_slot(cur, user_id, *, cooldown_seconds=TEST_PUSH_COOLDOWN_SECONDS):
    """Atomically reserve a user's test-push slot across all workers.

    Returns the remaining cooldown in seconds, or ``None`` when sending is allowed.
    The caller must commit this short transaction before doing network I/O.
    """
    cur.execute(
        """
        INSERT INTO push_test_cooldowns (user_id, last_sent_at)
        VALUES (%s, now())
        ON CONFLICT (user_id) DO UPDATE
        SET last_sent_at = now()
        WHERE push_test_cooldowns.last_sent_at
              <= now() - (%s * INTERVAL '1 second')
        RETURNING last_sent_at
        """,
        (user_id, cooldown_seconds),
    )
    if cur.fetchone():
        return None

    cur.execute(
        "SELECT last_sent_at FROM push_test_cooldowns WHERE user_id = %s",
        (user_id,),
    )
    row = cur.fetchone()
    if not row or not row[0]:
        return cooldown_seconds
    last_sent_at = row[0]
    if last_sent_at.tzinfo is None:
        last_sent_at = last_sent_at.replace(tzinfo=timezone.utc)
    elapsed = (datetime.now(timezone.utc) - last_sent_at).total_seconds()
    return max(1, int(cooldown_seconds - elapsed + 0.999))


def disable_expired_subscription(cur, endpoint):
    """Disable one provider-invalid endpoint without exposing its secrets."""
    normalized = normalize_endpoint(endpoint)
    cur.execute(
        """
        UPDATE push_subscriptions
        SET enabled = FALSE, updated_at = now()
        WHERE endpoint = %s AND enabled = TRUE
        """,
        (normalized,),
    )
    return normalized


def delivery_error_status(error):
    """Extract an HTTP status from pywebpush errors without logging secrets."""
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    if status is None:
        status = getattr(error, "status_code", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def send_push(subscription, payload, *, ttl=300):
    """Send one JSON Web Push payload using the configured VAPID credentials."""
    if not WEB_PUSH_VAPID_PRIVATE_KEY or not WEB_PUSH_VAPID_SUBJECT:
        raise RuntimeError("push_not_configured")

    from pywebpush import WebPushException, webpush

    try:
        return webpush(
            subscription_info=subscription,
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=WEB_PUSH_VAPID_PRIVATE_KEY,
            vapid_claims={"sub": WEB_PUSH_VAPID_SUBJECT},
            ttl=ttl,
            timeout=10,
        )
    except WebPushException:
        logger.warning("web push delivery failed endpoint=%s", endpoint_fingerprint(subscription["endpoint"]))
        raise
