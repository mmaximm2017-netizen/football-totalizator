from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services import auto_result_finalization_service as finalizer
from scripts import auto_result_runtime as runtime
from scripts.auto_result_sources import Observation

worker = runtime.dry
SourceError = worker.SourceError


def test_parser_failure_does_not_emit_false_recovery():
    state = {'source_health': {'sports_rpl': False}}
    messages = []
    for _ in range(2):
        cache = worker.PageCache()
        cache._pages['sports_rpl'] = '<html>broken</html>'
        worker._update_source_health(state, cache.source_status(), None)
        observation = worker._guard_observation(cache, 'sports_rpl', lambda cache=cache: worker.find_sports_rpl_result(
                cache.page('sports_rpl'), home='Зенит', away='ЦСКА', match_date='2026-09-05'))
        assert observation.status == 'parser_error'
        with patch.object(worker, '_queue_message', side_effect=lambda o,m: messages.append(m)):
            worker._update_source_health(state, cache.source_status(), None)
    assert messages == []
    cache = worker.PageCache()
    worker._guard_observation(cache, 'sports_rpl', lambda: Observation('sports','not_found'))
    with patch.object(worker, '_queue_message', side_effect=lambda o,m: messages.append(m)):
        worker._update_source_health(state, cache.source_status(), None)
        worker._update_source_health(state, cache.source_status(), None)
    assert len(messages) == 1 and 'восстановился' in messages[0]


@pytest.mark.parametrize('elapsed,expected', [(119.999,'too_early'),(120,'active'),(180,'active'),(180.001,'active'),(360,'active'),(360.001,'expired_grace')])
def test_exact_window(elapsed, expected):
    kickoff = datetime(2026,9,5,tzinfo=timezone.utc)
    assert worker.window_state(kickoff, kickoff+timedelta(minutes=elapsed)) == expected


def test_worker_started_359_59_cannot_call_finalizer_after_delayed_lookup(monkeypatch, tmp_path):
    kickoff = datetime(2026,9,5,tzinfo=timezone.utc)
    m = {'id':401, 'kickoff_time':kickoff, 'scope':'rpl', 'tournament_id':5,
         'home_team':'Зенит', 'away_team':'ЦСКА', 'league':'rpl', 'match_category':'rpl'}
    monkeypatch.setattr(worker, '_load_matches', lambda now:[m])
    monkeypatch.setattr(worker.PageCache,'load_many',lambda *a:None)
    monkeypatch.setattr(worker,'observe_match',lambda *a:(Observation('a','finished',home_score=2,away_score=1),)*2)
    monkeypatch.setattr(runtime.delivery,'record_check',lambda *a:None)
    class Clock:
        @staticmethod
        def now(tz):
            return kickoff+timedelta(minutes=360,seconds=1)
    monkeypatch.setattr(runtime,'datetime',Clock)
    with patch.object(finalizer,'finalize_auto_result') as save, patch.object(runtime,'_expired') as expired:
        runtime._run_live(kickoff+timedelta(minutes=359,seconds=59),state_path=tmp_path/'state',outbox=None)
    save.assert_not_called()
    expired.assert_called_once_with(m)


@pytest.mark.parametrize('first,second,decision,expected', [
    ('not_found','not_found','waiting','не нашёл матч'),
    ('finished','not_finished','one_source_confirmed','не подтвердил завершение'),
    ('finished','finished','score_conflict','разные счета'),
])
def test_final_reason_only_reports_observed_facts(first,second,decision,expected):
    a=Observation('A',first,home_score=1,away_score=0)
    b=Observation('B',second,home_score=2,away_score=0)
    assert expected in runtime._reason((a,b),{'decision':decision})


def test_outbox_file_is_only_visible_after_complete_write(tmp_path):
    worker._queue_message(tmp_path, 'complete message')
    assert [p.read_text() for p in tmp_path.glob('*.msg')] == ['complete message']
    assert list(tmp_path.glob('*.tmp')) == []


def test_state_failure_cannot_consume_unsent_event(tmp_path):
    state={}
    with patch.object(Path,'write_text',side_effect=OSError('unavailable')), pytest.raises(OSError):
        worker._event_once(state,'key','message',tmp_path)
    assert not state['events'].get('key')


def test_auto_result_wrapper_defaults_to_user_writable_state_dir():
    wrapper = Path('scripts/run_auto_results.sh').read_text(encoding='utf-8')
    assert 'STATE_DIR="${TOTISH_AUTO_RESULTS_STATE_DIR:-$HOME/.local/state/totish}"' in wrapper
    assert 'LOG_FILE="${TOTISH_AUTO_RESULTS_LOG:-$STATE_DIR/auto-results.log}"' in wrapper
    assert 'LOCK_FILE="${TOTISH_AUTO_RESULTS_LOCK:-$STATE_DIR/auto-results.lock}"' in wrapper
    assert '/var/log/totish-auto-results.log' not in wrapper


def test_independent_monitor_reports_timeout(monkeypatch):
    import subprocess

    from scripts import monitor_production
    messages=[]
    monkeypatch.setattr(monitor_production,'run',lambda *a,**k: (_ for _ in ()).throw(subprocess.TimeoutExpired('worker',45)))
    monkeypatch.setattr(monitor_production,'alert',lambda *a:messages.append(a))
    assert monitor_production.check_auto_results() is False
    assert messages[0][0] == 'auto_results:monitor_failed'
