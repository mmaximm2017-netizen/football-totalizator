import json
from datetime import datetime

from scripts import db_activity_gate


def epoch_for_minute(minute):
    return int(datetime(2026, 9, 14, 8, minute).timestamp())


def test_gate_baseline_runs_without_state(tmp_path):
    due, reason = db_activity_gate.should_run(tmp_path / "missing.json", epoch_for_minute(15))
    assert due is True
    assert reason == "baseline"


def test_gate_missing_state_fails_open(tmp_path):
    due, reason = db_activity_gate.should_run(tmp_path / "missing.json", epoch_for_minute(7))
    assert due is True
    assert reason == "missing_plan_fail_open"


def test_gate_idle_skips(tmp_path):
    state = tmp_path / "gate.json"
    now = epoch_for_minute(7)
    state.write_text(json.dumps({"generated_at": now, "active": False, "active_until": 0}))
    assert db_activity_gate.should_run(state, now) == (False, "idle")


def test_gate_active_window_runs(tmp_path):
    state = tmp_path / "gate.json"
    now = epoch_for_minute(7)
    state.write_text(json.dumps({"generated_at": now, "active": True, "active_until": now + 1200}))
    assert db_activity_gate.should_run(state, now) == (True, "active_window")


def test_gate_stale_state_fails_open(tmp_path):
    state = tmp_path / "gate.json"
    now = epoch_for_minute(7)
    state.write_text(json.dumps({
        "generated_at": now - db_activity_gate.PLAN_TTL_SECONDS - 1,
        "active": False,
        "active_until": 0,
    }))
    assert db_activity_gate.should_run(state, now) == (True, "stale_plan_fail_open")
