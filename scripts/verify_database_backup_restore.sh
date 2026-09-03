#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "\${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="\${TOTISH_STATE_DIR:-$HOME/.local/state/totish}"
BACKUP_DIR="\${TOTISH_DB_BACKUP_DIR:-$STATE_DIR/backups}"
LOCK_FILE="\${TOTISH_DB_RESTORE_VERIFY_LOCK:-$STATE_DIR/database-restore-verify.lock}"
POSTGRES_IMAGE="\${TOTISH_DB_BACKUP_IMAGE:-postgres:17}"

for tool in docker flock sha256sum find sort head; do
    command -v "$tool" >/dev/null 2>&1 || {
        echo "Missing required command: $tool" >&2
        exit 1
    }
done

install -d -m 700 "$STATE_DIR"
umask 077

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "RESTORE VERIFY SKIPPED: another verification is running"
    exit 0
fi

latest="$(
    find "$BACKUP_DIR" -maxdepth 1 -type f -name 'totish-daily-*.dump' -printf '%p\n' 2>/dev/null |
    sort -r |
    head -n 1
)"

[[ -n "$latest" && -f "$latest" ]] || {
    echo "RESTORE VERIFY FAILED: no daily backup found" >&2
    exit 1
}

[[ -f "$latest.sha256" ]] || {
    echo "RESTORE VERIFY FAILED: checksum file missing" >&2
    exit 1
}

(
    cd "$BACKUP_DIR"
    sha256sum --check "$(basename "$latest").sha256"
) >/dev/null

container="totish-restore-check-$$"
cleanup() {
    docker rm -f "$container" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker run -d \
    --name "$container" \
    -e POSTGRES_HOST_AUTH_METHOD=trust \
    "$POSTGRES_IMAGE" >/dev/null

ready=0
for _ in {1..30}; do
    if docker exec "$container" pg_isready -U postgres >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 1
done

(( ready == 1 )) || {
    echo "RESTORE VERIFY FAILED: temporary PostgreSQL did not become ready" >&2
    exit 1
}

docker exec "$container" createdb -U postgres restore_test

docker exec -i "$container" \
    pg_restore -U postgres -d restore_test --no-owner --no-acl \
    < "$latest"

counts="$(
    docker exec "$container" psql -U postgres -d restore_test -At -F '|' -c "
        SELECT
            (SELECT COUNT(*) FROM users),
            (SELECT COUNT(*) FROM matches),
            (SELECT COUNT(*) FROM predictions),
            (SELECT COUNT(*) FROM tournaments),
            (SELECT COALESCE(MAX(version), 0) FROM schema_migrations);
    "
)"

IFS='|' read -r users_count matches_count predictions_count tournaments_count migration_version <<< "$counts"

for value in "$users_count" "$matches_count" "$predictions_count" "$tournaments_count"; do
    [[ "$value" =~ ^[0-9]+$ ]] || {
        echo "RESTORE VERIFY FAILED: invalid restored counts" >&2
        exit 1
    }
done

[[ "$migration_version" =~ ^[0-9]+$ ]] || {
    echo "RESTORE VERIFY FAILED: invalid migration version" >&2
    exit 1
}

echo "RESTORE VERIFY OK: backup=$(basename "$latest") users=$users_count matches=$matches_count predictions=$predictions_count tournaments=$tournaments_count migration=$migration_version"
