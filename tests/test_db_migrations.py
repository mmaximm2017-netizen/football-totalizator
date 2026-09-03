from pathlib import Path

import pytest

from app.services import db_migration_service as migrations


def test_baseline_schema_migration_is_non_destructive():
    root = Path(__file__).resolve().parents[1]
    baseline = (root / "migrations" / "0001_baseline_current_schema.sql").read_text(
        encoding="utf-8"
    )

    normalized = baseline.upper()
    assert "SELECT 1;" in normalized
    assert "DROP TABLE" not in normalized
    assert "DELETE FROM" not in normalized
    assert "TRUNCATE" not in normalized


def test_repository_migrations_are_rollback_safe():
    found = migrations.discover_migrations()
    migrations.validate_rollback_safe_migrations(found)
    assert [item.version for item in found] == [1, 2, 3, 4]


@pytest.mark.parametrize(
    "statement",
    [
        "DROP TABLE users;",
        "ALTER TABLE matches DROP COLUMN status;",
        "ALTER TABLE matches RENAME COLUMN status TO old_status;",
        "ALTER TABLE matches ALTER COLUMN status TYPE VARCHAR(10);",
        "ALTER TABLE predictions ALTER COLUMN points SET NOT NULL;",
        "TRUNCATE predictions;",
        "DELETE FROM predictions;",
    ],
)
def test_rollback_safety_rejects_destructive_migrations(tmp_path, statement):
    path = tmp_path / "0002_unsafe.sql"
    path.write_text(statement + "\n", encoding="utf-8")
    found = migrations.discover_migrations(tmp_path)

    with pytest.raises(migrations.MigrationError, match="not rollback-safe"):
        migrations.validate_rollback_safe_migrations(found)


def test_rollback_safety_allows_additive_migration(tmp_path):
    path = tmp_path / "0002_add_index.sql"
    path.write_text(
        "ALTER TABLE matches ADD COLUMN IF NOT EXISTS note TEXT;\n"
        "CREATE INDEX IF NOT EXISTS idx_matches_note ON matches(note);\n",
        encoding="utf-8",
    )
    found = migrations.discover_migrations(tmp_path)
    migrations.validate_rollback_safe_migrations(found)
