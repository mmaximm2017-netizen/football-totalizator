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

    assert callbacks == ["adm:today", "adm:predictions", "adm:table", "adm:calendar", "adm:problems", "adm:system", "adm:main"]
    assert all(callback.startswith("adm:") for callback in callbacks)


def test_dashboard_navigation_is_present_on_main_sections():
    renderers = (
        bot._format_today({"matches": []}),
        bot._format_prediction_status({"match": None, "participants": []}),
        bot._format_calendar({"matches": []}),
        bot._format_problems({"issues": []}),
        bot._format_system({"system": {}, "worker_statuses": []}),
    )
    required = {"adm:today", "adm:predictions", "adm:table", "adm:calendar", "adm:problems", "adm:system"}
    for _, markup in renderers:
        callbacks = {button["callback_data"] for row in json.loads(markup)["inline_keyboard"] for button in row}
        assert required <= callbacks
        assert all(len(callback) <= 64 for callback in callbacks)


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

    assert calls == ["answerCallbackQuery", "editMessageText"]


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
    assert calls == ["answerCallbackQuery", "editMessageText"]


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
    assert calls == ["answerCallbackQuery", "editMessageText"]


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

    assert calls[1][0] == "editMessageText"
    assert "Не удалось получить данные ТОТИШа" in calls[1][1]["text"]
    assert "secret" not in calls[1][1]["text"]


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
    sleeps = []

    assert bot.run_forever(max_cycles=4, sleep=sleeps.append) == 0
    assert sleeps == [1, 2]


def test_default_cli_mode_runs_continuous_poller(monkeypatch):
    monkeypatch.setattr(bot, "_config", lambda: ("token", 123))
    monkeypatch.setattr(bot, "run_forever", lambda: 0)
    monkeypatch.setattr("sys.argv", ["telegram_admin_bot.py"])

    assert bot.main() == 0


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
        '{"id":1,"ok":true,"payload":{"matches":[]}}\n',
        '{"id":2,"ok":true,"payload":{"matches":[]}}\n',
    ))
    monkeypatch.setattr(client, "_readline", lambda *args: next(responses))

    assert client.query("today") == {"matches": []}
    assert client.query("calendar") == {"matches": []}
    assert popen.call_count == 1
    assert len(process.stdin.writes) == 2


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


def test_prediction_score_renderer_refuses_forged_predeadline_callback():
    text, _ = bot._format_prediction_scores({"match": {"match_id": 42, "home_team": "Акрон", "away_team": "ЦСКА"}, "deadline_open": True, "predictions": [{"username": "Игрок", "home_goals": 2, "away_goals": 1}]})

    assert text == "🔒 Прогнозы пока закрыты."
    assert "2:1" not in text


def test_prediction_screen_callbacks_are_bound_to_displayed_match():
    _, markup = bot._format_prediction_status({"match": {"match_id": 42, "home_team": "Акрон", "away_team": "ЦСКА", "kickoff_time": "2026-08-28T15:00:00+00:00"}, "deadline_open": False, "participants": []})
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
    text, _ = bot._format_system({"system": {"container": "ok", "local": "ok", "db": "ok", "public": "ok"}, "worker_statuses": [{"label": "Worker дедлайнов", "state": "ok", "minutes": 4}, {"label": "Worker обработки результатов", "state": "stale", "minutes": 17}]})

    assert "🟢 Worker дедлайнов — 4 мин назад" in text
    assert "🟠 Worker обработки результатов — 17 мин назад" in text
    assert "⚪ Telegram relay — нет данных" in text


def test_system_renderer_shows_missing_and_future_worker_metadata_without_false_alert():
    statuses = service.worker_heartbeat_statuses(10_000, None, 10_060)
    text, _ = bot._format_system({"system": {}, "worker_statuses": statuses})

    assert "🔴 Worker дедлайнов — нет данных" in text
    assert "🟢 Worker обработки результатов — 0 мин назад" in text


def test_systemd_unit_matches_user_service_runtime():
    unit = (bot.ROOT / "deploy" / "systemd" / "totish-telegram-admin.service").read_text(encoding="utf-8")

    assert "User=" not in unit
    assert "WorkingDirectory=/opt/football-totalizator" in unit
    assert "ExecStart=/usr/bin/python3 scripts/telegram_admin_bot.py" in unit
    assert "WantedBy=default.target" in unit
