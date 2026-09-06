"""Real PostgreSQL regressions. CI supplies an explicit, disposable DSN only."""
import os
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import psycopg2
import pytest
from psycopg2.pool import ThreadedConnectionPool

from app import db
from app.services import auto_result_delivery_service as delivery
from app.services import auto_result_finalization_service as finalizer
from scripts import auto_result_runtime as runtime
from scripts.migrate_push_delivery_log import DDL as PUSH_DELIVERY_DDL

worker = runtime.dry
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def pg(monkeypatch):
    dsn = os.getenv("AUTO_RESULTS_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Explicit disposable PostgreSQL DSN required; enabled in CI")
    schema = "auto_result_test_" + uuid.uuid4().hex
    admin = psycopg2.connect(dsn)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE SCHEMA "{schema}"')
    pool = ThreadedConnectionPool(1, 3, dsn=dsn, options=f"-csearch_path={schema}")
    monkeypatch.setattr(db, "db_pool", pool)
    try:
        conn = db.get_db()
        with conn.cursor() as cur:
            for path in sorted((ROOT / "migrations").glob("*.sql")):
                cur.execute(path.read_text())
            for statement in PUSH_DELIVERY_DDL:
                cur.execute(statement)
            cur.execute("INSERT INTO tournaments (id,name) VALUES (5,'RPL'),(6,'Cup')")
            cur.execute("INSERT INTO users (id,username,password) VALUES (1,'audit','unused')")
            cur.execute(
                "INSERT INTO matches (id,tournament_id,league,home_team,away_team,"
                "kickoff_time,deadline,status,match_category) VALUES "
                "(401,5,'rpl','Зенит','ЦСКА',clock_timestamp()-interval '125 minutes',"
                "clock_timestamp()-interval '130 minutes','SCHEDULED','rpl')"
            )
            cur.execute("INSERT INTO predictions VALUES (1,401,5,2,1,0)")
        conn.commit()
        db.close_db(conn)
        yield pool
    finally:
        pool.closeall()
        with admin.cursor() as cur:
            cur.execute(f'DROP SCHEMA "{schema}" CASCADE')
        admin.close()


def sql(query, params=(), *, write=False):
    conn = db.get_db()
    cur = conn.cursor()
    try:
        cur.execute(query, params or None)
        rows = cur.fetchall() if cur.description else []
        if write:
            conn.commit()
        return rows
    finally:
        db.close_db(conn, cur)


def match():
    return worker._load_matches(datetime.now(timezone.utc))[0]


def finalize(m):
    return finalizer.finalize_auto_result(
        m['id'], 2, 1, tournament_id=m['tournament_id'], league=m['league'],
        expected_home_team=m['home_team'], expected_away_team=m['away_team'],
        expected_kickoff_time=m['kickoff_time'], expected_match_category=m['match_category'],
    )


def consensus(monkeypatch):
    observation = worker.Observation('first', 'finished', home_score=2, away_score=1)
    monkeypatch.setattr(worker.PageCache, 'load_many', lambda *a: None)
    monkeypatch.setattr(worker, 'observe_match', lambda *a: (observation, worker.Observation('second', 'finished', home_score=2, away_score=1)))


def test_readonly_pool_to_real_for_update_score_and_push(pg):
    conn = db.get_db()
    pid = conn.get_backend_pid()
    db.close_db(conn)
    m = match()  # actual readonly selection -> actual pool return
    conn = db.get_db()
    assert conn.get_backend_pid() == pid  # test MUST reuse the same backend
    assert conn.readonly is False
    db.close_db(conn)
    assert finalize(m) == 'saved'  # real FOR UPDATE + UPDATE + scoring + commit
    assert sql('SELECT status,home_score,away_score FROM matches') == [('FINISHED',2,1)]
    assert sql('SELECT points FROM predictions') == [(10,)]
    assert sql("SELECT status FROM push_delivery_log WHERE event_type='match_result'") == [('ready',)]
    assert len(sql('SELECT event_key FROM auto_result_notifications')) == 1


@pytest.mark.parametrize('category,scope', [(None,'rpl'),('','rpl'),('rpl','rpl'),('national_team','national'),('other',None)])
def test_category_selection_matches_classification(pg, category, scope):
    sql('UPDATE matches SET match_category=%s', (category,), write=True)
    found = worker._load_matches(datetime.now(timezone.utc))
    assert ([m['scope'] for m in found]) == ([scope] if scope else [])


def test_manual_result_committed_while_worker_waits(pg):
    m = match()
    admin = db.get_db()
    cur = admin.cursor()
    cur.execute("UPDATE matches SET status='FINISHED',home_score=0,away_score=3 WHERE id=401")
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(finalize, m)
            try:
                # Observe a real lock wait, rather than assuming thread timing.
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    waiting = sql("SELECT count(*) FROM pg_stat_activity WHERE pid<>pg_backend_pid() "
                                  "AND wait_event_type='Lock' AND query LIKE '%FOR UPDATE%'")[0][0]
                    if waiting:
                        break
                    time.sleep(.02)
                assert waiting
            finally:
                admin.commit()
            assert future.result(timeout=5) == 'already_done'
    finally:
        db.close_db(admin, cur)
    assert sql('SELECT home_score,away_score FROM matches') == [(0,3)]
    assert sql('SELECT * FROM auto_result_notifications') == []


def test_strict_database_time_rejects_late_finalization(pg):
    m = match()
    sql("UPDATE matches SET kickoff_time=clock_timestamp()-interval '361 minutes'", write=True)
    m['kickoff_time'] = sql('SELECT kickoff_time FROM matches')[0][0]
    assert finalize(m) == 'window_expired'
    assert sql('SELECT home_score FROM matches') == [(None,)]


def test_scoring_delay_cannot_commit_after_window(pg, monkeypatch):
    m = match()
    original = finalizer.recalc_match_points
    def crosses_boundary(*args, **kwargs):
        original(*args, **kwargs)
        # Deterministically advance the finalizer's window predicate, without
        # weakening the first real database-time check.
        monkeypatch.setattr(finalizer, '_inside_window', lambda *a: False)
    monkeypatch.setattr(finalizer, 'recalc_match_points', crosses_boundary)
    assert finalize(m) == 'window_expired'
    assert sql('SELECT home_score FROM matches') == [(None,)]
    assert sql('SELECT points FROM predictions') == [(0,)]
    assert sql('SELECT * FROM push_delivery_log') == []


@pytest.mark.parametrize('failure', ['outbox', 'process_interruption'])
def test_commit_survives_notification_failure_and_next_run_never_refinalizes(pg, monkeypatch, tmp_path, failure):
    consensus(monkeypatch)
    m = match()
    assert finalize(m) == 'saved'
    if failure == 'outbox':
        with patch.object(Path, 'write_text', side_effect=OSError('disk unavailable')), pytest.raises(OSError):
            delivery.flush_notifications(tmp_path / 'outbox')
    # process_interruption: deliberately do not run any post-commit code.
    assert sql('SELECT queued_at FROM auto_result_notifications') == [(None,)]
    (tmp_path / 'state.json').write_text('{corrupted')
    with patch.object(finalizer, 'finalize_auto_result', side_effect=AssertionError('must not write twice')):
        runtime.run_live(datetime.now(timezone.utc), state_path=tmp_path / 'state.json', outbox=None)
    delivery.flush_notifications(tmp_path / 'outbox')
    assert len(list((tmp_path / 'outbox').glob('*.msg'))) == 1
    assert sql('SELECT home_score,away_score FROM matches') == [(2,1)]
    assert sql('SELECT points FROM predictions') == [(10,)]
    assert sql('SELECT count(*) FROM push_delivery_log') == [(1,)]


def test_real_failed_transaction_retries_with_fresh_consensus(pg, monkeypatch, tmp_path):
    consensus(monkeypatch)
    original = finalizer.recalc_match_points
    def database_error(*args, **kwargs):
        kwargs['cur'].execute('SELECT 1/0')
    monkeypatch.setattr(finalizer, 'recalc_match_points', database_error)
    runtime.run_live(datetime.now(timezone.utc), state_path=tmp_path/'state', outbox=None)
    assert sql('SELECT home_score FROM matches') == [(None,)]
    monkeypatch.setattr(finalizer, 'recalc_match_points', original)
    runtime.run_live(datetime.now(timezone.utc), state_path=tmp_path/'state', outbox=None)
    assert sql('SELECT home_score FROM matches') == [(2,)]


def test_two_workers_database_lock(pg):
    with delivery.worker_lock() as first:
        assert first
        with delivery.worker_lock() as second:
            assert not second
    with delivery.worker_lock() as later:
        assert later


def test_missed_window_detected_without_worker_json_and_reason_is_honest(pg, monkeypatch):
    monkeypatch.setenv('AUTO_RESULTS_ENABLED','true')
    monkeypatch.setenv('AUTO_RESULTS_DRY_RUN','false')
    sql("UPDATE auto_result_monitor SET enabled_since=clock_timestamp()-interval '1 day'", write=True)
    sql("UPDATE matches SET kickoff_time=clock_timestamp()-interval '400 minutes'", write=True)
    runtime.monitor(datetime.now(timezone.utc))
    runtime.monitor(datetime.now(timezone.utc))
    rows = sql('SELECT message FROM auto_result_notifications')
    assert len(rows) == 1
    assert 'Нет сохранённых проверок' in rows[0][0]


def test_liveness_only_when_matches_need_worker(pg, monkeypatch):
    monkeypatch.setenv('AUTO_RESULTS_ENABLED','true')
    monkeypatch.setenv('AUTO_RESULTS_DRY_RUN','false')
    sql("UPDATE matches SET kickoff_time=clock_timestamp()-interval '140 minutes'", write=True)
    runtime.monitor(datetime.now(timezone.utc))
    assert len(sql('SELECT * FROM auto_result_notifications')) == 1
    sql('DELETE FROM auto_result_notifications', write=True)
    sql("UPDATE matches SET status='FINISHED',home_score=0,away_score=0", write=True)
    runtime.monitor(datetime.now(timezone.utc))
    assert sql('SELECT * FROM auto_result_notifications') == []


def test_final_reason_uses_durable_identity_bound_evidence(pg, monkeypatch):
    monkeypatch.setenv('AUTO_RESULTS_ENABLED','true')
    monkeypatch.setenv('AUTO_RESULTS_DRY_RUN','false')
    m = match()
    delivery.record_check(m, datetime.now(timezone.utc), 'sports: не нашёл матч')
    runtime._expired(m)
    assert 'sports: не нашёл матч' in sql('SELECT message FROM auto_result_notifications')[0][0]
    sql('DELETE FROM auto_result_notifications', write=True)
    sql("UPDATE matches SET away_team='Ростов'", write=True)
    m = match()
    runtime._expired(m)
    assert 'причина неизвестна' in sql('SELECT message FROM auto_result_notifications')[0][0]


def test_final_notice_does_not_request_manual_result_already_saved(pg):
    m = match()
    assert finalize(m) == 'saved'
    runtime._expired(m)
    assert sql("SELECT count(*) FROM auto_result_notifications WHERE event_key LIKE 'expired:%'") == [(0,)]


@pytest.mark.parametrize('field,value', [('home_team','Ростов'),('match_category','national_team'),('league','other')])
def test_identity_change_after_lookup_never_writes(pg, field, value):
    m = match()
    sql(f'UPDATE matches SET {field}=%s', (value,), write=True)
    with pytest.raises(finalizer.AutoResultFinalizeError):
        finalize(m)
    assert sql('SELECT home_score FROM matches') == [(None,)]


def test_lost_commit_acknowledgement_does_not_request_manual_input(pg, monkeypatch, tmp_path):
    consensus(monkeypatch)
    original = finalizer.finalize_auto_result
    def committed_then_disconnected(*args, **kwargs):
        assert original(*args, **kwargs) == 'saved'
        raise psycopg2.OperationalError('lost acknowledgement')
    monkeypatch.setattr(finalizer,'finalize_auto_result',committed_then_disconnected)
    runtime.run_live(datetime.now(timezone.utc),state_path=tmp_path/'state',outbox=None)
    assert sql('SELECT home_score FROM matches') == [(2,)]
    assert all('внесите' not in r[0] and 'Нужен ручной' not in r[0]
               for r in sql('SELECT message FROM auto_result_notifications'))
    runtime.run_live(datetime.now(timezone.utc),state_path=tmp_path/'state',outbox=None)
    assert sql('SELECT count(*) FROM push_delivery_log') == [(1,)]


def test_actual_process_exit_immediately_after_commit(pg, tmp_path):
    child_code = """
import os
from datetime import datetime, timezone
from scripts.auto_result_worker import _load_matches
from app.services.auto_result_finalization_service import finalize_auto_result
m = _load_matches(datetime.now(timezone.utc))[0]
result = finalize_auto_result(
    m['id'], 2, 1, tournament_id=5, league='rpl',
    expected_home_team=m['home_team'], expected_away_team=m['away_team'],
    expected_kickoff_time=m['kickoff_time'], expected_match_category=m['match_category'],
)
os._exit(73 if result == 'saved' else 74)
"""
    dsn = psycopg2.extensions.make_dsn(
        os.environ['AUTO_RESULTS_TEST_DATABASE_URL'], options=pg._kwargs['options'],
    )
    child = subprocess.run([sys.executable, '-c', child_code],
                           env={**os.environ, 'DATABASE_URL': dsn},
                           cwd=ROOT, capture_output=True, text=True, timeout=15, check=False)
    assert child.returncode == 73, child.stderr
    assert worker._load_matches(datetime.now(timezone.utc)) == []
    assert sql('SELECT points FROM predictions') == [(10,)]
    delivery.flush_notifications(tmp_path)
    assert len(list(tmp_path.glob('*.msg'))) == 1


def test_live_processing_survives_non_utf8_state(pg, monkeypatch, tmp_path):
    consensus(monkeypatch)
    state = tmp_path / 'state.json'
    state.write_bytes(b'\xff\xfe{\x00}')
    runtime.run_live(datetime.now(timezone.utc), state_path=state, outbox=None)
    assert sql('SELECT status,home_score,away_score FROM matches') == [('FINISHED', 2, 1)]
    assert sql('SELECT points FROM predictions') == [(10,)]
    assert len(sql('SELECT event_key FROM auto_result_notifications')) == 1
    # Normal atomic diagnostic publication replaces the invalid bytes.
    assert isinstance(worker._load_state(state), dict)
    with patch.object(finalizer, 'finalize_auto_result', side_effect=AssertionError('duplicate')):
        runtime.run_live(datetime.now(timezone.utc), state_path=state, outbox=None)


@pytest.mark.parametrize('second_status,second_score', [('not_finished', None), ('finished', (0, 0))])
def test_manual_result_during_lookup_suppresses_observation_notice(pg, monkeypatch, tmp_path, second_status, second_score):
    monkeypatch.setattr(worker.PageCache, 'load_many', lambda *a: None)
    def observe(*args):
        # A real independent transaction commits while the worker is looking up
        # sources; the live loop still holds its previously loaded candidate.
        sql("UPDATE matches SET status='FINISHED',home_score=3,away_score=0 WHERE id=401", write=True)
        first = worker.Observation('first', 'finished', home_score=2, away_score=1)
        second = worker.Observation('second', second_status,
                                    home_score=second_score[0] if second_score else None,
                                    away_score=second_score[1] if second_score else None)
        return first, second
    monkeypatch.setattr(worker, 'observe_match', observe)
    with patch.object(finalizer, 'finalize_auto_result', side_effect=AssertionError('manual overwrite')):
        runtime.run_live(datetime.now(timezone.utc), state_path=tmp_path / 'state', outbox=None)
    assert sql('SELECT home_score,away_score FROM matches') == [(3, 0)]
    assert sql('SELECT event_key FROM auto_result_notifications') == []


@pytest.mark.parametrize('values,expected', [
    (((2,2),(2,2),(2,2)), 'saved'),
    (((2,2),'source_unavailable',(2,2)), 'saved'),
    (((2,2),'not_found',(2,2)), 'saved'),
    (('source_unavailable',(2,2),(2,2)), 'saved'),
    (((2,2),(3,2),'source_unavailable'), 'score_conflict'),
    (((2,2),(3,2),(2,2)), 'saved'),
    (((2,2),'parser_error','not_found'), 'one_source_confirmed'),
])
def test_v2_quorum_after_old_deadline_real_atomic_score_points_and_diagnostics(pg, monkeypatch, tmp_path, values, expected):
    sql("UPDATE matches SET kickoff_time=clock_timestamp()-interval '210 minutes'", write=True)
    observations = tuple(worker.Observation(name, 'finished', home_score=value[0], away_score=value[1])
                         if isinstance(value, tuple) else worker.Observation(name, value)
                         for name, value in zip(('livesport_rpl','sports_rpl','sportbox_rpl'),values))
    monkeypatch.setattr(worker.PageCache, 'load_many', lambda *a: None)
    monkeypatch.setattr(worker, 'observe_match', lambda *a: observations)
    result = runtime.run_live(datetime.now(timezone.utc),state_path=tmp_path/'state',outbox=None)
    entry=result['matches'][0]
    if expected == 'saved':
        assert entry['write_outcome']=='saved'
        assert sql('SELECT status,home_score,away_score FROM matches') == [('FINISHED',2,2)]
        assert sql('SELECT points FROM predictions') == [(2,)]
        assert sql('SELECT count(*) FROM push_delivery_log') == [(1,)]
        with patch.object(finalizer,'finalize_auto_result',side_effect=AssertionError('duplicate')):
            runtime.run_live(datetime.now(timezone.utc),state_path=tmp_path/'state',outbox=None)
    else:
        assert entry['decision']==expected
        assert sql('SELECT home_score,points FROM matches JOIN predictions ON matches.id=predictions.match_id') == [(None,0)]
        assert sql('SELECT count(*) FROM push_delivery_log') == [(0,)]
    if values == ((2,2),(3,2),(2,2)):
        assert 'sports_rpl: подтвердил 3:2' in delivery.last_check({**match_identity_row()})[1]
        assert sql("SELECT count(*) FROM auto_result_notifications WHERE event_key LIKE 'quorum-conflict:%'") == [(1,)]


def match_identity_row():
    row=sql('SELECT id,tournament_id,league,home_team,away_team,kickoff_time,match_category FROM matches')[0]
    return dict(zip(('id','tournament_id','league','home_team','away_team','kickoff_time','match_category'),row))


def test_v2_soft_notice_once_retry_then_hard_notice_with_durable_evidence(pg, monkeypatch, tmp_path):
    sql("UPDATE matches SET kickoff_time=clock_timestamp()-interval '181 minutes'", write=True)
    monkeypatch.setattr(worker.PageCache,'load_many',lambda *a:None)
    monkeypatch.setattr(worker,'observe_match',lambda *a:(worker.Observation('livesport_rpl','finished',home_score=2,away_score=2),worker.Observation('sports_rpl','not_found'),worker.Observation('sportbox_rpl','source_unavailable')))
    for _ in range(2):
        runtime.run_live(datetime.now(timezone.utc),state_path=tmp_path/'state',outbox=None)
    assert sql("SELECT count(*) FROM auto_result_notifications WHERE event_key LIKE 'delayed:%'") == [(1,)]
    m=match()
    # Keep identity intact, advance only the runtime clock for expiry detection.
    from datetime import timedelta
    runtime.run_live(m['kickoff_time']+timedelta(minutes=361),state_path=tmp_path/'state',outbox=None)
    runtime.run_live(m['kickoff_time']+timedelta(minutes=362),state_path=tmp_path/'state',outbox=None)
    notices=sql("SELECT message FROM auto_result_notifications WHERE event_key LIKE 'expired:%'")
    assert len(notices)==1
    assert 'sports_rpl: не нашёл матч' in notices[0][0] and 'sportbox_rpl: ошибка загрузки' in notices[0][0]
    assert sql('SELECT home_score,away_score FROM matches') == [(None,None)]


def test_v2_database_hard_boundary_rejects_after_scoring_delay(pg, monkeypatch):
    m=match()
    original=finalizer._inside_window
    calls=[]
    def clock_check(cur,kickoff):
        calls.append(1)
        if len(calls)>1:
            # Real PostgreSQL wall-clock check with an expired kickoff, after
            # UPDATE/recalc have run in the same real transaction.
            cur.execute("SELECT clock_timestamp()-interval '361 minutes'")
            kickoff=cur.fetchone()[0]
        return original(cur,kickoff)
    monkeypatch.setattr(finalizer,'_inside_window',clock_check)
    assert finalize(m)=='window_expired'
    assert sql('SELECT status,home_score,away_score FROM matches') == [('SCHEDULED',None,None)]
    assert sql('SELECT points FROM predictions') == [(0,)]
    assert sql('SELECT count(*) FROM push_delivery_log') == [(0,)]
