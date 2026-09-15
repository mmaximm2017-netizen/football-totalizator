import json
from pathlib import Path

from scripts.db_activity_gate import monitor_due


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_hourly_monitor_skips_valid_idle_plan(tmp_path):
    state = tmp_path / "plan.json"
    recovery = tmp_path / "recovery.json"
    write_json(state, {"generated_at": 1000, "active": False, "active_until": 0, "next_due": 5000})
    write_json(recovery, {})
    assert monitor_due(state, recovery, now=1200) == (False, "idle")


def test_hourly_monitor_runs_in_active_window(tmp_path):
    state = tmp_path / "plan.json"
    recovery = tmp_path / "recovery.json"
    write_json(state, {"generated_at": 1000, "active": True, "active_until": 2000, "next_due": 0})
    write_json(recovery, {})
    assert monitor_due(state, recovery, now=1200) == (True, "active_window")


def test_hourly_monitor_runs_for_db_recovery_probe(tmp_path):
    state = tmp_path / "plan.json"
    recovery = tmp_path / "recovery.json"
    write_json(state, {"generated_at": 1000, "active": False, "active_until": 0, "next_due": 5000})
    write_json(recovery, {"health_db": {"key": "health:db:connection"}})
    assert monitor_due(state, recovery, now=1200) == (True, "recovery_probe")


def test_hourly_monitor_fails_open_without_plan(tmp_path):
    recovery = tmp_path / "recovery.json"
    write_json(recovery, {})
    assert monitor_due(tmp_path / "missing.json", recovery, now=1200) == (
        True,
        "missing_plan_fail_open",
    )
