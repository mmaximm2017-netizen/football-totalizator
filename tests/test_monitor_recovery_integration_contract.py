from pathlib import Path


def test_production_monitor_tracks_and_recovers_all_incident_families():
    source = Path("scripts/monitor_production.py").read_text(encoding="utf-8")

    assert "remember_incident(INCIDENT_STATE_FILE, key)" in source

    for family in (
        "container",
        "health:local",
        "health:db",
        "control_plane",
        "backup",
        "health:public",
    ):
        assert (
            f'recover_incident(INCIDENT_STATE_FILE, "{family}", send_message)'
            in source
        )
