from unittest.mock import MagicMock, patch

import pytest
from app import create_app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("TOTISH_AGENT_TOKEN", "test-agent-token")
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def auth():
    return {"Authorization": "Bearer test-agent-token"}


def test_openapi_create_actions_require_confirmation_token(client):
    p = client.get("/api/agent/v1/openapi.json").get_json()["paths"]
    assert "confirmation_token" in str(p["/matches"]["post"])
    assert "confirmation_token" in str(p["/russian-cup/matches"]["post"])


def test_rpl_create_without_token_never_creates(client):
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    cur.fetchone.return_value = None

    with patch("app.routes.agent_api.get_db", return_value=conn), \
         patch("app.routes.agent_api.get_rpl_tournament", return_value={"id": 5}), \
         patch("app.routes.agent_api.create_manual_match") as create:
        r = client.post(
            "/api/agent/v1/matches",
            headers=auth(),
            json={"matches": [{
                "home_team": "Зенит",
                "away_team": "Спартак",
                "date": "2099-12-31",
                "time": "19:00",
                "round": 99,
            }]},
        )

    assert r.status_code == 409
    assert r.get_json()["error"] == "confirmation_required"
    assert not create.called


def test_rcup_create_without_token_never_creates(client):
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    cur.fetchone.return_value = None

    with patch("app.routes.agent_api.get_db", return_value=conn), \
         patch("app.routes.agent_api.get_russian_cup_tournament", return_value={"id": 6}), \
         patch("app.routes.agent_api.create_manual_match") as create:
        r = client.post(
            "/api/agent/v1/russian-cup/matches",
            headers=auth(),
            json={"matches": [{
                "home_team": "Зенит",
                "away_team": "Спартак",
                "date": "2099-12-31",
                "time": "19:00",
                "stage": "Групповой этап",
            }]},
        )

    assert r.status_code == 409
    assert r.get_json()["error"] == "confirmation_required"
    assert not create.called


def test_rpl_preview_issues_confirmation_token(client):
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    cur.fetchone.return_value = None

    def fake_issue(cur, conn, *, action, payload):
        return {
            "confirmation_token": "abc",
            "confirmation_required": True,
            "confirmation_min_age_seconds": 8,
            "confirmation_expires_in_seconds": 300,
        }

    with patch("app.routes.agent_api.get_db", return_value=conn), \
         patch("app.routes.agent_api.get_rpl_tournament", return_value={"id": 5}), \
         patch("app.routes.agent_api._issue_schedule_confirmation", side_effect=fake_issue):
        r = client.post(
            "/api/agent/v1/matches/preview",
            headers=auth(),
            json={"matches": [{
                "home_team": "Зенит",
                "away_team": "Спартак",
                "date": "2099-12-31",
                "time": "19:00",
                "round": 99,
            }]},
        )

    d = r.get_json()
    assert r.status_code == 200
    assert d["dry_run"] is True
    assert d["confirmation_required"] is True
    assert d["confirmation_token"] == "abc"
