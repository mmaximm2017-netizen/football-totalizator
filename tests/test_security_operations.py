import json
from pathlib import Path
import subprocess
from unittest.mock import Mock

import pytest

from scripts import check_backup_readiness, export_offsite_backup, external_health_monitor
from scripts import monitor_production as monitor


def test_missing_backup_directory_alerts_instead_of_reporting_success(tmp_path, monkeypatch):
    alert = Mock()
    monkeypatch.setattr(monitor, "STATE_DIR", tmp_path)
    monkeypatch.setattr(monitor, "alert", alert)
    assert not monitor.check_database_backup()
    assert alert.call_args.args[0] == "backup:missing"


def test_release_refuses_missing_backup_before_contacting_docker(tmp_path, monkeypatch):
    run = Mock()
    monkeypatch.setattr(check_backup_readiness.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="fresh_backup_required"):
        check_backup_readiness.check_backup(tmp_path)
    run.assert_not_called()


def test_offsite_encrypts_before_upload_and_records_only_success(tmp_path, monkeypatch):
    root, state = tmp_path / "source", tmp_path / "state"
    root.mkdir()
    (state / "backups").mkdir(parents=True)
    (root / ".env").write_text("DATABASE_URL=private-test-value")
    (state / "backup-settings.json").write_text(json.dumps({"age_recipient": "age1test", "remote": "private:totish"}))
    (state / "backups/totish-daily-2026-10-27.dump").write_bytes(b"test dump")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[0] == "age":
            assert Path(command[-1]).exists()
            Path(command[4]).write_bytes(b"encrypted")
        if command[:2] == ["rclone", "copyto"]:
            assert command[2].endswith(".tar.gz.age")
            assert Path(command[2]).read_bytes() == b"encrypted"
            assert not (Path(command[2]).parent / "full.tar.gz").exists()
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(export_offsite_backup.shutil, "which", lambda name: "/bin/" + name)
    monkeypatch.setattr(export_offsite_backup.subprocess, "run", run)
    export_offsite_backup.export_backup(root, state)
    assert json.loads((state / "offsite-backup.json").read_text())["file"].endswith(".age")
    assert calls[-1][-2:] == ["--min-age", "35d"]
    (state / "offsite-backup.json").unlink()
    monkeypatch.setattr(export_offsite_backup.subprocess, "run", Mock(side_effect=RuntimeError("upload failed")))
    with pytest.raises(RuntimeError):
        export_offsite_backup.export_backup(root, state)
    assert not (state / "offsite-backup.json").exists()


@pytest.mark.parametrize("healthy, expected_code, expected_message", [
    (True, 0, "EXTERNAL HEALTH OK"),
    (False, 2, "EXTERNAL HEALTH FAILED; Telegram alerting unconfigured"),
])
@pytest.mark.parametrize("missing_credentials", [
    ("TELEGRAM_ERROR_BOT_TOKEN",),
    ("TELEGRAM_ERROR_CHAT_ID",),
    ("TELEGRAM_ERROR_BOT_TOKEN", "TELEGRAM_ERROR_CHAT_ID"),
])
def test_external_monitor_checks_health_without_alert_credentials(
    monkeypatch, capsys, healthy, expected_code, expected_message, missing_credentials
):
    monkeypatch.setenv("TELEGRAM_ERROR_BOT_TOKEN", "test-secret")
    monkeypatch.setenv("TELEGRAM_ERROR_CHAT_ID", "123")
    for name in missing_credentials:
        monkeypatch.delenv(name, raising=False)
    health_check = Mock(return_value=healthy)
    monkeypatch.setattr(external_health_monitor, "healthy", health_check)
    send = Mock(side_effect=AssertionError("Unexpected network request"))
    monkeypatch.setattr(external_health_monitor, "urlopen", send)

    assert external_health_monitor.main() == expected_code
    assert capsys.readouterr().out.strip() == expected_message
    health_check.assert_called_once_with()
    send.assert_not_called()


def test_external_monitor_checks_outside_vps_and_alerts_when_down(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ERROR_BOT_TOKEN", "test-secret")
    monkeypatch.setenv("TELEGRAM_ERROR_CHAT_ID", "123")
    monkeypatch.setattr(external_health_monitor, "healthy", lambda: False)
    response = Mock()
    response.__enter__ = lambda self: self
    response.__exit__ = lambda *args: None
    response.read.return_value = b'{"ok":true}'
    send = Mock(return_value=response)
    monkeypatch.setattr(external_health_monitor, "urlopen", send)
    assert external_health_monitor.main() == 1
    assert json.loads(send.call_args.args[0].data)["chat_id"] == "123"
