import json
from datetime import datetime, timezone

from scripts import db_activity_gate


def epoch_for_minute(minute):
    return int(datetime(2026, 9, 14, 8, minute, tzinfo=timezone.utc).timestamp())


def write_state(path, **overrides):
    now = overrides.pop("generated_at", epoch_for_minute(7))
    value = {
        "generated_at": now,
        "active": False,
        "active_until": 0,
        "next_due": now + 3600,
    }
    value.update(overrides)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_gate_missing_state_fails_open(tmp_path):
    due, reason = db_activity_gate.should_run(tmp_path / "missing.json", epoch_for_minute(7))
    assert due is True
    assert reason == "missing_plan_fail_open"


def test_gate_idle_skips_until_next_due(tmp_path):
    state = tmp_path / "gate.json"
    now = epoch_for_minute(7)
    write_state(state, generated_at=now, next_due=now + 1800)
    assert db_activity_gate.should_run(state, now) == (False, "idle")


def test_gate_active_window_runs(tmp_path):
    state = tmp_path / "gate.json"
    now = epoch_for_minute(7)
    write_state(
        state,
        generated_at=now,
        active=True,
        active_until=now + 1200,
        next_due=0,
    )
    assert db_activity_gate.should_run(state, now) == (True, "active_window")


def test_gate_next_due_fails_open_until_refresh_catches_up(tmp_path):
    state = tmp_path / "gate.json"
    now = epoch_for_minute(7)
    write_state(state, generated_at=now - 1200, next_due=now)
    assert db_activity_gate.should_run(state, now) == (True, "next_due_fail_open")


def test_gate_stale_idle_state_fails_open(tmp_path):
    state = tmp_path / "gate.json"
    now = epoch_for_minute(7)
    generated = (
        now
        - db_activity_gate.MAX_IDLE_REFRESH_SECONDS
        - db_activity_gate.FAIL_OPEN_GRACE_SECONDS
        - 1
    )
    write_state(state, generated_at=generated, next_due=now + 3600)
    assert db_activity_gate.should_run(state, now) == (True, "stale_plan_fail_open")


def test_refresh_missing_state_is_due(tmp_path):
    assert db_activity_gate.refresh_due(tmp_path / "missing.json", epoch_for_minute(7)) == (
        True,
        "missing_plan",
    )


def test_refresh_idle_waits_for_next_due(tmp_path):
    state = tmp_path / "gate.json"
    now = epoch_for_minute(7)
    write_state(state, generated_at=now, next_due=now + 1800)
    assert db_activity_gate.refresh_due(state, now) == (False, "idle_wait")


def test_refresh_at_next_due(tmp_path):
    state = tmp_path / "gate.json"
    now = epoch_for_minute(7)
    write_state(state, generated_at=now - 1200, next_due=now)
    assert db_activity_gate.refresh_due(state, now) == (True, "next_due")


def test_refresh_idle_watchdog_is_hourly(tmp_path):
    state = tmp_path / "gate.json"
    now = epoch_for_minute(7)
    write_state(
        state,
        generated_at=now - db_activity_gate.MAX_IDLE_REFRESH_SECONDS,
        next_due=now + 7200,
    )
    assert db_activity_gate.refresh_due(state, now) == (True, "idle_watchdog")


def test_refresh_active_plan_every_fifteen_minutes(tmp_path):
    state = tmp_path / "gate.json"
    now = epoch_for_minute(7)
    write_state(
        state,
        generated_at=now - db_activity_gate.ACTIVE_REFRESH_SECONDS,
        active=True,
        active_until=now + 600,
        next_due=0,
    )
    assert db_activity_gate.refresh_due(state, now) == (True, "active_refresh")
