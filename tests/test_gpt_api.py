import inspect
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app import create_app
from app.routes import gpt_api


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("TOTISH_GPT_API_KEY", "test-gpt-token")
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def auth():
    return {"Authorization": "Bearer test-gpt-token"}


def mock_gpt_db(monkeypatch, *, rows=None, row=None):
    conn, cur = MagicMock(), MagicMock()
    conn.cursor.return_value = cur
    cur.fetchall.return_value = [] if rows is None else rows
    cur.fetchone.return_value = row
    monkeypatch.setattr(gpt_api.gpt_db, "get_gpt_db", lambda: conn)
    monkeypatch.setattr(gpt_api.gpt_db, "close_gpt_db", lambda connection, cursor: None)
    return conn, cur


def test_health_requires_read_only_gpt_database(client, monkeypatch):
    _, cur = mock_gpt_db(monkeypatch, row=("on",))

    response = client.get("/api/gpt/health", headers=auth())

    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "service": "totish-gpt-api", "database": "ok", "read_only": True}
    assert response.headers["Cache-Control"] == "no-store"
    cur.execute.assert_called_once_with("SHOW transaction_read_only", ())


def test_health_fails_closed_when_connection_is_not_read_only(client, monkeypatch):
    mock_gpt_db(monkeypatch, row=("off",))

    response = client.get("/api/gpt/health", headers=auth())

    assert response.status_code == 503


@pytest.mark.parametrize("headers", ({}, {"Authorization": "Bearer wrong-token"}))
def test_gpt_api_rejects_missing_or_invalid_bearer_key(client, headers):
    assert client.get("/api/gpt/health", headers=headers).status_code == 401


def test_gpt_api_rejects_when_server_key_is_missing(monkeypatch):
    monkeypatch.delenv("TOTISH_GPT_API_KEY", raising=False)
    app = create_app()
    app.config.update(TESTING=True)

    assert app.test_client().get("/api/gpt/health", headers=auth()).status_code == 401


def test_gpt_api_does_not_import_or_call_primary_database(client, monkeypatch):
    mock_gpt_db(monkeypatch, row=("on",))
    primary_get_db = MagicMock()
    monkeypatch.setattr("app.db.get_db", primary_get_db)

    response = client.get("/api/gpt/health", headers=auth())

    assert response.status_code == 200
    primary_get_db.assert_not_called()
    assert "from app.db" not in inspect.getsource(gpt_api)


def test_gpt_requests_skip_browser_session_processing(client, monkeypatch):
    mock_gpt_db(monkeypatch, row=("on",))
    with client.session_transaction() as session:
        session["user_id"] = 7
    primary_get_db = MagicMock()
    monkeypatch.setattr("app.get_db", primary_get_db)

    assert client.get("/api/gpt/health", headers=auth()).status_code == 200
    primary_get_db.assert_not_called()


def test_users_exclude_sensitive_fields_and_use_gpt_connection(client, monkeypatch):
    conn, cur = mock_gpt_db(monkeypatch, rows=[(7, "Игрок")])

    response = client.get("/api/gpt/users", headers=auth())

    assert response.get_json() == {"users": [{"user_id": 7, "username": "Игрок"}]}
    query = cur.execute.call_args.args[0].lower()
    assert "password" not in query and "last_seen" not in query
    assert conn.commit.called is False


def test_matches_supports_bounded_filters_and_moscow_dates(client, monkeypatch):
    _, cur = mock_gpt_db(monkeypatch, rows=[(42, 5, "РПЛ", "ЦСКА", "Зенит", datetime(2026, 8, 1, 21, 30, tzinfo=timezone.utc), None, "SCHEDULED", None, None, None, "rpl")])

    response = client.get("/api/gpt/matches?match_id=42&tournament_id=5&team=ЦСКА&status=SCHEDULED&date_from=2026-08-02&date_to=2026-08-02&finished=false&limit=2&offset=1", headers=auth())

    assert response.status_code == 200
    assert response.get_json()["matches"][0]["kickoff_time"] == "2026-08-01T21:30:00+00:00"
    query, params = cur.execute.call_args.args
    assert "AT TIME ZONE 'Europe/Moscow'" in query
    assert "m.status = %s" in query
    assert params[-2:] == (2, 1)


@pytest.mark.parametrize("path", ("/api/gpt/matches?limit=501", "/api/gpt/matches?offset=-1", "/api/gpt/matches?date_from=bad", "/api/gpt/matches?tournament_id=1%20OR%201=1"))
def test_invalid_pagination_and_injection_attempts_return_400(client, path):
    assert client.get(path, headers=auth()).status_code == 400


def test_prediction_values_are_withheld_before_deadline(client, monkeypatch):
    _, cur = mock_gpt_db(monkeypatch, rows=[])

    response = client.get("/api/gpt/predictions?finished=false", headers=auth())

    assert response.status_code == 200
    query = cur.execute.call_args.args[0]
    assert "m.deadline <= CURRENT_TIMESTAMP" in query
    assert response.get_json()["predictions"] == []


def test_predictions_and_raw_analytics_return_finished_stored_points(client, monkeypatch):
    row = (42, 7, 5, "Игрок", "РПЛ", 1, 0, "ЦСКА", "Зенит", datetime(2026, 8, 15, 12, tzinfo=timezone.utc), "FINISHED", 2, 1, 5, "тур", "rpl")
    _, cur = mock_gpt_db(monkeypatch, rows=[row])

    response = client.get("/api/gpt/analytics/predictions?finished=true&limit=1", headers=auth())

    assert response.status_code == 200
    item = response.get_json()["analytics_predictions"][0]
    assert item["predicted_home"] == 1 and item["actual_home"] == 2 and item["points"] == 5
    assert item["tournament_name"] == "РПЛ"
    assert "CASE WHEN UPPER(m.status)" in cur.execute.call_args.args[0]


def test_player_summary_uses_stored_exact_point_values(client, monkeypatch):
    mock_gpt_db(monkeypatch, row=(4, 25, 6.25, 1, 1, 1, 1))

    response = client.get("/api/gpt/analytics/player-summary?user_id=7", headers=auth())

    assert response.status_code == 200
    assert response.get_json() == {"user_id": 7, "matches_count": 4, "total_points": 25, "average_points": 6.25, "points_0": 1, "points_5": 1, "points_7": 1, "points_10_or_11": 1}


@pytest.mark.parametrize("method", ("post", "put", "patch", "delete"))
def test_mutating_methods_have_no_gpt_route(client, method):
    assert getattr(client, method)("/api/gpt/health", headers=auth()).status_code >= 400


def test_openapi_documents_every_gpt_route():
    specification = (Path(__file__).parents[1] / "docs" / "totish_gpt_openapi.yaml").read_text(encoding="utf-8")
    documented_paths = {
        "/api/gpt/health",
        "/api/gpt/tournaments",
        "/api/gpt/users",
        "/api/gpt/matches",
        "/api/gpt/predictions",
        "/api/gpt/analytics/predictions",
        "/api/gpt/analytics/player-summary",
        "/api/gpt/player-stats",
    }

    assert all(path in specification for path in documented_paths)
    assert "https://totish.ru" in specification
