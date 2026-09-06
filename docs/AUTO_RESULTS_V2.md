# Auto-results v2

Baseline: `8d56135f3a33af7a4840693535807c71bd1a121b` (PR #64).

## Incident and evidence

The operator reported that on 2026-09-05 Krylya Sovetov–Krasnodar (14:00 MSK,
2:2) had a LiveSport confirmation but Sports.ru alternated between fetch/parser
failure and not_found. Production logs were not available to this implementation.
The baseline code proves that either condition prevented the mandatory two-source
consensus, and the +180 cutoff stopped retries permanently. The supplied timeline
is reproduced deterministically in `test_today_incident_retries_after_soft_deadline_then_records_quorum`.
The actual reason for the two historical monitor_failed alerts cannot be recovered
from their generic text. The monitor does not fetch football sources.

## Sources and decision

- RPL: LiveSport, Sports.ru, Sportbox (MatchTV ecosystem): 2 of 3.
- Cup: LiveSport + RFS: 2 of 2; unchanged 90-minute score, no guessed extra-time score.
- National: Sportbox + RFS: 2 of 2.

Each adapter casts at most one vote. Only explicit finished scores count.
A unique score with two confirmations is sufficient. Two different scores without
such a majority cannot write. A 2–1 majority can write, but all three observations
and the dissent are recorded in PostgreSQL. Absence, network failure and parser
failure are not score votes.

The new Sportbox adapter uses the stable RPL calendar, exact predefined aliases,
home/away order, full calendar date and a second full-date/team/tournament check in
JSON. `live=0`, an actual terminal timeline class and matching header/JSON scores
are all required. Ambiguity or structure changes yield parser_error. Fixtures
contain captured public responses and provenance, not synthetic claims of uptime.
Two distinct publishers agreeing can still share an erroneous upstream feed;
independence of their underlying data suppliers cannot be guaranteed.

Sportbox RPL's shared `sportbox_rpl` cache/health key describes the calendar
fetch/parser only. Each game's JSON is fetched and parsed afresh. Detail failures
are isolated observations (`source_unavailable` / `parser_error`, including game ID
in their detail), never shared calendar errors. The runtime records the affected
match's diagnostic; a healthy calendar does not claim every game detail is healthy.
Source health transitions remain recorded in diagnostic state but are intentionally
silent in live Telegram notifications.

## Timing and Telegram policy

All supported scopes start at +120 minutes. At +180, if the result still has not
been written automatically, one concise pending-result warning is persisted once
per match identity. Retries continue every five minutes. +360 is the hard deadline,
inclusive. Beyond it there is no automatic write or source retry.

Live match-result Telegram notifications are intentionally limited to two user-facing
states:

1. success: the finalizer reports that the result was added automatically and points
   were calculated;
2. +180 pending: automatic entry has not succeeded yet and retries continue to +360.

Intermediate source outage/recovery, one-source confirmation, parser/not_found
states, score-conflict diagnostics, stalled-check notices and hard-deadline match
notices stay in logs/PostgreSQL diagnostic state and are not sent as live Telegram
match-progress messages. General production-monitor incidents (database/site/
container/worker technical failures) remain separate operational alerts.

The independent monitor can catch a missed +180 notice after recovery within its
existing seven-day lookback. No cron or monitoring can alert while the entire VPS
is down.

Six hours provides 36 additional five-minute retry opportunities beyond the old
window, without indefinite polling. It also accommodates delayed availability for
Cup/national games without changing their score semantics. Rescheduling/identity
changes still invalidate old observations. PostgreSQL wall time is checked after
the row lock, after scoring and after notification insertion before commit.

## Transactions and operation

The finalizer only imports the shared timing constants; row locks, identity/manual
protection, match→predictions order, scoring, result push dedupe and atomic commit
are unchanged. JSON stores diagnostic health only. Durable checks survive its loss.
All votes are reobtained on retry; no cached votes authorize writes.

Monitor failures retain exit code, bounded beginning/end of stdout and stderr,
timeout/shell/DB/unknown classification, and an explicit unknown-result warning.
URL/DSN, credential/environment lines and configured sensitive values are redacted
before log/Telegram publication. The original five-minute alert dedupe remains.
The wrapper exposes monitor child streams to the caller and retains worker logging;
it does not send a second generic monitor-failure Telegram. Deployment flock,
immutable image checks and worker locking are unchanged.

## Validation

Run the complete existing CI, including its disposable PostgreSQL 17 service,
scoring/concurrency regressions from #63/#64, pip-audit, blocking Ruff and Docker.
Image verification imports both runtime and finalizer, including the shared policy.
No production database access or deployment is needed. Tests explicitly prove that
there are no live match-progress Telegram messages before +180, the +180 notice is
sent once, retries continue afterward, and the hard deadline still blocks writes.
