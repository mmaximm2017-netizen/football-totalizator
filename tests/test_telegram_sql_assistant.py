from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app import gpt_db
from app.services import telegram_sql_assistant as a
from app.services import telegram_admin_service as service
from scripts import telegram_admin_bot as bot

NOW = datetime(2026, 10, 22, 10, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def clear_sessions():
    a.SESSIONS.clear()
    yield
    a.SESSIONS.clear()


@pytest.mark.parametrize('text,intent', [
    ('Кто сейчас на первом месте?', 'leader'),
    ('Покажи таблицу РПЛ.', 'ranking'),
    ('Покажи таблцу РПЛ', 'ranking'),
    ('Сколько очков набрал Бек?', 'points'),
    ('Кто сколько набрал за 17, 18 и 19 октября?', 'points'),
    ('Кто поставил на Локомотив — Краснодар?', 'predictions'),
    ('Как поставили на сегодняшние матчи?', 'predictions'),
    ('Кто угадал точный счёт?', 'exact'),
    ('Когда Макс Зенит последний раз получал 10 очков?', 'last'),
    ('Сколько очков набрали игроки в девятом туре?', 'points'),
    ('Какие матчи сегодня?', 'matches'),
    ('Кто не сделал прогноз?', 'missing'),
    ('Какой результат у конкретного игрока за выбранный период?', 'clarify'),
])
def test_intents(text, intent):
    if intent == 'clarify':
        with pytest.raises(a.Clarify):
            a.parse_question(text, now=NOW)
    else:
        assert a.parse_question(text, now=NOW).intent == intent


@pytest.mark.parametrize('text,dates', [
    ('за 17, 18 и 19 октября', ['2026-10-17', '2026-10-18', '2026-10-19']),
    ('17 и 19 октября 2025', ['2025-10-17', '2025-10-19']),
    ('с 17 по 19 октября 2026', ['2026-10-17', '2026-10-18', '2026-10-19']),
    ('с 17.10.2026 по 19.10.2026', ['2026-10-17', '2026-10-18', '2026-10-19']),
    ('2026-10-17 и 2026-10-19', ['2026-10-17', '2026-10-19']),
    ('сегодня', ['2026-10-22']), ('вчера', ['2026-10-21']),
    ('позавчера', ['2026-10-20']), ('завтра', ['2026-10-23']),
])
def test_dates(text, dates):
    assert a.parse_dates(text, NOW) == dates


def test_midnight_is_moscow():
    assert a.parse_dates('сегодня', datetime(2026, 10, 21, 22, tzinfo=timezone.utc)) == ['2026-10-22']


@pytest.mark.parametrize('text', ['32 октября', '31.02.2026', 'за неделю', 'за 17 и 19', 'с 19.10 по 17.10', 'с 2024-01-01 по 2026-10-01'])
def test_invalid_or_unsupported_dates_never_broaden(text):
    with pytest.raises(a.Clarify):
        a.parse_dates(text, NOW)


def test_round_and_filters():
    q = a.parse_question('Сколько очков набрали игроки в девятом туре РПЛ?', now=NOW)
    assert q.round_number == 9
    assert q.dates == []
    sql, params = a.match_filters(q, 5)
    assert 'm.tournament_id = %s' in sql and 'm.round_number = %s' in sql
    assert params == [5, 9]


def test_fuzzy_entity_is_never_automatic():
    values, exact = a.candidates('очки Бекк', ['Бек', 'Макс Зенит'])
    assert values == ['Бек'] and not exact
    assert a.candidates('очки Макс Зенит', ['Макс', 'Макс Зенит']) == (['Макс Зенит'], True)


class Cursor:
    def __init__(self):
        self.queries = []

    def execute(self, sql, params=()):
        self.queries.append((sql, params))

    def fetchall(self):
        sql = self.queries[-1][0]
        if 'FROM gpt_safe.tournaments' in sql:
            return [(5, 'Чемпионат России 🇷🇺'), (6, 'Кубок России')]
        if sql.startswith('SELECT username'):
            return [('Бек',), ('Макс Зенит',)]
        if sql.startswith('SELECT home_team'):
            return [('Локомотив',), ('Краснодар',)]
        if 'COALESCE(SUM' in sql:
            return [('Бек', 10, 1, 1, 0, 0)]
        return []


@contextmanager
def cursor_context(cur):
    yield cur


def test_tournament_clarification_keeps_original_player():
    cur = Cursor()
    with patch.object(a, 'short_gpt_read', side_effect=lambda: cursor_context(cur)) as connect:
        assert 'Какой турнир' in a.answer('Сколько очков набрал Бек?', '1', NOW)['text']
        connect.assert_not_called()
        result = a.answer('РПЛ', '1', NOW)['text']
    assert 'Бек: 10' in result
    aggregate = next(p for sql, p in cur.queries if 'COALESCE(SUM' in sql)
    assert aggregate[-3:-1] == ['Бек', 'Бек'] or aggregate[-3:-1] == ('Бек', 'Бек')


def test_followup_keeps_intent_and_changes_date_only():
    cur = Cursor()
    with patch.object(a, 'short_gpt_read', side_effect=lambda: cursor_context(cur)):
        a.answer('Кто сколько набрал сегодня в РПЛ?', '1', NOW)
        result = a.answer('за вчера', '1', NOW)['text']
    assert '21.10.2026' in result
    assert a.SESSIONS['1'][1].intent == 'points'


def test_context_isolation_and_expiry():
    a.SESSIONS['first'] = (0, a.Question(intent='points', tournament='рпл'), None)
    with patch.object(a, 'time') as clock, patch.object(a, 'short_gpt_read') as db:
        clock.monotonic.return_value = a.TTL + 1
        assert a.answer('РПЛ', 'second', NOW)['text'] == a.UNSUPPORTED
        assert 'first' not in a.SESSIONS
        db.assert_not_called()


@pytest.mark.parametrize('text', ['SELECT * FROM users', 'удали прогноз Бека', '/help', '/reset', 'какая погода'])
def test_local_nonqueries_do_not_open_database(text):
    with patch.object(a, 'short_gpt_read') as db:
        assert a.answer(text, '1', NOW)['text']
        db.assert_not_called()


@pytest.mark.parametrize('deadline', [NOW + timedelta(minutes=1), None])
@pytest.mark.parametrize('intent', ['predictions', 'missing'])
def test_deadline_gate_does_not_even_select_prediction_fields(deadline, intent):
    cur = MagicMock()
    cur.fetchall.return_value = [(42, 'Локомотив', 'Краснодар', NOW, deadline, 'SCHEDULED')]
    lines, _ = a.execute_question(cur, a.Question(intent=intent, dates=['2026-10-22']), 5, NOW)
    assert 'закрыты' in '\n'.join(lines)
    assert cur.execute.call_count == 1
    assert 'gpt_safe.predictions' not in cur.execute.call_args.args[0]


def test_at_deadline_predictions_are_visible_and_scoped():
    cur = MagicMock()
    cur.fetchall.side_effect = [[(42, 'Локомотив', 'Краснодар', NOW, NOW, 'LIVE')], [('Бек', 1, 0)]]
    lines, _ = a.execute_question(cur, a.Question(intent='predictions', dates=['2026-10-22']), 5, NOW)
    assert 'Бек: 1:0' in '\n'.join(lines)
    assert cur.execute.call_args.args[1][:2] == (42, 5)


@pytest.mark.parametrize('intent', ['points', 'ranking', 'leader', 'last', 'exact'])
def test_point_queries_require_finished_and_use_parameters(intent):
    cur = MagicMock()
    cur.fetchall.return_value = []
    q = a.Question(intent=intent, player="name'; DELETE FROM users; --", dates=['2026-10-17'])
    a.execute_question(cur, q, 6, NOW)
    sql, params = cur.execute.call_args.args
    assert q.player not in sql
    assert 'm.tournament_id = %s' in sql
    assert 'FINISHED' in sql and 'm.kickoff_time <= CURRENT_TIMESTAMP' in sql
    assert 'gpt_safe.predictions' in sql
    assert params[0] == 6


def test_short_connection_readonly_and_closed_on_error(monkeypatch):
    conn = MagicMock()
    monkeypatch.setenv('TOTISH_GPT_DATABASE_URL', 'postgresql://test.invalid/test')
    with patch.object(gpt_db.psycopg2, 'connect', return_value=conn):
        with pytest.raises(RuntimeError):
            with gpt_db.short_gpt_read():
                raise RuntimeError('test')
    conn.set_session.assert_called_once_with(readonly=True, autocommit=False)
    conn.rollback.assert_called_once()
    conn.close.assert_called_once()


def test_short_connection_fails_closed_without_reader_url(monkeypatch):
    monkeypatch.delenv('TOTISH_GPT_DATABASE_URL', raising=False)
    monkeypatch.setenv('DATABASE_URL', 'postgresql://primary.invalid/production')
    with patch.object(gpt_db.psycopg2, 'connect') as connect:
        with pytest.raises(gpt_db.GPTDatabaseUnavailable):
            with gpt_db.short_gpt_read():
                pass
        connect.assert_not_called()


@pytest.mark.parametrize('user_id,chat_id,allowed', [(456, -123, True), (999, -123, False), (456, 123, False), (None, -123, False)])
@pytest.mark.parametrize('callback', [False, True])
def test_user_and_chat_authorization_are_independent(user_id, chat_id, allowed, callback, monkeypatch):
    monkeypatch.setattr(bot, 'load_env', lambda: {'TELEGRAM_ADMIN_USER_ID': '456'})
    message = {'chat': {'id': chat_id}, 'from': {'id': user_id}, 'text': 'очки Бека в РПЛ'}
    update = {'callback_query': {'from': message['from'], 'message': message, 'data': 'adm:today', 'id': 'x'}} if callback else {'message': message}
    with patch.object(bot, '_helper') as helper, patch.object(bot, '_render_callback', return_value=None) as render, patch.object(bot, '_deliver_with_retry', return_value=(True, {})), patch.object(bot, '_answer_callback_best_effort'):
        helper._exchange.return_value = {'text': 'ответ'}
        bot._handle_update('token', -123, update)
        assert (render.called if callback else helper._exchange.called) is allowed


def test_missing_user_configuration_denies_even_matching_private_chat(monkeypatch):
    monkeypatch.setattr(bot, 'load_env', lambda: {})
    monkeypatch.delenv('TELEGRAM_ADMIN_USER_ID', raising=False)
    assert not bot._authorized_sender({'message': {'from': {'id': 123}, 'chat': {'id': 123}}})


def test_help_callback_and_text_do_not_query(monkeypatch):
    monkeypatch.setattr(bot, 'load_env', lambda: {'TELEGRAM_ADMIN_USER_ID': '456'})
    with patch.object(bot, '_helper') as helper, patch.object(bot, '_deliver_with_retry', return_value=(True, {})):
        bot._handle_update('token', 123, {'message': {'chat': {'id': 123}, 'from': {'id': 456}, 'text': '/help'}})
        helper._exchange.assert_not_called()
    assert 'Бек' in bot._render_callback('adm:help')[0]


def test_long_output_has_no_lost_characters():
    text = ('Бек: 10 очк.\n' * 800).rstrip('\n')
    chunks = list(bot._assistant_chunks(text))
    assert len(chunks) > 1 and all(len(c) <= bot.MAX_MESSAGE_LENGTH for c in chunks)
    assert '\n'.join(chunks) == text


def test_helper_protocol_routes_only_assistant_text_not_sql():
    with patch.object(a, 'answer', return_value={'text': 'answer'}) as answer:
        assert service.dispatch_request({'action': 'assistant', 'text': 'очки Бека', 'scope': '-123:456'}) == {'text': 'answer'}
        answer.assert_called_once_with('очки Бека', '-123:456')
    with pytest.raises(ValueError):
        service.dispatch_request({'action': 'sql', 'sql': 'SELECT 1'})
