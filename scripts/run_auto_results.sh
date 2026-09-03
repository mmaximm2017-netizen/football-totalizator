#!/usr/bin/env bash
set -Eeuo pipefail

# Production dry-run cron entry (install separately after verification):
# */5 * * * * /opt/football-totalizator/scripts/run_auto_results.sh

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${TOTISH_PROJECT_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
LOG_FILE="${TOTISH_AUTO_RESULTS_LOG:-/var/log/totish-auto-results.log}"
LOCK_FILE="${TOTISH_AUTO_RESULTS_LOCK:-/tmp/totish-auto-results.lock}"
TIMEOUT_SECONDS="${TOTISH_AUTO_RESULTS_TIMEOUT:-120}"
MAX_LOG_BYTES=10485760

for command in docker flock timeout; do
  command -v "$command" >/dev/null 2>&1 || { echo "$command is required" >&2; exit 1; }
done
DOCKER_BIN="$(command -v docker)"; FLOCK_BIN="$(command -v flock)"; TIMEOUT_BIN="$(command -v timeout)"

if [[ -z "${TOTISH_IMAGE:-}" ]]; then
  TOTISH_IMAGE="$("$DOCKER_BIN" inspect --format='{{.Config.Image}}' football-totalizator-app-1 2>/dev/null || true)"
  export TOTISH_IMAGE
fi
[[ -n "${TOTISH_IMAGE:-}" ]] || { echo "TOTISH_IMAGE is not available" >&2; exit 1; }
[[ -d "$PROJECT_ROOT" ]] || { echo "Project root does not exist: $PROJECT_ROOT" >&2; exit 1; }

mkdir -p "$(dirname "$LOG_FILE")" "$PROJECT_ROOT/runtime/telegram-outbox"
touch "$LOG_FILE"; chmod 600 "$LOG_FILE" 2>/dev/null || true
exec 9>"$LOCK_FILE"
if ! "$FLOCK_BIN" -n 9; then
  printf '%s SKIP overlapping run\n' "$(date -Is)" >>"$LOG_FILE"
  exit 0
fi
if [[ "$(stat -c %s "$LOG_FILE" 2>/dev/null || echo 0)" -gt "$MAX_LOG_BYTES" ]]; then
  tail -c 5242880 "$LOG_FILE" >"${LOG_FILE}.tmp.$$" && mv "${LOG_FILE}.tmp.$$" "$LOG_FILE"
fi

cd "$PROJECT_ROOT"
printf '%s START auto-result worker mode=dry-run\n' "$(date -Is)" >>"$LOG_FILE"
set +e
"$TIMEOUT_BIN" --signal=TERM --kill-after=10s "${TIMEOUT_SECONDS}s" \
  "$DOCKER_BIN" compose run --rm -T --interactive=false \
    -e PYTHONPATH=/app \
    -e AUTO_RESULTS_DRY_RUN=true \
    -v "$PROJECT_ROOT/scripts:/app/scripts:ro" \
    app python scripts/auto_result_worker.py \
      --state-file /app/runtime/telegram-outbox/.auto-results-state.json \
      --outbox /app/runtime/telegram-outbox \
    >>"$LOG_FILE" 2>&1
STATUS=$?
set -e
printf '%s FINISH auto-result worker exit_code=%s\n' "$(date -Is)" "$STATUS" >>"$LOG_FILE"

python3 "$PROJECT_ROOT/scripts/host_telegram_notifier.py" >>"$LOG_FILE" 2>&1 || true
if [[ "$STATUS" -ne 0 ]]; then
  python3 "$PROJECT_ROOT/scripts/host_telegram_notifier.py" --message "🚨 ТОТИШ: ошибка dry-run автоматической проверки результатов. Код выхода: ${STATUS}. Результаты матчей и очки не изменялись." >/dev/null 2>&1 || true
fi
exit "$STATUS"
