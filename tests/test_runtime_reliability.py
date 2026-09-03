from pathlib import Path

from app import db
from app.services import login_rate_limit_service as login_guard


ROOT = Path(__file__).resolve().parents[1]


def test_gunicorn_runs_two_workers_with_same_total_thread_concurrency():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert '"--workers", "2"' in dockerfile
    assert '"--threads", "2"' in dockerfile
    assert '"--workers", "1"' not in dockerfile


def test_primary_pool_is_bounded_per_worker():
    source = (ROOT / "app" / "db.py").read_text(encoding="utf-8")

    assert "maxconn=3" in source


def test_shared_login_guard_uses_database_table_and_hashed_key():
    source = (ROOT / "app" / "services" / "login_rate_limit_service.py").read_text(
        encoding="utf-8"
    )

    assert "login_rate_limits" in source
    assert "pg_advisory_xact_lock" in source
    assert "hashlib.sha256" in source
    assert not hasattr(login_guard, "_failures")
    assert not hasattr(login_guard, "_blocked_until")


def test_login_rate_limit_migration_is_additive():
    migration = (ROOT / "migrations" / "0002_add_login_rate_limits.sql").read_text(
        encoding="utf-8"
    ).upper()

    assert "CREATE TABLE IF NOT EXISTS LOGIN_RATE_LIMITS" in migration
    assert "DROP TABLE" not in migration
    assert "TRUNCATE" not in migration
