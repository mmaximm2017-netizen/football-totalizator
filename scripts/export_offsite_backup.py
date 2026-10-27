#!/usr/bin/env python3
"""Encrypt database/config/runtime and copy to an independently configured remote.

Requires age, rclone and private STATE_DIR/backup-settings.json containing
age_recipient and remote (an existing authenticated rclone remote directory).
Unconfigured/failed export is an error, never reported as an offsite success.
"""
import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = Path(os.getenv("TOTISH_STATE_DIR", Path.home() / ".local/state/totish"))


def export_backup(root=ROOT, state=STATE_DIR):
    settings = json.loads((state / "backup-settings.json").read_text())
    recipient, remote = settings["age_recipient"], settings["remote"]
    if not isinstance(recipient, str) or not recipient.startswith("age1") or not isinstance(remote, str) or ":" not in remote:
        raise ValueError("invalid_backup_settings")
    for program in ("age", "rclone"):
        if not shutil.which(program):
            raise RuntimeError(f"missing_{program}")
    dumps = sorted((state / "backups").glob("totish-daily-*.dump"))
    if not dumps or time.time() - dumps[-1].stat().st_mtime > 36 * 3600:
        raise RuntimeError("fresh_database_backup_required")
    dump = dumps[-1]
    subprocess.run(["sha256sum", "--check", str(dump) + ".sha256"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    name = f"totish-full-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.tar.gz.age"
    with tempfile.TemporaryDirectory(prefix="totish-offsite-") as temporary:
        archive = Path(temporary) / "full.tar.gz"
        encrypted = Path(temporary) / name
        checksum = Path(temporary) / "database.sha256"
        with dump.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        checksum.write_text(digest + "  database.dump\n")
        with tarfile.open(archive, "w:gz", dereference=False) as tar:
            tar.add(dump, arcname="database.dump")
            tar.add(checksum, arcname="database.sha256")
            for relative in (".env", "docker-compose.yml", ".totish-managed-release", "deploy", "runtime"):
                path = root / relative
                if path.exists():
                    tar.add(path, arcname=relative)
                elif relative == ".env":
                    raise RuntimeError("production_configuration_missing")
        subprocess.run(["age", "-r", recipient, "-o", str(encrypted), str(archive)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        archive.unlink()
        subprocess.run(["rclone", "copyto", str(encrypted), remote.rstrip("/") + "/" + name], check=True, timeout=600, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Dedicated, explicitly configured remote; never touch unrelated filenames.
        subprocess.run(["rclone", "delete", remote, "--include", "/totish-full-*.tar.gz.age", "--min-age", "35d"], check=True, timeout=600, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    stamp = state / "offsite-backup.json"
    temporary_stamp = stamp.with_suffix(".tmp")
    temporary_stamp.write_text(json.dumps({"completed_at": time.time(), "file": name}))
    temporary_stamp.chmod(0o600)
    temporary_stamp.replace(stamp)


if __name__ == "__main__":
    try:
        export_backup()
        print("OFFSITE BACKUP OK")
    except Exception as exc:
        print(f"OFFSITE BACKUP FAILED: {type(exc).__name__}; check private backup configuration")
        raise SystemExit(1)
