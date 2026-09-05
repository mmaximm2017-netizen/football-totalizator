#!/usr/bin/env bash
set -Eeuo pipefail

# Production cron entry:
# */5 * * * * /opt/football-totalizator/scripts/run_auto_results.sh

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${TOTISH_PROJECT_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
LOG_FILE="${TOTISH_AUTO_RESULTS_LOG:-/var/log/totish-auto-results.log}"
LOCK_FILE="${TOTISH_AUTO_RESULTS_LOCK:-/tmp/totish-auto-results.lock}"
TIMEOUT_SECONDS="${TOTISH_AUTO_RESULTS_TIMEOUT:-120}"
MAX_LOG_BYTES=10485760
if [[ "${1:-}" == "--monitor" ]]; then TIMEOUT_SECONDS=30; fi

for command in docker flock timeout; do
  command -v "$command" >/dev/null 2>&1 || { echo "$command is required" >&2; exit 1; }
done
DOCKER_BIN="$(command -v docker)"; FLOCK_BIN="$(command -v flock)"; TIMEOUT_BIN="$(command -v timeout)"

# Shared lock spans verification and worker lifetime. Deployment holds the same
# lock exclusively across image replacement AND control-plane synchronization.
DEPLOY_LOCK="${TOTISH_DEPLOY_LOCK_FILE:-/tmp/totish-production-deploy.lock}"
exec 8>"$DEPLOY_LOCK"
"$FLOCK_BIN" -sn 8 || { echo "AUTO_RESULTS_SKIPPED: deploy in progress"; exit 0; }
CONTAINER_ID="$("$DOCKER_BIN" inspect --format='{{.Id}}' football-totalizator-app-1)"
TOTISH_IMAGE="$("$DOCKER_BIN" inspect --format='{{.Config.Image}}' "$CONTAINER_ID")"
export TOTISH_IMAGE
[[ "$TOTISH_IMAGE" =~ ^ghcr\.io/mmaximm2017-netizen/football-totalizator@sha256:[0-9a-f]{64}$ ]] || {
  echo "AUTO_RESULTS_REFUSED: non-immutable image" >&2; exit 1;
}
[[ "$("$DOCKER_BIN" inspect --format='{{.State.Running}}|{{.State.Health.Status}}' "$CONTAINER_ID")" == "true|healthy" ]] || {
  echo "AUTO_RESULTS_REFUSED: unhealthy container" >&2; exit 1;
}
release="$("$DOCKER_BIN" image inspect "$TOTISH_IMAGE" --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^TOTISH_RELEASE=//p')"
[[ "$release" =~ ^[0-9a-f]{40}$ && -f "$PROJECT_ROOT/.totish-managed-release" ]] || {
  echo "AUTO_RESULTS_REFUSED: unknown release" >&2; exit 1;
}
[[ "$(cat "$PROJECT_ROOT/.totish-managed-release")" == "$release" ]] || {
  echo "AUTO_RESULTS_REFUSED: release mismatch" >&2; exit 1;
}
[[ "$("$DOCKER_BIN" inspect --format='{{.Image}}' "$CONTAINER_ID")" == "$("$DOCKER_BIN" image inspect --format='{{.Id}}' "$TOTISH_IMAGE")" ]] || {
  echo "AUTO_RESULTS_REFUSED: image ID mismatch" >&2; exit 1;
}
[[ -d "$PROJECT_ROOT" ]] || { echo "Project root does not exist: $PROJECT_ROOT" >&2; exit 1; }

mkdir -p "$(dirname "$LOG_FILE")" "$PROJECT_ROOT/runtime/telegram-outbox"
touch "$LOG_FILE"; chmod 600 "$LOG_FILE" 2>/dev/null || true
exec 9>"$LOCK_FILE"
if [[ "${1:-}" != "--monitor" ]] && ! "$FLOCK_BIN" -n 9; then
  printf '%s SKIP overlapping run\n' "$(date -Is)" >>"$LOG_FILE"
  exit 0
fi
if [[ "$(stat -c %s "$LOG_FILE" 2>/dev/null || echo 0)" -gt "$MAX_LOG_BYTES" ]]; then
  tail -c 5242880 "$LOG_FILE" >"${LOG_FILE}.tmp.$$" && mv "${LOG_FILE}.tmp.$$" "$LOG_FILE"
fi

cd "$PROJECT_ROOT"
printf '%s START auto-result runtime\n' "$(date -Is)" >>"$LOG_FILE"
set +e
"$TIMEOUT_BIN" --signal=TERM --kill-after=10s "${TIMEOUT_SECONDS}s" \
  "$DOCKER_BIN" compose run --rm --no-deps -T --interactive=false \
    -e PYTHONPATH=/app \
    app python scripts/auto_result_runtime.py "$@" \
      --state-file /app/runtime/telegram-outbox/.auto-results-state.json \
      --outbox /app/runtime/telegram-outbox \
    >>"$LOG_FILE" 2>&1
STATUS=$?
set -e
printf '%s FINISH auto-result runtime exit_code=%s\n' "$(date -Is)" "$STATUS" >>"$LOG_FILE"

python3 "$PROJECT_ROOT/scripts/host_telegram_notifier.py" >>"$LOG_FILE" 2>&1 || true
if [[ "$STATUS" -ne 0 ]]; then
  python3 "$PROJECT_ROOT/scripts/host_telegram_notifier.py" --message "🚨 ТОТИШ: ошибка автоматической проверки результатов. Код выхода: ${STATUS}. Проверьте лог и состояние БД; сохранение могло завершиться до ошибки." >/dev/null 2>&1 || true
fi
exit "$STATUS"
