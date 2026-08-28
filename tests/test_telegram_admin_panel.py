import json
from datetime import datetime, timezone
from io import BytesIO, StringIO
from subprocess import TimeoutExpired
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

from app.services import telegram_admin_service as service
from app.utils import is_before_deadline
from scripts import telegram_admin_bot as bot


def match_row(deadline):
    return (42, 5, "Чемпионат России 🇷🇺", "Акрон", "ЦСКА", datetime(2026, 8, 28, 15, 0, tzinfo=timezone.utc), deadline, "SCHEDULED")


class Cursor:
    def __init__(self, first=None, rows=None):
        self.first = first
        self.rows = rows or []
        self.queries = []

    def execute(self, query, params=None):
        self.queries.append((query, params))

    def fetchone(self):
        return self.first

    def fetchall(self):
        return self.rows


class SequencedCursor:
    def __init__(self, result_sets):
        self.result_sets = iter(result_sets)
        self.current = []
        self.queries = []

    def execute(self, query, params=None):
        self.queries.append((query, params))
        self.current = next(self.result_sets)

    def fetchall(self):
        return self.current


def db_cursor(cursor):
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn


def callback_http_error(code, description):
    return HTTPError(
        "https://api.telegram.org/bot/token/answerCallbackQuery",
        code,
        "Bad Request",
        None,
        BytesIO(description.encode("utf-8")),
    )


def test_main_keyboard_layout_is_fixed_and_safe():
    keyboard = json.loads(bot.main_keyboard())
    callbacks = [button["callback_data"] for row in keyboard["inline_keyboard"] for button in row]

    assert callbacks == ["adm:today", "adm:table", "adm:calendar", "adm:problems", "adm:system", "adm:main"]
    assert "adm:predictions" not in callbacks
    assert all(callback.startswith("adm:") for callback in callbacks)


def test_dashboard_navigation_is_present_on_main_sections():
    renderers = (
        bot._format_today({"matches": []}),
        bot._format_prediction_status({"match": None, "participants": []}),
        bot._format_calendar({"matches": []}),
        bot._format_problems({"issues": []}),
        bot._format_system({"system": {}, "worker_statuses": []}),
    )
    required = {"adm:today", "adm:table", "adm:calendar", "adm:problems", "adm:system"}
    for _, markup in renderers:
        callbacks = {button["callback_data"] for row in json.loads(markup)["inline_keyboard"] for button in row}
        assert required <= callbacks
        assert all(len(callback) <= 64 for callback in callbacks)


def test_legacy_predictions_callback_routes_to_today_dashboard():
    payload = {"ok": True, "matches": [], "photo_paths": [], "photo_error": False}
    with patch.object(bot, "_docker_query", return_value=payload) as query:
        text, _markup, returned = bot._render_callback("adm:predictions")

    query.assert_called_once_with("today-dashboard")
    assert "⚽ Сегодня" in text
    assert returned is payload


def test_unauthorized_callback_is_acknowledged_without_query():
    token_calls = []
    update = {"callback_query": {"id": "cb", "data": "adm:today", "message": {"chat": {"id": 999}, "message_id": 1}}}

    with patch.object(bot, "_telegram_request", side_effect=lambda *args, **kwargs: token_calls.append(args[1]) or True), patch.object(bot, "_docker_query") as query:
        assert bot._handle_update("token", 123, update) is True

    assert token_calls == ["answerCallbackQuery"]
    query.assert_not_called()


def test_authorized_callback_acknowledges_then_edits_message():
    calls = []
    update = {"callback_query": {"id": "cb", "data": "adm:main", "message": {"chat": {"id": 123}, "message_id": 7}}}
    with patch.object(bot, "_telegram_request", side_effect=lambda *args, **kwargs: calls.append(args[1]) or True):
        assert bot._handle_update("token", 123, update) is True

    assert calls == ["editMessageText", "answerCallbackQuery"]


def test_message_not_modified_is_benign():
    error = callback_http_error(400, "Bad Request: message is not modified")
    with patch.object(bot, "_telegram_request", side_effect=error):
        assert bot._edit_or_send("token", 123, "same", bot.main_keyboard(), message_id=7) is None


@pytest.mark.parametrize(
    "description",
    (
        "Bad Request: query is too old and response timeout expired or query ID is invalid",
        "Bad Request: query ID is invalid",
    ),
)
def test_expired_callback_acknowledgement_does_not_block_authorized_action(description):
    calls = []
    update = {"callback_query": {"id": "old", "data": "adm:today", "message": {"chat": {"id": 123}, "message_id": 7}}}

    def request(token, method, payload, timeout, **kwargs):
        calls.append(method)
        if method == "answerCallbackQuery":
            raise callback_http_error(400, description)
        return True

    with patch.object(bot, "_telegram_request", side_effect=request), patch.object(bot, "_render_callback", return_value=("today", bot.main_keyboard())) as render:
        assert bot._handle_update("token", 123, update) is True

    render.assert_called_once_with("adm:today")
    assert calls == ["editMessageText", "answerCallbackQuery"]


def test_callback_ack_network_timeout_does_not_block_authorized_action():
    calls = []
    update = {"callback_query": {"id": "fresh", "data": "adm:today", "message": {"chat": {"id": 123}, "message_id": 7}}}

    def request(token, method, payload, timeout, **kwargs):
        calls.append(method)
        if method == "answerCallbackQuery":
            raise URLError("timed out")
        return True

    with patch.object(bot, "_telegram_request", side_effect=request), patch.object(bot, "_render_callback", return_value=("today", bot.main_keyboard())) as render:
        assert bot._handle_update("token", 123, update) is True

    render.assert_called_once_with("adm:today")
    assert calls == ["editMessageText", "answerCallbackQuery"]


@pytest.mark.parametrize(
    "error",
    (
        callback_http_error(400, "Bad Request: unrelated error"),
        callback_http_error(401, "Unauthorized"),
        callback_http_error(500, "Internal Server Error"),
    ),
)
def test_nonexpired_callback_ack_errors_do_not_block_render(error):
    update = {"callback_query": {"id": "cb", "data": "adm:today", "message": {"chat": {"id": 123}, "message_id": 7}}}

    def request(token, method, payload, timeout, **kwargs):
        if method == "answerCallbackQuery":
            raise error
        return True

    with (
        patch.object(bot, "_telegram_request", side_effect=request),
        patch.object(bot, "_render_callback", return_value=("today", bot.main_keyboard())) as render,
    ):
        assert bot._handle_update("token", 123, update) is True

    render.assert_called_once_with("adm:today")


def test_callback_ack_uses_one_second_timeout():
    with patch.object(bot, "_telegram_request", return_value=True) as request:
        assert bot._answer_callback_best_effort("token", "callback") is True

    request.assert_called_once_with(
        "token",
        "answerCallbackQuery",
        {"callback_query_id": "callback"},
        timeout=1,
    )


def test_long_poll_has_grace_timeout_but_regular_post_does_not(monkeypatch):
    calls = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(self.payload).encode("utf-8")

    monkeypatch.setattr(bot, "urlopen", lambda request, timeout: calls.append(timeout) or Response({"ok": True, "result": []}))

    bot._telegram_request("token", "getUpdates", {}, timeout=50, long_poll=True)
    bot._telegram_request("token", "sendMessage", {}, timeout=5)

    assert calls == [60, 5]


def test_unknown_callback_is_safe():
    update = {"callback_query": {"id": "cb", "data": "adm:unknown", "message": {"chat": {"id": 123}, "message_id": 7}}}
    with patch.object(bot, "_telegram_request", return_value=True) as request, patch.object(bot, "_docker_query") as query:
        assert bot._handle_update("token", 123, update) is True

    request.assert_called_once()
    query.assert_not_called()


@pytest.mark.parametrize(
    "callback",
    (
        {"id": "cb", "data": "adm:today", "message": None},
        {"id": "cb", "data": "adm:today"},
        {"id": "cb", "data": "adm:today", "message": {"chat": None}},
        {"id": "cb", "data": "adm:today", "message": {"chat": {}}},
        {"id": "cb", "data": "adm:today", "message": {"chat": {"id": "123"}}},
    ),
)
def test_malformed_callbacks_never_query_totish(callback):
    with patch.object(bot, "_telegram_request", return_value=True), patch.object(bot, "_docker_query") as query:
        assert bot._handle_update("token", 123, {"callback_query": callback}) is True

    query.assert_not_called()


def test_container_query_failure_returns_safe_admin_message_without_traceback():
    calls = []
    update = {"callback_query": {"id": "cb", "data": "adm:today", "message": {"chat": {"id": 123}, "message_id": 7}}}
    with patch.object(bot, "_telegram_request", side_effect=lambda *args, **kwargs: calls.append((args[1], args[2])) or True), patch.object(bot, "_docker_query", side_effect=RuntimeError("DATABASE_URL=secret")):
        assert bot._handle_update("token", 123, update) is True

    assert calls[0][0] == "editMessageText"
    assert "Не удалось получить данные ТОТИШа" in calls[0][1]["text"]
    assert "secret" not in calls[0][1]["text"]


def test_offset_state_is_atomic_and_malformed_state_is_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "STATE_DIR", tmp_path)
    monkeypatch.setattr(bot, "OFFSET_FILE", tmp_path / "offset.json")
    bot._save_offset(42)
    assert bot._state() == 42
    bot.OFFSET_FILE.write_text("not-json", encoding="utf-8")
    assert bot._state() is None


def test_duplicate_update_is_not_processed_after_offset_persisted(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "STATE_DIR", tmp_path)
    monkeypatch.setattr(bot, "OFFSET_FILE", tmp_path / "offset.json")
    monkeypatch.setattr(bot, "LOCK_FILE", tmp_path / "lock")
    monkeypatch.setattr(bot, "_config", lambda: ("token", 123))
    monkeypatch.setattr(bot, "_lock", lambda: type("Lock", (), {"close": lambda self: None})())
    handled = []

    def fake_request(token, method, payload, timeout, **kwargs):
        if method == "getUpdates":
            if payload.get("offset") is None:
                return [{"update_id": 10, "message": {"chat": {"id": 999}}}]
            assert payload["offset"] == 11
            return []
        return True

    monkeypatch.setattr(bot, "_telegram_request", fake_request)
    monkeypatch.setattr(bot, "_handle_update", lambda *args: handled.append(args[2]["update_id"]) or True)

    assert bot.run_once() == 0
    assert bot.run_once() == 0
    assert handled == [10]


def test_continuous_poller_retries_with_bounded_backoff_and_resets_after_success(monkeypatch):
    monkeypatch.setattr(bot, "_config", lambda: ("token", 123))
    monkeypatch.setattr(bot, "_lock", lambda: type("Lock", (), {"close": lambda self: None})())
    outcomes = iter((1, 1, 0, 0))
    monkeypatch.setattr(bot, "_poll_cycle", lambda *args: next(outcomes))
    monkeypatch.setattr(bot._helper, "warmup", lambda **kwargs: {"ready": True})
    sleeps = []

    assert bot.run_forever(max_cycles=4, sleep=sleeps.append) == 0
    assert sleeps == [1, 2]


def test_default_cli_mode_runs_continuous_poller(monkeypatch):
    monkeypatch.setattr(bot, "_config", lambda: ("token", 123))
    monkeypatch.setattr(bot, "run_forever", lambda: 0)
    monkeypatch.setattr("sys.argv", ["telegram_admin_bot.py"])

    assert bot.main() == 0


def test_daemon_warmup_runs_once_before_polling(monkeypatch):
    monkeypatch.setattr(bot, "_config", lambda: ("token", 123))
    monkeypatch.setattr(bot, "_lock", lambda: type("Lock", (), {"close": lambda self: None})())
    events = []
    monkeypatch.setattr(bot._helper, "warmup", lambda **kwargs: events.append("warmup") or {"ready": True})
    monkeypatch.setattr(bot, "_poll_cycle", lambda *args: events.append("poll") or 0)

    assert bot.run_forever(max_cycles=2, sleep=lambda _: None) == 0
    assert events == ["warmup", "poll", "poll"]


def test_daemon_warmup_failure_is_nonfatal(monkeypatch):
    monkeypatch.setattr(bot, "_config", lambda: ("token", 123))
    monkeypatch.setattr(bot, "_lock", lambda: type("Lock", (), {"close": lambda self: None})())
    monkeypatch.setattr(bot._helper, "warmup", lambda **kwargs: (_ for _ in ()).throw(BrokenPipeError("restart")))
    polls = []
    monkeypatch.setattr(bot, "_poll_cycle", lambda *args: polls.append(True) or 0)

    assert bot.run_forever(max_cycles=1, sleep=lambda _: None) == 0
    assert polls == [True]


def test_send_menu_does_not_warm_helper(monkeypatch):
    monkeypatch.setattr(bot, "_config", lambda: ("token", 123))
    monkeypatch.setattr(bot, "_edit_or_send", lambda *args, **kwargs: True)
    warmup = MagicMock()
    monkeypatch.setattr(bot._helper, "warmup", warmup)
    monkeypatch.setattr("sys.argv", ["telegram_admin_bot.py", "--send-menu"])

    assert bot.main() == 0
    warmup.assert_not_called()


def test_expired_callback_advances_offset_after_successful_render(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "STATE_DIR", tmp_path)
    monkeypatch.setattr(bot, "OFFSET_FILE", tmp_path / "offset.json")
    monkeypatch.setattr(bot, "LOCK_FILE", tmp_path / "lock")
    monkeypatch.setattr(bot, "_config", lambda: ("token", 123))
    monkeypatch.setattr(bot, "_lock", lambda: type("Lock", (), {"close": lambda self: None})())
    update = {"update_id": 10, "callback_query": {"id": "old", "data": "adm:today", "message": {"chat": {"id": 123}, "message_id": 7}}}

    def request(token, method, payload, timeout, **kwargs):
        if method == "getUpdates":
            return [update]
        if method == "answerCallbackQuery":
            raise callback_http_error(400, "query is too old")
        return True

    monkeypatch.setattr(bot, "_telegram_request", request)
    with patch.object(bot, "_render_callback", return_value=("today", bot.main_keyboard())) as render:
        assert bot.run_once() == 0

    render.assert_called_once_with("adm:today")
    assert bot._state() == 11


def test_callback_network_timeout_advances_offset_after_successful_render(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "STATE_DIR", tmp_path)
    monkeypatch.setattr(bot, "OFFSET_FILE", tmp_path / "offset.json")
    monkeypatch.setattr(bot, "LOCK_FILE", tmp_path / "lock")
    monkeypatch.setattr(bot, "_config", lambda: ("token", 123))
    monkeypatch.setattr(bot, "_lock", lambda: type("Lock", (), {"close": lambda self: None})())
    update = {"update_id": 10, "callback_query": {"id": "fresh", "data": "adm:today", "message": {"chat": {"id": 123}, "message_id": 7}}}

    def request(token, method, payload, timeout, **kwargs):
        if method == "getUpdates":
            return [update]
        if method == "answerCallbackQuery":
            raise URLError("timed out")
        return True

    monkeypatch.setattr(bot, "_telegram_request", request)
    with patch.object(bot, "_render_callback", return_value=("today", bot.main_keyboard())) as render:
        assert bot.run_once() == 0

    render.assert_called_once_with("adm:today")
    assert bot._state() == 11


def test_get_updates_network_error_remains_controlled_poller_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "STATE_DIR", tmp_path)
    monkeypatch.setattr(bot, "OFFSET_FILE", tmp_path / "offset.json")
    monkeypatch.setattr(bot, "LOCK_FILE", tmp_path / "lock")
    monkeypatch.setattr(bot, "_config", lambda: ("token", 123))
    monkeypatch.setattr(bot, "_lock", lambda: type("Lock", (), {"close": lambda self: None})())
    monkeypatch.setattr(bot, "_telegram_request", lambda *args, **kwargs: (_ for _ in ()).throw(URLError("timed out")))

    assert bot.run_once() == 1
    assert bot._state() is None


def test_delivery_retries_once_then_succeeds_and_advances_offset(monkeypatch):
    updates = [{"update_id": 10, "callback_query": {"id": "cb1", "data": "adm:main", "message": {"chat": {"id": 123}, "message_id": 7}}}]
    edit_attempts = []
    saved_offsets = []

    def request(token, method, payload, timeout, **kwargs):
        if method == "getUpdates":
            return updates
        if method == "editMessageText":
            edit_attempts.append(payload["message_id"])
            if len(edit_attempts) == 1:
                raise URLError("timed out")
        return True

    monkeypatch.setattr(bot, "_state", lambda: None)
    monkeypatch.setattr(bot, "_save_offset", saved_offsets.append)
    monkeypatch.setattr(bot, "_telegram_request", request)
    monkeypatch.setattr(bot.time, "sleep", lambda _: None)

    assert bot._poll_cycle("token", 123) == 0
    assert edit_attempts == [7, 7]
    assert saved_offsets == [11]


def test_poisoned_delivery_does_not_block_next_update_or_offset(monkeypatch):
    updates = [
        {"update_id": 10, "callback_query": {"id": "cb1", "data": "adm:main", "message": {"chat": {"id": 123}, "message_id": 7}}},
        {"update_id": 11, "callback_query": {"id": "cb2", "data": "adm:main", "message": {"chat": {"id": 123}, "message_id": 8}}},
    ]
    attempts = {7: 0, 8: 0}
    saved_offsets = []

    def request(token, method, payload, timeout, **kwargs):
        if method == "getUpdates":
            return updates
        if method == "editMessageText":
            message_id = payload["message_id"]
            attempts[message_id] += 1
            if message_id == 7:
                raise URLError("timed out")
        return True

    monkeypatch.setattr(bot, "_state", lambda: None)
    monkeypatch.setattr(bot, "_save_offset", saved_offsets.append)
    monkeypatch.setattr(bot, "_telegram_request", request)
    monkeypatch.setattr(bot.time, "sleep", lambda _: None)

    assert bot._poll_cycle("token", 123) == 0
    assert attempts == {7: 2, 8: 1}
    assert saved_offsets == [11, 12]


def test_malformed_update_with_id_advances_offset_and_does_not_block_following_update(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "STATE_DIR", tmp_path)
    monkeypatch.setattr(bot, "OFFSET_FILE", tmp_path / "offset.json")
    monkeypatch.setattr(bot, "LOCK_FILE", tmp_path / "lock")
    monkeypatch.setattr(bot, "_config", lambda: ("token", 123))
    monkeypatch.setattr(bot, "_lock", lambda: type("Lock", (), {"close": lambda self: None})())
    monkeypatch.setattr(bot, "_telegram_request", lambda *args, **kwargs: [{"update_id": 10, "callback_query": {"id": "cb", "message": None}}, {"update_id": 11, "message": {"chat": {"id": 123}, "text": "/start"}}])
    handled = []
    monkeypatch.setattr(bot, "_handle_update", lambda *args: handled.append(args[2]["update_id"]) or True)

    assert bot.run_once() == 0
    assert handled == [10, 11]
    assert bot._state() == 12


@pytest.mark.skipif(bot.fcntl is None, reason="fcntl is unavailable on Windows")
def test_lock_prevents_concurrent_poller(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "STATE_DIR", tmp_path)
    monkeypatch.setattr(bot, "LOCK_FILE", tmp_path / "lock")
    first = bot._lock()
    assert first is not None
    assert bot._lock() is None
    first.close()


def test_telegram_api_timeout_is_not_exposed(monkeypatch):
    monkeypatch.setattr(bot, "urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("network")))
    with pytest.raises(TimeoutError):
        bot._telegram_request("secret-token", "getUpdates", {}, timeout=1)


def test_container_query_timeout_and_bad_json_fail_closed(monkeypatch):
    monkeypatch.setattr(
        bot,
        "_host_metadata",
        lambda _: {
            "TOTISH_DIGEST_HOST_NOW_EPOCH": "1",
            "TOTISH_DEADLINE_WORKER_MTIME": "1",
            "TOTISH_RESULT_WORKER_MTIME": "1",
            "TOTISH_CONTAINER_STATE": "running|true|false",
        },
    )
    monkeypatch.setattr(bot._helper, "query", lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutExpired("docker", 1)))
    with pytest.raises(TimeoutExpired):
        bot._docker_query("today")

    monkeypatch.setattr(bot._helper, "query", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("container_query_invalid_response")))
    with pytest.raises(RuntimeError, match="invalid_response"):
        bot._docker_query("today")


class FakePipe:
    def __init__(self):
        self.writes = []

    def write(self, value):
        self.writes.append(value)

    def flush(self):
        pass


class FakeProcess:
    def __init__(self):
        self.args = ["docker", "exec"]
        self.stdin = FakePipe()
        self.stdout = object()
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


def test_persistent_helper_reuses_one_process_for_sequential_actions(monkeypatch):
    process = FakeProcess()
    client = bot.PersistentHelper("docker")
    popen = MagicMock(return_value=process)
    monkeypatch.setattr(bot.subprocess, "Popen", popen)
    responses = iter((
        '{"id":1,"ok":true,"payload":{"ready":true}}\n',
        '{"id":2,"ok":true,"payload":{"matches":[]}}\n',
        '{"id":3,"ok":true,"payload":{"matches":[]}}\n',
    ))
    monkeypatch.setattr(client, "_readline", lambda *args: next(responses))

    assert client.warmup() == {"ready": True}
    assert client.query("today") == {"matches": []}
    assert client.query("calendar") == {"matches": []}
    assert popen.call_count == 1
    assert len(process.stdin.writes) == 3


def test_persistent_helper_restarts_after_broken_pipe(monkeypatch):
    first = FakeProcess()
    second = FakeProcess()
    client = bot.PersistentHelper("docker")
    popen = MagicMock(side_effect=(first, second))
    monkeypatch.setattr(bot.subprocess, "Popen", popen)
    responses = iter((BrokenPipeError("restart"), '{"id":1,"ok":true,"payload":{"matches":[]}}\n'))

    def read(*args):
        value = next(responses)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(client, "_readline", read)

    assert client.query("today") == {"matches": []}
    assert popen.call_count == 2
    assert first.terminated is True


def test_persistent_helper_timeout_stops_process(monkeypatch):
    process = FakeProcess()
    client = bot.PersistentHelper("docker")
    monkeypatch.setattr(bot.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(client, "_readline", lambda *args: (_ for _ in ()).throw(TimeoutExpired("helper", 1)))

    with pytest.raises(TimeoutExpired):
        client.query("today", timeout=1)

    assert process.terminated is True


def test_helper_server_handles_multiple_requests_and_rejects_malformed(monkeypatch):
    calls = []
    monkeypatch.setattr(service, "dispatch_request", lambda request: calls.append(request["action"]) or {"ok": True})
    input_stream = StringIO('{"id":1,"action":"today"}\nnot-json\n{"id":2,"action":"calendar"}\n')
    output_stream = StringIO()

    assert service.serve(input_stream, output_stream) == 0
    responses = [json.loads(line) for line in output_stream.getvalue().splitlines()]
    assert calls == ["today", "calendar"]
    assert responses[0]["ok"] is True
    assert responses[1]["ok"] is False
    assert responses[2]["ok"] is True


def test_protocol_warmup_initializes_db_without_business_data():
    cursor = Cursor(first=(1,))
    conn = db_cursor(cursor)
    with patch.object(service, "get_db", return_value=conn), patch.object(service, "close_db") as close:
        payload = service.dispatch_protocol_request({"command": "warmup"})

    assert payload == {"ready": True}
    assert cursor.queries == [("SELECT 1", None)]
    close.assert_called_once_with(conn, cursor)


def test_protocol_rejects_unknown_internal_command():
    with pytest.raises(ValueError, match="invalid_command"):
        service.dispatch_protocol_request({"command": "business-data"})
    with pytest.raises(ValueError, match="invalid_command"):
        service.dispatch_protocol_request({"command": "warmup", "match_id": 1})


@pytest.mark.parametrize(
    "payload",
    (
        {"action": "unknown"},
        {"action": "ranking", "kind": "invalid"},
        {"action": "prediction-scores", "match_id": "1"},
        {"action": "prediction-scores", "match_id": 0},
    ),
)
def test_helper_request_schema_rejects_unknown_or_invalid_values(payload):
    with pytest.raises(ValueError):
        service.dispatch_request(payload)


def test_prediction_scores_are_not_selected_before_deadline():
    deadline = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
    cursor = Cursor(first=match_row(deadline))
    conn = db_cursor(cursor)
    with patch.object(service, "get_db", return_value=conn), patch.object(service, "close_db"):
        payload = service.prediction_scores(match_id=42, now=datetime(2026, 8, 28, 11, 59, tzinfo=timezone.utc))

    assert payload["deadline_open"] is True
    assert payload["predictions"] == []
    assert cursor.queries[0][1] == (42,)
    assert len(cursor.queries) == 1
    assert "home_goals" not in cursor.queries[0][0]


def test_exact_deadline_is_closed_and_scores_can_be_selected():
    deadline = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
    cursor = Cursor(first=match_row(deadline), rows=[("Игрок", 2, 1)])
    conn = db_cursor(cursor)
    with patch.object(service, "get_db", return_value=conn), patch.object(service, "close_db"):
        payload = service.prediction_scores(match_id=42, now=deadline)

    assert is_before_deadline({"deadline": deadline}, now=deadline) is False
    assert payload["deadline_open"] is False
    assert payload["predictions"] == [{"username": "Игрок", "home_goals": 2, "away_goals": 1}]
    assert "p.home_goals" in cursor.queries[1][0]


def test_prediction_status_before_deadline_returns_participation_only():
    deadline = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
    cursor = Cursor(first=match_row(deadline), rows=[(1, "Игрок")])
    conn = db_cursor(cursor)
    with patch.object(service, "get_db", return_value=conn), patch.object(service, "close_db"), patch.object(service, "_visible_participants", return_value=[{"user_id": 1, "username": "Игрок"}]):
        payload = service.prediction_status(match_id=42, now=datetime(2026, 8, 28, 11, 0, tzinfo=timezone.utc))

    assert payload["deadline_open"] is True
    assert payload["participants"][0]["has_prediction"] is True
    assert all("home_goals" not in query for query, _ in cursor.queries)


def test_today_dashboard_mixed_deadlines_select_scores_only_for_closed_match(tmp_path):
    closed_deadline = datetime(2026, 8, 28, 11, 0, tzinfo=timezone.utc)
    open_deadline = datetime(2026, 8, 28, 13, 0, tzinfo=timezone.utc)
    matches = [
        (1, 5, "Чемпионат России 🇷🇺", "Акрон", "ЦСКА", datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc), closed_deadline, "SCHEDULED"),
        (2, 5, "Чемпионат России 🇷🇺", "Зенит", "Ростов", datetime(2026, 8, 28, 15, 0, tzinfo=timezone.utc), open_deadline, "SCHEDULED"),
    ]
    cursor = SequencedCursor(
        [
            matches,
            [(10, "Игрок A"), (11, "Игрок B")],
            [(1, 10), (2, 11)],
            [("Игрок A", 2, 1), ("Игрок B", None, None)],
        ]
    )
    conn = db_cursor(cursor)
    with (
        patch.object(service, "get_db", return_value=conn),
        patch.object(service, "close_db"),
        patch.object(service, "render_today_cards", return_value=[tmp_path / "today.png"]),
        patch.dict("os.environ", {"TELEGRAM_ADMIN_CARD_DIR": str(tmp_path)}),
    ):
        payload = service.today_dashboard(now=datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc))

    closed, opened = payload["matches"]
    assert closed["deadline_open"] is False
    assert closed["predictions"][0]["home_goals"] == 2
    assert opened["deadline_open"] is True
    assert opened["predictions"] == []
    score_queries = [(query, params) for query, params in cursor.queries if "p.home_goals" in query]
    assert len(score_queries) == 1
    assert score_queries[0][1] == (1, 5)
    assert all("p.home_goals" not in query for query, _ in cursor.queries[:3])


def test_prediction_score_renderer_refuses_forged_predeadline_callback():
    text, _ = bot._format_prediction_scores({"match": {"match_id": 42, "home_team": "Акрон", "away_team": "ЦСКА"}, "deadline_open": True, "predictions": [{"username": "Игрок", "home_goals": 2, "away_goals": 1}]})

    assert "🔒 Прогнозы пока закрыты." in text
    assert "2:1" not in text


def test_prediction_screen_callbacks_are_bound_to_displayed_match():
    _, markup = bot._format_prediction_status({"match": {"match_id": 42, "tournament_name": "Чемпионат России 🇷🇺", "home_team": "Акрон", "away_team": "ЦСКА", "kickoff_time": "2026-08-28T15:00:00+00:00"}, "deadline_open": False, "participants": []})
    callbacks = [button["callback_data"] for row in json.loads(markup)["inline_keyboard"] for button in row]

    assert "adm:pred:show:42" in callbacks
    assert "adm:pred:refresh:42" in callbacks
    assert all(len(callback) <= 64 for callback in callbacks)


def test_old_prediction_callback_queries_its_bound_match_not_new_nearest_match():
    with patch.object(bot, "_docker_query", return_value={"match": {"match_id": 42, "home_team": "Акрон", "away_team": "ЦСКА"}, "deadline_open": True, "predictions": []}) as query:
        bot._render_callback("adm:pred:show:42")

    query.assert_called_once_with("prediction-scores", match_id=42)


def test_table_opens_rpl_directly_and_preserves_tabs():
    payload = {"tournament": {"name": "Чемпионат России 🇷🇺"}, "ranking": []}
    with patch.object(bot, "_docker_query", return_value=payload) as query:
        _, markup = bot._render_callback("adm:table")

    query.assert_called_once_with("ranking", "rpl")
    callbacks = {button["callback_data"] for row in json.loads(markup)["inline_keyboard"] for button in row}
    assert {"adm:table:rpl", "adm:table:cup"} <= callbacks


def test_malformed_prediction_callback_never_reaches_docker():
    with patch.object(bot, "_docker_query") as query:
        assert bot._render_callback("adm:pred:show:not-an-id") is None

    query.assert_not_called()


def test_nonexistent_match_id_is_safe():
    cursor = Cursor(first=None)
    conn = db_cursor(cursor)
    with patch.object(service, "get_db", return_value=conn), patch.object(service, "close_db"):
        payload = service.prediction_scores(match_id=999)

    assert payload["match"] is None
    assert payload["predictions"] == []
    assert cursor.queries[0][1] == (999,)
    assert "COALESCE(t.is_active, 0) = 1" in cursor.queries[0][0]


def test_today_renderer_never_renders_future_prediction_scores():
    text, _ = bot._format_today({"matches": [{"tournament_name": "Чемпионат России 🇷🇺", "kickoff_time": "2026-08-28T15:00:00+00:00", "home_team": "Акрон", "away_team": "ЦСКА", "predicted_count": 5, "participant_count": 6, "home_goals": 2}]})

    assert "Прогнозы: 5/6" in text
    assert "2:1" not in text


def test_first_today_sends_photo_and_refresh_edits_existing_photo(tmp_path, monkeypatch):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(b"png")
    second.write_bytes(b"png")
    monkeypatch.setattr(bot, "_today_photo_path", lambda value: first if value == "first" else second)
    bot._today_photo_messages.clear()
    send = MagicMock(return_value={"message_id": 77})
    edit = MagicMock(return_value=True)
    monkeypatch.setattr(bot, "_send_today_photo", send)
    monkeypatch.setattr(bot, "_edit_today_photo", edit)

    assert bot._sync_today_photos("token", 123, {"photo_paths": ["first"], "photo_error": False}) is True
    assert bot._today_photo_messages[123] == [77]
    assert bot._sync_today_photos("token", 123, {"photo_paths": ["second"], "photo_error": False}) is True

    assert send.call_count == 1
    edit.assert_called_once()
    assert not first.exists()
    assert not second.exists()


def test_today_photo_edit_failure_falls_back_to_send(tmp_path, monkeypatch):
    image = tmp_path / "page.png"
    image.write_bytes(b"png")
    monkeypatch.setattr(bot, "_today_photo_path", lambda value: image)
    bot._today_photo_messages[123] = [77]
    monkeypatch.setattr(bot, "_edit_today_photo", MagicMock(side_effect=URLError("network")))
    send = MagicMock(return_value={"message_id": 88})
    monkeypatch.setattr(bot, "_send_today_photo", send)

    assert bot._sync_today_photos("token", 123, {"photo_paths": ["page"], "photo_error": False}) is True
    assert bot._today_photo_messages[123] == [88]
    send.assert_called_once()


def test_today_photo_not_modified_does_not_create_duplicate(tmp_path, monkeypatch):
    image = tmp_path / "same.png"
    image.write_bytes(b"png")
    monkeypatch.setattr(bot, "_today_photo_path", lambda value: image)
    bot._today_photo_messages[123] = [77]
    monkeypatch.setattr(bot, "_edit_today_photo", MagicMock(return_value=None))
    send = MagicMock()
    monkeypatch.setattr(bot, "_send_today_photo", send)

    assert bot._sync_today_photos("token", 123, {"photo_paths": ["same"], "photo_error": False}) is True
    assert bot._today_photo_messages[123] == [77]
    send.assert_not_called()


def test_send_photo_builds_real_multipart_and_returns_message_id(tmp_path, monkeypatch):
    image = tmp_path / "page.png"
    image.write_bytes(b"\x89PNG\r\nprototype")
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"ok":true,"result":{"message_id":77}}'

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(bot, "urlopen", fake_urlopen)

    result = bot._send_today_photo("token", 123, image, "Today")
    request = captured["request"]
    content_type = request.headers["Content-type"]
    boundary = content_type.split("boundary=", 1)[1].encode()

    assert request.full_url.endswith("/sendPhoto")
    assert content_type.startswith("multipart/form-data; boundary=")
    assert b'name="chat_id"\r\n\r\n123' in request.data
    assert b'name="caption"\r\n\r\nToday' in request.data
    assert b'name="photo"; filename="totish_today.png"' in request.data
    assert b"\x89PNG\r\nprototype" in request.data
    assert request.data.endswith(b"--" + boundary + b"--\r\n")
    assert result == {"message_id": 77}


def test_edit_message_media_builds_attach_photo_multipart(tmp_path, monkeypatch):
    image = tmp_path / "page.png"
    image.write_bytes(b"png-binary")
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"ok":true,"result":{"message_id":77}}'

    def fake_urlopen(request, timeout):
        captured["request"] = request
        return Response()

    monkeypatch.setattr(bot, "urlopen", fake_urlopen)

    result = bot._edit_today_photo("token", 123, 77, image, "Today")
    request = captured["request"]

    assert request.full_url.endswith("/editMessageMedia")
    assert b'name="chat_id"\r\n\r\n123' in request.data
    assert b'name="message_id"\r\n\r\n77' in request.data
    assert b'"media": "attach://photo"' in request.data
    assert b'name="photo"; filename="totish_today.png"' in request.data
    assert b"png-binary" in request.data
    assert result == {"message_id": 77}


def test_edit_message_media_not_modified_is_benign(tmp_path, monkeypatch):
    image = tmp_path / "page.png"
    image.write_bytes(b"png")
    error = callback_http_error(400, "Bad Request: message is not modified")
    monkeypatch.setattr(bot, "_multipart_photo_request", MagicMock(side_effect=error))

    assert bot._edit_today_photo("token", 123, 77, image, "Today") is None


def test_today_photo_failure_keeps_text_callback_handled(monkeypatch):
    payload = {"matches": [{"tournament_name": "РПЛ", "kickoff_time": "2026-08-28T15:00:00+00:00", "home_team": "Акрон", "away_team": "ЦСКА", "predicted_count": 1, "participant_count": 2}], "photo_paths": ["bad"], "photo_error": False}
    update = {"callback_query": {"id": "cb", "data": "adm:today", "message": {"chat": {"id": 123}, "message_id": 7}}}
    deliveries = []
    monkeypatch.setattr(bot, "_render_callback", lambda data: bot._format_today_dashboard(payload))
    monkeypatch.setattr(bot, "_deliver_with_retry", lambda *args, **kwargs: deliveries.append(args[2]) or (True, None))
    monkeypatch.setattr(bot, "_sync_today_photos", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "_answer_callback_best_effort", lambda *args: True)

    assert bot._handle_update("token", 123, update) is True
    assert len(deliveries) == 2
    assert "Все подробности" in deliveries[0]
    assert "Акрон — ЦСКА" in deliveries[1]


def test_today_without_matches_never_sends_photo(monkeypatch):
    payload = {"matches": [], "photo_paths": [], "photo_error": False}
    text, _markup, returned = bot._format_today_dashboard(payload)
    send = MagicMock()
    monkeypatch.setattr(bot, "_send_today_photo", send)

    assert "Сегодня матчей нет." in text
    assert bot._sync_today_photos("token", 123, returned) is True
    send.assert_not_called()


def test_non_today_callbacks_never_invoke_photo_pipeline(monkeypatch):
    payload = {"system": {}, "worker_statuses": []}
    with patch.object(bot, "_docker_query", return_value=payload), patch.object(bot, "_sync_today_photos") as photos:
        rendered = bot._render_callback("adm:system")

    assert len(rendered) == 2
    photos.assert_not_called()


def test_ranking_service_is_reused_without_writes():
    cursor = Cursor(first=(5, "Чемпионат России 🇷🇺"))
    conn = db_cursor(cursor)
    with patch.object(service, "get_db", return_value=conn), patch.object(service, "close_db"), patch.object(service, "get_tournament_ranking", return_value=[{"place": 1, "username": "Игрок", "points": 10}]) as ranking:
        payload = service.ranking("rpl")

    ranking.assert_called_once_with(5)
    assert payload["ranking"][0]["points"] == 10
    assert all(not any(keyword in query.upper() for keyword in ("INSERT", "UPDATE", "DELETE")) for query, _ in cursor.queries)


def test_host_metadata_reads_only_stat_for_worker_logs(monkeypatch):
    calls = []
    def fake_run(command, **kwargs):
        calls.append(command)
        if command[0] == "stat":
            return type("Result", (), {"returncode": 0, "stdout": "100\n"})()
        return type("Result", (), {"returncode": 0, "stdout": "running|true|false\n"})()
    monkeypatch.setattr(bot.subprocess, "run", fake_run)

    values = bot._host_metadata("docker")

    assert values["TOTISH_DEADLINE_WORKER_MTIME"] == "100"
    assert values["TOTISH_RESULT_WORKER_MTIME"] == "100"
    assert all(command[0] != "cat" for command in calls)


def test_system_renderer_shows_truthful_workers_and_unknown_relay():
    text, _ = bot._format_system({"system": {"container": "ok", "local": "ok", "db": "ok", "public": "ok"}, "worker_statuses": [{"key": "deadline", "label": "Worker дедлайнов", "state": "ok", "minutes": 4}, {"key": "result", "label": "Worker обработки результатов", "state": "stale", "minutes": 17}]})

    assert "🟢 Дедлайны — 4 мин назад" in text
    assert "🟠 Обработка результатов — 17 мин назад" in text
    assert "⚪ Relay — нет данных" in text


def test_system_renderer_shows_missing_and_future_worker_metadata_without_false_alert():
    statuses = service.worker_heartbeat_statuses(10_000, None, 10_060)
    text, _ = bot._format_system({"system": {}, "worker_statuses": statuses})

    assert "🔴 Дедлайны — нет данных" in text
    assert "🟢 Обработка результатов — 0 мин назад" in text


def test_systemd_unit_matches_user_service_runtime():
    unit = (bot.ROOT / "deploy" / "systemd" / "totish-telegram-admin.service").read_text(encoding="utf-8")

    assert "User=" not in unit
    assert "WorkingDirectory=/opt/football-totalizator" in unit
    assert "ExecStart=/usr/bin/python3 scripts/telegram_admin_bot.py" in unit
    assert "WantedBy=default.target" in unit


def test_visual_today_empty_and_grouped_matches(monkeypatch):
    monkeypatch.setattr(bot, "_updated_line", lambda now=None: "🕒 Обновлено: 15:00")
    empty, _ = bot._format_today({"matches": []})
    matches = [
        {"tournament_name": "Чемпионат России 🇷🇺", "kickoff_time": "2026-08-28T15:00:00+00:00", "home_team": "Акрон", "away_team": "ЦСКА", "predicted_count": 6, "participant_count": 6},
        {"tournament_name": "Чемпионат России 🇷🇺", "kickoff_time": "2026-08-28T17:00:00+00:00", "home_team": "Локомотив", "away_team": "Динамо", "predicted_count": 5, "participant_count": 6},
        {"tournament_name": "Кубок России", "kickoff_time": "2026-08-28T18:00:00+00:00", "home_team": "Зенит", "away_team": "Ростов", "predicted_count": 4, "participant_count": 6},
    ]
    text, _ = bot._format_today({"matches": matches})

    assert empty == "⚽ Сегодня\n🕒 Обновлено: 15:00\n\nСегодня матчей нет."
    assert text.count("🏆 РПЛ") == 1
    assert text.count("🏆 Кубок России") == 1
    assert "Акрон — ЦСКА\n🕕 18:00\n🎯 6/6" in text
    assert "Локомотив — Динамо\n🕗 20:00\n🎯 5/6" in text


def test_visual_prediction_status_and_scores_preserve_confidentiality(monkeypatch):
    monkeypatch.setattr(bot, "_updated_line", lambda now=None: "🕒 Обновлено: 15:00")
    status_payload = {
        "match": {"match_id": 42, "tournament_name": "Чемпионат России 🇷🇺", "home_team": "Факел", "away_team": "Зенит", "kickoff_time": "2026-08-29T12:00:00+00:00"},
        "deadline_open": True,
        "participants": [{"username": "Bowb", "has_prediction": True}, {"username": "Byza-Zenit", "has_prediction": False}],
    }
    status_text, _ = bot._format_prediction_status(status_payload)
    score_payload = {
        "match": {"match_id": 42, "tournament_name": "Чемпионат России 🇷🇺", "home_team": "Факел", "away_team": "Зенит"},
        "deadline_open": False,
        "predictions": [{"username": "Bowb", "home_goals": 1, "away_goals": 2}, {"username": "Byza-Zenit", "home_goals": None, "away_goals": None}],
    }
    score_text, _ = bot._format_prediction_scores(score_payload)

    assert "👥 Поставили: 1/2" in status_text
    assert "✅ Bowb" in status_text
    assert "❌ Byza-Zenit" in status_text
    assert "1:2" not in status_text
    assert "🔒 Сами прогнозы будут доступны после дедлайна." in status_text
    assert "🔓 Прогнозы открыты" in score_text
    assert "Bowb — 1:2" in score_text
    assert "Byza-Zenit — нет прогноза" in score_text


def test_visual_ranking_medals_and_tabs_are_preserved(monkeypatch):
    monkeypatch.setattr(bot, "_updated_line", lambda now=None: "🕒 Обновлено: 15:00")
    payload = {
        "tournament": {"name": "Чемпионат России 🇷🇺"},
        "ranking": [
            {"place": 1, "username": "A", "points": 161},
            {"place": 2, "username": "B", "points": 151},
            {"place": 3, "username": "C", "points": 148},
            {"place": 4, "username": "D", "points": 141},
        ],
    }
    text, markup = bot._format_table(payload)
    callbacks = {button["callback_data"] for row in json.loads(markup)["inline_keyboard"] for button in row}

    assert "🥇 A — 161" in text
    assert "🥈 B — 151" in text
    assert "🥉 C — 148" in text
    assert "4. D — 141" in text
    assert {"adm:table:rpl", "adm:table:cup"} <= callbacks


def test_visual_calendar_groups_moscow_dates_and_tournaments_without_locale(monkeypatch):
    monkeypatch.setattr(bot, "_updated_line", lambda now=None: "🕒 Обновлено: 15:00")
    payload = {
        "matches": [
            {"tournament_name": "Чемпионат России 🇷🇺", "kickoff_time": "2026-08-29T12:00:00+00:00", "home_team": "Факел", "away_team": "Зенит"},
            {"tournament_name": "Чемпионат России 🇷🇺", "kickoff_time": "2026-08-29T17:00:00+00:00", "home_team": "Локомотив", "away_team": "Динамо"},
            {"tournament_name": "Кубок России", "kickoff_time": "2026-09-01T13:15:00+00:00", "home_team": "Акрон", "away_team": "Локомотив"},
        ]
    }
    text, _ = bot._format_calendar(payload)

    assert text.count("29 августа") == 1
    assert text.count("1 сентября") == 1
    assert text.count("🏆 РПЛ") == 1
    assert text.count("🏆 Кубок России") == 1
    assert text.index("Факел — Зенит") < text.index("Локомотив — Динамо")


def test_visual_problems_and_message_limit(monkeypatch):
    monkeypatch.setattr(bot, "_updated_line", lambda now=None: "🕒 Обновлено: 15:00")
    healthy, _ = bot._format_problems({"issues": []})
    failed, _ = bot._format_problems({"issues": ["🔴 Ошибка"]})
    bounded, _ = bot._format_problems({"issues": ["x" * (bot.MAX_MESSAGE_LENGTH + 100)]})

    assert healthy.startswith("🟢 Всё работает штатно\n🕒 Обновлено: 15:00")
    assert "Проблем не обнаружено." in healthy
    assert failed.startswith("🚨 Обнаружена проблема\n🕒 Обновлено: 15:00")
    assert len(bounded) == bot.MAX_MESSAGE_LENGTH


def test_visual_system_blocks_and_updated_time(monkeypatch):
    monkeypatch.setattr(bot, "_updated_line", lambda now=None: "🕒 Обновлено: 15:00")
    text, _ = bot._format_system({"system": {"container": "ok", "local": "problem", "db": "unknown", "public": "ok"}, "worker_statuses": [{"key": "deadline", "label": "Worker дедлайнов", "state": "ok", "minutes": 0}, {"key": "result", "label": "Worker результатов", "state": "unavailable", "minutes": None}]})

    assert "🩺 Сервисы" in text
    assert "🟢 Сайт" in text
    assert "🔴 Local health" in text
    assert "⚪ База данных" in text
    assert "⚙️ Workers" in text
    assert "🟢 Дедлайны — 0 мин назад" in text
    assert "🔴 Обработка результатов — нет данных" in text
    assert "📡 Telegram\n\n⚪ Relay — нет данных" in text
