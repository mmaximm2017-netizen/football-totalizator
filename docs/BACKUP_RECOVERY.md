# Database backup and recovery

TOTISH uses two independent recovery layers:

1. Neon point-in-time history for recent mistakes.
2. PostgreSQL custom-format dumps stored on the production VPS outside Neon.

## Automatic backup

The managed VPS cron runs:

- daily backup at 03:30 Moscow time;
- restore verification every Monday at 04:15.

Backups are written to:

```text
$HOME/.local/state/totish/backups
```

The directory is mode 0700. Dump and checksum files are mode 0600.

Retention:

- 7 daily dumps;
- 4 weekly dumps.

The dump is created with PostgreSQL 17 `pg_dump` using the official
`postgres:17` image, so the client major version matches production.

Each backup is accepted only after:

- the dump is non-empty;
- `pg_restore --list` can read it;
- the dump contains table data for `users`, `matches`, `predictions`,
  `tournaments`, and `schema_migrations`;
- a SHA-256 checksum is created.

The production monitor alerts if an initialized backup directory has no daily
dump, the newest dump is older than 36 hours, or its checksum file is missing.

## Restore verification

`scripts/verify_database_backup_restore.sh` never writes to production.

It starts a temporary local PostgreSQL 17 container, verifies the SHA-256
checksum, restores the newest daily dump into a temporary database, and checks
that the core tables and migration history can be queried. The temporary
container is removed after the check.

Manual verification:

```bash
cd /opt/football-totalizator
bash scripts/verify_database_backup_restore.sh
```

Manual backup:

```bash
cd /opt/football-totalizator
bash scripts/run_database_backup.sh
```

## Recovery rule

For a recent accidental change, prefer Neon point-in-time recovery.

For an older incident or a Neon-side failure, restore one of the VPS dumps into
a new PostgreSQL/Neon database first. Never overwrite the current production
database until the restored copy has been inspected.

Database dumps contain private application data. They must never be committed
to Git, copied to public storage, or included in support logs.
