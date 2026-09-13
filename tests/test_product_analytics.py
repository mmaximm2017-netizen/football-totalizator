from unittest.mock import patch

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
