"""Authenticated Web Push subscription and test-delivery API."""

import logging

from flask import Blueprint, jsonify, request, session

from app.config import WEB_PUSH_VAPID_PUBLIC_KEY
from app.db import close_db, get_db
from app.services.web_push_service import (
    SubscriptionOwnershipError,
    SubscriptionValidationError,
    disable_subscription,
    disable_expired_subscription,
    delivery_error_status,
    endpoint_fingerprint,
    get_enabled_subscriptions,
    reserve_test_push_slot,
    send_push,
    upsert_subscription,
)

push_bp = Blueprint("push", __name__, url_prefix="/api/push")
MAX_PUSH_REQUEST_BYTES = 32 * 1024
logger = logging.getLogger(__name__)
TEST_PUSH_PAYLOAD = {
    "title": "ТОТИШ",
    "body": "Push-уведомления работают ⚽",
    "url": "/",
    "tag": "totish-test",
}


def _auth_required():
    if not session.get("user_id"):
        return jsonify({"ok": False, "error": "authentication_required"}), 401
    return None


@push_bp.get("/vapid-public-key")
def vapid_public_key():
    unauthorized = _auth_required()
    if unauthorized:
        return unauthorized
    if not WEB_PUSH_VAPID_PUBLIC_KEY:
        return jsonify({"ok": False, "error": "push_not_configured"}), 503
    return jsonify({"ok": True, "public_key": WEB_PUSH_VAPID_PUBLIC_KEY})


@push_bp.post("/subscribe")
def subscribe():
    unauthorized = _auth_required()
    if unauthorized:
        return unauthorized
    if request.content_length is not None and request.content_length > MAX_PUSH_REQUEST_BYTES:
        return jsonify({"ok": False, "error": "request_too_large"}), 413
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "invalid_json"}), 400

    conn = get_db()
    cur = conn.cursor()
    try:
        upsert_subscription(
            cur,
            session["user_id"],
            payload,
            user_agent=request.user_agent.string,
            device_label=payload.get("device_label"),
        )
        conn.commit()
        return jsonify({"ok": True, "subscribed": True}), 201
    except SubscriptionOwnershipError:
        conn.rollback()
        return jsonify({"ok": False, "error": "subscription_owned_by_another_user"}), 409
    except SubscriptionValidationError as exc:
        conn.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 400
    finally:
        close_db(conn, cur)


@push_bp.post("/unsubscribe")
def unsubscribe():
    unauthorized = _auth_required()
    if unauthorized:
        return unauthorized
    if request.content_length is not None and request.content_length > MAX_PUSH_REQUEST_BYTES:
        return jsonify({"ok": False, "error": "request_too_large"}), 413
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "invalid_json"}), 400

    conn = get_db()
    cur = conn.cursor()
    try:
        disable_subscription(cur, session["user_id"], payload.get("endpoint"))
        conn.commit()
        return jsonify({"ok": True, "subscribed": False})
    except SubscriptionValidationError as exc:
        conn.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 400
    finally:
        close_db(conn, cur)


@push_bp.post("/test")
def test_push():
    unauthorized = _auth_required()
    if unauthorized:
        return unauthorized
    if request.content_length is not None and request.content_length > MAX_PUSH_REQUEST_BYTES:
        return jsonify({"ok": False, "error": "request_too_large"}), 413
    payload = request.get_json(silent=True)
    if request.data and payload is None:
        return jsonify({"ok": False, "error": "invalid_json"}), 400
    if payload is not None and not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "invalid_json"}), 400

    from app.config import WEB_PUSH_VAPID_PRIVATE_KEY, WEB_PUSH_VAPID_SUBJECT
    if not WEB_PUSH_VAPID_PRIVATE_KEY or not WEB_PUSH_VAPID_SUBJECT:
        return jsonify({"ok": False, "error": "push_not_configured"}), 503

    user_id = session["user_id"]
    conn = get_db()
    cur = conn.cursor()
    try:
        subscriptions = get_enabled_subscriptions(cur, user_id)
        if not subscriptions:
            conn.rollback()
            return jsonify({"ok": False, "error": "no_active_subscription"}), 409
        retry_after = reserve_test_push_slot(cur, user_id)
        if retry_after is not None:
            conn.rollback()
            response = jsonify({
                "ok": False,
                "error": "test_push_cooldown",
                "retry_after": retry_after,
            })
            response.status_code = 429
            response.headers["Retry-After"] = str(retry_after)
            return response
        # Release the read/reservation transaction before provider HTTP calls.
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        close_db(conn, cur)

    sent = expired = failed = 0
    expired_endpoints = []
    for subscription in subscriptions:
        try:
            send_push(subscription, TEST_PUSH_PAYLOAD)
            sent += 1
        except Exception as exc:
            status = delivery_error_status(exc)
            if status in (404, 410):
                expired += 1
                expired_endpoints.append(subscription["endpoint"])
            else:
                failed += 1
                logger.warning(
                    "web push test delivery failed user_id=%s endpoint=%s status=%s",
                    user_id,
                    endpoint_fingerprint(subscription["endpoint"]),
                    status or "unknown",
                )

    if expired_endpoints:
        conn = get_db()
        cur = conn.cursor()
        try:
            for endpoint in expired_endpoints:
                disable_expired_subscription(cur, endpoint)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            close_db(conn, cur)

    if sent:
        return jsonify({"ok": True, "sent": sent, "expired": expired, "failed": failed})
    if expired and not failed:
        return jsonify({"ok": False, "error": "no_active_subscription", "expired": expired}), 409
    return jsonify({"ok": False, "error": "push_delivery_failed", "expired": expired, "failed": failed}), 502
