from datetime import datetime
from unittest.mock import MagicMock

import pytest

from app.services import telegram_player_profile as profile
from app.services.telegram_analytics_queries import round_win_rows
from app.services.telegram_sql_assistant import Question
from app.utils import MSK


@pytest.mark.parametrize('tid', [5, 6])
def test_profile_uses_id_keeps_zero_and_pending_points(monkeypatch, tid):
    wins = MagicMock(return_value=[('Одинаковый ник', 0, 11, 11)])
    read = MagicMock(return_value=[('Ахмат', 'Оренбург', datetime(2026, 10, 17, tzinfo=MSK), score)
                                 for score in (10, 0, None, 99)])
    monkeypatch.setattr(profile, 'round_win_rows', wins)
    monkeypatch.setattr(profile, 'read', read)
    ranking = [(42, 'Одинаковый ник', 21, 4, 1, 0, 0), (99, 'Одинаковый ник', 10, 1, 1, 0, 0)]
    result = profile.player_profile(MagicMock(), tid, ranking, 1, True)
    assert result['user_id'] == 99 and result['place'] == 2
    assert result['round_wins'] == 0 and result['average'] == '10.00'
    assert wins.call_args.kwargs == {'user_id': 99}
    assert wins.call_args.args[2] == tid
    assert [row['points'] for row in result['recent']] == [10, 0, None, None]
    sql, params = read.call_args.args[1:]
    assert params == (tid, 99)
    assert 'm.deadline <= CURRENT_TIMESTAMP' in sql and 'm.home_score BETWEEN 0 AND 99' in sql
    assert 'm.tournament_id = p.tournament_id' in sql and 'LIMIT 5' in sql


def test_archive_and_empty_profiles_do_not_invent_statistics(monkeypatch):
    wins, read = MagicMock(return_value=[]), MagicMock(return_value=[])
    monkeypatch.setattr(profile, 'round_win_rows', wins)
    monkeypatch.setattr(profile, 'read', read)
    ranking = [(42, 'Архив', 0, 0, 0, 0, 0)]
    result = profile.player_profile(MagicMock(), 6, ranking, 0, False)
    assert result['place'] is None and result['average'] is None and result['exact'] is None
    assert result['round_wins'] is None and result['recent'] == []
    wins.assert_not_called()
    read.assert_not_called()
    assert profile.player_profile(MagicMock(), 6, ranking, 0, True)['round_wins'] is None


def test_round_wins_identity_filter_is_bound_after_ranking():
    cur = MagicMock()
    cur.fetchall.return_value = [('Игрок', 2, 11, 11)]
    round_win_rows(cur, Question(intent='round_wins'), 6, 1, user_id=42)
    sql, params = cur.execute.call_args.args
    assert params[0] == 6 and params[-3:] == (42, 42, 2)
    assert sql.index('RANK() OVER') < sql.index('u.user_id = %s')
    assert 'BOOL_AND' in sql and 'bad.points IS NULL' in sql


def test_structured_card_starts_snapshot_before_reading(monkeypatch):
    from app.services import telegram_bot_ui_service as ui

    cur, db = MagicMock(), MagicMock()
    db.return_value.__enter__.return_value = cur
    ranking = [(42, 'Игрок', 10, 1, 1, 0, 0)]
    cur.fetchall.side_effect = [[(5, 'Чемпионат России')], [('Игрок',)], ranking, [(42,)]]
    detail = MagicMock(return_value={'user_id': 42})
    monkeypatch.setattr(ui, 'short_gpt_read', db)
    monkeypatch.setattr(profile, 'player_profile', detail)
    result = ui.render('ui:v:rpl:card:all:42:0', with_players=True, paginate=False)
    assert result['profile'] == {'user_id': 42}
    detail.assert_called_once_with(cur, 5, ranking, 0, True)
    assert cur.execute.call_args_list[0].args[0] == 'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ'
