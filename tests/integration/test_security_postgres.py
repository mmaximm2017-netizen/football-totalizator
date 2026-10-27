"""Actual cookies, session revocation and data operations on disposable PostgreSQL."""
import os
from pathlib import Path
import uuid

import psycopg2
from psycopg2.pool import ThreadedConnectionPool
import pytest

from app import create_app, db
from scripts.user_data import anonymize_user, export_user

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def security_app(monkeypatch):
    dsn = os.getenv("AUTO_RESULTS_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Explicit disposable PostgreSQL DSN required; enabled in CI")
    schema = "security_test_" + uuid.uuid4().hex
    admin = psycopg2.connect(dsn)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE SCHEMA "{schema}"')
    pool = ThreadedConnectionPool(1, 3, dsn=dsn, options=f"-csearch_path={schema}")
    monkeypatch.setattr(db, "db_pool", pool)
    try:
        conn = db.get_db()
        with conn.cursor() as cur:
            for path in sorted((ROOT / "migrations").glob("*.sql")):
                cur.execute(path.read_text())
            # Match the optional push tables used by the existing production bootstrap.
            from scripts.migrate_push_subscriptions import DDL
            for statement in DDL:
                cur.execute(statement)
            from scripts.migrate_push_test_cooldown import DDL as COOLDOWN_DDL
            cur.execute(COOLDOWN_DDL)
        conn.commit()
        db.close_db(conn)
        app = create_app()
        app.config["TESTING"] = True
        yield app
    finally:
        pool.closeall()
        with admin.cursor() as cur:
            cur.execute(f'DROP SCHEMA "{schema}" CASCADE')
        admin.close()


def csrf(client):
    client.get("/login")
    with client.session_transaction() as state:
        return state["csrf_token"]


def registered_client(app):
    from app.config import INVITE_CODE
    client = app.test_client()
    result = client.post("/register", data={"username": "journey", "password": "test-password", "invite_code": INVITE_CODE, "csrf_token": csrf(client)})
    assert result.status_code == 302
    result = client.post("/login", data={"username": "journey", "password": "test-password", "csrf_token": csrf(client)})
    assert result.status_code == 303
    return client


def test_logout_revokes_earlier_cookie_even_after_cookie_refresh(security_app):
    client = registered_client(security_app)
    stolen = client.get_cookie("session").value
    assert client.get("/profile").status_code == 200
    assert client.post("/logout", data={"csrf_token": csrf(client)}).status_code == 302
    replay = security_app.test_client()
    replay.set_cookie("session", stolen)
    result = replay.get("/profile")
    assert result.status_code == 302
    assert "/login" in result.headers["Location"]


def test_export_and_anonymization_preserve_points_but_remove_access(security_app):
    client = registered_client(security_app)
    with client.session_transaction() as state:
        uid = state["user_id"]
    conn = db.get_db()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO users(username,password) VALUES('someone-else','private')")
        cur.execute("INSERT INTO tournaments(id,name,is_active) VALUES(5,'RPL',1)")
        cur.execute("INSERT INTO matches(id,tournament_id,home_team,away_team,status,home_score,away_score) VALUES(10,5,'Home','Away','FINISHED',2,1)")
        cur.execute("INSERT INTO predictions(user_id,match_id,tournament_id,home_goals,away_goals,points) VALUES(%s,10,5,2,1,10)", (uid,))
        cur.execute("INSERT INTO push_subscriptions(user_id,endpoint,p256dh,auth) VALUES(%s,'https://fcm.googleapis.com/private-endpoint','PRIVATE-KEY','PRIVATE-AUTH')", (uid,))
        data = export_user(cur, uid)
        assert data["user"]["username"] == "journey"
        assert "password" not in data["user"]
        assert "someone-else" not in str(data)
        assert "PRIVATE-KEY" not in str(data) and "PRIVATE-AUTH" not in str(data)
        assert data["predictions"][0]["points"] == 10
        anonymize_user(cur, uid)
        conn.commit()
        cur.execute("SELECT username, is_deleted, last_seen FROM users WHERE id = %s", (uid,))
        row = cur.fetchone()
        assert row[0] != "journey" and row[1] == 1 and row[2] is None
        cur.execute("SELECT COUNT(*) FROM auth_sessions WHERE user_id = %s", (uid,))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT points FROM predictions WHERE user_id=%s", (uid,))
        assert cur.fetchone()[0] == 10
        cur.execute("SELECT COUNT(*) FROM push_subscriptions WHERE user_id=%s", (uid,))
        assert cur.fetchone()[0] == 0
    finally:
        db.close_db(conn, cur)
    assert client.get("/profile").status_code == 302
