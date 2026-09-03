from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask

from app.routes.predictions import predictions_bp


class Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def execute(self, query, params):
        self.calls.append((query, params))

    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self, cursor):
        self.cursor_value = cursor

    def cursor(self):
        return self.cursor_value


@pytest.fixture
def app():
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(predictions_bp)
    app.add_url_rule("/login", "auth.login", lambda: "login")
    app.add_url_rule("/", "main.index", lambda: "home")
    return app


def request_context(app, path, rows):
    cursor = Cursor(rows)
    captured = {}

    def render(_template, **context):
        captured.update(context)
        return "predictions"

    with (
        app.test_client() as client,
        patch("app.routes.predictions.get_db", return_value=Connection(cursor)),
        patch("app.routes.predictions.close_db"),
        patch("app.routes.predictions.get_all_tournaments", return_value=[{"id": 42, "name": "РПЛ", "is_active": 1}]),
        patch("app.routes.predictions.get_selected_tournament_id", return_value=42),
        patch("app.routes.predictions.render_template", side_effect=render),
    ):
        with client.session_transaction() as session:
            session["user_id"] = 7
        response = client.get(path)

    return response, captured, cursor


def test_history_loads_only_finished_predictions_for_selected_tournament(app):
    finished_row = (1, datetime(2026, 8, 31, 15, tzinfo=timezone.utc), "ЦСКА", "Зенит", 2, 1, 1, 0, 5)
    response, context, cursor = request_context(app, "/my-predictions?tid=42", [finished_row])

    assert response.status_code == 200
    assert context["current_tournament_id"] == 42
    assert context["finished"][0]["date"] == "31 августа"
    assert len(cursor.calls) == 1
    query, params = cursor.calls[0]
    assert "m.status = 'FINISHED'" in query
    assert "ORDER BY m.kickoff_time DESC, m.id DESC" in query
    assert "deadline" not in query.lower()
    assert params == (7, 42)


@pytest.mark.parametrize("legacy_filter", ("active", "finished", "unexpected"))
def test_legacy_filter_urls_keep_finished_only_history(app, legacy_filter):
    response, context, cursor = request_context(app, f"/my-predictions?tid=42&filter={legacy_filter}", [])

    assert response.status_code == 200
    assert context["finished"] == []
    assert len(cursor.calls) == 1
    assert "m.status = 'FINISHED'" in cursor.calls[0][0]


def test_template_has_no_filter_tabs_and_keeps_finished_card_themes():
    source = (Path(__file__).resolve().parents[1] / "templates" / "my_predictions.html").read_text(encoding="utf-8")

    assert "predictions-tabs" not in source
    assert "Активные" not in source
    assert "current_filter" not in source
    assert "Завершённые прогнозы" in source
    assert "У вас пока нет завершённых прогнозов." in source
    for theme in ("body.tournament-rpl", "body.tournament-rcup", "body.tournament-wc2026"):
        assert theme in source
    assert "prediction-date" in source
    assert "prediction-points-premium" in source
