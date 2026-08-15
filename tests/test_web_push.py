import unittest
from unittest.mock import patch

from flask import Flask, jsonify, request, session

from app.routes.push import push_bp
from app.services.web_push_service import (
    SubscriptionOwnershipError,
    SubscriptionValidationError,
    normalize_endpoint,
    upsert_subscription,
)


class Cursor:
    def __init__(self, existing=None):
        self.existing = existing
        self.executed = []
        self.insert_params = None

    def execute(self, query, params=None):
        self.executed.append((query, params))
        if query.lstrip().startswith("INSERT"):
            self.insert_params = params

    def fetchone(self):
        return self.existing

    def fetchall(self):
        return []

    @property
    def closed(self):
        return False

    def close(self):
        pass


class Connection:
    def __init__(self, cursor):
        self.cursor_value = cursor
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class WebPushTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(push_bp)

        # Mirror the production CSRF contract for this isolated blueprint test.
        @self.app.before_request
        def csrf():
            if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                if request.headers.get("X-CSRF-Token") != session.get("csrf_token"):
                    return jsonify({"ok": False, "error": "csrf"}), 400

    def client(self, user_id=None, csrf=True):
        client = self.app.test_client()
        with client.session_transaction() as current_session:
            if user_id is not None:
                current_session["user_id"] = user_id
            current_session["csrf_token"] = "csrf-token"
        headers = {"X-CSRF-Token": "csrf-token"} if csrf else {}
        return client, headers

    def test_unauthenticated_get_and_subscribe_rejected(self):
        client, headers = self.client()
        self.assertEqual(client.get("/api/push/vapid-public-key").status_code, 401)
        self.assertEqual(client.post("/api/push/subscribe", json={}, headers=headers).status_code, 401)

    def test_csrf_required(self):
        client, _ = self.client(user_id=7, csrf=False)
        self.assertEqual(client.post("/api/push/subscribe", json={}).status_code, 400)

    def test_vapid_public_key_never_exposes_private_key(self):
        client, _ = self.client(user_id=7)
        with patch("app.routes.push.WEB_PUSH_VAPID_PUBLIC_KEY", "public"), \
             patch("app.config.WEB_PUSH_VAPID_PRIVATE_KEY", "private"):
            response = client.get("/api/push/vapid-public-key")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"ok": True, "public_key": "public"})
        self.assertNotIn("private", response.get_data(as_text=True))

    def test_not_configured_is_controlled(self):
        client, _ = self.client(user_id=7)
        with patch("app.routes.push.WEB_PUSH_VAPID_PUBLIC_KEY", ""):
            response = client.get("/api/push/vapid-public-key")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json["error"], "push_not_configured")

    def test_subscription_validation(self):
        with self.assertRaises(SubscriptionValidationError):
            normalize_endpoint("http://push.example/sub")
        with self.assertRaises(SubscriptionValidationError):
            normalize_endpoint("javascript:alert(1)")
        with self.assertRaises(SubscriptionValidationError):
            upsert_subscription(Cursor(), 7, {"endpoint": "https://push.example/sub"})

    def test_subscribe_uses_session_user_and_accepts_device_metadata(self):
        cursor = Cursor()
        conn = Connection(cursor)
        client, headers = self.client(user_id=7)
        payload = {
            "user_id": 999,
            "endpoint": "https://push.example/sub",
            "keys": {"p256dh": "p-key", "auth": "a-key"},
            "device_label": "phone",
        }
        with patch("app.routes.push.get_db", return_value=conn), patch("app.routes.push.close_db"):
            response = client.post("/api/push/subscribe", json=payload, headers=headers)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(cursor.insert_params[0], 7)
        self.assertNotEqual(cursor.insert_params[0], payload["user_id"])
        self.assertEqual(conn.commits, 1)

    def test_foreign_endpoint_is_not_rebound(self):
        with self.assertRaises(SubscriptionOwnershipError):
            upsert_subscription(
                Cursor(existing=(8,)),
                7,
                {"endpoint": "https://push.example/sub", "keys": {"p256dh": "p", "auth": "a"}},
            )

    def test_unsubscribe_is_scoped_to_session_user(self):
        cursor = Cursor()
        conn = Connection(cursor)
        client, headers = self.client(user_id=7)
        with patch("app.routes.push.get_db", return_value=conn), patch("app.routes.push.close_db"):
            response = client.post(
                "/api/push/unsubscribe",
                json={"endpoint": "https://push.example/sub", "user_id": 999},
                headers=headers,
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(cursor.executed[-1][1], (7, "https://push.example/sub"))
        self.assertEqual(conn.commits, 1)

    def test_same_endpoint_upsert_updates_keys_and_multiple_endpoints_are_supported(self):
        cursor = Cursor()
        first = {"endpoint": "https://push.example/a", "keys": {"p256dh": "p1", "auth": "a1"}}
        second = {"endpoint": "https://push.example/b", "keys": {"p256dh": "p2", "auth": "a2"}}
        upsert_subscription(cursor, 7, first)
        upsert_subscription(cursor, 7, second)
        self.assertEqual(len([q for q, _ in cursor.executed if q.lstrip().startswith("INSERT")]), 2)
        insert_sql = next(q for q, _ in cursor.executed if q.lstrip().startswith("INSERT"))
        self.assertIn("ON CONFLICT (endpoint) DO UPDATE", insert_sql)
        self.assertIn("p256dh = EXCLUDED.p256dh", insert_sql)


if __name__ == "__main__":
    unittest.main()
