from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask

from app.routes.predictions import predictions_bp


class Cursor:
    def __init__(self, result_sets):
        self.result_sets = iter(result_sets)
        self.calls = []

    def execute(self, query, params):
        self.calls.append((query, params))

    def fetchall(self):
        return next(self.result_sets)


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


def request_context(app, monkeypatch, path, result_sets):
    cursor = Cursor(result_sets)
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
        patch("app.routes.predictions.get_tournament_by_id", return_value={"id": 42, "name": "РПЛ"}),
        patch("app.routes.predictions.render_template", side_effect=render),
    ):
        with client.session_transaction() as session:
            session["user_id"] = 7
        response = client.get(path)

    return response, captured, cursor


def test_default_filter_is_active_and_preserves_selected_tournament(app, monkeypatch):
    response, context, cursor = request_context(app, monkeypatch, "/my-predictions?tid=42", [[], [], [], []])

    assert response.status_code == 200
    assert context["current_filter"] == "active"
    assert context["current_tournament_id"] == 42
    assert all(params[1] == 42 for _, params in cursor.calls)


def test_finished_filter_passes_finished_predictions_to_template(app, monkeypatch):
    finished_row = (1, "ЦСКА", "Зенит", 2, 1, 1, 0, 5)
    response, context, cursor = request_context(app, monkeypatch, "/my-predictions?tid=42&filter=finished", [[], [], [finished_row], []])

    assert response.status_code == 200
    assert context["current_filter"] == "finished"
    assert context["finished"] == [{"id": 1, "home_team": "ЦСКА", "away_team": "Зенит", "home_score": 2, "away_score": 1, "home_goals": 1, "away_goals": 0, "points": 5}]
    assert "ORDER BY m.kickoff_time DESC, m.id DESC" in cursor.calls[2][0]


def test_invalid_filter_falls_back_to_active(app, monkeypatch):
    _, context, _ = request_context(app, monkeypatch, "/my-predictions?filter=unexpected", [[], [], [], []])

    assert context["current_filter"] == "active"


def test_template_tabs_preserve_tid_and_filters_and_have_selected_empty_states():
    source = (Path(__file__).resolve().parents[1] / "templates" / "my_predictions.html").read_text(encoding="utf-8")

    assert "url_for('predictions.my_predictions', tid=current_tournament_id, filter='active')" in source
    assert "url_for('predictions.my_predictions', tid=current_tournament_id, filter='finished')" in source
    assert "Сейчас нет активных прогнозов." in source
    assert "Нет завершённых матчей." in source
    assert source.count("У вас пока нет прогнозов. Перейдите к матчам и сделайте прогноз.") == 2
    assert "{% if current_filter == 'active' %}" in source
    assert "{% else %}\n    <section class=\"predictions-section\">" in source


def test_template_uses_scoped_tournament_themes_without_legacy_table_styles():
    source = (Path(__file__).resolve().parents[1] / "templates" / "my_predictions.html").read_text(encoding="utf-8")

    for theme in ("body.tournament-rpl", "body.tournament-rcup", "body.tournament-wc2026"):
        assert theme in source
    for legacy_color in ("#e8f5e9", "#fff3e0", "#ffebee", "background: white"):
        assert legacy_color not in source
    assert "<table" not in source
