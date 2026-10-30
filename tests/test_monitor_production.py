from unittest.mock import Mock

from scripts import monitor_production as monitor


def configure_alert_state(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "STATE_DIR", tmp_path)
    monkeypatch.setattr(monitor, "STATE_FILE", tmp_path / "alerts.json")
    monkeypatch.setattr(monitor, "RECOVERY_STATE_FILE", tmp_path / "recovery.json")
    send = Mock()
    monkeypatch.setattr(monitor, "send_message", send)
    clock = [100_000]
    monkeypatch.setattr(monitor.time, "time", lambda: clock[0])
    return send, clock


def test_unverified_recovery_reminds_daily_even_with_other_alerts(monkeypatch, tmp_path):
    send, clock = configure_alert_state(monkeypatch, tmp_path)
    key = "backup:recovery_unverified"
    assert monitor.alert(key, "evidence missing")
    assert "Ежедневная резервная копия базы отсутствует" not in send.call_args.args[0]
    assert "не подтверждены" in send.call_args.args[0]
    clock[0] += 300
    assert not monitor.alert(key, "evidence missing")
    clock[0] += 7200
    assert monitor.alert("health:public", "offline")
    assert not monitor.alert(key, "evidence missing")
    clock[0] = 100_000 + 86400
    assert monitor.alert(key, "evidence missing")
    assert send.call_count == 3


def test_actual_backup_failure_keeps_urgent_reminders(monkeypatch, tmp_path):
    _, clock = configure_alert_state(monkeypatch, tmp_path)
    assert monitor.alert("backup:missing", "no dump")
    clock[0] += 299
    assert not monitor.alert("backup:missing", "no dump")
    clock[0] += 1
    assert monitor.alert("backup:missing", "no dump")


def test_recovery_allows_immediate_alert_for_new_incident(monkeypatch, tmp_path):
    send, clock = configure_alert_state(monkeypatch, tmp_path)
    assert monitor.alert("backup:recovery_unverified", "missing")
    clock[0] += 60
    assert monitor.recover("backup")
    assert monitor.alert("backup:recovery_unverified", "missing again")
    assert send.call_count == 3


def test_db_health_success_uses_ten_second_timeout_without_retry(monkeypatch):
    fetch_json = Mock(return_value={"db": "ok", "active_tournament": "ok", "ranking": "ok"})
    sleep = Mock()
    alert = Mock()
    monkeypatch.setattr(monitor, "fetch_json", fetch_json)
    monkeypatch.setattr(monitor.time, "sleep", sleep)
    monkeypatch.setattr(monitor, "alert", alert)

    assert monitor.check_db_health() is True

    fetch_json.assert_called_once_with("http://127.0.0.1:8000/health/db", timeout=10)
    sleep.assert_not_called()
    alert.assert_not_called()


def test_db_health_retries_once_then_checks_successful_payload(monkeypatch, capsys):
    fetch_json = Mock(side_effect=[TimeoutError("slow"), {"db": "ok", "active_tournament": "ok", "ranking": "ok"}])
    sleep = Mock()
    alert = Mock()
    monkeypatch.setattr(monitor, "fetch_json", fetch_json)
    monkeypatch.setattr(monitor.time, "sleep", sleep)
    monkeypatch.setattr(monitor, "alert", alert)

    assert monitor.check_db_health() is True

    assert fetch_json.call_args_list == [
        (("http://127.0.0.1:8000/health/db",), {"timeout": 10}),
        (("http://127.0.0.1:8000/health/db",), {"timeout": 10}),
    ]
    sleep.assert_called_once_with(2)
    alert.assert_not_called()
    assert "DB HEALTH RETRY: TimeoutError: slow" in capsys.readouterr().out


def test_db_health_alerts_only_after_second_failure(monkeypatch):
    fetch_json = Mock(side_effect=[TimeoutError("first"), RuntimeError("second")])
    sleep = Mock()
    alert = Mock()
    monkeypatch.setattr(monitor, "fetch_json", fetch_json)
    monkeypatch.setattr(monitor.time, "sleep", sleep)
    monkeypatch.setattr(monitor, "alert", alert)

    assert monitor.check_db_health() is False

    assert fetch_json.call_count == 2
    assert all(call.kwargs == {"timeout": 10} for call in fetch_json.call_args_list)
    sleep.assert_called_once_with(2)
    alert.assert_called_once()
    key, details = alert.call_args.args
    assert key == "health:db:connection"
    assert "RuntimeError: second" in details


def test_stale_backup_is_logged_without_telegram_reminders(monkeypatch, tmp_path, capsys):
    send, clock = configure_alert_state(monkeypatch, tmp_path)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    dump = backup_dir / "totish-daily-2026-10-28.dump"
    dump.write_bytes(b"test dump")
    clock[0] = dump.stat().st_mtime + 36 * 3600 + 1

    assert monitor.check_database_backup() is False
    clock[0] += 300
    assert monitor.check_database_backup() is False

    send.assert_not_called()
    assert "backup:stale" in capsys.readouterr().out
    assert not monitor.RECOVERY_STATE_FILE.exists()
