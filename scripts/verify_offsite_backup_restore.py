#!/usr/bin/env python3
"""Decrypt a downloaded offsite copy and restore its DB into isolated PostgreSQL.

Never uses the archive's DATABASE_URL, executes its files or targets production.
Requires age and Docker on the independent verification machine.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
MAX_ARCHIVE_BYTES = 2 * 1024**3
ALLOWED_ROOTS = {'.env', 'docker-compose.yml', 'database.dump', 'database.sha256', '.totish-managed-release', 'deploy', 'runtime'}


def extract_backup(source, destination):
    destination.mkdir(mode=0o700, parents=True)
    total = 0
    with tarfile.open(source, 'r|gz') as tar:
        for count, member in enumerate(tar, 1):
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or not name.parts or name.parts[0] not in ALLOWED_ROOTS:
                raise ValueError('unsafe_archive_path')
            if count > 10000 or member.size < 0:
                raise ValueError('archive_entry_limit')
            total += member.size
            if total > MAX_ARCHIVE_BYTES or not (member.isdir() or member.isfile()):
                raise ValueError('unsafe_or_oversized_archive_member')
            target = destination.joinpath(*name.parts)
            if member.isdir():
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
                continue
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            # Exclusive creation rejects duplicates and file/directory collisions.
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as output, tar.extractfile(member) as contents:
                shutil.copyfileobj(contents, output, length=65536)


def validate_payload(root):
    for name in ('.env', 'docker-compose.yml', 'database.dump', 'database.sha256'):
        if not (root / name).is_file() or (root / name).stat().st_size == 0:
            raise ValueError('incomplete_backup_payload')
    if (root / 'database.sha256').stat().st_size > 128:
        raise ValueError('invalid_database_checksum_file')
    checksum = (root / 'database.sha256').read_text()
    match = re.fullmatch(r'([0-9a-f]{64})  database\.dump\n?', checksum)
    with (root / 'database.dump').open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    if not match or match[1] != digest:
        raise ValueError('database_checksum_mismatch')


def verify(archive, identity, evidence=None):
    if not archive.is_file() or archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError('encrypted_archive_missing_or_too_large')
    with tempfile.TemporaryDirectory(prefix='totish-external-restore-') as temporary:
        directory = Path(temporary)
        decrypted = directory / 'backup.tar.gz'
        subprocess.run(['age', '--decrypt', '-i', str(identity), '-o', str(decrypted), str(archive)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
        payload = directory / 'payload'
        extract_backup(decrypted, payload)
        decrypted.unlink()
        validate_payload(payload)
        state = directory / 'state'
        backups = state / 'backups'
        backups.mkdir(parents=True, mode=0o700)
        dump = backups / 'totish-daily-external.dump'
        shutil.copyfile(payload / 'database.dump', dump)
        dump.chmod(0o600)
        with dump.open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        Path(str(dump) + '.sha256').write_text(f'{digest}  {dump}\n')
        container = 'totish-external-restore-' + uuid.uuid4().hex
        env = dict(os.environ, TOTISH_STATE_DIR=str(state), TOTISH_DB_BACKUP_DIR=str(backups), TOTISH_DB_RESTORE_CONTAINER=container)
        # This script starts a throwaway container with no network or host mounts.
        try:
            subprocess.run(['bash', str(ROOT / 'scripts/verify_database_backup_restore.sh')], env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
        finally:
            try:
                subprocess.run(['docker', 'rm', '-f', container], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
            except (OSError, subprocess.TimeoutExpired):
                pass
    if evidence:
        with archive.open('rb') as source:
            archive_hash = hashlib.file_digest(source, 'sha256').hexdigest()
        fd = os.open(evidence, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as output:
            json.dump({'verified_at': time.time(), 'encrypted_archive_sha256': archive_hash, 'database_restored': True, 'configuration_files_present': True}, output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--identity-file', type=Path, required=True)
    parser.add_argument('--evidence', type=Path)
    args = parser.parse_args()
    try:
        verify(args.archive, args.identity_file, args.evidence)
        print('EXTERNAL DATABASE RESTORE AND CONFIGURATION CHECK OK')
    except Exception as exc:
        print(f'EXTERNAL RESTORE FAILED: {type(exc).__name__}; private content suppressed')
        raise SystemExit(1)
