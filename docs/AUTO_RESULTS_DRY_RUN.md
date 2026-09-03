# Automatic match results — dry-run phase

This phase is intentionally read-only.

## Safety

- `AUTO_RESULTS_ENABLED=false` disables the worker.
- `AUTO_RESULTS_DRY_RUN=true` is the only supported mode in this phase.
- If `AUTO_RESULTS_DRY_RUN=false`, the worker exits with `live_write_not_implemented`.
- The worker opens its PostgreSQL session with `readonly=True` and performs only `SELECT` queries.
- No result/status/points write path exists in this phase.

## Scope

Only:
- RPL matches (`tournament_id=5`, `league=rpl`, category `rpl`);
- Russia national-team matches stored in tournament 5 (`category=national_team`);
- Russian Cup (`tournament_id=6`, `league=rcup`).

## Timing

A normal cron run is eligible from 120 through 180 minutes after the kickoff stored in TOTISH. One 5-minute grace run exists only to emit the final `window_expired` event; it never writes a result.

## Sources validated from the production VPS

- RPL: LiveSport + Sports.ru.
- Russian Cup: LiveSport + official RFS.
- Russia national team: Sportbox + official RFS.

A result is considered ready only when both independent sources explicitly report the match as finished and the regulation score matches exactly.

## Russian Cup penalties

Penalty-shootout scores are never used as the TOTISH result. The worker keeps the 90-minute score only.

## Scheduler

Suggested dry-run cron entry after merge/deploy:

```cron
*/5 * * * * /opt/football-totalizator/scripts/run_auto_results.sh
```

Keep `AUTO_RESULTS_ENABLED=false` until the first controlled production dry-run is intentionally enabled.
