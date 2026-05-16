# TOTISH BRATISHEK

## Sync matches and points

Use this command to run a one-off sync:

```bash
python scripts/sync_once.py
```

What this command does:
- Updates matches from external sources.
- Recalculates points after match updates.
- Does not run automatically on web app startup.
- Can be run manually or scheduled later with Render Cron.

Quick check after running:
- In command output/logs you should see `sync done`.
- Then open the main page and the table page to confirm matches and points are updated.
