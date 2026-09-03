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
