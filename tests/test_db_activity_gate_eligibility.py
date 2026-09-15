from scripts import db_activity_gate


def test_auto_result_gate_matches_worker_supported_scopes():
    predicate = db_activity_gate.AUTO_RESULT_ELIGIBILITY_SQL

    assert "('SCHEDULED','TIMED','LIVE')" in predicate
    assert "m.tournament_id = 5 AND m.league = 'rpl'" in predicate
    assert "('rpl','national_team')" in predicate
    assert "m.tournament_id = 6 AND m.league = 'rcup'" in predicate
    assert "IN_PLAY" not in predicate
    assert "PAUSED" not in predicate
    assert "HALFTIME" not in predicate


def test_auto_result_gate_reuses_eligibility_for_active_and_next_due():
    import inspect

    source = inspect.getsource(db_activity_gate.build_plan)
    assert source.count("AUTO_RESULT_ELIGIBILITY_SQL") == 2
