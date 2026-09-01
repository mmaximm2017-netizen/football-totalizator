from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from flask import Flask

from app.routes.profile import profile_bp


class Cursor:
    def __init__(self, one_rows=(), all_rows=()):
        self.one_rows = iter(one_rows)
        self.all_rows = iter(all_rows)
        self.calls = []

    def execute(self, query, params):
        self.calls.append((query, params))

    def fetchone(self):
        return next(self.one_rows, None)

    def fetchall(self):
        return next(self.all_rows, [])


class Connection:
    def __init__(self, cursor):
        self.cursor_value = cursor

    def cursor(self):
        return self.cursor_value


@pytest.fixture
def app():
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(profile_bp)
    app.add_url_rule("/login", "auth.login", lambda: "login")
    app.add_url_rule("/table", "table.table", lambda: "table")
    app.add_url_rule("/my-predictions", "predictions.my_predictions", lambda: "predictions")
    return app


def common_patches(get_db, render):
    return (
        patch("app.routes.profile.get_db", side_effect=get_db),
        patch("app.routes.profile.close_db"),
        patch("app.routes.profile.get_all_tournaments", return_value=[{"id": 42, "name": "РПЛ", "is_active": 1}]),
        patch("app.routes.profile.get_selected_tournament_id", return_value=42),
        patch("app.routes.profile.get_tournament_by_id", return_value={"id": 42, "name": "РПЛ"}),
        patch("app.routes.profile.get_tournament_ranking", return_value=[{"user_id": 2, "place": 1, "points": 15}]),
        patch("app.routes.profile.render_template", side_effect=render),
    )


@contextmanager
def patched_client(app, get_db, render, extra=()):
    with ExitStack() as stack:
        for item in (*common_patches(get_db, render), *extra):
            stack.enter_context(item)
        with app.test_client() as client:
            yield client


def test_standings_link_uses_stable_public_user_id_and_tid():
    source = (Path(__file__).resolve().parents[1] / "templates" / "table_content.html").read_text(encoding="utf-8")

    assert "url_for('profile.public_profile', user_id=row.user_id, tid=selected_tid)" in source
    assert "/profile?username=" not in source


def test_public_profile_renders_requested_participant_and_preserves_tid(app):
    cursor = Cursor(one_rows=[(2, "Другой")], all_rows=[[("Титул", None)]])
    captured = {}

    def render(_template, **context):
        captured.update(context)
        return "profile"

    with patched_client(app, lambda: Connection(cursor), render) as client:
        with client.session_transaction() as session:
            session["user_id"] = 1
        response = client.get("/profile/2?tid=42")

    assert response.status_code == 200
    assert captured["username"] == "Другой"
    assert captured["is_own_profile"] is False
    assert captured["public_profile_user_id"] == 2
    assert captured["current_tournament_id"] == 42


def test_public_profile_stats_reuses_stats_service_for_requested_user(app):
    cursor = Cursor(one_rows=[(2, "Другой")], all_rows=[[]])
    captured = {}

    def render(_template, **context):
        captured.update(context)
        return "stats"

    with patched_client(
        app,
        lambda: Connection(cursor),
        render,
        extra=(patch("app.routes.profile.get_profile_stats", return_value={"submitted_count": 0}),),
    ) as client:
        with client.session_transaction() as session:
            session["user_id"] = 1
        response = client.get("/profile/2/stats?tid=42")

    assert response.status_code == 200
    assert captured["username"] == "Другой"
    assert captured["public_profile_user_id"] == 2


def test_public_history_query_is_finished_only_and_scoped(app):
    user_cursor = Cursor(one_rows=[(2, "Другой")], all_rows=[[]])
    history_cursor = Cursor(all_rows=[[(8, datetime(2026, 8, 31, 15, tzinfo=timezone.utc), "ЦСКА", "Зенит", 2, 1, 1, 0, 5)]])
    captured = {}

    def render(_template, **context):
        captured.update(context)
        return "history"

    with patched_client(app, Mock(side_effect=[Connection(user_cursor), Connection(history_cursor)]), render) as client:
        with client.session_transaction() as session:
            session["user_id"] = 1
        response = client.get("/profile/2/predictions?tid=42")

    assert response.status_code == 200
    assert captured["is_public_history"] is True
    assert captured["finished"][0]["id"] == 8
    assert len(history_cursor.calls) == 1
    query, params = history_cursor.calls[0]
    assert "m.status = 'FINISHED'" in query
    assert "deadline" not in query.lower()
    assert params == (2, 42)


def test_public_routes_return_404_for_unknown_user(app):
    cursor = Cursor(one_rows=[None])

    with patched_client(app, lambda: Connection(cursor), lambda *_args, **_kwargs: "unexpected") as client:
        with client.session_transaction() as session:
            session["user_id"] = 1
        response = client.get("/profile/999?tid=42")

    assert response.status_code == 404


def test_own_public_url_redirects_to_canonical_private_route(app):
    with patched_client(app, Mock(), lambda *_args, **_kwargs: "unexpected") as client:
        with client.session_transaction() as session:
            session["user_id"] = 2
        response = client.get("/profile/2?tid=42")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/profile?tid=42")


def test_legacy_username_route_redirects_to_stable_public_user_id(app):
    cursor = Cursor(one_rows=[(2, "Другой", 0)])

    with patched_client(app, lambda: Connection(cursor), lambda *_args, **_kwargs: "unexpected") as client:
        with client.session_transaction() as session:
            session["user_id"] = 1
        response = client.get("/profile?username=%D0%94%D1%80%D1%83%D0%B3%D0%BE%D0%B9&tid=42")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/profile/2?tid=42")


def test_public_templates_keep_themes_and_hide_notification_controls():
    root = Path(__file__).resolve().parents[1]
    profile = (root / "templates" / "profile.html").read_text(encoding="utf-8")
    history = (root / "templates" / "my_predictions.html").read_text(encoding="utf-8")

    for theme in ("body.tournament-rpl", "body.tournament-rcup", "body.tournament-wc2026"):
        assert theme in profile or theme in history
    assert "data-web-push" in profile
    assert "{% if is_own_profile %}" in profile
    assert "is_public_history|default(false)" in history
