from app import create_app


def test_public_database_health_details_are_hidden():
    app = create_app()
    app.config.update(TESTING=True)

    with app.test_client() as client:
        response = client.get("/health/db", environ_base={"REMOTE_ADDR": "8.8.8.8"})

    assert response.status_code == 404


def test_public_ipv6_database_health_details_are_hidden():
    app = create_app()
    app.config.update(TESTING=True)

    with app.test_client() as client:
        response = client.get(
            "/health/db",
            environ_base={"REMOTE_ADDR": "2001:4860:4860::8888"},
        )

    assert response.status_code == 404


def test_basic_health_endpoint_stays_public():
    app = create_app()
    app.config.update(TESTING=True)

    with app.test_client() as client:
        response = client.get("/health", environ_base={"REMOTE_ADDR": "8.8.8.8"})

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}
