from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from app.services import telegram_mini_app_matches as matches
from app.utils import MSK

NOW = datetime(2026, 10, 22, 18, tzinfo=MSK)


@pytest.fixture
def database(monkeypatch):
    cur = MagicMock()
    db = MagicMock()
    db.return_value.__enter__.return_value = cur
    monkeypatch.setattr(matches, 'short_gpt_read', db)
    return cur, db


def fixture(mid=1, status='FINISHED', score=(2, 1), kickoff=None, deadline=None):
    return (mid, 'Зенит', 'Динамо Мх', kickoff or NOW - timedelta(hours=3),
            deadline if deadline is not None else NOW - timedelta(hours=3), status, *score)


def results(cur, fixtures, predictions=(), rounds=None):
    cur.fetchall.side_effect = [
        [(5, 'Чемпионат России'), (6, 'Кубок России')],
        rounds if rounds is not None else [(12, NOW - timedelta(days=1), NOW + timedelta(days=1))],
        fixtures,
        predictions,
    ]


@pytest.mark.parametrize('kind,tid', [('rpl', 5), ('cup', 6)])
def test_whole_round_preserves_results_zero_and_unscored_points(database, kind, tid):
    cur, _ = database
    fixtures = [fixture(mid=i) for i in range(1, 17)]
    results(cur, fixtures, [(1, 21, 'Бек', 2, 1, 10), (1, 22, 'Ноль', 0, 4, 0),
                            (1, 23, 'Ожидает', 1, 1, None), (1, 24, 'Пропуск', None, None, None)])
    payload = matches.matches_payload(kind, 12, NOW)
    assert len(payload['matches']) == 16  # A large round is never split or truncated.
    match = payload['matches'][0]
    assert match['score'] == [2, 1]
    assert match['predictions'] == [
        {'user_id': 21, 'name': 'Бек', 'score': [2, 1], 'points': 10},
        {'user_id': 22, 'name': 'Ноль', 'score': [0, 4], 'points': 0},
        {'user_id': 23, 'name': 'Ожидает', 'score': [1, 1], 'points': None},
        {'user_id': 24, 'name': 'Пропуск', 'score': None, 'points': None},
    ]
    query, params = cur.execute.call_args_list[3].args
    assert params == (tid, 12) and 'r.tournament_id = m.tournament_id' in query
    query, params = cur.execute.call_args_list[4].args
    assert params == (tid, list(range(1, 17)))
    assert 'p.tournament_id = m.tournament_id' in query
    assert 'p.points' in query


@pytest.mark.parametrize('status', ['SCHEDULED', 'LIVE', 'POSTPONED', 'CANCELLED', 'unknown', None])
def test_unfinished_never_reads_or_returns_predictions_even_with_scores(database, status):
    cur, _ = database
    results(cur, [fixture(status=status)])
    payload = matches.matches_payload('rpl', now=NOW)
    match = payload['matches'][0]
    assert match['finished'] is False
    assert 'predictions' not in match and 'score' not in match
    assert cur.execute.call_count == 4


@pytest.mark.parametrize('changes', [
    {'score': (None, 1)}, {'score': (2, -1)}, {'score': (100, 1)},
    {'kickoff': NOW + timedelta(days=1)}, {'deadline': NOW + timedelta(minutes=1)},
])
def test_invalid_or_premature_finished_result_fails_closed(database, changes):
    cur, _ = database
    results(cur, [fixture(**changes)])
    assert matches.matches_payload('rpl', now=NOW)['matches'][0]['finished'] is False
    assert cur.execute.call_count == 4


def test_missing_deadline_fails_closed(database):
    cur, _ = database
    row = list(fixture()); row[4] = None
    results(cur, [tuple(row)])
    assert matches.matches_payload('rpl', now=NOW)['matches'][0]['finished'] is False
    assert cur.execute.call_count == 4


@pytest.mark.parametrize('status', ['FINISHED', 'COMPLETE', 'completed'])
def test_only_finished_ids_enter_batched_forecast_read(database, status):
    cur, _ = database
    results(cur, [fixture(1, status), fixture(2, 'LIVE'), fixture(3, 'SCHEDULED')], [(1, 21, 'Бек', 2, 1, 10)])
    payload = matches.matches_payload('rpl', now=NOW)
    assert [m['finished'] for m in payload['matches']] == [True, False, False]
    assert cur.execute.call_args.args[1] == (5, [1])


def test_default_current_or_next_round_and_last_when_season_over(database):
    cur, _ = database
    rounds = [(11, NOW - timedelta(days=8), NOW - timedelta(days=5)),
              (12, NOW - timedelta(days=1), NOW + timedelta(days=1)),
              (13, NOW + timedelta(days=6), NOW + timedelta(days=9))]
    results(cur, [], rounds=rounds)
    assert matches.matches_payload('rpl', now=NOW)['round_number'] == 12
    results(cur, [], rounds=rounds)
    assert matches.matches_payload('rpl', now=NOW + timedelta(days=30))['round_number'] == 13


def test_empty_calendar_and_unknown_round(database):
    cur, _ = database
    results(cur, [], rounds=[])
    assert matches.matches_payload('cup', now=NOW)['matches'] == []
    results(cur, [])
    with pytest.raises(ValueError):
        matches.matches_payload('rpl', 999, NOW)
    assert cur.execute.call_count == 6  # Two snapshot settings and two calendar reads each.


@pytest.mark.parametrize('kind,round_number', [([], 1), ('bad', 1), ('rpl', True), ('cup', '12'), ('rpl', 0), ('cup', 10000)])
def test_bad_request_does_not_open_database(database, kind, round_number):
    _, db = database
    with pytest.raises(ValueError):
        matches.matches_payload(kind, round_number, NOW)
    db.assert_not_called()


def test_current_round_stays_selected_through_last_moscow_day(database):
    cur, _ = database
    rounds = [(12, NOW - timedelta(days=2), NOW - timedelta(hours=1)),
              (13, NOW + timedelta(days=6), NOW + timedelta(days=9))]
    results(cur, [], rounds=rounds)
    assert matches.matches_payload('rpl', now=NOW)['round_number'] == 12
