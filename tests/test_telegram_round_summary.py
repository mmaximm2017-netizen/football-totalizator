from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from app.services import telegram_round_summary as summary
from app.utils import MSK

NOW = datetime(2026, 10, 22, 18, tzinfo=MSK)


@pytest.fixture
def reader(monkeypatch):
    cur, db = MagicMock(), MagicMock()
    db.return_value.__enter__.return_value = cur
    monkeypatch.setattr(summary, 'short_gpt_read', db)
    return cur, db


def fixture(status='FINISHED', deadline=NOW - timedelta(hours=2), score=(2, 1), kickoff=NOW - timedelta(hours=2)):
    return (1, kickoff, deadline, status, *score)


def results(cur, fixtures, rows=()):
    cur.fetchall.side_effect = [[(5, 'Чемпионат России'), (6, 'Кубок России')], fixtures, rows]


@pytest.mark.parametrize('status', ['LIVE', 'SCHEDULED', 'POSTPONED', 'CANCELLED', None])
def test_no_finished_results_never_reads_predictions(reader, status):
    cur, _ = reader
    results(cur, [fixture(status=status)])
    payload = summary.round_summary_payload('rpl', 2, NOW)
    assert payload['status'] == 'no_results' and not payload['rows']
    assert cur.execute.call_count == 3
    assert all('gpt_safe.predictions' not in call.args[0] for call in cur.execute.call_args_list)


@pytest.mark.parametrize('changes', [{'deadline': None}, {'deadline': NOW + timedelta(hours=1)},
                                      {'kickoff': NOW + timedelta(hours=1)}, {'score': (None, 1)}, {'score': (-1, 0)}])
def test_premature_or_invalid_finished_scores_do_not_reveal_totals(reader, changes):
    cur, _ = reader
    results(cur, [fixture(**changes)])
    assert summary.round_summary_payload('cup', 1, NOW)['status'] == 'no_results'
    assert cur.execute.call_count == 3


@pytest.mark.parametrize('kind,number', [([], 1), ('x', 1), ('rpl', None), ('cup', True), ('cup', 0), ('rpl', '1'), ('rpl', 10000)])
def test_bad_arguments_do_not_access_database(reader, kind, number):
    _, db = reader
    with pytest.raises(ValueError):
        summary.round_summary_payload(kind, number, NOW)
    db.assert_not_called()


def test_unknown_round_is_rejected(reader):
    cur, _ = reader
    results(cur, [])
    with pytest.raises(ValueError):
        summary.round_summary_payload('rpl', 3, NOW)
    assert cur.execute.call_count == 3


def test_tied_winners_and_stored_zero_points(reader):
    cur, _ = reader
    rows = [(2, 'Бек', 12, 2, 0, 2, 1, 3, 5, 0, 1),
            (1, 'Макс', 12, 2, 0, 1, 2, 3, 5, 0, 1),
            (3, 'Ноль', 0, 1, 0, 3, 3, 1, 2, 0, 3)]
    results(cur, [fixture()], rows)
    payload = summary.round_summary_payload('cup', 2, NOW)
    assert payload['status'] == 'final'
    assert [row['winner'] for row in payload['rows']] == [True, True, False]
    assert [row['movement'] for row in payload['rows']] == [1, -1, 0]
    assert payload['rows'][2]['points'] == 0
    sql, params = cur.execute.call_args.args
    assert params[0] == 6 and params[4] == [1]
    assert 'p.tournament_id = m.tournament_id' in sql
    assert 'p.predicted_home' not in sql  # No forecast-score exposure.
    assert cur.execute.call_args_list[0].args[0] == 'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ'


def test_pending_scores_do_not_become_zero_or_create_a_winner(reader):
    cur, _ = reader
    results(cur, [fixture()], [(1, 'Бек', 0, 0, 1, 1, 1, 2, 2, 1, 1)])
    payload = summary.round_summary_payload('rpl', 2, NOW)
    assert payload['status'] == 'pending_points' and not payload['rows']


def test_first_round_has_no_invented_previous_places_or_winner_without_forecasts(reader):
    cur, _ = reader
    results(cur, [fixture()], [(1, 'Бек', 0, 0, 0, 1, 1, 0, 0, 0, 1)])
    payload = summary.round_summary_payload('rpl', 1, NOW)
    row = payload['rows'][0]
    assert row['before_place'] is None and row['after_place'] is None and row['movement'] is None
    assert row['winner'] is False
