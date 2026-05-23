# Sync History

Sync runs are recorded in the `sync_runs` table. Recording is best-effort: if history insert or update fails, the sync keeps running and the failure is logged as a warning.

## Code

- `app/services/sync_history_service.py::create_sync_run()` creates a started history row.
- `app/services/sync_history_service.py::finish_sync_run()` finalizes the row with counters, status, finish time, and JSON summary.
- `app/services/sync_history_service.py::get_last_sync()` returns the most recent history row.
- `app/services/sync_history_service.py::recover_stale_syncs()` marks old unfinished started rows as abandoned before a new sync starts.
- `app/services/match_service.py::run_sync_with_lock()` owns history recording for manual and worker sync paths.

## Final statuses

- `success`: sync and scoring finished without recorded errors.
- `partial_success`: sync and scoring returned, but the summary contains recorded errors.
- `skipped_already_running`: advisory lock was held by another sync.
- `lock_error`: advisory lock could not be checked or acquired safely.
- `failed`: sync raised an exception.
- `abandoned`: a previous `started` row was still unfinished after the stale timeout and was closed by recovery.

## Stored fields

The table stores start and finish timestamps, final status, match counters, recalculated prediction count, error count, and `summary_json` with the orchestration summary.

## Stale Recovery

A stale sync is a history row that stayed in `status = 'started'` for more than 30 minutes. This can happen if the process exits between `create_sync_run()` and `finish_sync_run()`.

Before a new sync creates its own history row, `run_sync_with_lock()` calls `recover_stale_syncs()`. Recovery finds old `started` rows, sets `status = 'abandoned'`, writes `finished_at`, stores a reason in `summary_json`, and logs how many rows were recovered plus their ids.

Recovery is also best-effort. If it fails, the failure is logged and the new sync continues.
