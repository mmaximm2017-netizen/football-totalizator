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

def rcup_tournament():
    return {"id": 6, "name": "Кубок России", "is_active": 1, "start_date": None, "end_date": None}

def test_openapi_contains_all_russian_cup_actions(client):
    spec = client.get("/api/agent/v1/openapi.json").get_json()
    text = str(spec)
    for operation_id in [
        "getRussianCupTeams", "getRussianCupMatches", "getUpcomingRussianCupMatches",
        "getRecentRussianCupMatches", "findRussianCupMatch", "previewRussianCupMatches",
        "createRussianCupMatches", "setRussianCupMatchResult",
    ]:
        assert operation_id in text

def test_russian_cup_teams_use_canonical_catalog(client):
    response = client.get("/api/agent/v1/russian-cup/teams", headers=auth())
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["tournament"] == "rcup"
    assert "Зенит" in payload["teams"] and "Спартак" in payload["teams"]

def test_find_russian_cup_match_normalizes_aliases(client):
    conn = MagicMock(); cur = MagicMock(); conn.cursor.return_value = cur
    cur.fetchall.return_value = [(500, "Зенит", "Динамо", None, None, "SCHEDULED", None, None, "Групповой этап", "russian_cup", "rcup", 6)]
    with patch("app.routes.agent_api.get_db", return_value=conn), patch("app.routes.agent_api.get_russian_cup_tournament", return_value=rcup_tournament()):
        response = client.get("/api/agent/v1/russian-cup/matches/find?home_team=ФК%20Зенит&away_team=Динамо%20Москва", headers=auth())
    payload = response.get_json()
    assert response.status_code == 200
    assert payload["scope"] == "rcup"
    assert payload["query"]["home_team"] == "Зенит"
    assert payload["query"]["away_team"] == "Динамо"
    assert payload["unique"] is True

def test_preview_russian_cup_is_dry_run(client):
    conn = MagicMock(); cur = MagicMock(); conn.cursor.return_value = cur; cur.fetchone.return_value = None
    with patch("app.routes.agent_api.get_db", return_value=conn), patch("app.routes.agent_api.get_russian_cup_tournament", return_value=rcup_tournament()), \
        patch(
            "app.routes.agent_api._issue_schedule_confirmation",
            return_value={
                "confirmation_id": 999,
                "confirmation_handle": "cfm_test",
                "confirmation_token": "test-token",
                "confirmation_required": True,
            },
        ):
        response = client.post("/api/agent/v1/russian-cup/matches/preview", headers=auth(), json={"matches":[{"home_team":"Зенит","away_team":"Спартак","date":"2099-12-31","time":"19:00","round":4,"stage":"Групповой этап"}]})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["dry_run"] is True and payload["scope"] == "rcup" and payload["ready_count"] == 1
    assert payload["confirmation_required"] is True
    assert bool(payload["confirmation_token"])
    assert conn.commit.called is False
    sql_calls = [
        " ".join(call.args[0].split())
        for call in cur.execute.call_args_list
        if call.args
    ]
    assert any("league = 'rcup'" in sql for sql in sql_calls)

def test_create_russian_cup_uses_rcup_fields(client):
    conn = MagicMock(); cur = MagicMock(); conn.cursor.return_value = cur; cur.fetchone.return_value = None
    with patch("app.routes.agent_api.get_db", return_value=conn), patch("app.routes.agent_api.get_russian_cup_tournament", return_value=rcup_tournament()), patch("app.routes.agent_api.create_manual_match", return_value=888) as create, patch("app.routes.agent_api._consume_schedule_confirmation", return_value=None):
        response = client.post("/api/agent/v1/russian-cup/matches", headers=auth(), json={"matches":[{"home_team":"Зенит","away_team":"Спартак","date":"2099-12-31","time":"19:00","stage":"Групповой этап"}]})
    assert response.status_code == 201
    data = create.call_args.args[1]
    assert data.league == "rcup" and data.match_category == "russian_cup" and data.tournament_id == 6
    assert conn.commit.called is True

def test_russian_cup_result_rejects_existing_different_result(client):
    conn = MagicMock(); cur = MagicMock(); conn.cursor.return_value = cur
    cur.fetchone.return_value = (777, "Зенит", "Спартак", "FINISHED", 3, 0)
    with patch("app.routes.agent_api.get_db", return_value=conn), patch("app.routes.agent_api.get_russian_cup_tournament", return_value=rcup_tournament()), patch("app.routes.agent_api.recalc_match_points") as recalc, patch("app.routes.agent_api._consume_schedule_confirmation", return_value=None):
        response = client.post("/api/agent/v1/russian-cup/matches/777/result", headers=auth(), json={"home_score":2,"away_score":0})
    assert response.status_code == 409
    assert response.get_json()["error"] == "existing_result_requires_manual_review"
    assert recalc.called is False and conn.commit.called is False

def test_russian_cup_result_sets_manual_override_and_recalculates(client):
    conn = MagicMock(); cur = MagicMock(); conn.cursor.return_value = cur
    cur.fetchone.return_value = (777, "Зенит", "Спартак", "SCHEDULED", None, None)
    with patch("app.routes.agent_api.get_db", return_value=conn), patch("app.routes.agent_api.get_russian_cup_tournament", return_value=rcup_tournament()), patch("app.routes.agent_api.recalc_match_points") as recalc, patch("app.routes.agent_api._consume_schedule_confirmation", return_value=None):
        response = client.post("/api/agent/v1/russian-cup/matches/777/result", headers=auth(), json={"home_score":2,"away_score":1})
    assert response.status_code == 200
    sql_calls = [" ".join(call.args[0].split()) for call in cur.execute.call_args_list]
    assert any("manual_result_override = 1" in sql and "league = 'rcup'" in sql for sql in sql_calls)
    assert recalc.called is True and conn.commit.called is True

def test_upcoming_and_recent_russian_cup_queries_are_scoped(client):
    conn = MagicMock(); cur = MagicMock(); conn.cursor.return_value = cur; cur.fetchall.return_value = []
    with patch("app.routes.agent_api.get_db", return_value=conn), patch("app.routes.agent_api.get_russian_cup_tournament", return_value=rcup_tournament()):
        up = client.get("/api/agent/v1/russian-cup/matches/upcoming?limit=5", headers=auth())
        recent = client.get("/api/agent/v1/russian-cup/matches/recent?limit=5", headers=auth())
    assert up.status_code == 200 and recent.status_code == 200
    sql_calls = [" ".join(call.args[0].split()) for call in cur.execute.call_args_list]
    assert any("league = 'rcup'" in sql and "kickoff_time >= NOW()" in sql for sql in sql_calls)
    assert any("league = 'rcup'" in sql and "status = 'FINISHED'" in sql for sql in sql_calls)
