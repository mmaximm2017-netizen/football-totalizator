from pathlib import Path

from scripts.monitor_recovery_state import (
    incident_family,
    load_active_incidents,
    recover_incident,
    remember_incident,
)


def test_incident_family_groups_variants():
    assert incident_family("health:public:unavailable") == "health:public"
    assert incident_family("health:db:connection") == "health:db"
    assert incident_family("container:missing") == "container"
    assert incident_family("backup:stale") == "backup"


def test_recovery_is_sent_once(tmp_path: Path):
    state = tmp_path / "incidents.json"
    messages = []
    remember_incident(state, "health:public:unavailable")

    assert recover_incident(state, "health:public", messages.append) is True
    assert len(messages) == 1
    assert "работа восстановлена" in messages[0]
    assert "health:public:unavailable" in messages[0]
    assert load_active_incidents(state) == {}

    assert recover_incident(state, "health:public", messages.append) is False
    assert len(messages) == 1


def test_new_error_replaces_same_family_variant(tmp_path: Path):
    state = tmp_path / "incidents.json"
    remember_incident(state, "health:db:connection")
    remember_incident(state, "health:db:ranking")
    assert load_active_incidents(state) == {"health:db": "health:db:ranking"}
