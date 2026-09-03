import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_backup_shell_scripts_have_valid_syntax():
    for relative in (
        "scripts/run_database_backup.sh",
        "scripts/verify_database_backup_restore.sh",
    ):
        result = subprocess.run(
            ["bash", "-n", str(ROOT / relative)],
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr


def test_backup_uses_matching_postgres_major_and_private_retention():
    source = (ROOT / "scripts" / "run_database_backup.sh").read_text(
        encoding="utf-8"
    )

    assert "postgres:17" in source
    assert 'TOTISH_DB_DAILY_KEEP:-7' in source
    assert 'TOTISH_DB_WEEKLY_KEEP:-4' in source
    assert "pg_dump" in source
    assert "--format=custom" in source
    assert "sha256sum" in source
    assert 'install -d -m 700' in source
    assert 'chmod 600 "$daily_path"' in source
    assert 'echo "$DATABASE_URL"' not in source


def test_restore_check_uses_temporary_postgres_and_never_targets_neon():
    source = (ROOT / "scripts" / "verify_database_backup_restore.sh").read_text(
        encoding="utf-8"
    )

    assert "postgres:17" in source
    assert "restore_test" in source
    assert "pg_restore" in source
    assert "docker rm -f" in source
    assert "DATABASE_URL" not in source


def test_backup_jobs_are_managed_by_production_cron():
    cron = (ROOT / "deploy" / "production.cron").read_text(encoding="utf-8")
    manifest = (ROOT / "deploy" / "production-managed-files.txt").read_text(
        encoding="utf-8"
    )
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(
        encoding="utf-8"
    )

    assert "30 3 * * *" in cron
    assert "run_database_backup.sh" in cron
    assert "15 4 * * 1" in cron
    assert "verify_database_backup_restore.sh" in cron
    assert "scripts/run_database_backup.sh" in manifest
    assert "scripts/verify_database_backup_restore.sh" in manifest
    assert "run_database_backup\\.sh" in workflow
    assert "verify_database_backup_restore\\.sh" in workflow


def test_monitor_checks_backup_freshness_without_exposing_dump_contents():
    source = (ROOT / "scripts" / "monitor_production.py").read_text(
        encoding="utf-8"
    )

    assert "def check_database_backup()" in source
    assert '36 * 60 * 60' in source
    assert '"backup:stale"' in source
    assert '"backup:checksum_missing"' in source
    assert "check_database_backup()" in source


def test_backup_shell_variables_are_not_literal_escaped_text():
    for relative in (
        "scripts/run_database_backup.sh",
        "scripts/verify_database_backup_restore.sh",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert r"\${" not in source
