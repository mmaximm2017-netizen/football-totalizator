from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app import create_app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("TOTISH_GPT_API_KEY", "test-gpt-token")
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def auth():
    return {"Authorization": "Bearer test-gpt-token"}


def mock_db(rows=None, row=None):
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    cur.fetchall.return_value = rows if rows is not None else []
    cur.fetchone.return_value = row
    return conn, cur


def test_health_accepts_valid_key(client):
    response = client.get("/api/gpt/health", headers=auth())

    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "service": "totish-gpt-api"}
    assert response.headers["Cache-Control"] == "no-store"


def test_health_rejects_missing_key(client):
    response = client.get("/api/gpt/health")

    assert response.status_code == 401
    assert response.get_json()["error"] == "unauthorized"


def test_health_rejects_wrong_key(client):
    response = client.get(
        "/api/gpt/health",
        headers={"Authorization": "Bearer wrong-token"},
    )

    assert response.status_code == 401


def test_health_rejects_requests_when_server_key_is_missing(monkeypatch):
    monkeypatch.delenv("TOTISH_GPT_API_KEY", raising=False)
    app = create_app()
    app.config.update(TESTING=True)

    response = app.test_client().get(
        "/api/gpt/health",
        headers={"Authorization": "Bearer any-token"},
    )

    assert response.status_code == 401


@pytest.mark.parametrize(
    "headers, expected_status",
    ((auth(), 200), ({}, 401), ({"Authorization": "Bearer wrong-token"}, 401)),
)
def test_gpt_requests_skip_session_user_processing(client, headers, expected_status):
    with client.session_transaction() as session:
        session["user_id"] = 7

    with patch("app.get_db") as session_get_db:
        response = client.get("/api/gpt/health", headers=headers)

    assert response.status_code == expected_status
    session_get_db.assert_not_called()


def test_tournaments_returns_public_fields(client):
    conn, cur = mock_db(rows=[(5, "Чемпионат России")])

    with patch("app.routes.gpt_api.get_db", return_value=conn):
        response = client.get("/api/gpt/tournaments", headers=auth())

    assert response.status_code == 200
    assert response.get_json() == {"tournaments": [{"id": 5, "name": "Чемпионат России"}]}
    assert conn.commit.called is False
    assert "SELECT id, name" in cur.execute.call_args.args[0]


def test_users_excludes_sensitive_fields(client):
    conn, cur = mock_db(rows=[(7, "Игрок")])

    with patch("app.routes.gpt_api.get_db", return_value=conn):
        response = client.get("/api/gpt/users", headers=auth())

    payload = response.get_json()
    assert response.status_code == 200
    assert payload == {"users": [{"id": 7, "username": "Игрок"}]}
    assert "password" not in str(payload).lower()
    assert "last_seen" not in str(payload).lower()
    assert "password" not in cur.execute.call_args.args[0].lower()
    assert conn.commit.called is False


def test_matches_applies_filters_and_pagination(client):
    conn, cur = mock_db(
        rows=[
            (
                42,
                5,
                "ЦСКА",
                "Зенит",
                datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc),
                2,
                1,
                "FINISHED",
            )
        ]
    )

    with patch("app.routes.gpt_api.get_db", return_value=conn):
        response = client.get(
            "/api/gpt/matches?tournament_id=5&date_from=2026-08-01&date_to=2026-08-31&finished=true&limit=2&offset=1",
            headers=auth(),
        )

    payload = response.get_json()
    query, params = cur.execute.call_args.args
    assert response.status_code == 200
    assert payload["matches"][0]["match_id"] == 42
    assert payload["matches"][0]["match_datetime"] == "2026-08-15T12:00:00+00:00"
    assert payload["limit"] == 2
    assert payload["offset"] == 1
    assert "m.tournament_id = %s" in query
    assert "UPPER(m.status) IN" in query
    assert "AT TIME ZONE 'Europe/Moscow'" in query
    assert params == (5, "2026-08-01", "2026-08-31", 2, 1)
    assert conn.commit.called is False


def test_matches_date_filters_use_moscow_calendar_day_at_utc_boundary(client):
    conn, cur = mock_db(
        rows=[
            (
                43,
                5,
                "Команда А",
                "Команда Б",
                datetime(2026, 8, 1, 21, 30, tzinfo=timezone.utc),
                None,
                None,
                "SCHEDULED",
            )
        ]
    )

    with patch("app.routes.gpt_api.get_db", return_value=conn):
        response = client.get(
            "/api/gpt/matches?date_from=2026-08-02&date_to=2026-08-02",
            headers=auth(),
        )

    query, params = cur.execute.call_args.args
    assert response.status_code == 200
    assert response.get_json()["matches"][0]["match_id"] == 43
    assert query.count("AT TIME ZONE 'Europe/Moscow'") == 2
    assert params == ("2026-08-02", "2026-08-02", 100, 0)


@pytest.mark.parametrize(
    "path",
    (
        "/api/gpt/matches?limit=501",
        "/api/gpt/matches?offset=-1",
        "/api/gpt/matches?date_from=not-a-date",
        "/api/gpt/matches?finished=yes",
        "/api/gpt/player-stats",
    ),
)
def test_invalid_parameters_return_400(client, path):
    response = client.get(path, headers=auth())

    assert response.status_code == 400
    assert response.is_json


def test_predictions_applies_filters_and_hides_unfinished_results(client):
    conn, cur = mock_db(rows=[(42, 7, 5, "Игрок", 1, 0, "SCHEDULED", None, None, None)])

    with patch("app.routes.gpt_api.get_db", return_value=conn):
        response = client.get(
            "/api/gpt/predictions?match_id=42&user_id=7&tournament_id=5&date_from=2026-08-01&date_to=2026-08-31&limit=1&offset=0",
            headers=auth(),
        )

    payload = response.get_json()
    query, params = cur.execute.call_args.args
    assert response.status_code == 200
    assert payload["predictions"] == [
        {
            "match_id": 42,
            "user_id": 7,
            "tournament_id": 5,
            "username": "Игрок",
            "predicted_home": 1,
            "predicted_away": 0,
            "status": "SCHEDULED",
            "actual_home": None,
            "actual_away": None,
            "points": None,
        }
    ]
    assert payload["prediction_id_available"] is False
    assert "p.match_id = %s" in query
    assert "AT TIME ZONE 'Europe/Moscow'" in query
    assert params == (42, 7, 5, "2026-08-01", "2026-08-31", 1, 0)
    assert conn.commit.called is False


def test_player_stats_uses_stored_points_for_finished_matches(client):
    conn, cur = mock_db(row=(4, 25, 6.25, 1, 1, 2))
    cur.fetchall.return_value = [(7,)]

    with patch("app.routes.gpt_api.get_db", return_value=conn):
        response = client.get(
            "/api/gpt/player-stats?user_id=7&tournament_id=5&date_from=2026-08-01&date_to=2026-08-31",
            headers=auth(),
        )

    payload = response.get_json()
    assert response.status_code == 200
    assert payload == {
        "user_id": 7,
        "matches_count": 4,
        "total_points": 25,
        "average_points": 6.25,
        "points_10_or_11": 1,
        "zero_points": 1,
        "points_7_or_more": 2,
    }
    assert "SUM(p.points)" in cur.execute.call_args.args[0]
    assert "AT TIME ZONE 'Europe/Moscow'" in cur.execute.call_args.args[0]
    assert conn.commit.called is False


def test_player_stats_hides_admin_or_deleted_users(client):
    conn, cur = mock_db(rows=[])

    with patch("app.routes.gpt_api.get_db", return_value=conn):
        response = client.get("/api/gpt/player-stats?user_id=99", headers=auth())

    assert response.status_code == 404
    assert response.get_json() == {"ok": False, "error": "not_found"}
    query, params = cur.execute.call_args.args
    assert "is_admin = 0" in query
    assert "COALESCE(is_deleted, 0) = 0" in query
    assert params == (99,)


@pytest.mark.parametrize("method", ("post", "put", "patch", "delete"))
def test_mutating_methods_are_not_supported(client, method):
    response = getattr(client, method)("/api/gpt/health", headers=auth())

    assert response.status_code == 400
