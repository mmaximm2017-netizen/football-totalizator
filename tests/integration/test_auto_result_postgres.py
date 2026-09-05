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
        cur.execute(query, params)
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
    observation = worker.Observation('test', 'finished', home_score=2, away_score=1)
    monkeypatch.setattr(worker.PageCache, 'load_many', lambda *a: None)
    monkeypatch.setattr(worker, 'observe_match', lambda *a: (observation, observation))


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
    sql("UPDATE matches SET kickoff_time=clock_timestamp()-interval '181 minutes'", write=True)
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
    sql("UPDATE matches SET kickoff_time=clock_timestamp()-interval '220 minutes'", write=True)
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
