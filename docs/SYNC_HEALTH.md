# Sync Health

Sync health is a small JSON health layer over the existing `sync_runs` table. It does not run sync, schedule sync, or change the admin UI.

## Endpoint

`GET /admin/sync-health`

The endpoint is protected with the existing admin check and returns `get_sync_health()` as JSON.

## Response Fields

- `last_sync_id`: id of the newest `sync_runs` row.
- `last_status`: status of the newest run.
- `last_started_at`: newest run start time as an ISO timestamp.
- `last_finished_at`: newest run finish time as an ISO timestamp.
- `minutes_since_last_finished`: minutes since the newest run finished, or `null`.
- `errors_count`: error count stored on the newest run.
- `is_healthy`: boolean health result.
- `health_reason`: short machine-readable reason.

## Healthy Statuses

- `success`: healthy if finished within the freshness threshold.
- `partial_success`: healthy if finished within the freshness threshold. It still means errors were recorded, but sync and scoring returned.
- `skipped_already_running`: healthy only if a previous `success` or `partial_success` finished within the freshness threshold.

## Unhealthy Statuses

- `failed`
- `lock_error`
- `abandoned`
- no sync runs
- no previous successful or partially successful sync
- last successful or partially successful sync is too old

## Freshness Threshold

The threshold is 6 hours (`SYNC_HEALTH_MAX_AGE_HOURS = 6`). If the latest successful or partially successful sync is older than that, health becomes unhealthy even if the newest row is only a lock skip.
