#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="${TOTISH_STATE_DIR:-$HOME/.local/state/totish}"
BACKUP_DIR="${TOTISH_DB_BACKUP_DIR:-$STATE_DIR/backups}"
LOCK_FILE="${TOTISH_DB_BACKUP_LOCK:-$STATE_DIR/database-backup.lock}"
POSTGRES_IMAGE="${TOTISH_DB_BACKUP_IMAGE:-postgres:17}"
DAILY_KEEP="${TOTISH_DB_DAILY_KEEP:-7}"
WEEKLY_KEEP="${TOTISH_DB_WEEKLY_KEEP:-4}"

require_command() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "Missing required command: $1" >&2
        exit 1
    }
}

prune_kind() {
    local kind="$1"
    local keep="$2"
    local -a files=()
    local index

    mapfile -t files < <(
        find "$BACKUP_DIR" -maxdepth 1 -type f -name "totish-${kind}-*.dump" -printf '%f\n' |
        sort -r
    )

    for (( index=keep; index<${#files[@]}; index++ )); do
        rm -f \
            "$BACKUP_DIR/${files[$index]}" \
            "$BACKUP_DIR/${files[$index]}.sha256"
    done
}

for tool in docker flock sha256sum find sort stat; do
    require_command "$tool"
done

[[ -f "$PROJECT_ROOT/.env" ]] || {
    echo "Missing production .env" >&2
    exit 1
}

install -d -m 700 "$STATE_DIR" "$BACKUP_DIR"
umask 077

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "BACKUP SKIPPED: another database backup is running"
    exit 0
fi

backup_date="$(TZ=Europe/Moscow date +%F)"
iso_week="$(TZ=Europe/Moscow date +%G-W%V)"
weekday="$(TZ=Europe/Moscow date +%u)"
daily_name="totish-daily-${backup_date}.dump"
daily_path="$BACKUP_DIR/$daily_name"
tmp_path="$BACKUP_DIR/.${daily_name}.tmp"

cleanup() {
    rm -f "$tmp_path"
}
trap cleanup EXIT

echo "DATABASE BACKUP START: date=$backup_date"

docker run --rm \
    --env-file "$PROJECT_ROOT/.env" \
    "$POSTGRES_IMAGE" \
    sh -eu -c '
        test -n "${DATABASE_URL:-}"
        exec pg_dump \
            --dbname="$DATABASE_URL" \
            --format=custom \
            --compress=6 \
            --no-owner \
            --no-acl
    ' > "$tmp_path"

[[ -s "$tmp_path" ]] || {
    echo "DATABASE BACKUP FAILED: empty dump" >&2
    exit 1
}

toc="$(
    docker run --rm -i "$POSTGRES_IMAGE" pg_restore --list < "$tmp_path"
)"

for table in users matches predictions tournaments schema_migrations; do
    if ! grep -Eq "TABLE DATA public ${table} " <<< "$toc"; then
        echo "DATABASE BACKUP FAILED: missing table data for $table" >&2
        exit 1
    fi
done

mv "$tmp_path" "$daily_path"
chmod 600 "$daily_path"
sha256sum "$daily_path" > "$daily_path.sha256"
chmod 600 "$daily_path.sha256"

if [[ "$weekday" == "7" ]]; then
    weekly_path="$BACKUP_DIR/totish-weekly-${iso_week}.dump"
    cp "$daily_path" "$weekly_path"
    chmod 600 "$weekly_path"
    sha256sum "$weekly_path" > "$weekly_path.sha256"
    chmod 600 "$weekly_path.sha256"
    echo "WEEKLY BACKUP CREATED: $(basename "$weekly_path")"
fi

prune_kind daily "$DAILY_KEEP"
prune_kind weekly "$WEEKLY_KEEP"

daily_count="$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'totish-daily-*.dump' | wc -l)"
weekly_count="$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'totish-weekly-*.dump' | wc -l)"
size_bytes="$(stat -c %s "$daily_path")"

echo "DATABASE BACKUP OK: file=$daily_name size_bytes=$size_bytes daily=$daily_count weekly=$weekly_count"
