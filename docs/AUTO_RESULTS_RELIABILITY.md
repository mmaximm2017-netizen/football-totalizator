# Automatic results: reliability contract

Rechecked against main `5381be4287a0f60a3a19a77449b6e5b64049756e` (PR #59).
All nine audit items were present. Source URLs, scoring and game rules are unchanged.

| Audit item | Root cause | Change |
| --- | --- | --- |
| 1 | `readonly=True` survived return to the shared psycopg2 pool | Roll back and restore read/write, non-autocommit state before pool return; discard on cleanup failure. Separate GPT reader is unchanged. |
| 2 | Host scripts overrode image scripts during a multi-step deploy | Package the three worker scripts in the immutable image. No scripts bind mount. Shared existing deploy lock spans verification and runtime; deployment holds it exclusively across the complete image/control-plane/rollback operation. Verify digest, image ID, health and managed release. |
| 3 | Time was sampled before external I/O | Recheck wall time before finalization, then PostgreSQL `clock_timestamp()` after the row lock and before commit, including after scoring and durable notification insertion. Roll back if outside 120–180 minutes. |
| 4 | Notification I/O shared the save error handler | Store the success notice in the same DB transaction as result/points/user-push rows. Publish separately through an atomic file rename; pending rows survive interruption. |
| 5 | SQL excluded the empty category | Normalize NULL and empty string to RPL in selection, retaining national-team routing and exclusion of other categories. |
| 6 | HTTP success was treated as parser recovery | Only validated parsing can mark a source healthy; parsing exceptions mark it unhealthy. |
| 7 | JSON contained permanent save-failure blocks | Remove JSON write authority. Retry with fresh source consensus and row-locked identity/manual-result guards within the original window. No automatic writes after +180. |
| 8 | Monitor did not observe result-worker progress | Existing production monitor independently invokes `--monitor`. Alert once per match identity after 12 minutes without a recorded check while work is due; recover expired notices for seven days, excluding pre-enable backlog. |
| 9 / Q42 | Final notice discarded source evidence | Store the latest observed reason in PostgreSQL, bound to match identity. Explain not-found, not-finished, conflict or technical failure. Say that the cause is unknown if evidence is absent. |

## Authority and concurrency

- The match row, not JSON or a notification, determines eligibility. Existing scores
  (including a partial score) or disallowed status prevent automatic modification.
- The finalizer rechecks teams, kickoff, tournament, league and category under
  `FOR UPDATE`. A manual result committed while the worker waits wins.
- A transaction-scoped PostgreSQL advisory lock serializes live runtime invocations
  even when a caller bypasses shell flock; it works with transaction pooling.
- Retry is bounded by the 60-minute eligibility interval and the five-minute cron.
  A lost commit acknowledgement is reported as unknown, not as failed storage.
  The next selection excludes a result whose commit actually succeeded.
- The existing user result-push outbox remains in the scoring transaction. The
  new `auto_result_notifications` table is only for operational Telegram notices.
- A final notice checks that the match is still pending and has the same identity
  under a row lock before asking for manual intervention.
- Deploy waits up to 150 seconds for the existing lock. Its nested deploy script
  reuses inherited fd 9, avoiding self-deadlock. Worker acquisition is nonblocking.
  On first rollout, deploy also drains the existing managed cron worker lock,
  because the previous wrapper does not yet participate in the deploy lock.

## Deployment and operation

Migration 0005 is additive and installed by the existing migration step. It adds
checks, operational notices and a monitor enable boundary, without changing
match/scoring tables. Existing PR #59 protections and the admin auto marker remain.

`AUTO_RESULTS_ENABLED` and `AUTO_RESULTS_DRY_RUN` continue to come from production
`.env` through Compose. The monitor does not fetch sources or write match results.
The live runner and monitor publish pending operational notices into the existing
Telegram relay directory. Diagnostic tables may be updated even when live writes
are disabled; dry-run never changes scores/statuses/points.

On a technical save failure, the next scheduled run may retry within the window.
If the window ends unresolved, a durable notice requests manual intervention with
known evidence. Deleting/corrupting the diagnostic JSON cannot permit overwrites,
remove a DB result, lose a committed success notice or impose a permanent block.

After deployment, verify image/managed-release agreement, worker and independent
monitor runs, enabled/live flags, and a controlled successful finalization. A
successful web health check alone does not prove automatic results are operating.

## Tests and limits

Real PostgreSQL tests use only the explicit `AUTO_RESULTS_TEST_DATABASE_URL`, with
an isolated schema per test. CI points this at its existing disposable PostgreSQL
service. Tests cover pooled readonly-to-write reuse, manual write contention,
rollback at the time boundary, committed result/points/push/operational notice,
notification interruption, retry, identity mutation, duplicate-worker exclusion,
category selection, monitor catch-up and identity-bound final reasons. Shell tests
exercise real flock with a substituted Docker boundary.

Residual operational limits:

- Two agreeing finished sources inside the window are mandatory; unavailable,
  delayed or disagreeing sources can still require manual results.
- Telegram file publication is at-least-once. An interruption after publication
  before its DB acknowledgement may duplicate a notice, never a match write.
- The monitor shares the VPS/cron infrastructure. A complete VPS/cron outage cannot
  report itself in real time; catch-up reports pending matches on recovery within
  seven days. A separate external uptime monitor would be needed for that failure.
- Health-transition deduplication remains diagnostic JSON; losing it can duplicate
  an outage notice, but cannot authorize a result.
- Strict timing is checked immediately before commit; no application can guarantee
  the physical commit acknowledgement arrives before the boundary during a network
  stall. No retry or lookup widens the configured window.
