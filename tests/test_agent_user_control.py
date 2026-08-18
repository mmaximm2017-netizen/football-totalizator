from datetime import datetime, timezone
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


def test_openapi_contains_user_control_actions(client):
    spec = client.get("/api/agent/v1/openapi.json").get_json()
    paths = spec["paths"]
    assert paths["/users/activity"]["get"]["operationId"] == "getTotishUsers"
    assert paths["/users/{user_id}/activity"]["get"]["operationId"] == "getTotishUserActivity"
    assert paths["/matches/{match_id}/prediction-participation"]["get"]["operationId"] == "getMatchPredictionParticipation"
    assert paths["/prediction-participation"]["post"]["operationId"] == "getTournamentPredictionParticipation"


def test_users_activity_has_no_sensitive_fields(client):
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    seen = datetime(2026, 8, 18, 6, 0, tzinfo=timezone.utc)
    cur.fetchall.return_value = [(1, "Макс Зенит", 0, seen, 0)]

    with patch("app.routes.agent_api.get_db", return_value=conn):
        response = client.get("/api/agent/v1/users/activity", headers=auth())

    payload = response.get_json()
    assert response.status_code == 200
    text = str(payload).lower()
    assert "password" not in text
    assert "home_goals" not in text
    assert "away_goals" not in text
    assert payload["users"][0]["username"] == "Макс Зенит"
    assert conn.commit.called is False


def test_user_activity_exposes_prediction_presence_not_score(client):
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    seen = datetime(2026, 8, 18, 6, 0, tzinfo=timezone.utc)
    kickoff = datetime(2026, 8, 22, 17, 15, tzinfo=timezone.utc)
    cur.fetchone.side_effect = [
        (1, "Макс Зенит", 0, seen, 0),
        (3, 2, 5),
    ]
    cur.fetchall.return_value = [
        (428, "ЦСКА", "Локомотив", kickoff, "rpl", "Тур 5", "SCHEDULED")
    ]

    with patch("app.routes.agent_api.get_db", return_value=conn):
        response = client.get("/api/agent/v1/users/1/activity", headers=auth())

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["prediction_scores_exposed"] is False
    assert payload["prediction_created_at_available"] is False
    text = str(payload).lower()
    assert "home_goals" not in text
    assert "away_goals" not in text
    assert payload["recent_prediction_matches"][0]["has_prediction"] is True


def test_match_participation_splits_predicted_and_missing_without_scores(client):
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    kickoff = datetime(2026, 8, 22, 17, 15, tzinfo=timezone.utc)
    match = (
        428, "ЦСКА", "Локомотив", kickoff,
        datetime(2026, 8, 22, 8, 0, tzinfo=timezone.utc),
        "SCHEDULED", None, None, "Тур 5", "rpl", "rpl", 5
    )
    cur.fetchone.return_value = match
    cur.fetchall.side_effect = [
        [(1, "Макс Зенит"), (2, "БЕК125125")],
        [(1,)],
    ]

    with patch("app.routes.agent_api.get_db", return_value=conn):
        response = client.get("/api/agent/v1/matches/428/prediction-participation", headers=auth())

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["predicted_users"] == [{"user_id": 1, "username": "Макс Зенит"}]
    assert payload["missing_users"] == [{"user_id": 2, "username": "БЕК125125"}]
    assert payload["prediction_scores_exposed"] is False


def test_multi_match_participation_returns_completion_only(client):
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    k1 = datetime(2026, 8, 22, 17, 15, tzinfo=timezone.utc)
    k2 = datetime(2026, 8, 23, 15, 0, tzinfo=timezone.utc)
    cur.fetchall.side_effect = [
        [
            (428, "ЦСКА", "Локомотив", k1, "rpl", "Тур 5", "SCHEDULED"),
            (429, "Зенит", "Спартак", k2, "rpl", "Тур 5", "SCHEDULED"),
        ],
        [(1, "Макс Зенит"), (2, "БЕК125125")],
        [(1, 428), (1, 429), (2, 428)],
    ]

    with patch("app.routes.agent_api.get_db", return_value=conn):
        response = client.post(
            "/api/agent/v1/prediction-participation",
            headers=auth(),
            json={"match_ids": [428, 429]},
        )

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["prediction_scores_exposed"] is False
    assert payload["users"][0]["username"] == "Макс Зенит"
    assert payload["users"][0]["completion_percent"] == 100.0
    assert payload["users"][1]["completion_percent"] == 50.0
    assert "home_goals" not in str(payload).lower()
    assert "away_goals" not in str(payload).lower()
