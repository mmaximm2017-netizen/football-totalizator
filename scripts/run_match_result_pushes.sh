#!/usr/bin/env bash
set -Eeuo pipefail

# Production cron entry (install manually; this script does not modify crontab):
# SHELL=/bin/bash
# PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
# */5 * * * * /opt/football-totalizator/scripts/run_match_result_pushes.sh

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${TOTISH_PROJECT_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
LOG_FILE="${TOTISH_MATCH_RESULT_PUSH_LOG:-/var/log/totish-match-result-push.log}"
LOCK_FILE="${TOTISH_MATCH_RESULT_PUSH_LOCK:-/tmp/totish-match-result-push.lock}"
TIMEOUT_SECONDS="${TOTISH_MATCH_RESULT_PUSH_TIMEOUT:-180}"
STATE_DIR="${TOTISH_STATE_DIR:-$HOME/.local/state/totish}"
RECOVERY_MARKER="$STATE_DIR/match-result-worker.failed"
MAX_LOG_BYTES=10485760

usage() {
    echo "Usage: $0 [--dry-run]" >&2
}

if [[ "$#" -gt 1 || ( "$#" -eq 1 && "$1" != "--dry-run" ) ]]; then
    usage
    exit 2
fi

for required_command in docker flock timeout; do
    if ! command -v "$required_command" >/dev/null 2>&1; then
        echo "$required_command is required" >&2
        exit 1
    fi
done

DOCKER_BIN="$(command -v docker)"
FLOCK_BIN="$(command -v flock)"
TIMEOUT_BIN="$(command -v timeout)"

if [[ -z "${TOTISH_IMAGE:-}" ]]; then
    TOTISH_IMAGE="$("$DOCKER_BIN" inspect --format='{{.Config.Image}}' football-totalizator-app-1 2>/dev/null || true)"
    export TOTISH_IMAGE
fi

if [[ -z "${TOTISH_IMAGE:-}" ]]; then
    echo "TOTISH_IMAGE is not set and current production image could not be detected" >&2
    exit 1
fi

if ! "$DOCKER_BIN" compose version >/dev/null 2>&1; then
    echo "docker compose is required" >&2
    exit 1
fi

if [[ ! -d "$PROJECT_ROOT" ]]; then
    echo "Project root does not exist: $PROJECT_ROOT" >&2
    exit 1
fi

mkdir -p "$(dirname "$LOG_FILE")" "$STATE_DIR"
touch "$LOG_FILE"
chmod 600 "$LOG_FILE" 2>/dev/null || true

exec 9>"$LOCK_FILE"
if ! "$FLOCK_BIN" -n 9; then
    printf '%s SKIP overlapping run\n' "$(date -Is)" >>"$LOG_FILE"
    exit 0
fi

if [[ "$(stat -c %s "$LOG_FILE" 2>/dev/null || echo 0)" -gt "$MAX_LOG_BYTES" ]]; then
    tail -c 5242880 "$LOG_FILE" >"${LOG_FILE}.tmp.$$"
    mv "${LOG_FILE}.tmp.$$" "$LOG_FILE"
    chmod 600 "$LOG_FILE" 2>/dev/null || true
fi

cd "$PROJECT_ROOT"

STARTED_AT="$(date -Is)"
printf '%s START match-result worker%s\n' "$STARTED_AT" "${1:+ mode=$1}" >>"$LOG_FILE"

WORKER_ARGS=()
if [[ "${1:-}" == "--dry-run" ]]; then
    WORKER_ARGS+=("--dry-run")
fi

set +e
"$TIMEOUT_BIN" --signal=TERM --kill-after=10s "${TIMEOUT_SECONDS}s" \
    "$DOCKER_BIN" compose run --rm -T --interactive=false \
        -v "$PROJECT_ROOT/scripts:/app/scripts:ro" \
        app python scripts/send_match_result_pushes.py "${WORKER_ARGS[@]}" \
    >>"$LOG_FILE" 2>&1
WORKER_STATUS=$?
set -e

FINISHED_AT="$(date -Is)"
printf '%s FINISH match-result worker exit_code=%s\n' "$FINISHED_AT" "$WORKER_STATUS" >>"$LOG_FILE"

if [[ "$WORKER_STATUS" -ne 0 ]]; then
    if [[ "$WORKER_STATUS" -eq 124 || "$WORKER_STATUS" -eq 137 ]]; then
        HUMAN_REASON="Фоновая задача работала слишком долго и была принудительно остановлена по таймауту."
    else
        HUMAN_REASON="Фоновая задача завершилась с ошибкой и не смогла выполнить работу до конца."
    fi

    if python3 "$PROJECT_ROOT/scripts/host_telegram_notifier.py" \
        --message "🚨 ТОТИШ: ошибка фоновой задачи

Что именно произошло:
Не удалось обработать уведомления после завершения матчей.

Из-за этого пользователи могли не получить push с результатом матча и начисленными очками.

Причина:
${HUMAN_REASON}

Время: ${FINISHED_AT}

Технические детали:
match_result_worker / exit_code=${WORKER_STATUS}" \
        >/dev/null 2>&1; then
        : >"$RECOVERY_MARKER"
        chmod 600 "$RECOVERY_MARKER" 2>/dev/null || true
    fi
elif [[ -f "$RECOVERY_MARKER" ]]; then
    if python3 "$PROJECT_ROOT/scripts/host_telegram_notifier.py" \
        --message "✅ ТОТИШ: фоновая задача восстановилась

Обработка уведомлений после завершения матчей снова работает нормально.

Время: ${FINISHED_AT}

Технические детали:
match_result_worker / recovered" \
        >/dev/null 2>&1; then
        rm -f "$RECOVERY_MARKER"
    fi
fi

if [[ "$WORKER_STATUS" -eq 124 || "$WORKER_STATUS" -eq 137 ]]; then
    printf '%s TIMEOUT match-result worker limit=%ss\n' "$FINISHED_AT" "$TIMEOUT_SECONDS" >>"$LOG_FILE"
fi

exit "$WORKER_STATUS"
