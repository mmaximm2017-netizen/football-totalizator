from unittest.mock import MagicMock, patch
import pytest
from app import create_app
from app.routes import agent_api

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("TOTISH_AGENT_TOKEN","test-agent-token")
    app=create_app(); app.config.update(TESTING=True); return app.test_client()

def auth():
    return {"Authorization":"Bearer test-agent-token"}

def test_openapi_result_hard_confirmation(client):
    p=client.get("/api/agent/v1/openapi.json").get_json()["paths"]
    assert p["/matches/{match_id}/result/preview"]["post"]["operationId"]=="previewRplMatchResult"
    assert p["/russian-cup/matches/{match_id}/result/preview"]["post"]["operationId"]=="previewRussianCupMatchResult"
    assert "confirmation_token" in str(p["/matches/{match_id}/result"]["post"])
    assert "confirmation_token" in str(p["/russian-cup/matches/{match_id}/result"]["post"])

def test_hash_binds_exact_score():
    a=agent_api._confirmation_payload_hash("set_match_result",agent_api._result_confirmation_payload(league="rpl",match_id=428,home_score=1,away_score=0))
    b=agent_api._confirmation_payload_hash("set_match_result",agent_api._result_confirmation_payload(league="rpl",match_id=428,home_score=2,away_score=0))
    assert a!=b

def test_rpl_result_without_token_blocked(client):
    conn=MagicMock(); cur=MagicMock(); conn.cursor.return_value=cur
    with patch("app.routes.agent_api.get_db",return_value=conn), patch("app.routes.agent_api.get_rpl_tournament",return_value={"id":5}):
        r=client.post("/api/agent/v1/matches/428/result",headers=auth(),json={"home_score":1,"away_score":0})
    assert r.status_code==409
    assert r.get_json()["error"]=="confirmation_required"

def test_rcup_result_without_token_blocked(client):
    conn=MagicMock(); cur=MagicMock(); conn.cursor.return_value=cur
    with patch("app.routes.agent_api.get_db",return_value=conn), patch("app.routes.agent_api.get_russian_cup_tournament",return_value={"id":6}):
        r=client.post("/api/agent/v1/russian-cup/matches/387/result",headers=auth(),json={"home_score":1,"away_score":0})
    assert r.status_code==409
    assert r.get_json()["error"]=="confirmation_required"

def test_preview_issues_token(client):
    conn=MagicMock(); cur=MagicMock(); conn.cursor.return_value=cur
    cur.fetchone.return_value=(428,"ЦСКА","Локомотив","SCHEDULED",None,None)
    with patch("app.routes.agent_api.get_db",return_value=conn), patch("app.routes.agent_api.get_rpl_tournament",return_value={"id":5}), patch("app.routes.agent_api._issue_schedule_confirmation",return_value={"confirmation_token":"abc","confirmation_required":True,"confirmation_min_age_seconds":8,"confirmation_expires_in_seconds":300}):
        r=client.post("/api/agent/v1/matches/428/result/preview",headers=auth(),json={"home_score":1,"away_score":0})
    d=r.get_json()
    assert r.status_code==200 and d["dry_run"] is True and d["changed"] is True and d["confirmation_token"]=="abc"

def test_existing_different_result_still_rejected(client):
    conn=MagicMock(); cur=MagicMock(); conn.cursor.return_value=cur
    cur.fetchone.return_value=(428,"ЦСКА","Локомотив","FINISHED",2,1)
    with patch("app.routes.agent_api.get_db",return_value=conn), patch("app.routes.agent_api.get_rpl_tournament",return_value={"id":5}), patch("app.routes.agent_api._issue_schedule_confirmation") as issue:
        r=client.post("/api/agent/v1/matches/428/result/preview",headers=auth(),json={"home_score":1,"away_score":0})
    assert r.status_code==409
    assert r.get_json()["error"]=="existing_result_requires_manual_review"
    assert not issue.called
