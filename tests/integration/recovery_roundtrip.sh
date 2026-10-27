#!/usr/bin/env bash
# Actual age encryption, rclone transport and PostgreSQL restore of a CI fixture.
set -Eeuo pipefail
TASK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
temporary="$(mktemp -d)"
source_container="totish-ci-backup-source-$$"
cleanup() {
    docker rm -f "$source_container" >/dev/null 2>&1 || true
    rm -rf "$temporary"
}
trap cleanup EXIT
umask 077
mkdir -p "$temporary/source/runtime/telegram-outbox" "$temporary/state/backups" "$temporary/remote"
docker run -d --name "$source_container" --network none -e POSTGRES_HOST_AUTH_METHOD=trust postgres:17 >/dev/null
for attempt in {1..30}; do
    if docker exec "$source_container" pg_isready -U postgres >/dev/null 2>&1; then break; fi
    if [[ "$attempt" == 30 ]]; then exit 1; fi
    sleep 1
done
for migration in "$TASK_ROOT"/migrations/*.sql; do
    docker exec -i "$source_container" psql -U postgres -v ON_ERROR_STOP=1 >/dev/null < "$migration"
done
docker exec -i "$source_container" psql -U postgres -v ON_ERROR_STOP=1 >/dev/null <<'SQL'
CREATE TABLE schema_migrations(version integer primary key,filename text,checksum text,applied_at timestamptz default now());
INSERT INTO schema_migrations(version,filename,checksum) VALUES(10,'0010_add_revocable_sessions.sql','ci');
INSERT INTO users(id,username,password) VALUES(1,'restore-fixture','not-a-real-password');
INSERT INTO tournaments(id,name,is_active) VALUES(5,'RPL fixture',1);
INSERT INTO matches(id,tournament_id,home_team,away_team,status,home_score,away_score) VALUES(10,5,'Home','Away','FINISHED',2,1);
INSERT INTO predictions VALUES(1,10,5,2,1,10);
SQL
dump="$temporary/state/backups/totish-daily-ci.dump"
docker exec "$source_container" pg_dump -U postgres -Fc --no-owner --no-acl postgres > "$dump"
sha256sum "$dump" > "$dump.sha256"
printf 'SECRET_KEY=CI_NOT_A_REAL_SECRET\n' > "$temporary/source/.env"
printf 'services: {}\n' > "$temporary/source/docker-compose.yml"
printf 'CI fixture queue\n' > "$temporary/source/runtime/telegram-outbox/fixture.msg"
git -C "$TASK_ROOT" rev-parse HEAD > "$temporary/source/.totish-managed-release"
age-keygen -o "$temporary/identity" >/dev/null 2>&1
age-keygen -o "$temporary/wrong-identity" >/dev/null 2>&1
RECOVERY_FIXTURE="$temporary" PYTHONPATH="$TASK_ROOT" python - <<'PY'
import json, os, subprocess
from pathlib import Path
from scripts.export_offsite_backup import export_backup
from scripts.verify_offsite_backup_restore import verify
root=Path(os.environ['RECOVERY_FIXTURE'])
recipient=subprocess.check_output(['age-keygen','-y',str(root/'identity')],text=True).strip()
(root/'state/backup-settings.json').write_text(json.dumps({'age_recipient':recipient,'remote':':local:'+str(root/'remote')}))
export_backup(root/'source',root/'state')
copies=list((root/'remote').glob('*.age'))
assert len(copies)==1
try:
    verify(copies[0],root/'wrong-identity',root/'bad-evidence.json')
except subprocess.CalledProcessError:
    pass
else:
    raise AssertionError('wrong decryption identity was accepted')
assert not (root/'bad-evidence.json').exists()
verify(copies[0],root/'identity',root/'evidence.json')
assert json.loads((root/'evidence.json').read_text())['database_restored'] is True
print('CI RECOVERY ROUNDTRIP OK: encrypted export, independent copy, wrong-key rejection and isolated DB restore')
PY
