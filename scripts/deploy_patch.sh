#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONTINUE=0; CHECK_ONLY=0; PATCH_FILE=""
usage() { echo "Usage: TEST_MODULES='tests.test_x tests.test_y' $0 [--check] /path/change.patch" >&2; echo "       TEST_MODULES='...' $0 --continue" >&2; }
if [[ "${1:-}" == "--continue" ]]; then CONTINUE=1; shift; elif [[ "${1:-}" == "--check" ]]; then CHECK_ONLY=1; shift; fi
if (( ! CONTINUE )); then PATCH_FILE="${1:-}"; [[ -n "$PATCH_FILE" && -f "$PATCH_FILE" && -r "$PATCH_FILE" && -s "$PATCH_FILE" ]] || { usage; exit 1; }; fi
cd "$PROJECT_ROOT"
echo "[1/8] Git checks"
for tool in git docker curl; do command -v "$tool" >/dev/null 2>&1 || { echo "Missing required command: $tool" >&2; exit 1; }; done
docker compose version >/dev/null 2>&1 || { echo "docker compose is required." >&2; exit 1; }
[[ "$(git rev-parse --is-inside-work-tree)" == true ]] || { echo "Not a git worktree." >&2; exit 1; }
[[ "$(git branch --show-current)" == main ]] || { echo "Deploy is allowed only from main." >&2; exit 1; }
if (( ! CONTINUE )); then [[ -z "$(git status --porcelain)" ]] || { echo "Working tree is dirty; refusing to deploy." >&2; exit 1; }; fi
if (( ! CONTINUE )); then
  echo "[2/8] Apply patch"
  if ! git am "$PATCH_FILE"; then echo "Patch application failed. Resolve the git am state manually; no automatic abort was performed." >&2; exit 1; fi
else echo "[2/8] Apply patch (skipped; continue mode)"; fi
echo "[3/8] Build"
if ! docker compose build --pull=false app; then echo "Build failed. If the patch was already applied, rerun with --continue. Do NOT apply the patch again." >&2; exit 1; fi
echo "[4/8] Tests"
if [[ -n "${TEST_MODULES:-}" ]]; then docker compose run --rm -v "$PROJECT_ROOT/tests:/app/tests:ro" -v "$PROJECT_ROOT/scripts:/app/scripts:ro" app sh -lc "python -m unittest $TEST_MODULES"; elif [[ "${SKIP_TESTS:-0}" == 1 ]]; then echo "WARNING: tests explicitly skipped"; else echo "TEST_MODULES is empty. Set it or use SKIP_TESTS=1 intentionally." >&2; exit 1; fi
echo "[5/8] Static checks"
git diff --check HEAD^ HEAD
docker compose run --rm app python -m compileall -q app
if (( CHECK_ONLY )); then echo "Checks passed; no push or deploy performed (--check)."; exit 0; fi
echo "[6/8] Push"
if ! GIT_TERMINAL_PROMPT=0 git push origin main; then echo "GitHub credentials are not configured. Run ./scripts/setup_github_credentials.sh once." >&2; exit 1; fi
echo "[7/8] Deploy"
docker compose up -d --force-recreate app
sleep 20
docker compose ps
echo "[8/8] Healthcheck"
HEADERS_FILE="$(mktemp)"
trap 'rm -f "$HEADERS_FILE"' EXIT
HTTP_CODE="$(curl -fsS -L --max-redirs 0 -D "$HEADERS_FILE" -o /dev/null -w '%{http_code}' https://totish.ru/ || true)"
LOCATION="$(awk 'BEGIN{IGNORECASE=1} /^Location:/{sub(/^[^:]*:[[:space:]]*/, ""); gsub(/\r/, ""); print; exit}' "$HEADERS_FILE")"
if [[ "$HTTP_CODE" != 302 || "$LOCATION" != "/login" ]]; then echo "DEPLOY HEALTHCHECK FAILED (HTTP $HTTP_CODE -> ${LOCATION:-<missing>})" >&2; exit 1; fi
SHORT_COMMIT="$(git rev-parse --short HEAD)"
SUBJECT="$(git log -1 --pretty=%s)"
cat <<EOF
==============================
DEPLOY OK
Commit: $SHORT_COMMIT $SUBJECT
Branch: main
Tests: OK
App: healthy
HTTP: 302 -> /login
==============================
EOF
