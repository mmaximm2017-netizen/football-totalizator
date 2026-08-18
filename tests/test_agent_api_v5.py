
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

def test_openapi_has_reconcile_actions(client):
    text = str(client.get("/api/agent/v1/openapi.json").get_json())
    assert "reconcileRplMatches" in text
    assert "reconcileRussianCupMatches" in text

def test_reconcile_rpl_splits_existing_and_missing(client):
    conn = MagicMock(); cur = MagicMock(); conn.cursor.return_value = cur
    cur.fetchone.side_effect = [(555, "SCHEDULED", None, None), None]
    with patch("app.routes.agent_api.get_db", return_value=conn), patch("app.routes.agent_api.get_rpl_tournament", return_value={"id": 5}):
        r = client.post("/api/agent/v1/matches/reconcile", headers=auth(), json={"matches": [
            {"home_team":"Зенит","away_team":"Спартак","date":"2099-12-30","time":"19:00","round":99},
            {"home_team":"ЦСКА","away_team":"Локомотив","date":"2099-12-31","time":"19:00","round":99}
        ]})
    p = r.get_json()
    assert r.status_code == 200
    assert p["existing_count"] == 1 and p["missing_count"] == 1 and p["invalid_count"] == 0
    assert p["existing"][0]["id"] == 555
    assert conn.commit.called is False

def test_reconcile_rcup_is_read_only(client):
    conn = MagicMock(); cur = MagicMock(); conn.cursor.return_value = cur
    cur.fetchone.side_effect = [None, (777, "FINISHED", 2, 1)]
    with patch("app.routes.agent_api.get_db", return_value=conn), patch("app.routes.agent_api.get_russian_cup_tournament", return_value={"id": 6}):
        r = client.post("/api/agent/v1/russian-cup/matches/reconcile", headers=auth(), json={"matches": [
            {"home_team":"Зенит","away_team":"Спартак","date":"2099-12-30","time":"19:00","stage":"Групповой этап","round":99},
            {"home_team":"ЦСКА","away_team":"Локомотив","date":"2099-12-31","time":"19:00","stage":"Групповой этап","round":99}
        ]})
    p = r.get_json()
    assert r.status_code == 200
    assert p["missing_count"] == 1 and p["existing_count"] == 1
    assert p["existing"][0]["id"] == 777
    assert conn.commit.called is False
