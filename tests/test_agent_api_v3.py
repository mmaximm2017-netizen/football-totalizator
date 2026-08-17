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


def _db_with_rows(rows):
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    cur.fetchall.return_value = rows
    return conn, cur


def test_openapi_contains_upcoming_and_recent(client):
    response = client.get("/api/agent/v1/openapi.json")
    assert response.status_code == 200
    spec = response.get_json()
    assert spec["paths"]["/matches/upcoming"]["get"]["operationId"] == "getUpcomingRplMatches"
    assert spec["paths"]["/matches/recent"]["get"]["operationId"] == "getRecentRplMatches"


def test_capabilities_expose_natural_language_read_actions(client):
    response = client.get("/api/agent/v1/capabilities", headers=auth())
    assert response.status_code == 200
    read = response.get_json()["read"]
    assert "get_upcoming_matches" in read
    assert "get_recent_matches" in read


def test_upcoming_matches_query_is_future_and_ascending(client):
    conn, cur = _db_with_rows([])
    with patch("app.routes.agent_api.get_db", return_value=conn), \
         patch("app.routes.agent_api.get_rpl_tournament", return_value={"id": 5, "name": "Чемпионат России"}):
        response = client.get("/api/agent/v1/matches/upcoming?limit=7", headers=auth())
    assert response.status_code == 200
    sql = " ".join(cur.execute.call_args.args[0].split())
    assert "kickoff_time >= NOW()" in sql
    assert "ORDER BY kickoff_time ASC, id ASC" in sql
    assert cur.execute.call_args.args[1] == (5, 7)


def test_recent_matches_query_is_finished_and_descending(client):
    conn, cur = _db_with_rows([])
    with patch("app.routes.agent_api.get_db", return_value=conn), \
         patch("app.routes.agent_api.get_rpl_tournament", return_value={"id": 5, "name": "Чемпионат России"}):
        response = client.get("/api/agent/v1/matches/recent?limit=9", headers=auth())
    assert response.status_code == 200
    sql = " ".join(cur.execute.call_args.args[0].split())
    assert "status = 'FINISHED'" in sql
    assert "kickoff_time <= NOW()" in sql
    assert "ORDER BY kickoff_time DESC, id DESC" in sql
    assert cur.execute.call_args.args[1] == (5, 9)


def test_upcoming_rejects_invalid_limit(client):
    response = client.get("/api/agent/v1/matches/upcoming?limit=abc", headers=auth())
    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_limit"
