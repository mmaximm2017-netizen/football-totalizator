"""Real PostgreSQL regressions for legacy status and result-event boundaries."""
import pytest

from app.routes import admin_matches as admin
from app.services import scoring_recalculation_service as scoring
from app.services.profile_stats_service import get_profile_stats
from test_auto_result_postgres import pg, sql  # noqa: F401
from test_scoring_consistency_postgres import assert_points, edit_status, finish, invoke


@pytest.mark.parametrize('status', ['FINISHED', 'COMPLETE', 'COMPLETED', 'completed', 'SCHEDULED', 'LIVE', 'POSTPONED', 'CANCELLED'])
@pytest.mark.parametrize('score', [(2, 1), (2, None), (-1, 0), (100, 0)])
def test_legacy_recalc_and_real_unfinished_invalidation(pg, status, score):
    finish()
    sql('UPDATE matches SET status=%s,home_score=%s,away_score=%s WHERE id=401', (status, *score), write=True)
    scoring.recalc_match_points(401)
    expected = 10 if status.upper() in ('FINISHED', 'COMPLETE', 'COMPLETED') and score == (2, 1) else 0
    assert_points(expected)
    if expected:
        assert get_profile_stats(1, 5)['total_points'] == 10


@pytest.mark.parametrize('status', ['FINISHED', 'COMPLETE', 'COMPLETED'])
@pytest.mark.parametrize('mode', ['all', 'tournament', 'cup', 'rpl'])
def test_mass_recalc_includes_legacy_finished_matches(pg, monkeypatch, status, mode):
    sql('UPDATE matches SET status=%s,home_score=2,away_score=1 WHERE id=401', (status,), write=True)
    if mode == 'all':
        scoring.recalc_all_points()
    elif mode == 'tournament':
        scoring.recalc_tournament_points(5)
    elif mode == 'cup':
        sql("UPDATE matches SET league='rcup' WHERE id=401", write=True)
        monkeypatch.setattr(admin, 'get_required_russian_cup_tournament', lambda cur: {'id': 5})
        invoke(admin.admin_russian_cup_recalc, {})
    else:
        monkeypatch.setattr(admin, 'get_required_rpl_tournament', lambda cur: {'id': 5})
        invoke(admin.admin_russia_2027_recalc, {})
    assert_points(10)


def snapshot():
    return (
        sql('SELECT status,home_score,away_score FROM matches ORDER BY id'),
        sql('SELECT * FROM predictions ORDER BY user_id,match_id'),
        sql('SELECT * FROM push_delivery_log ORDER BY id'),
        sql('SELECT * FROM auto_result_notifications ORDER BY event_key'),
    )


@pytest.mark.parametrize('league', ['rpl', 'rcup'])
@pytest.mark.parametrize('action', [None, '', 'unknown', 'HIDE'])
@pytest.mark.parametrize('score', [(2, 1), (None, None), (2, None)])
def test_malformed_visibility_has_no_database_or_outbox_effect(pg, monkeypatch, league, action, score):
    finish()
    sql('UPDATE matches SET league=%s,status=\'CANCELLED\',home_score=%s,away_score=%s WHERE id=401', (league, *score), write=True)
    helper = 'get_required_rpl_tournament' if league == 'rpl' else 'get_required_russian_cup_tournament'
    monkeypatch.setattr(admin, helper, lambda cur: pytest.fail('malformed action must be rejected before mutation access'))
    handler = admin.admin_russia_2027_visibility if league == 'rpl' else admin.admin_russian_cup_visibility
    before = snapshot()
    invoke(handler, {} if action is None else {'visibility_action': action})
    assert snapshot() == before


@pytest.mark.parametrize('existing_event', [False, True])
def test_metadata_only_edit_repairs_points_without_creating_result_event(pg, existing_event):
    finish()
    if not existing_event:
        sql('DELETE FROM push_delivery_log', write=True)
    before = sql('SELECT * FROM push_delivery_log ORDER BY id')
    # Recalculation remains mandatory even for metadata-only changes.
    sql('UPDATE predictions SET points=0 WHERE match_id=401', write=True)
    for _ in range(2):
        invoke(admin.admin_edit_match, {
            'home_team': 'Новое название', 'away_team': 'ЦСКА',
            'match_date': '2026-01-01', 'match_time': '12:00', 'status': 'FINISHED',
        })
        assert_points(10)
        assert sql('SELECT * FROM push_delivery_log ORDER BY id') == before
    assert sql('SELECT home_team FROM matches WHERE id=401') == [('Новое название',)]


def test_score_correction_and_finish_transition_keep_notification_semantics(pg):
    finish()
    sql('DELETE FROM push_delivery_log', write=True)
    invoke(admin.admin_fix_result, {'home_score': '1', 'away_score': '1'})
    assert_points(2)
    assert len(sql('SELECT * FROM push_delivery_log')) == 1
    edit_status('SCHEDULED')
    assert_points(0)
    sql('DELETE FROM push_delivery_log', write=True)
    edit_status('FINISHED')
    assert_points(2)
    before = sql('SELECT * FROM push_delivery_log ORDER BY id')
    assert len(before) == 1
    for _ in range(3):
        scoring.recalc_match_points(401)
    assert_points(2)
    assert sql('SELECT * FROM push_delivery_log ORDER BY id') == before


def test_metadata_edit_rollback_reverts_metadata_and_points(pg, monkeypatch):
    finish()
    sql('DELETE FROM push_delivery_log', write=True)
    sql('UPDATE predictions SET points=0 WHERE match_id=401', write=True)
    before = snapshot()
    old_name = sql('SELECT home_team FROM matches WHERE id=401')
    original = scoring._update_prediction_points
    def fail_after_update(cur, *args):
        original(cur, *args)
        cur.execute('SELECT 1/0')
    monkeypatch.setattr(scoring, '_update_prediction_points', fail_after_update)
    invoke(admin.admin_edit_match, {
        'home_team': 'Must rollback', 'away_team': 'ЦСКА',
        'match_date': '2026-01-01', 'match_time': '12:00', 'status': 'FINISHED',
    })
    assert snapshot() == before
    assert sql('SELECT home_team FROM matches WHERE id=401') == old_name


@pytest.mark.parametrize('league', ['rpl', 'rcup'])
@pytest.mark.parametrize('correct', [False, True])
def test_specialized_metadata_edits_only_notify_for_changed_result(pg, monkeypatch, league, correct):
    finish()
    sql('DELETE FROM push_delivery_log', write=True)
    sql('UPDATE matches SET league=%s WHERE id=401', (league,), write=True)
    helper = 'get_required_rpl_tournament' if league == 'rpl' else 'get_required_russian_cup_tournament'
    monkeypatch.setattr(admin, helper, lambda cur: {'id': 5})
    handler = admin.admin_russia_2027_edit if league == 'rpl' else admin.admin_russian_cup_edit
    invoke(handler, {
        'home_team': 'Спартак', 'away_team': 'ЦСКА', 'stage': 'Групповой этап',
        'match_date': '2026-01-01', 'match_time': '12:00', 'status': 'FINISHED',
        'home_score': '1' if correct else '2', 'away_score': '1',
    })
    assert sql('SELECT home_team FROM matches WHERE id=401') == [('Спартак',)]
    assert_points(2 if correct else 10)
    assert len(sql('SELECT * FROM push_delivery_log')) == int(correct)
