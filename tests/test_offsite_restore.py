import hashlib
import io
import tarfile

import pytest

from scripts.verify_offsite_backup_restore import extract_backup, validate_payload


def archive(path, members):
    with tarfile.open(path, 'w:gz') as tar:
        for name, value in members.items():
            entry = tarfile.TarInfo(name)
            if value is None:
                entry.type = tarfile.SYMTYPE
                entry.linkname = '/etc/passwd'
                tar.addfile(entry)
            else:
                entry.size = len(value)
                tar.addfile(entry, io.BytesIO(value))


def test_external_archive_restores_config_and_validates_database_digest(tmp_path):
    dump = b'fixture database'
    source = tmp_path / 'backup.tar.gz'
    archive(source, {'database.dump': dump, 'database.sha256': (hashlib.sha256(dump).hexdigest() + '  database.dump\n').encode(), '.env': b'SECRET_KEY=private', 'docker-compose.yml': b'services: {}', 'runtime/queue/test.msg': b'test'})
    restored = tmp_path / 'restored'
    extract_backup(source, restored)
    validate_payload(restored)
    assert (restored / '.env').read_bytes() == b'SECRET_KEY=private'
    assert (restored / '.env').stat().st_mode & 0o777 == 0o600
    assert (restored / 'runtime/queue/test.msg').read_bytes() == b'test'
    (restored / 'database.dump').write_bytes(b'corruption')
    with pytest.raises(ValueError, match='checksum'):
        validate_payload(restored)


@pytest.mark.parametrize('name,value', [('../escape', b'x'), ('/absolute', b'x'), ('runtime/link', None), ('unexpected/file', b'x')])
def test_external_restore_rejects_unsafe_members(tmp_path, name, value):
    source = tmp_path / 'backup.tar.gz'
    archive(source, {name: value})
    with pytest.raises(ValueError):
        extract_backup(source, tmp_path / 'restored')
