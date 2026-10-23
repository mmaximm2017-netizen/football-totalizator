import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.services import telegram_bot_ui_service as ui
from app.services import telegram_admin_service as service
from scripts import telegram_admin_bot as bot
from scripts.telegram_bot_ui import callback, local_screen, parse

NOW = datetime(2026, 10, 22, 10, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def state(monkeypatch):
    bot._ui_inputs.clear()
    monkeypatch.setattr(bot, "load_env", lambda: {"TELEGRAM_ADMIN_USER_ID": "456"})
    yield
    bot._ui_inputs.clear()


@contextmanager
def reader(cur):
    yield cur


def callback_update(data, sender=456):
    return {"callback_query": {"id": "cb", "from": {"id": sender}, "data": data, "message": {"chat": {"id": 123}, "message_id": 7}}}


def message(text, sender=456):
    return {"message": {"chat": {"id": 123}, "from": {"id": sender}, "text": text}}


@pytest.mark.parametrize("section", ["ranking", "matches", "predictions", "stats", "player"])
def test_section_and_tournament_navigation_do_not_wake_database(section):
    with patch.object(bot, "_helper") as helper:
        text, markup = bot._ui_screen(f"ui:s:{section}", "123:456")
        assert "турнир" in text
        rows = json.loads(markup)["inline_keyboard"]
        assert rows[0][0]["callback_data"] == f"ui:t:{section}:rpl"
        if section not in {"player", "ranking"}:
            bot._ui_screen(f"ui:t:{section}:cup", "123:456")
        helper._exchange.assert_not_called()


@pytest.mark.parametrize("kind", ["rpl", "cup"])
def test_table_selection_loads_current_ranking_immediately(kind):
    with patch.object(bot, "_helper") as helper:
        helper._exchange.return_value = {"text": "Таблица", "rows": []}
        bot._ui_screen(f"ui:t:ranking:{kind}", "123:456")
    assert helper._exchange.call_args.args[0]["callback"] == callback("v", kind, "ranking")


@pytest.mark.parametrize("kind, tid", [("rpl", 5), ("cup", 6)])
def test_table_has_no_period_controls_and_pagination_keeps_tournament(kind, tid):
    cur = MagicMock()
    def execute(_cur, question, tournament, now):
        assert tournament == tid
        assert question.intent == "ranking"
        assert question.dates == [] and question.round_number is None
        assert question.player is None
        return [f"Участник {i}" for i in range(20)], None
    with patch.object(ui, "short_gpt_read", side_effect=lambda: reader(cur)), patch.object(ui, "choose_tournament", return_value=(tid, kind)), patch.object(ui, "execute_question", side_effect=execute):
        first = ui.render(callback("p", kind, "ranking"), now=NOW)
        buttons = [b for row in first["rows"] for b in row]
        next_page = next(b["callback_data"] for b in buttons if b["text"] == "Следующие ›")
        second = ui.render(next_page, now=NOW)
        legacy = ui.render(callback("v", kind, "ranking", "yesterday", user=12), now=NOW)
    for result in (first, second, legacy):
        labels = [b["text"] for row in result["rows"] for b in row]
        assert set(labels) <= {"‹ Предыдущие", "Следующие ›", "↻ Обновить", "‹ Назад", "🏠 Меню"}
        assert result["rows"][-1][0]["callback_data"] == "ui:s:ranking"
        assert "МСК" not in result["text"] and "Весь турнир" not in result["text"]
    assert "Участник 8" in second["text"]


def test_all_generated_local_routes_are_valid_and_callbacks_fit_telegram_limit():
    queue = [f"ui:s:{section}" for section in ("ranking", "matches", "predictions", "stats", "player")]
    seen = set()
    while queue:
        data = queue.pop()
        if data in seen:
            continue
        seen.add(data)
        screen = local_screen(data)
        if not screen:
            if data.startswith("ui:"):
                parse(data)
            continue
        if "next" in screen:
            parse(screen["next"])
        for row in screen.get("rows", []):
            for button in row:
                target = button["callback_data"]
                assert len(target.encode()) <= 64
                if target.startswith("ui:"):
                    queue.append(target)
    assert len(seen) > 50


@pytest.mark.parametrize("data", ["ui:v:rpl:delete:all:0:0", "ui:v:rpl:points:all:-1:0", "ui:v:cup:points:r0:0:0", "ui:v:rpl:points:all:0:10000", "ui:v:foo:points:all:0:0", "ui: :rpl:points:all:0:0"])
def test_invalid_callbacks_rejected_before_database(data):
    with patch.object(ui, "short_gpt_read") as db, pytest.raises(ValueError):
        ui.render(data, now=NOW)
    db.assert_not_called()


def test_periods_use_moscow_and_reject_reversed_dates():
    dates, _, _ = ui._period("today", datetime(2026, 10, 21, 22, tzinfo=timezone.utc))
    assert dates == ["2026-10-22"]
    assert ui._period("d20261017-20261019", NOW)[0] == ["2026-10-17", "2026-10-18", "2026-10-19"]
    with pytest.raises(ValueError):
        ui._period("d20261019-20261017", NOW)


@pytest.mark.parametrize("text", ["за неделю", "32 октября", "17 и 19 октября", "с 19.10.2026 по 17.10.2026"])
def test_invalid_custom_period_stays_in_prompt_and_never_broadens_query(text):
    with patch.object(ui, "short_gpt_read") as db:
        result = ui.render(callback("d", "rpl", "points"), text=text, now=NOW)
    assert result["expect"] == callback("d", "rpl", "points")
    db.assert_not_called()


def test_pagination_preserves_every_character_including_long_blocks():
    lines = [f"участник {i}: " + "x" * 500 for i in range(80)]
    pages = ui._pages(lines)
    assert "\n".join(pages) == "\n".join(lines)
    assert all(len(page) <= 2400 for page in pages)
    assert "".join(ui._pages(["я" * 7000])) == "я" * 7000


@pytest.mark.parametrize("kind", ["rpl", "cup"])
@pytest.mark.parametrize("metric", ["ranking", "points", "round_wins", "average", "hits", "streak"])
def test_app_keeps_all_six_participants_with_explanations_on_one_page(kind, metric):
    notes = ["Пояснение " + "я" * 800 for _ in range(3)]
    participants = [f"{i}. Участник {i}: {7 - i} побед" for i in range(1, 7)]
    lines = notes + participants
    with patch.object(ui, "short_gpt_read", side_effect=lambda: reader(MagicMock())), patch.object(ui, "choose_tournament", return_value=(5 if kind == "rpl" else 6, kind)), patch.object(ui, "execute_question", return_value=(lines, None)):
        # A saved old page callback must recover the complete table, too.
        result = ui.render(callback("v", kind, metric, page=1), now=NOW, paginate=False)
    assert result["text"].endswith("\n".join(lines))
    assert "Страница" not in result["text"]
    assert not any(b["text"] in {"‹ Предыдущие", "Следующие ›"} for row in result["rows"] for b in row)


def test_admin_tools_callback_acknowledged_before_render_and_edits_same_message():
    events = []
    with patch.object(bot, "_telegram_request", side_effect=lambda token, method, *a, **k: events.append(method)), patch.object(bot, "_ui_screen", side_effect=lambda *a: events.append("query") or ("данные", "{}")):
        bot._handle_update("token", 123, callback_update("ui:tools"))
    assert events == ["answerCallbackQuery", "query", "editMessageText"]


def test_unauthorized_guided_callback_does_not_query():
    with patch.object(bot, "_ui_screen") as screen, patch.object(bot, "_telegram_request"):
        bot._handle_update("token", 123, callback_update(callback("v", "rpl", "ranking"), sender=999))
    screen.assert_not_called()


def test_date_input_is_scoped_and_menu_cancels_it():
    bot._ui_screen(callback("d", "cup", "points"), "123:456")
    assert "123:456" in bot._ui_inputs
    with patch.object(bot, "_deliver_with_retry", return_value=(True, {})):
        bot._handle_update("token", 123, message("/menu"))
    assert not bot._ui_inputs


def test_old_custom_date_prompt_does_not_capture_a_new_message():
    bot._ui_screen(callback("d", "cup", "points"), "123:456")
    with patch.object(bot, "_helper") as helper, patch.object(bot, "_deliver_with_retry", return_value=(True, {})):
        helper._exchange.return_value = {"text": "очки", "rows": []}
        bot._handle_update("token", 123, message("17.10.2026"))
        request = helper._exchange.call_args.args[0]
    assert request == {"action": "assistant", "text": "17.10.2026", "scope": "123:456"}
    assert not bot._ui_inputs


def test_expired_custom_date_prompt_does_not_capture_new_question():
    bot._ui_inputs["123:456"] = (-bot.UI_INPUT_TTL, callback("d", "cup", "points"))
    with patch.object(bot, "_helper") as helper, patch.object(bot, "_deliver_with_retry", return_value=(True, {})):
        helper._exchange.return_value = {"text": "очки"}
        bot._handle_update("token", 123, message("очки Бека в РПЛ"))
    assert helper._exchange.call_args.args[0]["action"] == "assistant"


def test_text_clarification_button_uses_authenticated_scope():
    with patch.object(bot, "_helper") as helper, patch.object(bot, "_deliver_with_retry", return_value=(True, {})), patch.object(bot, "_telegram_request"):
        helper._exchange.return_value = {"text": "результат"}
        bot._handle_update("token", 123, callback_update("qa:rpl"))
    assert helper._exchange.call_args.args[0] == {"action": "assistant", "text": "РПЛ", "scope": "123:456"}


def test_helper_protocol_supports_ui_without_primary_database():
    with patch.object(ui, "render", return_value={"text": "ready", "rows": []}) as render:
        result = service.dispatch_request({"action": "assistant-ui", "callback": callback("u", "cup", "card")})
    assert result["text"] == "ready"
    render.assert_called_once_with(callback("u", "cup", "card"), None)


def test_long_clarification_reply_edits_first_chunk_then_sends_remaining_in_order():
    text = ("Бек: 10 очк.\n" * 700).rstrip()
    with patch.object(bot, "_helper") as helper, patch.object(bot, "_deliver_with_retry", return_value=(True, {})) as deliver, patch.object(bot, "_telegram_request"):
        helper._exchange.return_value = {"text": text}
        bot._handle_update("token", 123, callback_update("qa:rpl"))
    calls = deliver.call_args_list
    assert calls[0].args[4] == 7
    assert all(len(call.args) == 4 for call in calls[1:])
    assert "\n".join(call.args[2] for call in calls) == text


def test_personal_period_back_button_returns_to_participant_card():
    screen = local_screen(callback("p", "cup", "points", user=12))
    assert screen["rows"][-1][0]["callback_data"] == callback("v", "cup", "card", user=12)


def test_round_picker_scoped_and_uses_calendar_rounds():
    cur = MagicMock()
    cur.fetchall.side_effect = [[(5, "Чемпионат России 🇷🇺"), (6, "Кубок России")], [(2, NOW, NOW + timedelta(days=2))]]
    with patch.object(ui, "short_gpt_read", side_effect=lambda: reader(cur)):
        result = ui.render(callback("r", "cup", "points"), now=NOW)
    assert "2 тур" in result["rows"][0][0]["text"]
    assert result["rows"][0][0]["callback_data"] == callback("v", "cup", "points", "r2")
    assert "gpt_safe.calendar_rounds" in cur.execute.call_args.args[0]
    assert cur.execute.call_args.args[1] == (6,)


def test_participant_picker_paginates_without_losing_users():
    cur = MagicMock()
    users = [(i, f"Участник {i}") for i in range(1, 20)]
    selected = []
    for page in range(3):
        cur.fetchall.side_effect = [[(5, "Чемпионат России 🇷🇺")], users]
        with patch.object(ui, "short_gpt_read", side_effect=lambda: reader(cur)):
            result = ui.render(callback("u", "rpl", "card", page=page), now=NOW)
        selected += [row[0]["text"] for row in result["rows"] if row[0]["text"].startswith("Участник")]
    assert selected == [name for _, name in users]


@pytest.mark.parametrize('kind, tid', [('rpl', 5), ('cup', 6)])
def test_mini_app_directory_reuses_ranking_identity_points_and_place(kind, tid):
    cur = MagicMock()
    ranking = [(42, 'Одинаковый ник', 12, 2, 1, 0, 0), (99, 'Одинаковый ник', 0, 0, 0, 0, 0)]
    cur.fetchall.side_effect = [[(tid, 'Чемпионат России' if tid == 5 else 'Кубок России')], ranking]
    with patch.object(ui, 'short_gpt_read', side_effect=lambda: reader(cur)):
        result = ui.render(callback('u', kind, 'card'), with_players=True, paginate=False)
    assert result['players'] == [{'user_id': 42, 'name': 'Одинаковый ник', 'points': 12, 'place': 1},
                                 {'user_id': 99, 'name': 'Одинаковый ник', 'points': 0, 'place': 2}]
    assert cur.execute.call_count == 3  # Snapshot, tournament, one ranking read.
    assert cur.execute.call_args.args[1] == (tid,)


def test_empty_directory_and_unscored_roster_have_no_invented_places():
    cur = MagicMock()
    for ranking in [[], [(42, 'Игрок', 0, 0, 0, 0, 0)]]:
        cur.fetchall.side_effect = [[(5, 'Чемпионат России')], ranking]
        with patch.object(ui, 'short_gpt_read', side_effect=lambda: reader(cur)):
            result = ui.render(callback('u', 'rpl', 'card'), with_players=True, paginate=False)
        assert all(player['place'] is None for player in result['players'])
        assert len(result['players']) == len(ranking)


def test_inline_round_picker_returns_all_rounds_with_player_and_tournament_scope():
    cur = MagicMock()
    cur.fetchall.side_effect = [[(6, 'Кубок России')], [(i, NOW, NOW) for i in range(12, 0, -1)]]
    with patch.object(ui, 'short_gpt_read', side_effect=lambda: reader(cur)):
        result = ui.render(callback('r', 'cup', 'hits', user=42, page=1), paginate=False)
    targets = [b['callback_data'] for row in result['rows'] for b in row if b['callback_data'].startswith('ui:v:')]
    assert targets == [callback('v', 'cup', 'hits', f'r{i}', 42) for i in range(12, 0, -1)]
    assert cur.execute.call_args.args[1] == (6,)
    assert not any(b['text'] in {'Следующие ›', '‹ Предыдущие'} for row in result['rows'] for b in row)


def test_mini_app_ranking_attaches_authoritative_ids_without_name_lookup():
    cur = MagicMock()
    # Same visible nickname, different IDs. Place and ID come from one ordered read.
    cur.fetchall.side_effect = [[(5, 'Чемпионат России')],
                              [(42, 'Один ник', 10, 1, 1, 0, 0), (99, 'Один ник', 7, 1, 0, 1, 0)]]
    with patch.object(ui, 'short_gpt_read', side_effect=lambda: reader(cur)):
        payload = ui.render('ui:v:rpl:ranking:all:0:0', now=NOW, with_players=True)
    assert payload['participants'] == [
        {'place': 1, 'user_id': 42, 'name': 'Один ник'},
        {'place': 2, 'user_id': 99, 'name': 'Один ник'},
    ]
    assert cur.execute.call_count == 2
    assert cur.execute.call_args.args[1] == (5,)
    assert '1. Один ник: 10 очк.' in payload['text']


@pytest.mark.parametrize('data', ['ui:s:ranking', 'ui:s:matches', 'ui:s:predictions', 'ui:s:stats', 'ui:s:player',
                                 'ui:t:ranking:cup', 'ui:v:rpl:ranking:all:0:0',
                                 'ui:r:cup:points:all:0:0', 'ui:d:rpl:points:all:0:0',
                                 'ui:v:cup:card:all:42:0', 'adm:table', 'adm:table:rpl', 'adm:table:cup:refresh'])
def test_retired_bot_buttons_offer_app_without_helper_or_database(data, monkeypatch):
    monkeypatch.setattr(bot, 'load_env', lambda: {'TELEGRAM_ADMIN_USER_ID': '456', 'TELEGRAM_ERROR_CHAT_ID': '123'})
    with patch.object(bot, '_helper') as helper, patch.object(bot, '_ui_screen') as screen, patch.object(bot, '_docker_query') as query, patch.object(bot, '_telegram_request'), patch.object(bot, '_deliver_with_retry', return_value=(True, {})) as deliver:
        assert bot._is_local_update(callback_update(data))
        assert bot._handle_update('token', 123, callback_update(data))
    helper._exchange.assert_not_called()
    screen.assert_not_called()
    query.assert_not_called()
    assert 'теперь в приложении' in deliver.call_args.args[2]
    buttons = [b for row in json.loads(deliver.call_args.args[3])['inline_keyboard'] for b in row]
    assert any(b.get('web_app', {}).get('url') == 'https://totish.ru/telegram-app' for b in buttons)
    assert not any(b.get('callback_data', '').startswith('ui:') and b.get('callback_data') != 'ui:tools' for b in buttons)


def test_start_and_menu_use_compact_app_menu_without_helper(monkeypatch):
    monkeypatch.setattr(bot, 'load_env', lambda: {'TELEGRAM_ADMIN_USER_ID': '456', 'TELEGRAM_ERROR_CHAT_ID': '123'})
    for command in ('/start', '/menu'):
        with patch.object(bot, '_helper') as helper, patch.object(bot, '_deliver_with_retry', return_value=(True, {})) as deliver:
            assert bot._handle_update('token', 123, message(command))
        helper._exchange.assert_not_called()
        assert 'в приложении' in deliver.call_args.args[2]
        buttons = [b for row in json.loads(deliver.call_args.args[3])['inline_keyboard'] for b in row]
        assert len(buttons) == 3
        assert {b.get('callback_data') for b in buttons if 'callback_data' in b} == {'adm:help', 'ui:tools'}
