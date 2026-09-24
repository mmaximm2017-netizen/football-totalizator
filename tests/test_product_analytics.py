from unittest.mock import MagicMock, patch

import pytest

from app import create_app
from app.services import product_analytics


def analytics_client():
    app = create_app()
    app.config.update(TESTING=True)
    client = app.test_client()
    with client.session_transaction() as current_session:
        current_session["csrf_token"] = "analytics-csrf"
    headers = {
        "X-CSRF-Token": "analytics-csrf",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "http://localhost",
        "Content-Type": "application/json",
    }
    return client, headers


def test_pageview_is_sanitized_and_queued():
    client, headers = analytics_client()

    with patch("app.enqueue_posthog_event") as enqueue:
        response = client.post(
            "/__analytics/event",
            headers=headers,
            json={
                "event": "$pageview",
                "properties": {
                    "pathname": "/profile/123?secret=ignored",
                    "unexpected": "drop-me",
                },
            },
        )

    assert response.status_code == 204
    enqueue.assert_called_once()
    event_name, distinct_id, properties = enqueue.call_args.args
    assert event_name == "$pageview"
    assert len(distinct_id) == 64
    assert properties == {
        "$pathname": "/profile/123",
        "$current_url": "http://localhost/profile/123",
    }



def test_identified_pageview_uses_server_session_username():
    client, headers = analytics_client()
    with client.session_transaction() as current_session:
        current_session["user_id"] = 2
        current_session["analytics_username"] = "Игрок"

    with patch("app.enqueue_posthog_event") as enqueue:
        response = client.post(
            "/__analytics/event",
            headers=headers,
            json={"event": "$pageview", "properties": {
                "pathname": "/table", "totish_username": "подмена",
            }},
        )

    assert response.status_code == 204
    properties = enqueue.call_args.args[2]
    assert properties["totish_username"] == "Игрок"
    assert properties["totish_user_ref"]
    assert properties["$pathname"] == "/table"
    assert properties["$set"]["username"] == "Игрок"


def test_anonymous_event_cannot_inherit_or_supply_identity():
    client, headers = analytics_client()
    with client.session_transaction() as current_session:
        current_session["analytics_username"] = "stale-name"
    with patch("app.enqueue_posthog_event") as enqueue, patch("app.get_db") as db:
        response = client.post("/__analytics/event", headers=headers, json={
            "event": "$pageview", "properties": {
                "pathname": "/", "totish_username": "forged",
                "$set": {"username": "forged"}, "distinct_id": "another-user",
            },
        })
    assert response.status_code == 204
    assert enqueue.call_args.args[2] == {
        "$pathname": "/", "$current_url": "http://localhost/",
    }
    db.assert_not_called()


def test_logout_and_next_account_have_separate_analytics_identity():
    client, headers = analytics_client()
    with client.session_transaction() as current_session:
        current_session["user_id"] = 2
        current_session["analytics_username"] = "Первый"
    payload = {"event": "$pageview", "properties": {"pathname": "/"}}
    with patch("app.enqueue_posthog_event") as enqueue, patch("app.get_db") as db:
        client.post("/__analytics/event", headers=headers, json=payload)
        first_id = enqueue.call_args.args[1]
        assert client.post("/logout", headers=headers).status_code == 302
        client.post("/__analytics/event", headers=headers, json=payload)
        anonymous_id = enqueue.call_args.args[1]
        assert "totish_username" not in enqueue.call_args.args[2]
        assert "$set" not in enqueue.call_args.args[2]
        with client.session_transaction() as current_session:
            assert "analytics_username" not in current_session
            current_session["user_id"] = 3
            current_session["analytics_username"] = "Второй"
        client.post("/__analytics/event", headers=headers, json=payload)
        assert enqueue.call_args.args[2]["$set"]["username"] == "Второй"
        assert len({first_id, anonymous_id, enqueue.call_args.args[1]}) == 3
    db.assert_not_called()


@pytest.mark.parametrize("path", ["/", "/table"])
def test_existing_session_gets_identity_on_normal_page_request(path):
    client, headers = analytics_client()
    with client.session_transaction() as current_session:
        current_session["user_id"] = 2
    conn = MagicMock()
    conn.cursor.return_value.fetchone.return_value = (0, None, 0, "Игрок")
    # Exercise the real before_request hook without running unrelated page SQL.
    endpoint = "main.index" if path == "/" else "table.table"
    with client.application.test_request_context(path):
        from flask import request
        endpoint = request.endpoint
    with patch.dict(client.application.view_functions, {endpoint: lambda: "ok"}):
        with patch("app.get_db", return_value=conn), patch("app.close_db"):
            assert client.get(path).status_code == 200
    with patch("app.enqueue_posthog_event") as enqueue, patch("app.get_db") as db:
        assert client.post("/__analytics/event", headers=headers, json={
            "event": "$pageview", "properties": {"pathname": path},
        }).status_code == 204
    assert enqueue.call_args.args[2]["totish_username"] == "Игрок"
    assert enqueue.call_args.args[2]["$set"]["username"] == "Игрок"
    db.assert_not_called()


def test_prediction_event_keeps_only_match_id():
    client, headers = analytics_client()

    with patch("app.enqueue_posthog_event") as enqueue:
        response = client.post(
            "/__analytics/event",
            headers=headers,
            json={
                "event": "prediction_submitted",
                "properties": {
                    "match_id": "42",
                    "home_score": 7,
                    "away_score": 0,
                    "username": "must-not-leave-browser",
                },
            },
        )

    assert response.status_code == 204
    assert enqueue.call_args.args[2] == {"match_id": 42}


def test_unknown_event_is_rejected():
    client, headers = analytics_client()

    with patch("app.enqueue_posthog_event") as enqueue:
        response = client.post(
            "/__analytics/event",
            headers=headers,
            json={"event": "arbitrary_event", "properties": {"anything": "value"}},
        )

    assert response.status_code == 400
    enqueue.assert_not_called()


def test_cross_origin_analytics_is_rejected():
    client, headers = analytics_client()
    headers["Origin"] = "https://evil.example"

    with patch("app.enqueue_posthog_event") as enqueue:
        response = client.post(
            "/__analytics/event",
            headers=headers,
            json={"event": "login", "properties": {}},
        )

    assert response.status_code == 403
    enqueue.assert_not_called()


def test_csrf_is_required_for_analytics_endpoint():
    client, headers = analytics_client()
    headers.pop("X-CSRF-Token")

    with patch("app.enqueue_posthog_event") as enqueue:
        response = client.post(
            "/__analytics/event",
            headers=headers,
            json={"event": "login", "properties": {}},
        )

    assert response.status_code == 400
    enqueue.assert_not_called()


def test_server_delivery_uses_posthog_ingestion_endpoint():
    fake_response = type("Response", (), {"raise_for_status": lambda self: None})()

    with patch.object(product_analytics.requests, "post", return_value=fake_response) as post:
        product_analytics._deliver_posthog_event(
            "login",
            "anonymous-distinct-id",
            {},
        )

    assert post.call_count == 1
    url = post.call_args.args[0]
    payload = post.call_args.kwargs["json"]
    assert url.endswith("/i/v0/e/")
    assert payload["event"] == "login"
    assert payload["distinct_id"] == "anonymous-distinct-id"
    assert payload["properties"]["$process_person_profile"] is False


def test_identified_player_has_profile_and_username():
    fake_response = type("Response", (), {"raise_for_status": lambda self: None})()
    with patch.object(product_analytics.requests, "post", return_value=fake_response) as post:
        product_analytics._deliver_posthog_event(
            "$identify", "stable-player-id",
            {"totish_user_ref": "player-ref", "$set": {"username": "player"}},
        )
    payload = post.call_args.kwargs["json"]
    assert payload["properties"]["$process_person_profile"] is True
    assert payload["properties"]["$set"] == {"username": "player"}
