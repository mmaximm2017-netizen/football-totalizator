from unittest.mock import MagicMock

import pytest

from app import create_app


@pytest.mark.parametrize("path", [
    "/service-worker.js", "/static/manifest.json", "/health",
])
def test_technical_routes_do_not_load_session_user_from_database(monkeypatch, path):
    app = create_app()
    app.config.update(TESTING=True)
    get_db = MagicMock()
    monkeypatch.setattr("app.get_db", get_db)

    with app.test_client() as client:
        with client.session_transaction() as session:
            session["user_id"] = 7
        response = client.get(path)

    assert response.status_code == 200
    get_db.assert_not_called()
    response.close()


@pytest.mark.parametrize("path", ["/profile", "/profile?username=other"])
def test_guest_profile_redirects_before_opening_database(monkeypatch, path):
    app = create_app()
    app.config.update(TESTING=True)
    get_db = MagicMock()
    monkeypatch.setattr("app.routes.profile.get_db", get_db)

    response = app.test_client().get(path)

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")
    get_db.assert_not_called()


def test_application_routes_still_validate_deleted_users(monkeypatch):
    app = create_app()
    app.config.update(TESTING=True)
    conn = MagicMock()
    conn.cursor.return_value.fetchone.return_value = (0, None, 1)
    get_db = MagicMock(return_value=conn)
    monkeypatch.setattr("app.get_db", get_db)
    monkeypatch.setattr("app.close_db", MagicMock())
    with app.test_client() as client:
        with client.session_transaction() as session:
            session["user_id"] = 7
        response = client.get("/")
        assert response.status_code == 302
        with client.session_transaction() as session:
            assert "user_id" not in session
    get_db.assert_called_once()
