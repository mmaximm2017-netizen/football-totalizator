from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from app import create_app
from app.routes import agent_api


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("TOTISH_AGENT_TOKEN", "test-agent-token")
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def auth():
    return {"Authorization": "Bearer test-agent-token"}


def test_missing_confirmation_token_is_rejected():
    with create_app().app_context():
        response = agent_api._consume_schedule_confirmation(
            MagicMock(), token=None, action="x", payload={}
        )
    assert response.status_code == 409
    assert response.get_json()["error"] == "confirmation_required"


def test_not_yet_valid_confirmation_is_rejected():
    cur = MagicMock()
    payload = {"x": 1}
    payload_hash = agent_api._confirmation_payload_hash("a", payload)
    cur.fetchone.return_value = ("a", payload_hash, False, True, None)
    with create_app().app_context():
        response = agent_api._consume_schedule_confirmation(
            cur, token="abc", action="a", payload=payload
        )
    assert response.status_code == 409
    assert response.get_json()["error"] == "confirmation_not_yet_valid"


def test_payload_mismatch_is_rejected():
    cur = MagicMock()
    cur.fetchone.return_value = ("a", "wrong", True, True, None)
    with create_app().app_context():
        response = agent_api._consume_schedule_confirmation(
            cur, token="abc", action="a", payload={"x": 1}
        )
    assert response.status_code == 409
    assert response.get_json()["error"] == "confirmation_payload_mismatch"


def test_used_confirmation_is_rejected():
    cur = MagicMock()
    payload = {"x": 1}
    payload_hash = agent_api._confirmation_payload_hash("a", payload)
    cur.fetchone.return_value = (
        "a", payload_hash, True, True, datetime.now(timezone.utc)
    )
    with create_app().app_context():
        response = agent_api._consume_schedule_confirmation(
            cur, token="abc", action="a", payload=payload
        )
    assert response.status_code == 409
    assert response.get_json()["error"] == "confirmation_already_used"


def test_schedule_write_without_confirmation_never_updates(client):
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    with patch("app.routes.agent_api.get_db", return_value=conn), \
         patch("app.routes.agent_api.get_rpl_tournament", return_value={"id": 5}), \
         patch("app.routes.agent_api._apply_schedule_update") as apply_update:
        response = client.post(
            "/api/agent/v1/matches/428/schedule",
            headers=auth(),
            json={"date": "2026-08-22", "time": "21:45"},
        )
    assert response.status_code == 409
    assert response.get_json()["error"] == "confirmation_required"
    assert not apply_update.called


def test_confirmation_hash_is_bound_to_exact_time():
    a = agent_api._confirmation_payload_hash(
        "update_rpl_match_schedule",
        {"league": "rpl", "match_id": 428, "date": "2026-08-22", "time": "20:15"},
    )
    b = agent_api._confirmation_payload_hash(
        "update_rpl_match_schedule",
        {"league": "rpl", "match_id": 428, "date": "2026-08-22", "time": "21:45"},
    )
    assert a != b
