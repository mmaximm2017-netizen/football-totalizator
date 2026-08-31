from unittest.mock import Mock

from scripts import monitor_production as monitor


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
