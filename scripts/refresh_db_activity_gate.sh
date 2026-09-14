#!/usr/bin/env bash
set -Eeuo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${TOTISH_PROJECT_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
STATE_DIR="${TOTISH_STATE_DIR:-$HOME/.local/state/totish}"
STATE_FILE="$STATE_DIR/db-activity-gate.json"
TMP_FILE="$STATE_FILE.tmp.$$"

mkdir -p "$STATE_DIR"
cd "$PROJECT_ROOT"

DOCKER_BIN="$(command -v docker)"
if [[ -z "${TOTISH_IMAGE:-}" ]]; then
    TOTISH_IMAGE="$("$DOCKER_BIN" inspect --format='{{.Config.Image}}' football-totalizator-app-1 2>/dev/null || true)"
    export TOTISH_IMAGE
fi

# Failure intentionally leaves the previous plan in place. If it becomes stale,
# db_activity_gate.py fails open and workers resume their full 5-minute cadence.
set +e
"$DOCKER_BIN" compose run --rm -T --interactive=false \
    -v "$PROJECT_ROOT/scripts:/app/scripts:ro" \
    app python scripts/db_activity_gate.py plan >"$TMP_FILE" 2>/dev/null
status=$?
set -e

if [[ "$status" -eq 0 && -s "$TMP_FILE" ]]; then
    chmod 600 "$TMP_FILE" 2>/dev/null || true
    mv "$TMP_FILE" "$STATE_FILE"
    exit 0
fi

rm -f "$TMP_FILE"
exit "$status"
