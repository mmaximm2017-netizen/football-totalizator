"""PR #65: per-game detail isolation and scope-correct health guidance."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event

import pytest

from scripts import auto_result_runtime as runtime

worker = runtime.dry
FIXTURES = Path(__file__).parent / 'fixtures' / 'auto_results_v2'


def game_b(score):
    data = json.loads((FIXTURES / 'sportbox_game_1380958245.json').read_text())
    data['score'] = score
    data['head_html'] = data['head_html'].replace('2 : 2', score)
    return json.dumps(data)


@pytest.mark.parametrize('failure', ['network', 'parser'])
@pytest.mark.parametrize('first', ['A', 'B'])
def test_sportbox_detail_failure_isolated_across_parallel_match_futures(monkeypatch, failure, first):
    calendar = (FIXTURES / 'sportbox_rpl_calendar.html').read_text()
    calls = []
    detail_b = [game_b('2 : 1')]
    def fetch(url):
        if url == worker.SPORTBOX_RPL:
            return calendar
        calls.append(url)
        if url.endswith('/1380958247'):
            if failure == 'network':
                raise worker.SourceUnavailable('request_failed:Timeout')
            return '{}'  # A real parser failure, not a mocked observation.
        assert url.endswith('/1380958245')
        return detail_b[0]
    monkeypatch.setattr(worker, 'fetch_text', fetch)
    monkeypatch.setattr(worker, 'find_livesport_result', lambda *a, **k:
                        worker.Observation('livesport', 'finished', home_score=2, away_score=1))
    monkeypatch.setattr(worker, 'find_sports_rpl_result', lambda *a, **k:
                        worker.Observation('sports', 'not_found'))
    cache = worker.PageCache()
    cache.load_many({'sportbox_rpl': worker.SPORTBOX_RPL})
    cache._pages.update(livesport_rpl='fixture', sports_rpl='fixture')
    matches = {name: dict(scope='rpl', home_team=home, away_team=away, match_date='2026-09-05')
               for name, home, away in [('A', 'Ростов', 'Факел'), ('B', 'Крылья Советов', 'Краснодар')]}
    started, first_done = Barrier(2), Event()
    def process(name):
        started.wait(timeout=5)
        if name != first:
            assert first_done.wait(timeout=5)
        try:
            return runtime._observe(cache, matches[name])
        finally:
            if name == first:
                first_done.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {name: pool.submit(process, name) for name in matches}
        results = {name: future.result(timeout=10) for name, future in futures.items()}
    a, b = results['A'][1][-1], results['B'][1][-1]
    assert a.status == ('source_unavailable' if failure == 'network' else 'parser_error')
    assert a.source == 'sportbox_rpl'
    assert 'game_id=1380958247' in a.detail
    assert b.status == 'finished' and (b.home_score, b.away_score) == (2, 1)
    assert results['B'][2]['decision'] == 'would_write'
    assert cache.page('sportbox_rpl') == calendar
    assert cache.source_status()['sportbox_rpl']['ok'] is True
    assert len(calls) == 2
    expected_reason = 'ошибка загрузки' if failure == 'network' else 'ошибка структуры'
    assert expected_reason in runtime._reason(results['A'][1], results['A'][2])
    # Even in the same run, no cached finished vote can leak into a new lookup.
    detail_b[0] = game_b('3 : 1')
    _, observations, decision = runtime._observe(cache, matches['B'])
    assert (observations[-1].home_score, observations[-1].away_score) == (3, 1)
    assert decision['decision'] == 'score_conflict'
    assert len(calls) == 3


@pytest.mark.parametrize('failure', ['network', 'parser'])
def test_sportbox_calendar_failure_remains_global(monkeypatch, failure):
    def fetch(url):
        assert url == worker.SPORTBOX_RPL  # No per-game request is possible.
        if failure == 'network':
            raise worker.SourceUnavailable('request_failed:Timeout')
        return '<html>changed calendar</html>'
    monkeypatch.setattr(worker, 'fetch_text', fetch)
    cache = worker.PageCache()
    cache.load_many({'sportbox_rpl': worker.SPORTBOX_RPL})
    for home in ('Ростов', 'Крылья Советов'):
        observations = worker.observe_match(cache, dict(scope='rpl', home_team=home,
                                                         away_team='Краснодар', match_date='2026-09-05'))
        assert observations[-1].status == ('source_unavailable' if failure == 'network' else 'parser_error')
    assert cache.source_status()['sportbox_rpl']['ok'] is False


@pytest.mark.parametrize('name', ['sports_rpl', 'livesport_rpl', 'sportbox_rpl',
                                   'livesport_cup', 'rfs_cup', 'sportbox_national', 'rfs_national'])
def test_outage_guidance_matches_scope_and_is_transition_only(monkeypatch, name):
    messages = []
    monkeypatch.setattr(worker, '_queue_message', lambda out, message: messages.append(message))
    state = {'source_health': {name: True}}
    for _ in range(2):
        worker._update_source_health(state, {name: {'ok': False}}, None)
    assert len(messages) == 1
    if name.endswith('_rpl'):
        assert 'два оставшихся источника' in messages[0]
        assert 'временно заблокирована' not in messages[0]
        assert 'невозможна' not in messages[0]
    else:
        assert 'временно заблокирована' in messages[0]
        assert 'других доступных источников' not in messages[0]
    for _ in range(2):
        worker._update_source_health(state, {name: {'ok': True}}, None)
    assert len(messages) == 2 and 'источник результатов восстановился' in messages[1]
