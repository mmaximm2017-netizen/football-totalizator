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

The morning digest is restored for October at 05:00 UTC (08:00 Moscow) daily.
Its wrapper retains the lock and per-Moscow-date state to prevent duplicate
delivery. It uses the existing Telegram outbox and host relay.

Result pushes, deadline pushes and scheduled restore verification remain absent
from the managed cron. Backups run on day-of-month `*/4`; see
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

## Telegram process activation

The live bot is a **system** unit, `/etc/systemd/system/totish-telegram-admin.service`,
currently running as root. The checked-in unit now describes that runtime; do not
use `systemctl --user` for this installation. The service template is delivered
as a managed file but is not silently installed into `/etc` by the deploy user.

One root bootstrap is required before the first deployment of this change.
Use the reviewed new checkout and the actual username used by the deployment:

```bash
python3 scripts/install_telegram_admin_service.py DEPLOY_USERNAME
```

This validates the username and sudoers rule, installs the system-unit template,
and grants only this command without a password:
`/usr/bin/systemctl restart totish-telegram-admin.service`.
It neither grants general sudo nor restarts the bot. Installation and activation
are production changes and belong to an explicitly authorized release.

The deployment preflight checks restart permission before replacing the
application or host files. Missing permission fails the deployment visibly.
After synchronization, `activate_telegram_admin.py` restarts the unit only when
its running PID/source hashes cannot be verified. It verifies ActiveState,
MainPID and hashes for all five loaded host modules against the managed files.
The root bot publishes those hashes, without secrets, to
`runtime/telegram-admin-process.json`. A release marker on disk alone is not
accepted as proof of the running bot version. Activation failure triggers
restoration of host files, static assets, crontab, image and the old bot process.

No root bootstrap, restart or production database migration was performed while
preparing this PR. The additive migration 0008 exposes only aggregate finished
rankings and grants the reader SELECT; it changes no forecasts or points.
