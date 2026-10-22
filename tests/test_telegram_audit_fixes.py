"""Regression checks for the concrete failures found in the October bot audit."""
from datetime import datetime, timezone
from io import BytesIO
import json
import threading
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

from app.services import telegram_admin_service as service
from scripts import activate_telegram_admin as activation
from scripts import telegram_admin_bot as bot
from scripts.telegram_update_queue import UpdateDispatcher

NOW = datetime(2026, 10, 22, tzinfo=timezone.utc)


def update(uid, data='ui:v:rpl:ranking:all:0:0'):
    return {'update_id': uid, 'callback_query': {'id': f'cb{uid}', 'from': {'id': 456}, 'data': data,
            'message': {'chat': {'id': 123}, 'message_id': 7}}}


def http_error(code, description, retry_after=0):
    body = json.dumps({'description': description, 'parameters': {'retry_after': retry_after}}).encode()
    return HTTPError('https://invalid.example', code, 'error', {}, BytesIO(body))


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
    monkeypatch.setattr(bot, 'STATE_DIR', tmp_path)
    monkeypatch.setattr(bot, 'OFFSET_FILE', tmp_path / 'offset.json')
    monkeypatch.setattr(bot, '_dispatcher', None)
    monkeypatch.setattr(bot, 'load_env', lambda: {'TELEGRAM_ADMIN_USER_ID': '456'})
    bot._today_photo_messages.clear()
    bot._ui_inputs.clear()


@pytest.mark.parametrize('deadline', [None, 'invalid', NOW.replace(day=23)])
def test_administrative_scores_closed_when_deadline_not_known_to_have_passed(deadline):
    cur = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value = cur
    with patch.object(service, 'get_db', return_value=conn), patch.object(service, 'close_db'), patch.object(service, '_relevant_match', return_value={'match_id': 42, 'tournament_id': 5, 'deadline': deadline}):
        result = service.prediction_scores(42, now=NOW)
    assert result['deadline_open'] is True
    assert result['predictions'] == []
    cur.execute.assert_not_called()


def test_today_with_null_deadline_never_selects_score_fields():
    cur = MagicMock()
    cur.fetchall.side_effect = [[(42, 5, 'РПЛ', 'А', 'Б', NOW, None, 'LIVE')], [(1, 'Игрок')], [(42, 1)]]
    conn = MagicMock()
    conn.cursor.return_value = cur
    with patch.object(service, 'get_db', return_value=conn), patch.object(service, 'close_db'), patch.object(service, 'render_today_cards', return_value=[]):
        result = service.today_dashboard(now=NOW)
    assert result['matches'][0]['predictions'] == []
    assert result['matches'][0]['deadline_open'] is True
    assert not any('p.home_goals' in call.args[0] for call in cur.execute.call_args_list)


@pytest.mark.parametrize('description', ['message to edit not found', "message can't be edited", 'there is no text in the message to edit'])
def test_uneditable_screen_falls_back_to_new_message(description):
    with patch.object(bot, '_telegram_request', side_effect=[http_error(400, description), {'message_id': 99}]) as request:
        assert bot._deliver_with_retry('token', 123, 'таблица', '{}', 7) == (True, {'message_id': 99})
    assert [c.args[1] for c in request.call_args_list] == ['editMessageText', 'sendMessage']
    assert 'message_id' not in request.call_args_list[1].args[2]


@pytest.mark.parametrize('code', [400, 401, 403])
def test_permanent_http_failure_is_not_retried(code):
    with patch.object(bot, '_telegram_request', side_effect=http_error(code, 'denied')) as request:
        assert bot._deliver_with_retry('token', 123, 'text', '{}', 7, sleep=lambda _: None) == (False, None)
    assert request.call_count == 1


def test_rate_limit_waits_retry_after_and_large_wait_is_deferred():
    sleeps = []
    with patch.object(bot, '_telegram_request', side_effect=[http_error(429, 'limited', 1), {}]):
        assert bot._deliver_with_retry('token', 123, 'text', '{}', 7, sleep=sleeps.append)[0]
    assert sleeps == [1]
    with patch.object(bot, '_telegram_request', side_effect=http_error(429, 'limited', 30)) as request:
        assert not bot._deliver_with_retry('token', 123, 'text', '{}', 7, sleep=sleeps.append)[0]
    assert request.call_count == 1


def test_failed_delivery_persists_notice_before_advancing_offset(tmp_path):
    def request(token, method, payload, **kwargs):
        if method == 'getUpdates':
            return [update(10, 'adm:main')]
        if method == 'editMessageText':
            raise URLError('network')
        return {}
    with patch.object(bot, '_telegram_request', side_effect=request), patch.object(bot.time, 'sleep'):
        assert bot._poll_cycle('token', 123) == 0
    pending = json.loads((tmp_path / 'telegram-admin-recovery.json').read_text())
    assert pending['update_id'] == 10 and bot._state() == 11
    assert 'text' not in pending and 'token' not in pending
    with patch.object(bot, '_telegram_request', return_value={}) as request:
        bot._retry_delivery_notice('token', 123)
    assert request.call_args.args[1] == 'sendMessage'
    assert not (tmp_path / 'telegram-admin-recovery.json').exists()


def test_failed_recovery_write_does_not_acknowledge_update(monkeypatch):
    monkeypatch.setattr(bot, '_telegram_request', lambda *a, **kw: [update(10)])
    monkeypatch.setattr(bot, '_handle_update', lambda *a: False)
    monkeypatch.setattr(bot, '_queue_delivery_notice', lambda *a: (_ for _ in ()).throw(OSError('disk full')))
    assert bot._poll_cycle('token', 123) == 1
    assert bot._state() is None


@pytest.mark.parametrize('kind', ['rpl', 'cup'])
@pytest.mark.parametrize('section', ['ranking', 'matches', 'predictions'])
def test_period_back_returns_to_tournament_selection(kind, section):
    _, markup = bot._ui_screen(f'ui:t:{section}:{kind}', 'scope')
    back = json.loads(markup)['inline_keyboard'][-1][0]['callback_data']
    text, _ = bot._ui_screen(back, 'scope')
    assert 'Выберите турнир' in text


def test_legacy_table_has_all_participants_and_delivery_preserves_long_answer():
    rows = [{'username': f'Участник {i}', 'place': i, 'points': i} for i in range(1, 42)]
    text, _ = bot._format_table({'tournament': {'name': 'РПЛ'}, 'ranking': rows})
    assert 'Участник 41' in text
    long = '\n'.join(f'Строка {i} ' + 'я' * 100 for i in range(90))
    with patch.object(bot, '_telegram_request', return_value={}) as request:
        assert bot._deliver_with_retry('token', 123, long, 'final-keyboard', 7)[0]
    calls = request.call_args_list
    assert calls[0].args[1] == 'editMessageText'
    assert all(c.args[1] == 'sendMessage' for c in calls[1:])
    assert '\n'.join(c.args[2]['text'] for c in calls) == long
    assert all(len(c.args[2]['text']) <= bot.MAX_MESSAGE_LENGTH for c in calls)
    assert calls[-1].args[2]['reply_markup'] == 'final-keyboard'


def test_blocked_data_query_does_not_block_menu_or_overwrite_it(tmp_path):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    displayed = []
    def handle(item):
        if item['update_id'] == 1:
            entered.set()
            assert release.wait(3)
            if item['_is_current']():
                displayed.append('old table')
            finished.set()
        else:
            displayed.append('menu')
    queue = UpdateDispatcher(tmp_path / 'inbox', handle, lambda u: u['update_id'] == 2, lambda _: None)
    queue.start()
    try:
        queue.submit(update(1))
        assert entered.wait(3)
        queue.submit(update(2, 'adm:main'))
        assert displayed == ['menu']
        release.set()
        assert finished.wait(3)
    finally:
        release.set()
        queue.close()
    assert displayed == ['menu']
    assert not list((tmp_path / 'inbox').glob('*.json'))


def test_durable_queue_survives_restart_and_bounds_pending_navigation(tmp_path):
    items = []
    directory = tmp_path / 'inbox'
    first = UpdateDispatcher(directory, lambda _: None, lambda _: False, lambda _: None, capacity=1)
    first.submit(update(1))
    first.submit(update(2))
    assert [p.name for p in directory.glob('*.json')] == ['2.json']
    assert (directory / '2.json').stat().st_mode & 0o777 == 0o600
    complete = threading.Event()
    def handle(item):
        items.append(item['update_id'])
        complete.set()
    second = UpdateDispatcher(directory, handle, lambda _: False, lambda _: None)
    second.start()
    try:
        assert complete.wait(3)
    finally:
        second.close()
    assert items == [2]


def test_superseded_operation_never_calls_telegram():
    context = bot._operation.set({'current': lambda: False})
    try:
        with patch.object(bot, '_telegram_request') as request:
            assert bot._deliver_with_retry('token', 123, 'stale', '{}', 7)[0]
        request.assert_not_called()
    finally:
        bot._operation.reset(context)


def test_process_verification_rejects_old_pid_and_old_source(tmp_path):
    (tmp_path / 'runtime').mkdir()
    (tmp_path / 'scripts').mkdir()
    import hashlib
    hashes = {}
    for name in activation.MODULES:
        (tmp_path / 'scripts' / name).write_text('new')
        hashes[name] = hashlib.sha256(b'new').hexdigest()
    marker = tmp_path / 'runtime' / 'telegram-admin-process.json'
    marker.write_text(json.dumps({'pid': 12, 'hashes': hashes}))
    with patch.object(activation.subprocess, 'run', return_value=MagicMock(stdout='MainPID=12\nActiveState=active\n')):
        assert activation.verified(tmp_path)
        (tmp_path / 'scripts' / activation.MODULES[0]).write_text('newer')
        assert not activation.verified(tmp_path)
    with patch.object(activation.subprocess, 'run', return_value=MagicMock(stdout='MainPID=13\nActiveState=active\n')):
        assert not activation.verified(tmp_path)


def test_delivery_logs_identify_route_and_http_category_without_secret(caplog):
    with patch.object(bot, '_telegram_request', side_effect=lambda *a, **kw: (_ for _ in ()).throw(http_error(403, 'SECRET_FORECAST'))):
        assert bot._handle_update('SECRET_TOKEN', 123, update(10, 'adm:main')) is False
    assert 'route=adm:main' in caplog.text and 'code=403' in caplog.text
    assert 'SECRET_FORECAST' not in caplog.text and 'SECRET_TOKEN' not in caplog.text


@pytest.mark.parametrize('policy,allowed', [
    ('(root) NOPASSWD: /usr/bin/systemctl restart totish-telegram-admin.service', True),
    ('(root) NOPASSWD: /usr/bin/systemctl restart\n    totish-telegram-admin.service', True),
    ('(root) /usr/bin/systemctl restart totish-telegram-admin.service', False),
    ('(root) NOPASSWD: /usr/bin/crontab', False),
])
def test_activation_requires_specific_passwordless_restart_rule(policy, allowed):
    with patch.object(activation.os, 'geteuid', return_value=1000), patch.object(activation.subprocess, 'run', return_value=MagicMock(stdout=policy)) as run:
        if allowed:
            activation.check_access()
        else:
            with pytest.raises(RuntimeError, match='permission_missing'):
                activation.check_access()
    assert run.call_args.args[0] == ['/usr/bin/sudo', '-n', '-l']


def test_superseded_photo_does_not_reach_telegram(tmp_path):
    photo = tmp_path / 'card.png'
    photo.write_bytes(b'png')
    context = bot._operation.set({'current': lambda: False})
    try:
        with patch.object(bot, 'urlopen') as send:
            assert bot._send_today_photo('token', 123, photo, 'caption') is None
        send.assert_not_called()
    finally:
        bot._operation.reset(context)


def test_recovery_does_not_delete_newer_failure_queued_during_send(tmp_path):
    bot._queue_delivery_notice(123, 1)
    def send(*args):
        bot._queue_delivery_notice(123, 2)
        return True, {}
    with patch.object(bot, '_deliver_with_retry', side_effect=send):
        bot._retry_delivery_notice('token', 123)
    assert json.loads((tmp_path / 'telegram-admin-recovery.json').read_text())['update_id'] == 2


def test_retry_does_not_sleep_past_operation_budget():
    import time
    context = bot._operation.set({'current': lambda: True, 'deadline': time.monotonic() + .1, 'failed': False})
    try:
        with patch.object(bot, '_telegram_request', side_effect=http_error(429, 'limited', 2)) as send, patch.object(bot.time, 'sleep') as sleep:
            assert not bot._deliver_with_retry('token', 123, 'text', '{}', 7)[0]
        assert send.call_count == 1
        sleep.assert_not_called()
    finally:
        bot._operation.reset(context)
