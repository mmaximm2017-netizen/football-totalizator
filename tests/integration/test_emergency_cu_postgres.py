"""Exercise real production SQL against disposable PostgreSQL, never Neon."""
from datetime import datetime, timedelta, timezone

import pytest

from app.services import match_result_push_service as push
from scripts import db_activity_gate as gate
from scripts import auto_result_worker as worker
from scripts.migrate_push_subscriptions import DDL as SUBSCRIPTIONS_DDL
from test_auto_result_postgres import pg, sql  # noqa: F401


@pytest.mark.parametrize('status', ['SCHEDULED', 'TIMED', 'LIVE', 'IN_PLAY', 'PAUSED',
                                   'HALFTIME', 'FINISHED', 'POSTPONED', 'scheduled', None])
@pytest.mark.parametrize('tid,league,category', [
    (5, 'rpl', 'rpl'), (5, 'rpl', None), (5, 'rpl', ''),
    (5, 'rpl', 'national_team'), (5, 'rpl', 'other'),
    (6, 'rcup', 'other'), (5, 'rcup', 'rpl'), (6, 'rpl', 'rpl'),
])
def test_gate_eligibility_is_actual_worker_eligibility(pg, status, tid, league, category):
    # Status is nullable in production. If a migration ever adds NOT NULL,
    # this assertion should change with both worker and gate, not silently coerce.
    sql('ALTER TABLE matches ALTER COLUMN status DROP NOT NULL', write=True)
    sql('UPDATE matches SET status=%s,tournament_id=%s,league=%s,match_category=%s',
        (status, tid, league, category), write=True)
    expected = bool(worker._load_matches(datetime.now(timezone.utc)))
    plan = gate.build_plan()
    assert plan['active'] is expected
    assert plan['next_due'] == 0
    sql("UPDATE matches SET kickoff_time=clock_timestamp()+interval '1 day'", write=True)
    plan = gate.build_plan()
    assert plan['active'] is False
    assert bool(plan['next_due']) is expected


@pytest.mark.parametrize('minutes,active', [(119, False), (121, True), (359, True),
                                          (366, True), (374, True), (376, False)])
def test_gate_covers_worker_window_and_final_notice(pg, minutes, active):
    sql("UPDATE matches SET kickoff_time=clock_timestamp()-(%s * interval '1 minute')",
        (minutes,), write=True)
    plan = gate.build_plan()
    assert plan['active'] is active
    assert bool(plan['next_due']) is (minutes < worker.FIRST_CHECK_MINUTES)


def test_unsupported_tournament_never_activates_gate(pg):
    sql("INSERT INTO tournaments (id,name) VALUES (7,'Unsupported')", write=True)
    sql('UPDATE matches SET tournament_id=7', write=True)
    assert gate.build_plan()['active'] is False
    sql("UPDATE matches SET kickoff_time=clock_timestamp()+interval '1 day'", write=True)
    assert gate.build_plan()['next_due'] == 0


@pytest.mark.parametrize('status', ['ready', 'pending', 'failed'])
def test_paused_outbox_does_not_replay_with_old_deployed_since(pg, monkeypatch, status):
    for statement in SUBSCRIPTIONS_DDL:
        sql(statement, write=True)
    sql("INSERT INTO push_subscriptions(user_id,endpoint,p256dh,auth) VALUES (1,'https://example.invalid/push','unused','unused')", write=True)
    sql("UPDATE matches SET status='FINISHED',home_score=2,away_score=1", write=True)
    sql('UPDATE predictions SET points=10', write=True)
    old = datetime(2026, 9, 20, tzinfo=timezone.utc)
    resume = push.EMERGENCY_RESUME_AT
    now = resume + timedelta(hours=1)
    sql("INSERT INTO push_delivery_log(user_id,match_id,event_type,event_key,status,sent_at,updated_at) "
        "VALUES (1,401,'match_result','match:401',%s,%s,%s)", (status, old, old), write=True)
    monkeypatch.setenv(push.BOOTSTRAP_CUTOFF_ENV, '2026-08-01T00:00:00Z')
    sent = []
    result = push.run_once(now=now, sender=lambda *args: sent.append(args))
    assert result['sent'] == 0
    assert sent == []
    assert sql('SELECT status,sent_at FROM push_delivery_log') == [(status, old)]
    # Direct claim cannot revive an event selected by an older worker either.
    from app import db
    conn = db.get_db()
    try:
        with conn.cursor() as cur:
            assert not push.claim_delivery(cur, dict(user_id=1, match_id=401,
                                            event_type='match_result', event_key='match:401'), now)
        conn.commit()
    finally:
        db.close_db(conn)
    # Both sides of the cutoff are tested using the same actual SELECT/UPDATE.
    sql('UPDATE push_delivery_log SET sent_at=%s,updated_at=%s',
        (resume - timedelta(microseconds=1), old), write=True)
    assert push.run_once(now=now, dry_run=True)['would_send'] == 0
    sql('UPDATE push_delivery_log SET sent_at=%s,updated_at=%s', (resume, resume), write=True)
    assert push.run_once(now=now, dry_run=True)['would_send'] == 1
    # A later operator cutoff continues to take precedence.
    assert push.run_once(now=now, since=now, dry_run=True)['would_send'] == 0
    result = push.run_once(now=now, sender=lambda *args: sent.append(args))
    assert result['sent'] == 1
    assert len(sent) == 1
    assert push.run_once(now=now, sender=lambda *args: sent.append(args))['sent'] == 0
    assert len(sent) == 1
