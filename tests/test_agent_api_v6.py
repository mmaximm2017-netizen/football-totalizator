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


def rpl_tournament():
    return {"id": 5, "name": "Чемпионат России"}


def rcup_tournament():
    return {"id": 6, "name": "Кубок России"}


def match_row(match_id=428, league="rpl", tournament_id=5, status="SCHEDULED"):
    return (
        match_id,
        "ЦСКА",
        "Локомотив",
        datetime(2026, 8, 22, 17, 30, tzinfo=timezone.utc),
        datetime(2026, 8, 22, 8, 0, tzinfo=timezone.utc),
        status,
        None,
        None,
        "Тур 5" if league == "rpl" else "Групповой этап",
        "rpl" if league == "rpl" else "russian_cup",
        league,
        tournament_id,
    )


def test_openapi_contains_schedule_update_actions(client):
    spec = client.get("/api/agent/v1/openapi.json").get_json()
    text = str(spec)
    for operation_id in [
        "previewRplMatchScheduleUpdate",
        "updateRplMatchSchedule",
        "previewRussianCupMatchScheduleUpdate",
        "updateRussianCupMatchSchedule",
    ]:
        assert operation_id in text


def test_rpl_schedule_preview_is_read_only_and_recalculates_deadline(client):
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur

    # 1) current match
    # 2) duplicate/conflict lookup
    cur.fetchone.side_effect = [match_row(), None]

    confirmation = {
        "confirmation_id": 999,
        "confirmation_handle": "cfm_test",
        "confirmation_token": "test-token",
        "confirmation_required": True,
    }

    with patch("app.routes.agent_api.get_db", return_value=conn), \
         patch(
             "app.routes.agent_api.get_rpl_tournament",
             return_value=rpl_tournament(),
         ), \
         patch(
             "app.routes.agent_api._issue_schedule_confirmation",
             return_value=confirmation,
         ) as issue_confirmation:

        response = client.post(
            "/api/agent/v1/matches/428/schedule/preview",
            headers=auth(),
            json={"date": "2026-08-22", "time": "20:15"},
        )

    assert response.status_code == 200

    payload = response.get_json()

    assert payload["dry_run"] is True
    assert payload["changed"] is True

    assert payload["requested"]["kickoff_time_msk"].startswith(
        "2026-08-22T20:15:00"
    )
    assert payload["requested"]["deadline_msk"].startswith(
        "2026-08-22T11:00:00"
    )

    assert payload["confirmation_required"] is True
    assert payload["confirmation_id"] == 999
    assert bool(payload["confirmation_token"])

    # Этот старый тест проверяет preview, а не внутреннее SQL-хранилище
    # подтверждений. Сам confirmation storage тестируется отдельно.
    issue_confirmation.assert_called_once()
    assert conn.commit.called is False



def test_schedule_preview_rejects_finished_match(client):
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    cur.fetchone.return_value = match_row(status="FINISHED")

    with patch("app.routes.agent_api.get_db", return_value=conn), \
         patch("app.routes.agent_api.get_rpl_tournament", return_value=rpl_tournament()):
        response = client.post(
            "/api/agent/v1/matches/428/schedule/preview",
            headers=auth(),
            json={"date": "2026-08-22", "time": "20:15"},
        )

    assert response.status_code == 409
    assert response.get_json()["error"] == "schedule_update_not_allowed_for_status"
    assert conn.commit.called is False


def test_schedule_preview_rejects_duplicate(client):
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    cur.fetchone.side_effect = [match_row(), (999,)]

    with patch("app.routes.agent_api.get_db", return_value=conn), \
         patch("app.routes.agent_api.get_rpl_tournament", return_value=rpl_tournament()):
        response = client.post(
            "/api/agent/v1/matches/428/schedule/preview",
            headers=auth(),
            json={"date": "2026-08-22", "time": "20:15"},
        )

    assert response.status_code == 409
    payload = response.get_json()
    assert payload["error"] == "schedule_update_would_create_duplicate"
    assert payload["duplicate_match_id"] == 999


def test_rpl_schedule_write_updates_only_schedule_fields(client):
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    cur.fetchone.side_effect = [match_row(), None]
    cur.rowcount = 1

    with patch("app.routes.agent_api.get_db", return_value=conn), \
         patch("app.routes.agent_api.get_rpl_tournament", return_value=rpl_tournament()), \
         patch("app.routes.agent_api._consume_schedule_confirmation", return_value=None):
        response = client.post(
            "/api/agent/v1/matches/428/schedule",
            headers=auth(),
            json={"date": "2026-08-22", "time": "20:15"},
        )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["changed"] is True
    assert payload["points_recalculated"] is False
    update_sql = " ".join(cur.execute.call_args_list[-1].args[0].split())
    assert "SET kickoff_time = %s, deadline = %s" in update_sql
    assert "home_score" not in update_sql
    assert "away_score" not in update_sql
    assert "playoff_stage_manual" not in update_sql
    assert conn.commit.called is True


def test_russian_cup_schedule_write_is_scoped_to_rcup(client):
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    cur.fetchone.side_effect = [match_row(match_id=500, league="rcup", tournament_id=6), None]
    cur.rowcount = 1

    with patch("app.routes.agent_api.get_db", return_value=conn), \
         patch("app.routes.agent_api.get_russian_cup_tournament", return_value=rcup_tournament()), \
         patch("app.routes.agent_api._consume_schedule_confirmation", return_value=None):
        response = client.post(
            "/api/agent/v1/russian-cup/matches/500/schedule",
            headers=auth(),
            json={"date": "2026-08-22", "time": "20:15"},
        )

    assert response.status_code == 200
    update_args = cur.execute.call_args_list[-1].args[1]
    assert update_args[-1] == "rcup"
    assert conn.commit.called is True


def test_schedule_write_noop_when_time_already_matches(client):
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    current = match_row()
    # 17:30 UTC = 20:30 MSK; request same existing schedule.
    cur.fetchone.side_effect = [current, None]

    with patch("app.routes.agent_api.get_db", return_value=conn), \
         patch("app.routes.agent_api.get_rpl_tournament", return_value=rpl_tournament()), \
         patch("app.routes.agent_api._consume_schedule_confirmation", return_value=None):
        response = client.post(
            "/api/agent/v1/matches/428/schedule",
            headers=auth(),
            json={"date": "2026-08-22", "time": "20:30"},
        )

    assert response.status_code == 200
    assert response.get_json()["changed"] is False
    assert conn.commit.called is False
