#!/usr/bin/env python3
"""Fail a release without a fresh, checksum-valid database recovery artifact."""
import argparse
import os
from pathlib import Path
import subprocess
import time


def check_backup(state, max_age_hours=1):
    dumps = sorted((state / "backups").glob("totish-daily-*.dump"))
    if not dumps or time.time() - dumps[-1].stat().st_mtime > max_age_hours * 3600:
        raise RuntimeError("fresh_backup_required")
    dump = dumps[-1]
    subprocess.run(["sha256sum", "--check", str(dump) + ".sha256"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with dump.open("rb") as source:
        subprocess.run(["docker", "run", "--rm", "-i", "postgres:17", "pg_restore", "--list"], stdin=source, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-age-hours", type=float, default=1)
    args = parser.parse_args()
    try:
        check_backup(Path(os.getenv("TOTISH_STATE_DIR", Path.home() / ".local/state/totish")), args.max_age_hours)
        print("PRE-MIGRATION BACKUP CHECK OK")
    except Exception as exc:
        print(f"PRE-MIGRATION BACKUP CHECK FAILED: {type(exc).__name__}")
        raise SystemExit(1)
