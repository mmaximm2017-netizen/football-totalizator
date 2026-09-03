# Versioned database migrations

TOTISH database schema changes live in the top-level `migrations/` directory.

## File naming

Use sequential files:

```text
0001_baseline_current_schema.sql
0002_add_example_column.sql
0003_add_example_index.sql
```

Never edit a migration after it has been applied in production. The runner stores
a SHA-256 checksum and stops if an applied file changes.

## Running migrations

```bash
python scripts/run_migrations.py
```

The runner:

- takes a PostgreSQL advisory lock so two deploys cannot migrate at the same time;
- creates the `schema_migrations` history table if needed;
- skips migrations already recorded with the same checksum;
- applies each pending migration in order;
- records version, filename, checksum and application time;
- stops on the first failure.

`0001_baseline_current_schema.sql` is intentionally non-destructive. It marks
the existing production schema as the starting point for versioned history.

Automatic execution during production deployment is deliberately handled as a
separate deployment-safety change.
