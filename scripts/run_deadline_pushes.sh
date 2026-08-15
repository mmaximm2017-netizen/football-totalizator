#!/usr/bin/env bash
set -Eeuo pipefail

# Production cron entry (install manually, do not run from this script):
# SHELL=/bin/bash
# PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
# */5 * * * * /opt/football-totalizator/scripts/run_deadline_pushes.sh

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${TOTISH_PROJECT_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
LOG_FILE="${TOTISH_DEADLINE_PUSH_LOG:-/var/log/totish-deadline-push.log}"
LOCK_FILE="${TOTISH_DEADLINE_PUSH_LOCK:-/tmp/totish-deadline-push.lock}"
TIMEOUT_SECONDS="${TOTISH_DEADLINE_PUSH_TIMEOUT:-180}"
MAX_LOG_BYTES=10485760

usage() {
    echo "Usage: $0 [--dry-run]" >&2
}

if [[ "$#" -gt 1 || ( "$#" -eq 1 && "$1" != "--dry-run" ) ]]; then
    usage
    exit 2
fi

if ! command -v docker >/dev/null 2>&1; then
    echo "docker is required" >&2
    exit 1
fi
if ! command -v flock >/dev/null 2>&1; then
    echo "flock is required" >&2
    exit 1
fi
if ! command -v timeout >/dev/null 2>&1; then
    echo "timeout is required" >&2
    exit 1
fi

DOCKER_BIN="$(command -v docker)"
FLOCK_BIN="$(command -v flock)"
TIMEOUT_BIN="$(command -v timeout)"

if ! "$DOCKER_BIN" compose version >/dev/null 2>&1; then
    echo "docker compose is required" >&2
    exit 1
fi

if [[ ! -d "$PROJECT_ROOT" ]]; then
    echo "Project root does not exist: $PROJECT_ROOT" >&2
    exit 1
fi

mkdir -p "$(dirname "$LOG_FILE")"
touch "$LOG_FILE"
chmod 600 "$LOG_FILE" 2>/dev/null || true

exec 9>"$LOCK_FILE"
if ! "$FLOCK_BIN" -n 9; then
    printf '%s SKIP overlapping run\n' "$(date -Is)" >>"$LOG_FILE"
    exit 0
fi

# Keep the fallback file bounded when host logrotate is not configured yet.
if [[ "$(stat -c %s "$LOG_FILE" 2>/dev/null || echo 0)" -gt "$MAX_LOG_BYTES" ]]; then
    tail -c 5242880 "$LOG_FILE" >"${LOG_FILE}.tmp.$$"
    mv "${LOG_FILE}.tmp.$$" "$LOG_FILE"
    chmod 600 "$LOG_FILE" 2>/dev/null || true
fi

cd "$PROJECT_ROOT"

STARTED_AT="$(date -Is)"
printf '%s START deadline worker%s\n' "$STARTED_AT" "${1:+ mode=$1}" >>"$LOG_FILE"

WORKER_ARGS=()
if [[ "${1:-}" == "--dry-run" ]]; then
    WORKER_ARGS+=("--dry-run")
fi

set +e
"$TIMEOUT_BIN" --signal=TERM --kill-after=10s "${TIMEOUT_SECONDS}s" \
    "$DOCKER_BIN" compose run --rm \
        -v "$PROJECT_ROOT/scripts:/app/scripts:ro" \
        app python scripts/send_deadline_pushes.py "${WORKER_ARGS[@]}" \
    >>"$LOG_FILE" 2>&1
WORKER_STATUS=$?
set -e

FINISHED_AT="$(date -Is)"
printf '%s FINISH deadline worker exit_code=%s\n' "$FINISHED_AT" "$WORKER_STATUS" >>"$LOG_FILE"

if [[ "$WORKER_STATUS" -eq 124 || "$WORKER_STATUS" -eq 137 ]]; then
    printf '%s TIMEOUT deadline worker limit=%ss\n' "$FINISHED_AT" "$TIMEOUT_SECONDS" >>"$LOG_FILE"
fi

exit "$WORKER_STATUS"
