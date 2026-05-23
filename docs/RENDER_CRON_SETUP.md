# Render Cron Setup

This project is ready to run sync through a Render Cron Job, but cron is not configured in the repository.

## Command

Use the existing worker entrypoint:

```bash
python scripts/sync_once.py
```

The script creates the Flask app, enters an app context, and calls `run_sync_with_lock(strict_lock=True)`. It does not depend on a request context, admin route, browser session, or UI.

## Required Environment

- `DATABASE_URL`
- `SECRET_KEY`
- `API_KEY`
- `LEAGUE_IDS`

Optional:

- `RPL_SEASON`

The deployed service also needs the normal Python dependencies from `requirements.txt`.

## Output

The script writes operational logs to stderr and prints one final machine-readable JSON object to stdout:

```json
{
  "status": "success",
  "sync_run_id": 123,
  "matches_updated": 0,
  "matches_finished": 0,
  "predictions_recalculated": 0,
  "errors_count": 0
}
```

## Exit Codes

Exit `0`:

- `success`
- `partial_success`
- `skipped_already_running`

Exit `1`:

- `failed`
- `lock_error`
- unexpected status
- unhandled worker exception

`skipped_already_running` is treated as a normal no-op because another sync already holds the advisory lock.

## Sync Health

Admins can inspect sync health through:

```text
GET /admin/sync-health
```

The health endpoint uses the `sync_runs` table. It marks recent `success` and `partial_success` runs healthy, treats recent lock skips as healthy only when there is a recent successful run, and reports `failed`, `lock_error`, and `abandoned` as unhealthy.

## Notes

- Do not add sync to Flask startup.
- Do not point cron at an admin web endpoint.
- Use only one external scheduler for this command.
