# GitHub Actions Sync

GitHub Actions can run the existing sync worker as a simple autonomous scheduler. This does not change the sync pipeline and does not add a web endpoint, UI, Docker, Celery, Redis, or another worker framework.

## Workflow

The workflow lives at:

```text
.github/workflows/sync.yml
```

It runs:

```bash
python scripts/sync_once.py
```

Schedule:

- every 30 minutes via GitHub Actions cron;
- manual trigger via `workflow_dispatch`.

GitHub cron schedules use UTC.

## Required Secrets

Add these repository secrets in GitHub:

- `DATABASE_URL`
- `FOOTBALL_DATA_API_KEY`
- `SECRET_KEY`
- `INVITE_CODE`
- `ADMIN_USERNAME`
- `ADMIN_PASSWORD`

Optional:


The application expects `API_KEY`, so the workflow maps `FOOTBALL_DATA_API_KEY` into the `API_KEY` environment variable. Secrets are referenced by name only and are not stored in the workflow file.

## Manual Run

In GitHub:

1. Open the repository.
2. Go to `Actions`.
3. Select `Sync Matches`.
4. Click `Run workflow`.

## Logs And Output

The worker writes operational logs to stderr and prints one final JSON object to stdout. GitHub Actions shows both in the job logs.

Example stdout:

```json
{"errors_count":0,"matches_finished":0,"matches_updated":0,"predictions_recalculated":0,"status":"success","sync_run_id":123}
```

## Exit Codes

The workflow succeeds when `scripts/sync_once.py` exits `0`:

- `success`
- `partial_success`
- `skipped_already_running`

The workflow fails when the script exits `1`:

- `failed`
- `lock_error`
- unexpected status
- unhandled worker exception

`skipped_already_running` is normal because the database advisory lock already protects against overlapping sync runs.

## Health

Admins can inspect the latest sync state through:

```text
GET /admin/sync-health
```

The endpoint reads `sync_runs` and reports whether the latest sync state is healthy.

## Notes

- Do not point Actions at an admin route.
- Do not run sync in Flask startup.
- Keep only one scheduler active for this command.
