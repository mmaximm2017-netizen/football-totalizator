# Pre-launch hardening, 27 October 2026

This change prepares application fixes and operational procedures. It does not
claim production settings, offsite delivery, a restore drill, billing caps or a
screen-reader review have been verified. No production data is changed by the PR.

## Application changes

- Analytics uses one lazy worker with a queue of 128 events per process. A full
  queue drops analytics instead of creating another thread or blocking a player.
- Admission limits run before expensive views, without opening PostgreSQL for
  anonymous analytics. Limits are per worker: login 20/IP/minute (120 total),
  registration 5/IP/minute (30 total), analytics 60/IP/minute (600 total),
  predictions 30/IP/minute (300 total), service API 60/IP/minute (300 total),
  admin writes 30/IP/minute (120 total). Existing failed-login and push cooldown
  guards remain. `deploy/nginx/request-limits.conf.example` prepares shared edge
  limits; inspect the actual virtual host before installing it.
- Match predictions publish only for a known deadline at or before server time.
- Migration 0010 adds hashed, revocable login tokens. Existing cookies require
  one fresh login after release. Logout deletes the token in PostgreSQL before
  clearing the browser session. All cookie variants for that login are revoked;
  other devices keep their independent sessions. Server expiry is seven days.
- Logs redact configured secrets, bearer tokens, Telegram tokens, email
  addresses, database URLs, SQL DETAIL lines and URL query strings. This is a
  defense against accidental leakage, not a guarantee that arbitrary prose
  contains no personal information. Client diagnostics remains opt-in.
- Push destinations are restricted to browser-vendor domains, HTTPS/443, public
  DNS answers and no redirects. User-controlled domains cannot select targets.
- Private responses have no-store/noindex. HTTPS responses have HSTS. Main-site
  CSP retains existing inline handlers/styles; removing unsafe-inline requires a
  separate template refactor. The stronger Mini App CSP is preserved.
- requirements.lock pins transitive dependencies with hashes. Docker and CI use
  --require-hashes, and CI audits that lock file.
- Native day/month buttons support Enter/Space and report expanded state.
  Registration/login inputs have programmatic labels and autocomplete values.

## Recovery and independent monitoring

On release, managed cron runs a daily database dump at 03:30 VPS time and a local
restore drill on Mondays at 04:15. Dump retention is 7 daily and 4 weekly copies.
The monitor alerts for a missing backup directory, dumps older than 36 hours,
missing checksums, missing/stale offsite receipts or restore evidence older than
8 days. Before migrations, deployment creates and validates a recovery dump.

Offsite copying requires an independent destination and a public age recipient.
The VPS needs age and rclone; rclone credentials stay in its private configuration.
The owner keeps the age private identity separately from the VPS and its backups.
Create a mode-0600 file at `$HOME/.local/state/totish/backup-settings.json`:

```json
{"age_recipient":"age1_REPLACE_WITH_REAL_PUBLIC_RECIPIENT","remote":"backup-remote:totish-private-backups"}
```

Use a dedicated authenticated remote, never a public bucket. `export_offsite_backup.py`
encrypts the database, .env, Compose, deploy files and runtime state before uploading.
It deletes only matching totish-full archives older than 35 days in that remote.
Tracked application/static files are reproducible from the exact Git release;
OCR screenshot uploads are temporary and are not persistent user assets.
New persistent upload directories must be added to the archive before rollout.

Run the backup and external-copy scripts, download the encrypted archive to an
isolated machine, decrypt it there with `age -d -i PRIVATE_IDENTITY`, and restore
database.dump into a disposable PostgreSQL 17 instance. Check user/match/prediction
counts, application login and the recovered runtime/configuration. Do not extract
over a live server or overwrite production for a drill. The checked-in local
restore script tests the local dump; it is not proof of an external full restore.

For the encrypted archive downloaded from the remote, run on an independent
verification machine with Python 3.12, age and Docker:

```bash
python3 scripts/verify_offsite_backup_restore.py downloaded-backup.tar.gz.age \
  --identity-file /private/backup-identity \
  --evidence /private/restore-evidence.json
```

The verifier decrypts into a private temporary directory, rejects traversal,
symlinks, unexpected paths and oversized payloads, checks database SHA-256 and
configuration-file presence, and restores PostgreSQL into a disposable container
with no network or host mounts. It never reads DATABASE_URL from the backup or
executes its files. Evidence is created only after success. This proves database
restore and configuration-file presence, not a complete restored application
login; the application recovery drill described above still needs actual access.

CI now tests real age encryption, an rclone local-backend copy, rejection of a
wrong key, and isolated PostgreSQL restoration. This validates the tools/procedure
on synthetic data; the local backend is not proof of a production offsite copy.

The independent GitHub Actions monitor checks HTTPS from outside the VPS every
15 minutes (GitHub scheduling can be delayed). It needs an `operations`
environment with TELEGRAM_ERROR_BOT_TOKEN and TELEGRAM_ERROR_CHAT_ID secrets.
It reports missing configuration as a failed run; it never reports undelivered
alerts as success. On a continuing outage it sends an alert per failed run.
Do not add an approval gate to this monitoring environment.

## User-data requests

Verify the requester's identity first. Inside the application container:

```bash
python scripts/user_data.py export USER_ID --output /tmp/user-data-USER_ID.json
python scripts/user_data.py anonymize USER_ID --confirm 'ANONYMIZE USER_ID'
```

Replace USER_ID with the same numeric ID in both positions. Export is private
(0600), excludes password hashes and push encryption keys, and refuses overwrite.
Transfer the file privately to the verified owner, then remove it. Anonymization
is transactional, refuses administrator targets, revokes sessions, deletes push
subscriptions/cooldowns/titles, replaces the nickname and password, and clears
last_seen. Anonymous match predictions and points remain for tournament integrity.
This is anonymization, not physical deletion of every historical row.

Delete the associated PostHog person/events using the recorded analytics identity
as a separate operator action; no PostHog deletion is claimed by the local command.
Encrypted offsite archives expire after 35 days; local dumps rotate over 7 days
and 4 weeks. Check Neon restore-history retention in the actual provider account.
After restoring an old backup, reapply verified deletion requests before reopening
access. Keep that restricted request register separately from the application
backup; retain only the identifiers and date needed for this reconciliation.

## Release gates still requiring actual access/configuration

Read-only VPS receipt (run from the reviewed PR checkout on the server):

```bash
python3 scripts/production_readiness.py --root /opt/football-totalizator --probe-db
```

The receipt contains booleans/statuses, never .env values, nginx config, SQL data
or credentials. It checks file permissions, nginx syntax, public HTTPS headers,
tool/configuration presence, recent backup/restore receipts and the reader role
using the running application's actual connection. A denied nginx read or missing
tool fails the check; it is not treated as success. `--probe-db` performs only
read-only queries. Without it, reader access remains explicitly not probed.

In this Work session there is no local SSH key, SSH credential environment,
remote-terminal connector or secret-management tool for GitHub environment
secrets. Existing PROD_SSH secrets are usable by deployment Actions, not readable
through the connected GitHub tools. Do not paste private keys into chat.
Therefore actual VPS setup, offsite credentials/recipient and independent
Telegram-secret configuration are not claimed completed by this PR.

1. Review nginx forwarding/HTTPS configuration and apply/test shared edge limits.
2. Configure the independent backup remote and encryption recipient; complete the
   external restore drill. A local success marker alone is insufficient.
3. Configure the operations Telegram secrets and verify both failure and delivery.
4. Run `python scripts/check_reader_permissions.py` using the actual GPT role.
   It checks privileges without changing them. Resolve excessive grants separately.
5. Check Neon and PostHog billing dashboards; set owner-approved limits/alerts.
   No provider spending limit is created or implied by request admission limits.
6. Review main-site mobile journey artifacts and do a real TalkBack pass on Android.

Automated accessibility coverage now includes WCAG 2.0/2.1 level A axe checks for
registration, login, predictions, result and the open tournament dialog at both
mobile sizes. Keyboard assertions cover dialog focus entry, Tab/Shift+Tab wrap,
Escape, focus restoration and day disclosures. Reports are CI artifacts. This is
not a claim of WCAG AA conformance or a substitute for actual Android TalkBack.

## Work log

- Verified current main 8cc865f and AGENTS.md; preserved scoring and auto-results.
- Applied ub-mode: wrote regression tests, observed failures, implemented fixes.
- Used a local source snapshot; did not edit or deploy production.
- Added bounded analytics/admission, revocable sessions, deadline publication,
  push provider restrictions, redaction, packaging exclusions and response headers.
- Added daily backups, weekly restore verification, pre-migration backup checks,
  encrypted offsite export and independent monitor configuration.
- Added owner-operated data export/anonymization and read-only grant verification.
- Added keyboard disclosures, form labels and a real mobile browser journey in CI.
- Generated hashed dependency lock and made it authoritative for CI/Docker.
- Focused application checks passed (187 tests and 10 subtests at that stage).
- Local dependency audit of requirements.lock: no known vulnerabilities found.
- Crapper, Dryer and Mutator were checked and are unavailable; their steps were
  skipped without installing companion tools.
- Final validation and CI results are recorded in the PR, including any remaining
  operational release gates. No external setup is represented as completed.
- Follow-up: kept all changes in PR 122, added the encrypted restore verifier,
  network-isolated recovery roundtrip, private readiness receipt, axe checks and
  keyboard-safe tournament dialog. Traversal/checksum tests were added before the restore implementation; focused
  checks subsequently passed.
