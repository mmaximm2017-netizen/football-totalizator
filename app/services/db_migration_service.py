"""Versioned database migration runner for TOTISH."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from app.db import close_db, get_db

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
MIGRATION_RE = re.compile(r"^(?P<version>\d{4,})_(?P<name>[a-z0-9_]+)\.sql$")
MIGRATION_LOCK_KEY = 847_202_609


class MigrationError(RuntimeError):
    """Raised when migration history is inconsistent or a migration fails."""


@dataclass(frozen=True)
class Migration:
    version: int
    filename: str
    path: Path
    checksum: str


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def discover_migrations(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    migrations = []
    seen_versions = set()

    if not directory.exists():
        raise MigrationError(f"Migration directory does not exist: {directory}")

    for path in sorted(directory.glob("*.sql")):
        match = MIGRATION_RE.fullmatch(path.name)
        if not match:
            raise MigrationError(f"Invalid migration filename: {path.name}")

        version = int(match.group("version"))
        if version in seen_versions:
            raise MigrationError(f"Duplicate migration version: {version}")
        seen_versions.add(version)

        migrations.append(
            Migration(
                version=version,
                filename=path.name,
                path=path,
                checksum=_checksum(path),
            )
        )

    return sorted(migrations, key=lambda item: item.version)


def _ensure_history_table(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            filename TEXT NOT NULL UNIQUE,
            checksum CHAR(64) NOT NULL,
            applied_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
        )
        """
    )


def _load_history(cur) -> dict[int, tuple[str, str]]:
    cur.execute(
        """
        SELECT version, filename, checksum
        FROM schema_migrations
        ORDER BY version
        """
    )
    return {
        int(row[0]): (str(row[1]), str(row[2]))
        for row in cur.fetchall()
    }


def run_migrations(directory: Path = MIGRATIONS_DIR) -> dict:
    migrations = discover_migrations(directory)
    conn = get_db()
    cur = conn.cursor()
    applied = []

    try:
        cur.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
        _ensure_history_table(cur)
        conn.commit()

        history = _load_history(cur)

        for migration in migrations:
            previous = history.get(migration.version)
            if previous is not None:
                previous_filename, previous_checksum = previous
                if previous_filename != migration.filename:
                    raise MigrationError(
                        f"Migration {migration.version} filename changed: "
                        f"{previous_filename} -> {migration.filename}"
                    )
                if previous_checksum != migration.checksum:
                    raise MigrationError(
                        f"Migration {migration.version} checksum changed: "
                        f"{migration.filename}"
                    )
                continue

            sql = migration.path.read_text(encoding="utf-8")
            try:
                cur.execute(sql)
                cur.execute(
                    """
                    INSERT INTO schema_migrations (version, filename, checksum)
                    VALUES (%s, %s, %s)
                    """,
                    (migration.version, migration.filename, migration.checksum),
                )
                conn.commit()
            except Exception as exc:
                conn.rollback()
                raise MigrationError(
                    f"Migration {migration.filename} failed"
                ) from exc

            applied.append(migration.filename)
            history[migration.version] = (migration.filename, migration.checksum)

        return {
            "ok": True,
            "applied": applied,
            "applied_count": len(applied),
            "known_count": len(migrations),
        }
    finally:
        try:
            cur.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))
            conn.commit()
        except Exception:
            conn.rollback()
        close_db(conn, cur)
