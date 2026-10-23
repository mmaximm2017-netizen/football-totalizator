import hashlib
import hmac
import json
import time
from unittest.mock import MagicMock
from urllib.parse import urlencode

import pytest

from app import create_app
from app.services import telegram_mini_app as mini
from scripts import telegram_admin_bot as bot

TOKEN = "test-bot-token"
UID = 456


def launch(uid=UID, age=0, **extra):
    fields = {"auth_date": str(int(time.time()) - age), "user": json.dumps({"id": uid, "first_name": "Макс"}), "query_id": "launch-1", **extra}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    key = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    return urlencode({**fields, "hash": hmac.new(key, check.encode(), hashlib.sha256).hexdigest()})


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ERROR_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_ADMIN_USER_ID", str(UID))
    mini._requests.clear()
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def post(client, body, raw=None):
    return client.post("/telegram-app/api", json=body, headers={"Authorization": "tma " + (launch() if raw is None else raw)})


@pytest.mark.parametrize("raw", ["", "hash=bad", launch(uid=999), launch(uid=True), launch(age=3602), launch(age=-90), launch() + "&auth_date=1", launch() + "&user=forged", launch().replace("launch-1", "tampered"), launch(user="[]"), launch(user='{"id":456,"is_bot":true}')])
def test_bad_launch_never_queries_or_uses_browser_session(client, monkeypatch, raw):
    primary, read = MagicMock(), MagicMock()
    monkeypatch.setattr("app.get_db", primary)
    monkeypatch.setattr(mini, "render", read)
    with client.session_transaction() as session:
        session["user_id"] = 1
    response = post(client, {"callback": "ui:t:ranking:rpl"}, raw)
    assert response.status_code == 401
    assert response.headers["Cache-Control"] == "no-store"
    primary.assert_not_called()
    read.assert_not_called()


def test_public_shell_is_database_free_and_has_no_secrets(client, monkeypatch):
    primary = MagicMock()
    monkeypatch.setattr("app.get_db", primary)
    with client.session_transaction() as session:
        session["user_id"] = 1
    response = client.get("/telegram-app")
    assert response.status_code == 200
    assert b'id="club-logos"' in response.data
    assert b"/static/clubs/akhmat-grozny-footballlogos-org.png" in response.data
    assert b"telegram-web-app.js" in response.data
    assert TOKEN.encode() not in response.data
    assert "frame-ancestors" in response.headers["Content-Security-Policy"]
    primary.assert_not_called()


@pytest.mark.parametrize("name", ["TELEGRAM_ERROR_BOT_TOKEN", "TELEGRAM_ADMIN_USER_ID"])
def test_missing_configuration_fails_closed(client, monkeypatch, name):
    monkeypatch.delenv(name)
    assert post(client, {"callback": "ui:t:ranking:rpl"}).status_code == 401


@pytest.mark.parametrize("kind", ["rpl", "cup"])
def test_ranking_uses_existing_read_only_renderer(client, monkeypatch, kind):
    render = MagicMock(return_value={"text": "1. Бек: 10 очк.", "rows": []})
    monkeypatch.setattr(mini, "render", render)
    response = post(client, {"callback": f"ui:t:ranking:{kind}"})
    assert response.status_code == 200
    render.assert_called_once_with(f"ui:v:{kind}:ranking:all:0:0", text=None, with_players=True, paginate=False)
    assert response.get_json()["text"] == "1. Бек: 10 очк."


@pytest.mark.parametrize("kind", ["rpl", "cup"])
def test_profile_requests_structured_readonly_details(client, monkeypatch, kind):
    render = MagicMock(return_value={"text": "Профиль", "rows": [], "profile": {"user_id": 42}})
    monkeypatch.setattr(mini, "render", render)
    response = post(client, {"callback": f"ui:v:{kind}:card:all:42:0"})
    assert response.status_code == 200 and response.get_json()["profile"]["user_id"] == 42
    render.assert_called_once_with(f"ui:v:{kind}:card:all:42:0", text=None, with_players=True, paginate=False)


def test_local_navigation_does_not_open_database(client, monkeypatch):
    render = MagicMock()
    monkeypatch.setattr(mini, "render", render)
    assert post(client, {"callback": "ui:t:stats:rpl"}).status_code == 200
    assert post(client, {"callback": "ui:p:rpl:matches:all:0:0"}).status_code == 200
    render.assert_not_called()


@pytest.mark.parametrize("callback", ["ui:tools", "adm:system", "SELECT * FROM users", "ui:invalid", "ui:v:rpl:delete:all:0:0", None, 7])
def test_invalid_actions_never_reach_renderer(client, monkeypatch, callback):
    render = MagicMock()
    monkeypatch.setattr(mini, "render", render)
    assert post(client, {"callback": callback}).status_code == 400
    render.assert_not_called()


def test_date_input_uses_same_renderer_and_deadline_policy(client, monkeypatch):
    render = MagicMock(return_value={"text": "До дедлайна прогнозы закрыты.", "rows": []})
    monkeypatch.setattr(mini, "render", render)
    data = "ui:d:cup:predictions:all:0:0"
    response = post(client, {"callback": data, "text": "22.10.2026"})
    assert response.status_code == 200
    render.assert_called_once_with(data, text="22.10.2026", paginate=False)


def test_errors_do_not_leak_database_or_launch_details(client, monkeypatch):
    monkeypatch.setattr(mini, "render", MagicMock(side_effect=RuntimeError("postgres://SECRET")))
    response = post(client, {"callback": "ui:t:ranking:rpl"})
    assert response.status_code == 503
    assert b"SECRET" not in response.data and TOKEN.encode() not in response.data


def test_burst_limit_is_bounded_and_returns_retry_after(client):
    body = {"callback": "ui:t:stats:rpl"}
    for _ in range(60):
        assert post(client, body).status_code == 200
    response = post(client, body)
    assert response.status_code == 429 and response.headers["Retry-After"] == "60"
    assert len(mini._requests) == 60


def test_question_followup_survives_worker_change_and_is_identity_bound(client, monkeypatch):
    first = post(client, {"action": "question", "text": "Покажи таблицу"}).get_json()
    assert first["state"] and first["choices"]
    # No process-local session is retained between HTTP requests.
    assert not any(k.startswith("mini:") for k in mini.assistant.SESSIONS)
    def answer(text, scope):
        _, question, _ = mini.assistant.SESSIONS[scope]
        assert question.intent == "ranking"
        assert text == "РПЛ"
        return {"text": "Таблица РПЛ"}
    monkeypatch.setattr(mini.assistant, "answer", answer)
    assert post(client, {"action": "question", "text": "РПЛ", "state": first["state"]}).get_json()["text"] == "Таблица РПЛ"
    assert post(client, {"action": "question", "text": "РПЛ", "state": first["state"] + "x"}).status_code == 400
    monkeypatch.setenv("TELEGRAM_ADMIN_USER_ID", "999")
    assert post(client, {"action": "question", "text": "РПЛ", "state": first["state"]}, launch(uid=999)).status_code == 400


def test_bot_launch_button_is_private_chat_only(monkeypatch):
    for chat_id in ("123", "-123", ""):
        monkeypatch.setattr(bot, "load_env", lambda: {"TELEGRAM_ERROR_CHAT_ID": chat_id})
        monkeypatch.delenv("TELEGRAM_ERROR_CHAT_ID", raising=False)
        buttons = [b for row in json.loads(bot.main_keyboard())["inline_keyboard"] for b in row]
        web = [b for b in buttons if "web_app" in b]
        assert bool(web) == (chat_id == "123")
        if web:
            assert web[0]["web_app"]["url"] == "https://totish.ru/telegram-app"


def test_round_matches_requires_signed_launch_before_database_access(client, monkeypatch):
    reader = MagicMock(return_value={"type": "round_matches", "matches": []})
    monkeypatch.setattr("app.routes.telegram_mini_app.matches_payload", reader)
    assert post(client, {"action": "matches", "kind": "rpl"}, raw="hash=bad").status_code == 401
    reader.assert_not_called()
    response = post(client, {"action": "matches", "kind": "cup", "round_number": 6})
    assert response.status_code == 200
    reader.assert_called_once_with("cup", 6)
    assert response.headers["Cache-Control"] == "no-store"


def test_round_summary_checks_auth_and_keeps_private_cache_policy(client, monkeypatch):
    reader = MagicMock(return_value={'type': 'round_summary', 'rows': []})
    monkeypatch.setattr('app.routes.telegram_mini_app.round_summary_payload', reader)
    assert post(client, {'action': 'round_summary', 'kind': 'rpl', 'round_number': 1}, raw='hash=bad').status_code == 401
    reader.assert_not_called()
    response = post(client, {'action': 'round_summary', 'kind': 'cup', 'round_number': 3})
    assert response.status_code == 200 and response.headers['Cache-Control'] == 'no-store'
    reader.assert_called_once_with('cup', 3)


def test_extra_matches_are_authenticated_and_cannot_select_other_tournament(client, monkeypatch):
    reader = MagicMock(return_value={'type': 'event_matches', 'matches': []})
    monkeypatch.setattr('app.routes.telegram_mini_app.event_matches_payload', reader)
    assert post(client, {'action': 'extra_matches', 'event': 'national'}, raw='hash=bad').status_code == 401
    reader.assert_not_called()
    response = post(client, {'action': 'extra_matches', 'event': 'national', 'year': 'all', 'kind': 'cup'})
    assert response.status_code == 200
    reader.assert_called_once_with('national', 'all')
    assert response.headers['Cache-Control'] == 'no-store'


def test_shell_reuses_site_flags_and_english_aliases(client):
    html = client.get('/telegram-app').get_data(as_text=True)
    logos = json.loads(html.split('<script id="club-logos" type="application/json">')[1].split('</script>')[0])
    assert logos['Россия'] == logos['Russia'] == '/static/flags/ru.svg'
    assert logos['Иран'] == '/static/flags/ir.svg'
