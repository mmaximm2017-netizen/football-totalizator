from datetime import datetime, timedelta, timezone
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


def test_openapi_contains_admin_attention_action(client):
    spec = client.get("/api/agent/v1/openapi.json").get_json()
    operation = spec["paths"]["/admin-attention"]["get"]
    assert operation["operationId"] == "getTotishAdminAttention"
    assert "READ-ONLY" in operation["description"]


def test_admin_attention_returns_clean_report_without_writes(client):
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    cur.fetchall.side_effect = [[], [], [], []]

    with patch("app.routes.agent_api.get_db", return_value=conn), \
         patch("app.routes.agent_api.get_rpl_tournament", return_value={"id": 5}), \
         patch("app.routes.agent_api.get_russian_cup_tournament", return_value={"id": 6}):
        response = client.get("/api/agent/v1/admin-attention", headers=auth())

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["read_only"] is True
    assert payload["needs_attention"] is False
    assert payload["summary"] == {"total_issues": 0, "critical": 0, "warnings": 0}
    assert payload["issues"] == []
    assert conn.commit.called is False


def test_admin_attention_detects_finished_without_score_and_overdue(client):
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur

    old_kickoff = datetime.now(timezone.utc) - timedelta(hours=5)
    deadline = old_kickoff - timedelta(hours=6)
    finished_bad = (
        101, "rpl", "Зенит", "Спартак",
        datetime.now(timezone.utc) - timedelta(days=1),
        datetime.now(timezone.utc) - timedelta(days=1, hours=6),
        "FINISHED", None, None,
    )
    overdue = (
        102, "rpl", "ЦСКА", "Локомотив",
        old_kickoff, deadline,
        "SCHEDULED", None, None,
    )
    cur.fetchall.side_effect = [[finished_bad, overdue], [], [], []]

    with patch("app.routes.agent_api.get_db", return_value=conn), \
         patch("app.routes.agent_api.get_rpl_tournament", return_value={"id": 5}), \
         patch("app.routes.agent_api.get_russian_cup_tournament", return_value={"id": 6}):
        response = client.get("/api/agent/v1/admin-attention", headers=auth())

    assert response.status_code == 200
    payload = response.get_json()
    codes = {item["code"] for item in payload["issues"]}
    assert "finished_without_valid_score" in codes
    assert "overdue_unfinished_match" in codes
    assert payload["needs_attention"] is True
    assert conn.commit.called is False


def test_admin_attention_detects_exact_duplicates(client):
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    kickoff = datetime(2026, 8, 22, 17, 15, tzinfo=timezone.utc)
    cur.fetchall.side_effect = [
        [],
        [("ЦСКА", "Локомотив", kickoff, 2, [428, 999])],
        [],
        [],
    ]

    with patch("app.routes.agent_api.get_db", return_value=conn), \
         patch("app.routes.agent_api.get_rpl_tournament", return_value={"id": 5}), \
         patch("app.routes.agent_api.get_russian_cup_tournament", return_value={"id": 6}):
        response = client.get("/api/agent/v1/admin-attention", headers=auth())

    payload = response.get_json()
    assert payload["summary"]["critical"] == 1
    issue = payload["issues"][0]
    assert issue["code"] == "exact_duplicate_matches"
    assert issue["match_ids"] == [428, 999]
    assert conn.commit.called is False


def test_admin_attention_detects_bad_deadline_and_score_on_scheduled(client):
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    kickoff = datetime.now(timezone.utc) + timedelta(days=2)
    bad_deadline = kickoff + timedelta(minutes=1)
    row = (
        700, "rcup", "Зенит", "Спартак",
        kickoff, bad_deadline,
        "SCHEDULED", 1, 0,
    )
    cur.fetchall.side_effect = [[], [], [row], []]

    with patch("app.routes.agent_api.get_db", return_value=conn), \
         patch("app.routes.agent_api.get_rpl_tournament", return_value={"id": 5}), \
         patch("app.routes.agent_api.get_russian_cup_tournament", return_value={"id": 6}):
        response = client.get("/api/agent/v1/admin-attention", headers=auth())

    payload = response.get_json()
    codes = {item["code"] for item in payload["issues"]}
    assert "deadline_not_before_kickoff" in codes
    assert "score_on_nonfinished_match" in codes
    assert payload["summary"]["total_issues"] == 2
    assert conn.commit.called is False
