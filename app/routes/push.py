"""Authenticated Web Push subscription API (stage 1: no sending endpoint)."""

from flask import Blueprint, jsonify, request, session

from app.config import WEB_PUSH_VAPID_PUBLIC_KEY
from app.db import close_db, get_db
from app.services.web_push_service import (
    SubscriptionOwnershipError,
    SubscriptionValidationError,
    disable_subscription,
    upsert_subscription,
)

push_bp = Blueprint("push", __name__, url_prefix="/api/push")
MAX_PUSH_REQUEST_BYTES = 32 * 1024


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
        subscription = upsert_subscription(
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
