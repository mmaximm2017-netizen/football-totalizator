from types import SimpleNamespace

from scripts import monitor_production as monitor


def isolated_state(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "STATE_DIR", tmp_path)
    monkeypatch.setattr(monitor, "STATE_FILE", tmp_path / "dedupe.json")
    monkeypatch.setattr(monitor, "RECOVERY_STATE_FILE", tmp_path / "recovery.json")
    messages = []
    monkeypatch.setattr(monitor, "send_message", messages.append)
    return messages


def test_recovery_is_sent_once_only_after_real_alert(monkeypatch, tmp_path):
    messages = isolated_state(monkeypatch, tmp_path)

    assert monitor.recover("health_db") is False
    assert messages == []

    assert monitor.alert("health:db:connection", "database unavailable") is True
    assert monitor.alert("health:db:connection", "database unavailable") is False
    assert len(messages) == 1
    assert "🚨 ТОТИШ: проблема с базой данных" in messages[0]

    assert monitor.recover("health_db") is True
    assert len(messages) == 2
    assert "✅ ТОТИШ: база данных снова доступна" in messages[1]
    assert "recovered_from=health:db:connection" in messages[1]

    assert monitor.recover("health_db") is False
    assert len(messages) == 2


def test_db_check_fail_fail_ok_ok_sends_one_alert_and_one_recovery(monkeypatch, tmp_path):
    messages = isolated_state(monkeypatch, tmp_path)
    monkeypatch.setattr(monitor.time, "sleep", lambda _: None)

    values = iter([
        TimeoutError("first"),
        TimeoutError("second"),
        {"db": "ok", "active_tournament": "ok", "ranking": "ok"},
        {"db": "ok", "active_tournament": "ok", "ranking": "ok"},
    ])

    def fetch_json(*args, **kwargs):
        value = next(values)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(monitor, "fetch_json", fetch_json)

    assert monitor.check_db_health() is False
    assert monitor.check_db_health() is True
    assert monitor.check_db_health() is True

    assert len(messages) == 2
    assert "🚨 ТОТИШ: проблема с базой данных" in messages[0]
    assert "✅ ТОТИШ: база данных снова доступна" in messages[1]


def test_auto_result_monitor_recovery_is_transition_only(monkeypatch, tmp_path):
    messages = isolated_state(monkeypatch, tmp_path)
    results = iter([
        SimpleNamespace(returncode=1, stdout="", stderr="OperationalError"),
        SimpleNamespace(returncode=0, stdout="", stderr=""),
        SimpleNamespace(returncode=0, stdout="", stderr=""),
    ])
    monkeypatch.setattr(monitor, "run", lambda *args, **kwargs: next(results))

    assert monitor.check_auto_results() is False
    assert monitor.check_auto_results() is True
    assert monitor.check_auto_results() is True

    assert len(messages) == 2
    assert "auto_results:monitor_failed" in messages[0]
    assert "✅ ТОТИШ: автоматическая проверка результатов восстановилась" in messages[1]


def test_different_failure_subtypes_share_one_recovery_family(monkeypatch, tmp_path):
    messages = isolated_state(monkeypatch, tmp_path)

    assert monitor.alert("health:public:unavailable", "timeout") is True
    assert monitor.alert("health:public:bad_status", "bad status") is True
    assert monitor.recover("health_public") is True

    assert len(messages) == 3
    assert "recovered_from=health:public:bad_status" in messages[-1]
    assert monitor.recover("health_public") is False
