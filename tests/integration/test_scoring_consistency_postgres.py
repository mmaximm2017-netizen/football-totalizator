"""Scoring lifecycle proof against CI's disposable PostgreSQL, never production."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from flask import Flask

from app import db
from app.routes import admin_matches as admin
from app.services import scoring_recalculation_service as scoring
from app.services.profile_stats_service import get_profile_stats
from app.services.ranking_service import get_tournament_ranking
from app.services.top_scorer_service import get_tournament_top_scorers
from test_auto_result_postgres import finalize, match, pg, sql  # noqa: F401


def finish(home=2, away=1):
    conn = db.get_db()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE matches SET status='FINISHED',home_score=%s,away_score=%s WHERE id=401", (home, away))
        scoring.recalc_match_points(401, conn=conn, cur=cur)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        db.close_db(conn, cur)


def assert_points(expected):
    assert sql('SELECT points FROM predictions WHERE match_id=401 ORDER BY user_id') == [(expected,)]


def test_standalone_recalc_serializes_correction_after_score_read(pg, monkeypatch):
    finish()
    read = threading.Event()
    release = threading.Event()
    pid_ready = threading.Event()
    pids = []
    original = scoring.calculate_points

    def paused(*args):
        if threading.current_thread().name.startswith('stale'):
            read.set()
            assert release.wait(10)
        return original(*args)

    def correction():
        conn = db.get_db()
        cur = conn.cursor()
        try:
            pids.append(conn.get_backend_pid())
            pid_ready.set()
            cur.execute("UPDATE matches SET home_score=1,away_score=1 WHERE id=401")
            scoring.recalc_match_points(401, conn=conn, cur=cur)
            conn.commit()
        finally:
            db.close_db(conn, cur)

    monkeypatch.setattr(scoring, 'calculate_points', paused)
    with ThreadPoolExecutor(1, thread_name_prefix='stale') as a, ThreadPoolExecutor(1) as b:
        first = a.submit(scoring.recalc_match_points, 401)
        try:
            assert read.wait(5)
            second = b.submit(correction)
            assert pid_ready.wait(5)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if sql("SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s", (pids[0],)) == [('Lock',)]:
                    break
                time.sleep(.02)
            else:
                pytest.fail('Correction did not wait for the recalc match lock (old implementation)')
        finally:
            release.set()
        first.result(timeout=10)
        second.result(timeout=10)
    assert sql('SELECT home_score,away_score FROM matches WHERE id=401') == [(1, 1)]
    assert_points(2)


def invoke(handler, data):
    app = Flask(__name__)
    app.secret_key = 'test'
    with app.test_request_context('/admin/test', method='POST', data={
        'match_id': '401', 'return_to': '/admin/test', **data,
    }):
        response = handler.__wrapped__()
        assert response.status_code == 302


def edit_status(status):
    invoke(admin.admin_edit_match, {
        'home_team': 'Зенит', 'away_team': 'ЦСКА',
        'match_date': '2026-01-01', 'match_time': '12:00', 'status': status,
    })


@pytest.mark.parametrize('unfinished', ['SCHEDULED', 'LIVE', 'TIMED', 'POSTPONED', 'CANCELLED'])
@pytest.mark.parametrize('correct', [False, True])
def test_real_admin_lifecycle_and_repeated_recalc(pg, unfinished, correct):
    finish()
    for _ in range(2):
        edit_status(unfinished)
        assert_points(0)
        assert get_profile_stats(1, 5)['submitted_count'] == 0
        assert get_tournament_ranking(5)[0]['points'] == 0
        if correct:
            invoke(admin.admin_fix_result, {'home_score': '1', 'away_score': '1'})
            assert_points(0)
        edit_status('FINISHED')
        expected = 2 if correct else 10
        assert_points(expected)
        for _ in range(10):
            scoring.recalc_match_points(401)
        assert_points(expected)
        assert get_profile_stats(1, 5)['total_points'] == expected
        assert get_tournament_ranking(5)[0]['points'] == expected
        assert bool(get_tournament_top_scorers(5)) is (not correct)


@pytest.mark.parametrize('league', ['rpl', 'rcup'])
@pytest.mark.parametrize('score', [(2, 1), (None, None), (2, None), (-1, 0), (100, 0)])
def test_visibility_restores_valid_result_only(pg, monkeypatch, league, score):
    sql('UPDATE matches SET league=%s,home_score=%s,away_score=%s WHERE id=401', (league, *score), write=True)
    if score == (2, 1):
        finish()
    helper = 'get_required_rpl_tournament' if league == 'rpl' else 'get_required_russian_cup_tournament'
    monkeypatch.setattr(admin, helper, lambda cur: {'id': 5})
    handler = admin.admin_russia_2027_visibility if league == 'rpl' else admin.admin_russian_cup_visibility
    for _ in range(2):
        invoke(handler, {'visibility_action': 'hide'})
        assert_points(0)
        assert get_profile_stats(1, 5)['submitted_count'] == 0
        invoke(handler, {'visibility_action': 'restore'})
        valid = score == (2, 1)
        assert sql('SELECT status,home_score,away_score FROM matches WHERE id=401') == [('FINISHED' if valid else 'SCHEDULED', *score)]
        assert_points(10 if valid else 0)
        assert get_tournament_ranking(5)[0]['points'] == (10 if valid else 0)
        assert get_profile_stats(1, 5)['submitted_count'] == int(valid)
        assert bool(get_tournament_top_scorers(5)) == valid


@pytest.mark.parametrize('active', [0, 1])
def test_archived_quality_scope_matches_canonical_participants(pg, active):
    sql('UPDATE tournaments SET is_active=%s WHERE id=5', (active,), write=True)
    sql("INSERT INTO users(id,username,password,is_deleted) VALUES(2,'historical','unused',1)", write=True)
    sql('INSERT INTO predictions VALUES(2,401,5,2,1,0)', write=True)
    sql('UPDATE predictions SET home_goals=0,away_goals=2 WHERE user_id=1', write=True)
    finish()
    stats = get_profile_stats(1, 5)
    expected = 1 if active else 2
    assert len(get_tournament_ranking(5)) == expected
    assert len(get_tournament_top_scorers(5)) == (0 if active else 1)
    for name in ('correct_outcome', 'seven_plus', 'zero_points'):
        assert stats['quality'][name]['rank'] == {'place': expected, 'total': expected}


def test_rollback_after_points_update_reverts_result_and_every_prediction(pg, monkeypatch):
    sql("INSERT INTO users(id,username,password) VALUES(2,'second','unused')", write=True)
    sql('INSERT INTO predictions VALUES(2,401,5,1,1,0)', write=True)
    finish()
    def fail(cur, *args):
        cur.execute('SELECT 1/0')
    monkeypatch.setattr(scoring, '_enqueue_result_events', fail)
    with pytest.raises(Exception):
        finish(1, 1)
    assert sql('SELECT home_score,away_score FROM matches WHERE id=401') == [(2, 1)]
    assert sql('SELECT points FROM predictions ORDER BY user_id') == [(10,), (2,)]


def test_two_recalcs_are_idempotent_and_other_match_is_not_locked(pg):
    finish()
    sql("INSERT INTO matches(id,tournament_id,status,home_score,away_score) VALUES(402,5,'FINISHED',0,0)", write=True)
    conn = db.get_db()
    cur = conn.cursor()
    cur.execute('SELECT id FROM matches WHERE id=401 FOR UPDATE')
    try:
        with ThreadPoolExecutor(2) as executor:
            other = executor.submit(scoring.recalc_match_points, 402)
            assert other.result(timeout=3)['found']
    finally:
        conn.rollback()
        db.close_db(conn, cur)
    with ThreadPoolExecutor(2) as executor:
        tasks = [executor.submit(scoring.recalc_match_points, 401) for _ in range(2)]
        for task in tasks:
            task.result(timeout=5)
    assert_points(10)
    assert sql("SELECT count(*) FROM push_delivery_log WHERE event_type='match_result'") == [(1,)]


def test_auto_and_manual_result_produce_same_derived_stats(pg):
    assert finalize(match()) == 'saved'
    before = (get_profile_stats(1, 5), get_tournament_ranking(5), get_tournament_top_scorers(5))
    finish(1, 1)
    assert_points(2)
    finish(2, 1)
    assert_points(10)
    assert (get_profile_stats(1, 5), get_tournament_ranking(5), get_tournament_top_scorers(5)) == before
