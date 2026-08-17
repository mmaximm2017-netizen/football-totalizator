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


def test_openapi_schema_is_public(client):
    response = client.get("/api/agent/v1/openapi.json")
    assert response.status_code == 200
    spec = response.get_json()
    assert spec["openapi"] == "3.1.0"
    assert "findRplMatch" in str(spec)
    assert "createRplMatches" in str(spec)
    assert "setRplMatchResult" in str(spec)


def test_capabilities_require_auth(client):
    assert client.get("/api/agent/v1/capabilities").status_code == 401
    response = client.get("/api/agent/v1/capabilities", headers=auth())
    assert response.status_code == 200
    assert response.get_json()["safety"]["explicit_confirmation_before_write"] is True


def test_find_match_normalizes_aliases(client):
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    cur.fetchall.return_value = [
        (123, "Зенит", "Динамо", None, None, "SCHEDULED",
         None, None, "Тур 7", "rpl", "rpl", 5)
    ]
    with patch("app.routes.agent_api.get_db", return_value=conn), \
         patch("app.routes.agent_api.get_rpl_tournament",
               return_value={"id": 5, "name": "Чемпионат России"}):
        response = client.get(
            "/api/agent/v1/matches/find?home_team=ФК%20Зенит&away_team=Динамо%20Москва",
            headers=auth(),
        )
    payload = response.get_json()
    assert response.status_code == 200
    assert payload["query"]["home_team"] == "Зенит"
    assert payload["query"]["away_team"] == "Динамо"
    assert payload["unique"] is True


def test_find_match_rejects_unknown_team(client):
    response = client.get(
        "/api/agent/v1/matches/find?home_team=Неизвестные&away_team=Зенит",
        headers=auth(),
    )
    assert response.status_code == 422


def test_result_sql_sets_manual_override(client):
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    cur.fetchone.return_value = (777, "Зенит", "Спартак", "SCHEDULED", None, None)

    with patch("app.routes.agent_api.get_db", return_value=conn), \
         patch("app.routes.agent_api.get_rpl_tournament",
               return_value={"id": 5, "name": "Чемпионат России"}), \
         patch("app.routes.agent_api.recalc_match_points"):
        response = client.post(
            "/api/agent/v1/matches/777/result",
            headers=auth(),
            json={"home_score": 2, "away_score": 1},
        )

    assert response.status_code == 200
    sql_calls = [" ".join(call.args[0].split()) for call in cur.execute.call_args_list]
    assert any("manual_result_override = 1" in sql for sql in sql_calls)
