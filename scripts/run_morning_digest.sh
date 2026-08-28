#!/usr/bin/env bash
set -Eeuo pipefail

# Production cron entry (VPS remains UTC):
# 0 5 * * * /opt/football-totalizator/scripts/run_morning_digest.sh

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${TOTISH_PROJECT_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
STATE_DIR="${TOTISH_MORNING_DIGEST_STATE_DIR:-$HOME/.local/state/totish}"
LOG_FILE="${TOTISH_MORNING_DIGEST_LOG:-$STATE_DIR/morning-digest.log}"
LOCK_FILE="${TOTISH_MORNING_DIGEST_LOCK:-$STATE_DIR/morning-digest.lock}"
STATE_FILE="${TOTISH_MORNING_DIGEST_STATE:-$STATE_DIR/morning-digest-state}"
TIMEOUT_SECONDS="${TOTISH_MORNING_DIGEST_TIMEOUT:-180}"

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" && "$#" -eq 1 ]]; then
    DRY_RUN=1
elif [[ "$#" -ne 0 ]]; then
    echo "Usage: $0 [--dry-run]" >&2
    exit 2
fi

for required_command in docker flock timeout; do
    command -v "$required_command" >/dev/null 2>&1 || {
        echo "$required_command is required" >&2
        exit 1
    }
done

DOCKER_BIN="$(command -v docker)"

is_totish_image_digest() {
    [[ "$1" =~ ^ghcr\.io/mmaximm2017-netizen/football-totalizator@sha256:[0-9a-f]{64}$ ]]
}

resolve_fallback_image() {
    local candidate=""
    local state_file
    local line
    local -a state_files=()
    local local_image
    local deploy_state_dir="${TOTISH_DEPLOY_STATE_DIR:-$HOME/.local/state/totish/deploys}"

    candidate="$("$DOCKER_BIN" inspect --format='{{.Config.Image}}' football-totalizator-app-1 2>/dev/null || true)"
    if [[ -n "$candidate" ]]; then
        printf '%s\n' "$candidate"
        return 0
    fi

    shopt -s nullglob
    state_files=("$deploy_state_dir"/*.env)
    shopt -u nullglob
    for (( index=${#state_files[@]} - 1; index>=0; index-- )); do
        state_file="${state_files[index]}"
        while IFS= read -r line; do
            if [[ "$line" == target_image=* ]]; then
                candidate="${line#target_image=}"
                break
            fi
        done <"$state_file"
        if is_totish_image_digest "$candidate"; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    while IFS= read -r local_image; do
        if is_totish_image_digest "$local_image"; then
            printf '%s\n' "$local_image"
            return 0
        fi
    done < <("$DOCKER_BIN" image ls --format '{{.Repository}}@{{.Digest}}')
    return 1
}

if [[ -z "${TOTISH_IMAGE:-}" ]]; then
    TOTISH_IMAGE="$(resolve_fallback_image || true)"
    export TOTISH_IMAGE
fi
[[ -n "${TOTISH_IMAGE:-}" ]] || {
    echo "TOTISH_IMAGE is not set and no production image fallback is available" >&2
    exit 1
}
"$DOCKER_BIN" compose version >/dev/null 2>&1 || {
    echo "docker compose is required" >&2
    exit 1
}

mkdir -p "$STATE_DIR" "$(dirname "$LOG_FILE")" "$(dirname "$LOCK_FILE")" "$(dirname "$STATE_FILE")"
touch "$LOG_FILE"
chmod 600 "$LOG_FILE" 2>/dev/null || true
exec 9>"$LOCK_FILE"
"$(command -v flock)" -n 9 || {
    printf '%s SKIP overlapping morning digest\n' "$(date -Is)" >>"$LOG_FILE"
    exit 0
}

TODAY_MSK="$(TZ=Europe/Moscow date +%F)"
if (( ! DRY_RUN )) && [[ -f "$STATE_FILE" ]] && [[ "$(<"$STATE_FILE")" == "$TODAY_MSK" ]]; then
    printf '%s SKIP already sent for %s\n' "$(date -Is)" "$TODAY_MSK" >>"$LOG_FILE"
    exit 0
fi

CONTAINER_STATE="$("$DOCKER_BIN" inspect -f '{{.State.Status}}|{{.State.Running}}|{{.State.Restarting}}' football-totalizator-app-1 2>/dev/null || echo missing)"
HEALTH_PY='import json, sys; from urllib.request import urlopen; response = urlopen(sys.argv[1], timeout=5); assert response.status == 200; print(json.dumps(json.loads(response.read().decode("utf-8"))))'
LOCAL_HEALTH_JSON="$("$DOCKER_BIN" exec football-totalizator-app-1 python -c "$HEALTH_PY" http://127.0.0.1:8000/health 2>/dev/null || true)"
DB_HEALTH_JSON="$("$DOCKER_BIN" exec football-totalizator-app-1 python -c "$HEALTH_PY" http://127.0.0.1:8000/health/db 2>/dev/null || true)"
cd "$PROJECT_ROOT"
ARGS=(python scripts/send_morning_digest.py --container-state "$CONTAINER_STATE")
if (( DRY_RUN )); then
    ARGS+=(--dry-run)
fi

printf '%s START morning digest%s\n' "$(date -Is)" "$([[ "$DRY_RUN" == 1 ]] && echo ' dry_run')" >>"$LOG_FILE"
set +e
timeout --signal=TERM --kill-after=10s "${TIMEOUT_SECONDS}s" \
    "$DOCKER_BIN" compose run --rm -T --interactive=false \
        -v "$PROJECT_ROOT/scripts:/app/scripts:ro" \
        -e "MORNING_DIGEST_LOCAL_HEALTH_JSON=$LOCAL_HEALTH_JSON" \
        -e "MORNING_DIGEST_DB_HEALTH_JSON=$DB_HEALTH_JSON" \
        app "${ARGS[@]}" >>"$LOG_FILE" 2>&1
STATUS=$?
set -e

if (( STATUS == 0 && ! DRY_RUN )); then
    tmp_state="${STATE_FILE}.tmp.$$"
    printf '%s\n' "$TODAY_MSK" >"$tmp_state"
    mv "$tmp_state" "$STATE_FILE"
    chmod 600 "$STATE_FILE" 2>/dev/null || true
fi
printf '%s FINISH morning digest exit_code=%s\n' "$(date -Is)" "$STATUS" >>"$LOG_FILE"
exit "$STATUS"
