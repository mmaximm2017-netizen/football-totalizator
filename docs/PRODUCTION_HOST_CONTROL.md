# Production host control

GitHub is the source of truth for the production control files listed in
`deploy/production-managed-files.txt`.

After a successful application deployment, the deployment workflow installs
those files from the exact Git commit embedded in the Docker image.

Managed files include:

- `docker-compose.yml`;
- production deployment and scheduled-worker scripts;
- Telegram relay and production monitor scripts;
- the TOTISH cron schedule.

The workflow does not replace `.env`, runtime state, logs, or unrelated cron
entries.

The managed cron block is installed between:

```text
# BEGIN TOTISH MANAGED
# END TOTISH MANAGED
```

On the first managed deployment, legacy TOTISH cron lines are removed before the
managed block is added. Unrelated user cron entries are preserved.

The file `.totish-managed-release` records which Git commit supplied the
production host files. `scripts/monitor_production.py` compares this marker
with the `TOTISH_RELEASE` embedded in the running Docker image and alerts when
they differ.

Do not edit managed production files directly on the VPS. Make the change in
GitHub and deploy it.

## Emergency CU policy through September 2026

Result pushes, deadline pushes, morning digest and scheduled restore verification
are absent from the managed cron. Backups run on day-of-month `*/4`; see
`BACKUP_RECOVERY.md` for freshness and weekly retention.

The light monitor and Telegram relay remain host-local. Every hour, monitor-due
checks local gate/recovery files before allowing the DB-aware monitor. Every
five minutes, the gate refresh wrapper and automatic-result wrapper likewise
make a local decision. No deadline window or push backlog participates in the
plan. Known matches trigger next_due at kickoff +120 minutes; the active plan
also covers the worker's terminal-notice lookback through +375 minutes.

An idle watchdog refreshes the schedule at most every two hours to discover
new/rescheduled matches. This is intentional bounded DB work, not zero idle
usage. It replaces hourly schedule refreshes. Active plans refresh every 15
minutes; missing/expired plans fail open. Unresolved DB/auto-result incidents
allow hourly recovery probes even while idle. A new actionable match missed by
the last idle plan has at most two hours plus a five-minute cron tick before
discovery, then less than one hour until an independent monitor probe.

Re-enable paused jobs through an explicit reviewed cron change after October 1.
There is no automatic re-enable or production .env rewrite in this release.
Keep the match-result service's `EMERGENCY_RESUME_AT` floor at
`2026-10-01T00:00:00Z` (03:00 Moscow) when restoring the worker. It excludes
pre-October outbox events at selection and atomic claim even when the deployed
`TOTISH_MATCH_RESULT_PUSH_SINCE` is old; a later configured SINCE still wins.
Events and scoring are retained unchanged. Do not revert this safety floor
with the cron policy. New October events remain eligible for normal delivery.
