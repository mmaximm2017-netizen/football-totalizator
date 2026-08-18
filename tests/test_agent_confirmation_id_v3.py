from unittest.mock import MagicMock, patch

import pytest

from app import create_app
from app.routes import agent_api


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("TOTISH_AGENT_TOKEN", "test-agent-token")
    app = create_app()
    app.config.update(TESTING=True)
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def test_openapi_requires_numeric_confirmation_id_for_all_six_writes(client):
    paths = client.get("/api/agent/v1/openapi.json").get_json()["paths"]
    actions = [
        paths["/matches"]["post"],
        paths["/russian-cup/matches"]["post"],
        paths["/matches/{match_id}/schedule"]["post"],
        paths["/russian-cup/matches/{match_id}/schedule"]["post"],
        paths["/matches/{match_id}/result"]["post"],
        paths["/russian-cup/matches/{match_id}/result"]["post"],
    ]
    for action in actions:
        text = str(action)
        assert "confirmation_id" in text
        assert "'type': 'integer'" in text


def test_issue_returns_database_confirmation_id():
    cur = MagicMock()
    cur.fetchone.return_value = (17,)
    conn = MagicMock()
    with patch("app.routes.agent_api.secrets.token_urlsafe", side_effect=["long-token", "shortref"]):
        response = agent_api._issue_schedule_confirmation(
            cur,
            conn,
            action="create_matches",
            payload={"league": "rpl", "matches": []},
        )
    assert response["confirmation_id"] == 17
    assert response["confirmation_handle"] == "cfm_shortref"
    assert response["confirmation_token"] == "long-token"
    assert "RETURNING id" in cur.execute.call_args_list[-1].args[0]
    assert conn.commit.called


def test_consume_numeric_id_uses_primary_key():
    payload = {"league": "rpl", "matches": [{"id": 1}]}
    cur = MagicMock()
    cur.fetchone.return_value = (
        "create_matches",
        agent_api._confirmation_payload_hash("create_matches", payload),
        True,
        True,
        None,
    )
    cur.rowcount = 1
    error = agent_api._consume_schedule_confirmation(
        cur,
        confirmation_id=17,
        action="create_matches",
        payload=payload,
    )
    assert error is None
    assert "WHERE id = %s" in cur.execute.call_args_list[0].args[0]
    assert cur.execute.call_args_list[0].args[1] == (17,)


def test_invalid_confirmation_id_is_blocked(app):
    cur = MagicMock()
    with app.app_context():
        response = agent_api._consume_schedule_confirmation(
            cur,
            confirmation_id="bad",
            action="create_matches",
            payload={"league": "rpl", "matches": []},
        )
    assert response.status_code == 409
    assert response.get_json()["error"] == "invalid_confirmation_id"
    cur.execute.assert_not_called()


def test_confirmation_id_still_enforces_exact_payload(app):
    original = {"league": "rpl", "matches": [{"time": "19:00"}]}
    changed = {"league": "rpl", "matches": [{"time": "19:15"}]}
    cur = MagicMock()
    cur.fetchone.return_value = (
        "create_matches",
        agent_api._confirmation_payload_hash("create_matches", original),
        True,
        True,
        None,
    )
    with app.app_context():
        response = agent_api._consume_schedule_confirmation(
            cur,
            confirmation_id=17,
            action="create_matches",
            payload=changed,
        )
    assert response.status_code == 409
    assert response.get_json()["error"] == "confirmation_payload_mismatch"


def test_create_batch_hash_is_order_insensitive_but_content_sensitive():
    a = {
        "league": "rpl",
        "matches": [
            {"home_team": "A", "away_team": "B", "time": "19:00"},
            {"home_team": "C", "away_team": "D", "time": "20:00"},
        ],
    }
    b = {"league": "rpl", "matches": list(reversed(a["matches"]))}
    changed = {
        "league": "rpl",
        "matches": [
            {"home_team": "A", "away_team": "B", "time": "19:00"},
            {"home_team": "C", "away_team": "D", "time": "20:15"},
        ],
    }
    assert agent_api._confirmation_payload_hash("create_matches", a) == agent_api._confirmation_payload_hash("create_matches", b)
    assert agent_api._confirmation_payload_hash("create_matches", a) != agent_api._confirmation_payload_hash("create_matches", changed)
