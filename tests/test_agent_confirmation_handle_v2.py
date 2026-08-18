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


def test_openapi_requires_handle_for_all_write_actions(client):
    p = client.get("/api/agent/v1/openapi.json").get_json()["paths"]
    for action in [
        p["/matches"]["post"],
        p["/russian-cup/matches"]["post"],
        p["/matches/{match_id}/schedule"]["post"],
        p["/russian-cup/matches/{match_id}/schedule"]["post"],
        p["/matches/{match_id}/result"]["post"],
        p["/russian-cup/matches/{match_id}/result"]["post"],
    ]:
        assert "confirmation_handle" in str(action)


def test_issue_returns_handle_and_persists_ref():
    cur = MagicMock()
    conn = MagicMock()
    with patch("app.routes.agent_api.secrets.token_urlsafe", side_effect=["long-token", "shortref"]):
        d = agent_api._issue_schedule_confirmation(cur, conn, action="create_matches", payload={"league": "rpl", "matches": []})
    assert d["confirmation_handle"] == "cfm_shortref"
    assert d["confirmation_token"] == "long-token"
    assert "confirmation_ref" in cur.execute.call_args.args[0]
    assert "cfm_shortref" in cur.execute.call_args.args[1]


def test_consume_handle_uses_ref():
    payload = {"league": "rpl", "matches": [{"id": 1}]}
    cur = MagicMock()
    cur.fetchone.return_value = (
        "create_matches",
        agent_api._confirmation_payload_hash("create_matches", payload),
        True, True, None,
    )
    cur.rowcount = 1
    err = agent_api._consume_schedule_confirmation(cur, handle="cfm_abc123", action="create_matches", payload=payload)
    assert err is None
    assert "confirmation_ref" in cur.execute.call_args_list[0].args[0]
    assert cur.execute.call_args_list[0].args[1] == ("cfm_abc123",)


def test_handle_payload_mismatch_blocked():
    app = create_app()
    app.config.update(TESTING=True)

    cur = MagicMock()
    cur.fetchone.return_value = (
        "create_matches",
        agent_api._confirmation_payload_hash(
            "create_matches",
            {"league": "rpl", "matches": [{"time": "19:00"}]},
        ),
        True, True, None,
    )

    with app.app_context():
        response = agent_api._consume_schedule_confirmation(
            cur,
            handle="cfm_abc123",
            action="create_matches",
            payload={"league": "rpl", "matches": [{"time": "19:15"}]},
        )

        assert response.status_code == 409
        assert response.get_json()["error"] == "confirmation_payload_mismatch"


def test_legacy_token_still_supported():
    payload = {"league": "rpl", "matches": []}
    cur = MagicMock()
    cur.fetchone.return_value = (
        "create_matches",
        agent_api._confirmation_payload_hash("create_matches", payload),
        True, True, None,
    )
    cur.rowcount = 1
    err = agent_api._consume_schedule_confirmation(cur, token="legacy-token", action="create_matches", payload=payload)
    assert err is None
    assert "token_hash" in cur.execute.call_args_list[0].args[0]
