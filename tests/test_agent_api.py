import pytest

from app import create_app


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("TOTISH_AGENT_TOKEN", "test-agent-token")
    app = create_app()
    app.config.update(TESTING=True)
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def auth_headers():
    return {"Authorization": "Bearer test-agent-token"}


def test_agent_health_rejects_missing_token(client):
    response = client.get("/api/agent/v1/health")
    assert response.status_code == 401
    assert response.get_json()["error"] == "unauthorized"


def test_agent_health_rejects_wrong_token(client):
    response = client.get(
        "/api/agent/v1/health",
        headers={"Authorization": "Bearer wrong"},
    )
    assert response.status_code == 401


def test_agent_health_accepts_bearer_token(client):
    response = client.get("/api/agent/v1/health", headers=auth_headers())
    assert response.status_code == 200
    assert response.get_json() == {
        "ok": True,
        "service": "totish-agent-api",
        "version": 1,
    }
    assert response.headers["Cache-Control"] == "no-store"


def test_agent_post_reaches_agent_auth_before_session_csrf(client):
    response = client.post(
        "/api/agent/v1/matches/preview",
        json={"matches": []},
    )
    # Agent auth must reject it first. Global session CSRF would return HTML 400.
    assert response.status_code == 401
    assert response.is_json


def test_agent_api_is_disabled_without_server_token(monkeypatch):
    monkeypatch.delenv("TOTISH_AGENT_TOKEN", raising=False)
    app = create_app()
    app.config.update(TESTING=True)
    client = app.test_client()
    response = client.get(
        "/api/agent/v1/health",
        headers={"Authorization": "Bearer anything"},
    )
    assert response.status_code == 503
    assert response.get_json()["error"] == "agent_api_disabled"
