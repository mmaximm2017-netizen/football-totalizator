from pathlib import Path

import pytest

from app.services import db_migration_service as migrations


def test_discover_migrations_orders_versions_and_hashes_content(tmp_path):
    (tmp_path / "0002_second.sql").write_text("SELECT 2;\n", encoding="utf-8")
    (tmp_path / "0001_first.sql").write_text("SELECT 1;\n", encoding="utf-8")

    found = migrations.discover_migrations(tmp_path)

    assert [item.version for item in found] == [1, 2]
    assert [item.filename for item in found] == ["0001_first.sql", "0002_second.sql"]
    assert all(len(item.checksum) == 64 for item in found)


def test_discover_migrations_rejects_invalid_filename(tmp_path):
    (tmp_path / "migration.sql").write_text("SELECT 1;", encoding="utf-8")

    with pytest.raises(migrations.MigrationError, match="Invalid migration filename"):
        migrations.discover_migrations(tmp_path)


def test_discover_migrations_rejects_duplicate_version(tmp_path):
    (tmp_path / "0001_first.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "0001_second.sql").write_text("SELECT 2;", encoding="utf-8")

    with pytest.raises(migrations.MigrationError, match="Duplicate migration version"):
        migrations.discover_migrations(tmp_path)


def test_repository_starts_with_non_destructive_baseline():
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
    assert [item.version for item in found] == [1, 2, 3, 4, 5]


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
