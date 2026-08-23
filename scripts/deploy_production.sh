#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
STATE_DIR="${TOTISH_DEPLOY_STATE_DIR:-$HOME/.local/state/totish/deploys}"
LOCK_FILE="${TOTISH_DEPLOY_LOCK_FILE:-/tmp/totish-production-deploy.lock}"
HEALTH_URL="http://127.0.0.1:8000/health"
DB_HEALTH_URL="http://127.0.0.1:8000/health/db"
DEPLOY_STARTED=0

usage() {
    echo "Usage: $0 GHCR_IMAGE@sha256:DIGEST" >&2
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "Missing required command: $1" >&2
        exit 1
    }
}

is_totish_image_digest() {
    [[ "$1" =~ ^ghcr\.io/mmaximm2017-netizen/football-totalizator@sha256:[0-9a-f]{64}$ ]]
}

json_field_is_ok() {
    local body="$1"
    local field="$2"

    [[ "$body" =~ \"${field}\"[[:space:]]*:[[:space:]]*\"ok\" ]]
}

json_field_is_ok_or_warn() {
    local body="$1"
    local field="$2"

    [[ "$body" =~ \"${field}\"[[:space:]]*:[[:space:]]*\"(ok|warn:[^\"]+)\" ]]
}

check_health_once() {
    local health_body
    local db_health_body
    local field

    health_body="$(curl --fail --silent --show-error --max-time 5 "$HEALTH_URL")" || return 1
    json_field_is_ok "$health_body" "status" || return 1

    db_health_body="$(curl --fail --silent --show-error --max-time 5 "$DB_HEALTH_URL")" || return 1
    for field in db active_tournament ranking; do
        json_field_is_ok "$db_health_body" "$field" || return 1
    done
    json_field_is_ok_or_warn "$db_health_body" "single_active" || return 1
}

wait_for_health() {
    local attempt

    echo "HEALTH CHECK"
    echo "Waiting 5 seconds for the app to begin accepting connections..."
    sleep 5

    for attempt in {1..30}; do
        if check_health_once; then
            echo "HEALTH CHECK OK"
            return 0
        fi

        echo "Health check attempt ${attempt}/30 failed; retrying..." >&2
        sleep 2
    done

    echo "Health check failed after 60 seconds." >&2
    return 1
}

rollback() {
    local rollback_ok=1

    echo "ROLLBACK"
    export TOTISH_IMAGE="$CURRENT_IMAGE_REFERENCE"

    if ! docker compose pull app; then
        echo "Rollback image pull failed." >&2
        rollback_ok=0
    fi

    if (( rollback_ok )) && ! docker compose up -d --force-recreate app; then
        echo "Rollback restart failed." >&2
        rollback_ok=0
    fi

    if (( rollback_ok )) && ! wait_for_health; then
        echo "Rollback health check failed." >&2
        rollback_ok=0
    fi

    if (( rollback_ok )); then
        docker inspect football-totalizator-app-1 --format '{{.Config.Image}}'
        echo "ROLLBACK SUCCESS: restored $CURRENT_IMAGE_REFERENCE"
        return 0
    fi

    echo "ROLLBACK FAILED: manual intervention is required." >&2
    return 1
}

on_error() {
    local status=$?

    trap - ERR
    echo "Deployment failed. Recent app logs:" >&2
    docker compose logs --tail=100 app >&2 || true

    if (( DEPLOY_STARTED )); then
        rollback || true
    fi

    exit "$status"
}

if [[ $# -ne 1 || -z "${1:-}" ]]; then
    usage
    exit 1
fi

TARGET_IMAGE="$1"
if ! is_totish_image_digest "$TARGET_IMAGE"; then
    echo "TARGET_IMAGE must be a full TOTISH GHCR image reference with sha256 digest." >&2
    exit 1
fi

echo "START"

for tool in docker curl flock; do
    require_command "$tool"
done
docker compose version >/dev/null 2>&1 || {
    echo "Missing required command: docker compose" >&2
    exit 1
}

cd "$PROJECT_ROOT"
[[ -f .env ]] || {
    echo "Missing production .env." >&2
    exit 1
}

export TOTISH_IMAGE="$TARGET_IMAGE"
umask 077
install -d -m 700 "$STATE_DIR"
exec 9>"$LOCK_FILE"
flock -n 9 || {
    echo "Another production deployment is already running." >&2
    exit 1
}

CURRENT_CONTAINER_ID="$(docker compose ps -q --status running app)"
[[ -n "$CURRENT_CONTAINER_ID" ]] || {
    echo "No running app container found; refusing deployment without a rollback image." >&2
    exit 1
}
CURRENT_IMAGE_ID="$(docker inspect --format '{{.Image}}' "$CURRENT_CONTAINER_ID")"
CURRENT_IMAGE_REFERENCE="$(docker image inspect --format '{{index .RepoDigests 0}}' "$CURRENT_IMAGE_ID")"
is_totish_image_digest "$CURRENT_IMAGE_REFERENCE" || {
    echo "Running app image is not a pullable TOTISH GHCR digest; refusing deployment." >&2
    exit 1
}

TIMESTAMP="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
STATE_FILE="$STATE_DIR/${TIMESTAMP}_${TARGET_IMAGE##*:}.env"

echo "BACKUP"
{
    printf 'timestamp=%s\n' "$TIMESTAMP"
    printf 'current_image=%s\n' "$CURRENT_IMAGE_REFERENCE"
    printf 'target_image=%s\n' "$TARGET_IMAGE"
} >"$STATE_FILE"
echo "Release metadata saved to $STATE_FILE"

trap on_error ERR
DEPLOY_STARTED=1

echo "DEPLOY"
docker compose pull app
docker compose up -d --force-recreate app

wait_for_health

docker inspect football-totalizator-app-1 --format '{{.Config.Image}}'
echo "SUCCESS: deployed $TARGET_IMAGE"
